# cnpj_interface/chat/tests/test_geoip.py
"""
Testes de chat/geoip.py, do comando `localizar_acessos` e da coluna
"Localização" do admin. Nenhum teste faz chamada externa de verdade e
nenhum depende do GeoLite2-City.mmdb: `requests`, o Reader do geoip2 e
`chat.geoip.consultar` sao sempre mockados.

Roda com:
    python manage.py test chat.tests.test_geoip
"""

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template.loader import get_template
from django.test import (
    RequestFactory, SimpleTestCase, TestCase, override_settings,
)
from django.utils import timezone
from geoip2.errors import AddressNotFoundError

from chat.geoip import (
    ATRIBUICAO,
    FONTE_PADRAO,
    GeoIPIndisponivel,
    _normalizar,
    _normalizar_maxmind,
    caminho_mmdb,
    consultar,
    fonte_ativa,
    ip_consultavel,
    ips_para_localizar,
    localizar,
    localizar_pendentes,
    verificar_fonte,
)
from chat.models import LocalizacaoIP, LogAcesso

User = get_user_model()


def criar_log(ip, dias_atras=1):
    """Log de acesso com IP e data ajustada (criado_em e auto_now_add)."""
    log = LogAcesso.objects.create(tipo=LogAcesso.TIPO_EMPRESAS_LISTA, ip=ip)
    LogAcesso.objects.filter(pk=log.pk).update(
        criado_em=timezone.now() - timedelta(days=dias_atras)
    )
    return log


def criar_local(ip, dias_atras=0, cidade='Sao Paulo', uf='SP', pais='Brazil'):
    """LocalizacaoIP com atualizado_em ajustado (auto_now=True)."""
    local = LocalizacaoIP.objects.create(ip=ip, cidade=cidade, uf=uf, pais=pais)
    LocalizacaoIP.objects.filter(pk=local.pk).update(
        atualizado_em=timezone.now() - timedelta(days=dias_atras)
    )
    return local


class IpConsultavelTestCase(SimpleTestCase):
    """Rede interna/loopback nunca tera localizacao: fica fora da fila."""

    def test_rede_interna_fica_fora(self):
        for ip in (
            '127.0.0.1', '10.1.2.3', '172.16.0.9', '192.168.3.132',
            '169.254.1.1', '::1', 'fe80::1', 'fd00::1',
        ):
            self.assertFalse(ip_consultavel(ip), ip)

    def test_ip_publico_entra(self):
        for ip in ('8.8.8.8', '200.160.2.3', '2804:14d:5c32:1::1'):
            self.assertTrue(ip_consultavel(ip), ip)

    def test_ip_invalido_fica_fora(self):
        self.assertFalse(ip_consultavel('nao-e-ip'))


class IpsParaLocalizarTestCase(TestCase):
    def setUp(self):
        criar_log('203.0.113.1')
        criar_log('203.0.113.2')
        criar_log('203.0.113.3')
        criar_log('203.0.113.4', dias_atras=200)
        criar_log('192.168.3.132')                  # rede interna: ignorado
        criar_log(None)
        criar_local('203.0.113.2', dias_atras=1)     # recente
        criar_local('203.0.113.3', dias_atras=90)    # vencida
        criar_local('203.0.113.99', dias_atras=1)    # sem log associado

    def test_entra_so_quem_falta_ou_esta_vencido(self):
        pendentes = ips_para_localizar(revalidar_dias=30)
        self.assertIn('203.0.113.1', pendentes)
        self.assertIn('203.0.113.3', pendentes)
        self.assertNotIn('203.0.113.2', pendentes)
        self.assertNotIn('203.0.113.99', pendentes)
        self.assertNotIn(None, pendentes)
        self.assertNotIn('192.168.3.132', pendentes)

    def test_revalidar_dias_none_nao_revalida(self):
        pendentes = ips_para_localizar(revalidar_dias=None)
        # entram so os que nunca foram localizados; o 3 esta vencido mas tem linha
        self.assertIn('203.0.113.1', pendentes)
        self.assertIn('203.0.113.4', pendentes)
        self.assertNotIn('203.0.113.3', pendentes)

    def test_janela_de_dias_limita_os_ips(self):
        pendentes = ips_para_localizar(dias=30, revalidar_dias=None)
        self.assertNotIn('203.0.113.4', pendentes)

    def test_limite_corta_a_lista(self):
        self.assertEqual(len(ips_para_localizar(revalidar_dias=30, limite=1)), 1)


class NormalizarTestCase(SimpleTestCase):
    def test_ip_api(self):
        dados = _normalizar({
            'status': 'success', 'country': 'Brazil', 'countryCode': 'BR',
            'region': 'SP', 'regionName': 'Sao Paulo', 'city': 'Sao Paulo',
            'lat': -23.5475, 'lon': -46.6361,
        }, 'ip-api')
        self.assertEqual(dados['uf'], 'SP')
        self.assertEqual(dados['pais'], 'Brazil')
        self.assertEqual(dados['codigo_pais'], 'BR')
        self.assertAlmostEqual(float(dados['latitude']), -23.5475, places=4)
        self.assertEqual(dados['fonte'], 'ip-api')

    def test_ipapi(self):
        dados = _normalizar({
            'city': 'Sao Paulo', 'region': 'Sao Paulo', 'region_code': 'SP',
            'country_name': 'Brazil', 'country_code': 'BR',
            'latitude': -23.5475, 'longitude': -46.6361,
        }, 'ipapi')
        self.assertEqual(dados['uf'], 'SP')
        self.assertEqual(dados['pais'], 'Brazil')
        self.assertEqual(dados['fonte'], 'ipapi')

    def test_coordenada_invalida_vira_none(self):
        self.assertIsNone(_normalizar({'lat': '', 'lon': None}, 'ip-api')['latitude'])


class ConsultarTestCase(SimpleTestCase):
    """A fonte externa e' sempre mockada."""

    def _resposta(self, dados):
        class Resposta:
            def raise_for_status(self):
                pass

            def json(self):
                return dados

        return Resposta()

    def test_ip_api_manda_lotes_de_100(self):
        ips = [f'203.0.113.{i}' for i in range(1, 102)]
        with patch('chat.geoip.requests.post') as post:
            post.return_value = self._resposta([
                {'status': 'success', 'city': 'X', 'region': 'SP'}
                for _ in range(100)
            ])
            consultar(ips, fonte='ip-api')
        self.assertEqual(post.call_count, 2)
        primeiro_lote = post.call_args_list[0].kwargs['json']
        self.assertEqual(len(primeiro_lote), 100)

    def test_ipapi_uma_requisicao_por_ip(self):
        with patch('chat.geoip.requests.get') as get:
            get.return_value = self._resposta({'city': 'Sao Paulo', 'country_name': 'Brazil'})
            resultado = consultar(['203.0.113.1', '203.0.113.2'], fonte='ipapi')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(set(resultado), {'203.0.113.1', '203.0.113.2'})

    def test_erro_de_rede_nao_levanta(self):
        with patch('chat.geoip.requests.post') as post:
            post.side_effect = requests.RequestException('sem rede')
            self.assertEqual(consultar(['203.0.113.1'], fonte='ip-api'), {})

    def test_fonte_invalida(self):
        with self.assertRaises(ValueError):
            consultar(['203.0.113.1'], fonte='inexistente')

    def test_lista_vazia_nao_chama_a_rede(self):
        with patch('chat.geoip.requests.post') as post:
            self.assertEqual(consultar([], fonte='ip-api'), {})
        post.assert_not_called()


class MaxmindTestCase(SimpleTestCase):
    """Fonte local: o Reader do geoip2 e' sempre mockado."""

    def _registro(self, **extra):
        dados = {
            'city': SimpleNamespace(name='Sao Paulo'),
            'subdivisions': [SimpleNamespace(iso_code='SP', name='Sao Paulo')],
            'country': SimpleNamespace(name='Brazil', iso_code='BR'),
            'location': SimpleNamespace(latitude=-23.5475, longitude=-46.6361),
        }
        dados.update(extra)
        return SimpleNamespace(**dados)

    def test_normaliza_registro(self):
        dados = _normalizar_maxmind(self._registro())
        self.assertEqual(dados['cidade'], 'Sao Paulo')
        self.assertEqual(dados['uf'], 'SP')
        self.assertEqual(dados['regiao'], 'Sao Paulo')
        self.assertEqual(dados['pais'], 'Brazil')
        self.assertEqual(dados['codigo_pais'], 'BR')
        self.assertAlmostEqual(float(dados['latitude']), -23.5475, places=4)
        self.assertEqual(dados['fonte'], 'maxmind')

    def test_registro_sem_cidade_nem_subdivisao(self):
        registro = self._registro(
            city=SimpleNamespace(name=None),
            subdivisions=[],
            location=SimpleNamespace(latitude=None, longitude=None),
        )
        dados = _normalizar_maxmind(registro)
        self.assertEqual(
            (dados['cidade'], dados['regiao'], dados['uf']), ('', '', ''),
        )
        self.assertIsNone(dados['latitude'])

    def test_consulta_varios_ips(self):
        leitor = SimpleNamespace(city=lambda ip: self._registro())
        with patch('chat.geoip._leitor_maxmind', return_value=leitor):
            resultado = consultar(
                ['203.0.113.1', '203.0.113.2'], fonte='maxmind',
            )
        self.assertEqual(set(resultado), {'203.0.113.1', '203.0.113.2'})
        self.assertEqual(resultado['203.0.113.1']['fonte'], 'maxmind')

    def test_ip_fora_da_base_fica_de_fora(self):
        def city(ip):
            raise AddressNotFoundError(ip)

        leitor = SimpleNamespace(city=city)
        with patch('chat.geoip._leitor_maxmind', return_value=leitor):
            self.assertEqual(consultar(['203.0.113.9'], fonte='maxmind'), {})


class FonteTestCase(SimpleTestCase):
    """Escolha e validacao da fonte (settings.GEOIP_FONTE)."""

    def test_padrao_e_maxmind(self):
        self.assertEqual(FONTE_PADRAO, 'maxmind')
        self.assertEqual(fonte_ativa(), 'maxmind')

    @override_settings(GEOIP_FONTE='')
    def test_setting_vazio_cai_no_padrao(self):
        self.assertEqual(fonte_ativa(), FONTE_PADRAO)

    @override_settings(GEOIP_CIDADE_MMDB='')
    def test_caminho_padrao_do_mmdb(self):
        self.assertTrue(
            str(caminho_mmdb()).endswith('geoip/GeoLite2-City.mmdb')
        )

    @override_settings(GEOIP_CIDADE_MMDB='/tmp/nao-existe-geoip.mmdb')
    def test_maxmind_sem_base_avisa(self):
        with self.assertRaises(GeoIPIndisponivel):
            verificar_fonte('maxmind')

    @override_settings(GEOIP_CIDADE_MMDB='/tmp/nao-existe-geoip.mmdb')
    def test_ipapi_nao_precisa_da_base(self):
        self.assertEqual(verificar_fonte('ipapi'), 'ipapi')

    def test_fonte_invalida_avisa(self):
        with self.assertRaises(GeoIPIndisponivel):
            verificar_fonte('inexistente')

    def test_atribuicao_credita_a_maxmind(self):
        self.assertIn('GeoLite2', ATRIBUICAO)
        self.assertIn('MaxMind', ATRIBUICAO)
        self.assertIn('https://www.maxmind.com', ATRIBUICAO)

    def test_rodape_do_site_mostra_a_atribuicao(self):
        html = get_template('core/base.html').template.source
        self.assertIn('GeoLite2', html)
        self.assertIn('https://www.maxmind.com', html)


class LocalizarTestCase(TestCase):
    def setUp(self):
        criar_log('203.0.113.1')
        criar_log('203.0.113.2')

    def test_grava_e_conta_os_sem_dados(self):
        dados = {'203.0.113.1': {
            'cidade': 'Sao Paulo', 'regiao': 'Sao Paulo', 'uf': 'SP',
            'pais': 'Brazil', 'codigo_pais': 'BR',
            'latitude': None, 'longitude': None, 'fonte': 'ip-api',
        }}
        with patch('chat.geoip.consultar', return_value=dados):
            gravados, sem_dados = localizar(['203.0.113.1', '203.0.113.2'])
        self.assertEqual((gravados, sem_dados), (1, 1))
        self.assertEqual(
            LocalizacaoIP.objects.get(ip='203.0.113.1').cidade, 'Sao Paulo',
        )

    def test_atualiza_a_linha_existente(self):
        criar_local('203.0.113.1', cidade='Antiga')
        dados = {'203.0.113.1': {'cidade': 'Nova', 'uf': 'SP', 'fonte': 'ip-api'}}
        with patch('chat.geoip.consultar', return_value=dados):
            localizar(['203.0.113.1'])
        self.assertEqual(LocalizacaoIP.objects.count(), 1)
        self.assertEqual(
            LocalizacaoIP.objects.get(ip='203.0.113.1').cidade, 'Nova',
        )


class LocalizarPendentesTestCase(TestCase):
    def setUp(self):
        criar_log('203.0.113.1')

    def test_dry_run_nao_consulta(self):
        with patch('chat.geoip.consultar') as consulta:
            resumo = localizar_pendentes(dry_run=True)
        consulta.assert_not_called()
        self.assertEqual(resumo['pendentes'], 1)
        self.assertEqual(resumo['gravados'], 0)

    def test_grava_os_pendentes(self):
        dados = {'203.0.113.1': {'cidade': 'Rio', 'fonte': 'ip-api'}}
        with patch('chat.geoip.consultar', return_value=dados) as consulta:
            resumo = localizar_pendentes()
        consulta.assert_called_once()
        self.assertEqual(resumo['gravados'], 1)
        self.assertTrue(LocalizacaoIP.objects.filter(ip='203.0.113.1').exists())


@override_settings(GEOIP_FONTE='ipapi')
class ComandoLocalizarAcessosTestCase(TestCase):
    """Fonte 'ipapi' so para nao depender do .mmdb: consultar() e' mockado."""

    def setUp(self):
        criar_log('203.0.113.1')
        criar_local('203.0.113.7', dias_atras=1)   # ja localizado (outro IP)

    def test_dry_run_nao_consulta_a_rede(self):
        saida = StringIO()
        with patch('chat.geoip.consultar') as consulta:
            call_command('localizar_acessos', '--dry-run', stdout=saida)
        consulta.assert_not_called()
        self.assertIn('DRY-RUN', saida.getvalue())
        self.assertIn('203.0.113.1', saida.getvalue())

    def test_comando_grava(self):
        saida = StringIO()
        dados = {'203.0.113.1': {'cidade': 'Curitiba', 'uf': 'PR', 'fonte': 'ip-api'}}
        with patch('chat.geoip.consultar', return_value=dados):
            call_command('localizar_acessos', stdout=saida)
        self.assertIn('Localizados 1', saida.getvalue())
        self.assertEqual(
            LocalizacaoIP.objects.get(ip='203.0.113.1').cidade, 'Curitiba',
        )

    @override_settings(
        GEOIP_FONTE='maxmind',
        GEOIP_CIDADE_MMDB='/tmp/nao-existe-geoip.mmdb',
    )
    def test_base_ausente_vira_erro_claro(self):
        with self.assertRaisesMessage(CommandError, 'atualizar_geoip.sh'):
            call_command('localizar_acessos')


class ColunaLocalizacaoTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            'admin_geo', 'geo@example.com', 'senha-teste',
        )
        self.model_admin = admin.site._registry[LogAcesso]

    def _queryset(self):
        request = RequestFactory().get('/admin/chat/logacesso/')
        request.user = self.staff
        return self.model_admin.get_queryset(request)

    def test_mostra_cidade_uf_e_pais(self):
        criar_log('203.0.113.1')
        criar_local('203.0.113.1', cidade='Sao Paulo', uf='SP', pais='Brazil')
        obj = self._queryset().get(ip='203.0.113.1')
        self.assertEqual(
            self.model_admin.localizacao(obj), 'Sao Paulo/SP, Brazil',
        )

    def test_sem_localizacao_mostra_traco(self):
        criar_log('203.0.113.2')
        obj = self._queryset().get(ip='203.0.113.2')
        self.assertEqual(self.model_admin.localizacao(obj), '-')

    def test_rodape_do_admin_mostra_a_atribuicao(self):
        self.client.force_login(self.staff)
        resposta = self.client.get('/admin/chat/logacesso/')
        self.assertContains(resposta, 'GeoLite2')
        self.assertContains(resposta, 'MaxMind')
