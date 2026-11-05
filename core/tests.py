"""Testes do app core (pagina de detalhe da empresa e log de acesso)."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from core.log_acesso import (
    TIPO_DETALHE_EMPRESA,
    TIPO_LISTA_EMPRESAS,
    pegar_ip,
    registrar_acesso,
)
from core.maps import montar_maps_nome_url, montar_maps_url
from chat.models import LogAcesso


class DetalheEmpresaTemplateTests(SimpleTestCase):
    """
    A lista de estabelecimentos (matriz + filiais) precisa mostrar a
    cidade de cada estabelecimento, nao so a UF.
    """

    @staticmethod
    def _render(estabelecimentos):
        return render_to_string('core/detalhe.html', {
            'empresa': {
                'razao_social': 'KOCH HIPERMERCADO S/A',
                'cnpj_completo': '02831172000190',
                'municipio_descricao': 'TIJUCAS',
                'uf': 'SC',
            },
            'estabelecimentos': estabelecimentos,
            'socios': [],
            'total_socios': 0,
            'total_filiais': 1,
            'total_estabelecimentos': len(estabelecimentos),
            'next_url': '/chat/',
        })

    @staticmethod
    def _filial(**extra):
        base = {
            'cnpj_completo': '02831172000270',
            'cnpj_ordem': '0002',
            'cnpj_dv': '70',
            'cd_matriz_filial': 2,
            'tipo': 'FILIAL',
            'nm_fantasia': 'KOCH PICARRAS',
            'situacao_descricao': 'ATIVA',
            'logradouro': 'AVENIDA DAS FLORES',
            'numero': '100',
            'bairro': 'CENTRO',
            'uf': 'SC',
            'data_abertura': None,
            'cnae_principal': '4711301',
        }
        base.update(extra)
        return base

    def test_filial_mostra_municipio_e_uf(self):
        html = self._render([
            self._filial(municipio_descricao='BALNEARIO DE PICARRAS'),
        ])
        self.assertIn('BALNEARIO DE PICARRAS / SC', html)

    def test_cabecalho_tem_coluna_municipio_uf(self):
        html = self._render([self._filial(municipio_descricao='TIJUCAS')])
        self.assertIn('Município/UF', html)

    def test_filial_no_exterior_usa_nm_cidade_ext(self):
        html = self._render([
            self._filial(municipio_descricao=None, nm_cidade_ext='MIAMI',
                         uf=None),
        ])
        self.assertIn('MIAMI', html)

    def test_sem_cidade_mostra_traco(self):
        html = self._render([
            self._filial(municipio_descricao=None, nm_cidade_ext=None,
                         uf=None),
        ])
        self.assertIn('>-</td>', html.replace('\n', '').replace(' ', ''))


class LogAcessoEmpresasTests(SimpleTestCase):
    """
    Log das paginas de empresas (core/log_acesso.py).

    Contrato: grava em chat.LogAcesso, nunca levanta excecao (nao pode
    derrubar a consulta) e deduplica eventos repetidos na janela.
    """

    @staticmethod
    def _request(**meta):
        request = HttpRequest()
        request.META.update(meta)
        request.user = SimpleNamespace(is_authenticated=False)
        return request

    # ----- pegar_ip -----

    def test_pegar_ip_ignora_xff_forjado_pelo_cliente(self):
        # Nginx usa $proxy_add_x_forwarded_for: ANEXA o IP real no fim.
        # O primeiro elemento e' o que o visitante mandou (pode ser falso).
        request = self._request(HTTP_X_FORWARDED_FOR='203.0.113.9, 198.51.100.20')
        self.assertEqual(pegar_ip(request), '198.51.100.20')

    def test_pegar_ip_x_forwarded_for_com_um_valor(self):
        request = self._request(HTTP_X_FORWARDED_FOR='198.51.100.21')
        self.assertEqual(pegar_ip(request), '198.51.100.21')

    def test_pegar_ip_usa_remote_addr_como_fallback(self):
        request = self._request(REMOTE_ADDR='198.51.100.7')
        self.assertEqual(pegar_ip(request), '198.51.100.7')

    def test_pegar_ip_invalido_retorna_none(self):
        request = self._request(HTTP_X_FORWARDED_FOR='nao-e-ip')
        self.assertIsNone(pegar_ip(request))

    # ----- registrar_acesso -----

    @patch('core.log_acesso.LogAcesso')
    @patch('core.log_acesso.cache')
    def test_grava_anonimo_com_ip_e_detalhe(self, mock_cache, mock_logacesso):
        mock_cache.add.return_value = True
        request = self._request(
            REMOTE_ADDR='198.51.100.7',
            HTTP_USER_AGENT='ua-teste',
        )

        self.assertTrue(registrar_acesso(request, TIPO_LISTA_EMPRESAS, 'uf=SC'))

        kwargs = mock_logacesso.objects.create.call_args.kwargs
        self.assertIsNone(kwargs['usuario'])
        self.assertEqual(kwargs['tipo'], 'empresas_lista')
        self.assertEqual(kwargs['ip'], '198.51.100.7')
        self.assertEqual(kwargs['user_agent'], 'ua-teste')
        self.assertEqual(kwargs['detalhe'], 'uf=SC')

    @patch('core.log_acesso.LogAcesso')
    @patch('core.log_acesso.cache')
    def test_grava_usuario_quando_autenticado(self, mock_cache, mock_logacesso):
        mock_cache.add.return_value = True
        request = self._request(REMOTE_ADDR='198.51.100.7')
        request.user = SimpleNamespace(is_authenticated=True)

        self.assertTrue(registrar_acesso(request, TIPO_DETALHE_EMPRESA, 'cnpj=123'))

        kwargs = mock_logacesso.objects.create.call_args.kwargs
        self.assertIs(kwargs['usuario'], request.user)
        self.assertEqual(kwargs['tipo'], 'empresas_detalhe')

    @patch('core.log_acesso.LogAcesso')
    @patch('core.log_acesso.cache')
    def test_deduplica_evento_repetido(self, mock_cache, mock_logacesso):
        mock_cache.add.return_value = False  # chave ja existia na janela
        request = self._request(REMOTE_ADDR='198.51.100.7')

        self.assertFalse(registrar_acesso(request, TIPO_LISTA_EMPRESAS, 'sem filtros'))
        mock_logacesso.objects.create.assert_not_called()

    @patch('core.log_acesso.LogAcesso')
    @patch('core.log_acesso.cache')
    def test_falha_no_banco_nao_propaga_e_libera_janela(self, mock_cache, mock_logacesso):
        mock_cache.add.return_value = True
        mock_logacesso.objects.create.side_effect = RuntimeError('boom')
        request = self._request(REMOTE_ADDR='198.51.100.7')

        self.assertFalse(registrar_acesso(request, TIPO_DETALHE_EMPRESA, 'cnpj=1'))
        mock_cache.delete.assert_called_once()

    @patch('core.log_acesso.LogAcesso')
    @patch('core.log_acesso.cache')
    def test_detalhe_longo_e_truncado(self, mock_cache, mock_logacesso):
        mock_cache.add.return_value = True
        request = self._request(REMOTE_ADDR='198.51.100.7')

        registrar_acesso(request, TIPO_LISTA_EMPRESAS, 'x' * 900)

        detalhe = mock_logacesso.objects.create.call_args.kwargs['detalhe']
        self.assertEqual(len(detalhe), 500)

    # ----- model -----

    def test_model_conhece_os_tipos_de_empresas(self):
        tipos = [valor for valor, _ in LogAcesso.TIPO_CHOICES]
        self.assertIn('empresas_lista', tipos)
        self.assertIn('empresas_detalhe', tipos)


class GoogleMapsUrlTests(SimpleTestCase):
    """
    Link do Google Maps (core/maps.py). O pino de c_ceps e do CEP (a media do
    trecho que ele cobre; no CEP generico, o centroide do municipio), entao o
    numero do imovel manda: com rua + numero o link vai em texto (sem CEP; e
    sem bairro no CEP generico, que cobre o municipio inteiro). Sem numero,
    usa a coordenada do CEP. A URL api=1&query= e publica, sem chave.
    """

    BASE = 'https://www.google.com/maps/search/?api=1&query='

    @staticmethod
    def _estab(**extra):
        base = {
            'logradouro': 'RUA AR 3',
            'numero': '119',
            'bairro': 'ARUANA II',
            'municipio_descricao': 'GOIANIA',
            'uf': 'GO',
            'cep': '74740300',
            'latitude': None,
            'longitude': None,
            'generico': False,
        }
        base.update(extra)
        return base

    def test_sem_numero_usa_pino_do_cep(self):
        """Sem numero o texto nao posiciona: o pino do CEP e o melhor que ha."""
        url = montar_maps_url(self._estab(numero='S/N',
                                         latitude=Decimal('-16.692071'),
                                         longitude=Decimal('-49.201760')))
        self.assertEqual(url, self.BASE + '-16.692071,-49.201760')

    def test_rua_com_numero_prefere_endereco_ao_pino_do_cep(self):
        """Caso do Posto Prudente (Joinville/SC): pino do CEP ~1,2 km fora do numero.

        c_ceps.89204251 = -26.286121,-48.850077 (media do trecho da Rua Blumenau
        na America); o numero 3011 fica em Santo Antonio, -26.274944,-48.851272.
        """
        url = montar_maps_url(self._estab(logradouro='BLUMENAU',
                                         tip_logradouro='RUA',
                                         numero='3011',
                                         bairro='SANTO ANTONIO',
                                         municipio_descricao='JOINVILLE',
                                         uf='SC',
                                         cep='89204251',
                                         latitude=Decimal('-26.286121'),
                                         longitude=Decimal('-48.850077')))
        self.assertEqual(
            url,
            self.BASE + 'RUA+BLUMENAU%2C+3011%2C+SANTO+ANTONIO%2C+JOINVILLE%2C+SC%2C+Brasil'
        )

    def test_cep_generico_usa_endereco_mesmo_com_coordenada(self):
        """CEP do municipio inteiro: coordenada e centroide, bairro/CEP sao ruido.

        'RUA AR 3, 119, ARUANA II, GOIANIA, GO, 74740300' nao resolve no Maps;
        'RUA AR 3, 119, GOIANIA, GO' resolve.
        """
        url = montar_maps_url(self._estab(latitude=Decimal('-27.387229'),
                                          longitude=Decimal('-51.782812'),
                                          generico=True))
        self.assertEqual(url, self.BASE + 'RUA+AR+3%2C+119%2C+GOIANIA%2C+GO%2C+Brasil')
        self.assertNotIn('-27.387229', url)
        self.assertNotIn('ARUANA', url)
        self.assertNotIn('74740300', url)

    def test_cep_generico_sem_endereco_usa_coordenada(self):
        url = montar_maps_url({'latitude': Decimal('-27.387229'),
                               'longitude': Decimal('-51.782812'),
                               'generico': True})
        self.assertEqual(url, self.BASE + '-27.387229,-51.782812')

    def test_sem_coordenada_monta_endereco(self):
        """Sem coordenada: endereco completo, sem CEP (com rua o CEP atrapalha)."""
        url = montar_maps_url(self._estab())
        self.assertEqual(
            url,
            self.BASE + 'RUA+AR+3%2C+119%2C+ARUANA+II%2C+GOIANIA%2C+GO%2C+Brasil'
        )

    def test_cep_so_entra_quando_nao_ha_rua_e_vai_formatado(self):
        url = montar_maps_url({'cep': '74740-300'})
        self.assertEqual(url, self.BASE + '74740-300')

    def test_numero_sem_numero_nao_entra_na_busca(self):
        url = montar_maps_url(self._estab(numero='S/N'))
        self.assertNotIn('S%2FN', url)
        self.assertIn('GOIANIA', url)

    def test_apenas_cep_ainda_gera_link(self):
        url = montar_maps_url({'cep': '74740300'})
        self.assertEqual(url, self.BASE + '74740-300')

    def test_endereco_no_exterior_nao_acrescenta_brasil(self):
        url = montar_maps_url({
            'logradouro': 'AV CORRIENTES',
            'numero': '1234',
            'nm_cidade_ext': 'BUENOS AIRES',
        })
        self.assertEqual(url, self.BASE + 'AV+CORRIENTES%2C+1234%2C+BUENOS+AIRES')

    def test_coordenada_fora_do_brasil_e_ignorada(self):
        url = montar_maps_url(self._estab(latitude=Decimal('40.712800'),
                                         longitude=Decimal('-74.006000')))
        self.assertNotIn('40.712800', url)
        self.assertIn('GOIANIA', url)

    def test_sem_coordenada_e_sem_endereco_retorna_vazio(self):
        self.assertEqual(montar_maps_url({}), '')
        self.assertEqual(montar_maps_url(None), '')

    def test_coordenada_invalida_nao_quebra(self):
        url = montar_maps_url(self._estab(latitude='x', longitude='y'))
        self.assertIn('GOIANIA', url)


class GoogleMapsNomeUrlTests(SimpleTestCase):
    """Segundo link, que busca pelo nome (core/maps.py).

    Serve para o caso em que o endereco fiscal da Receita diverge do cadastro
    do Google: Condor Auto Posto Atuba (CNPJ 01.184.650/0001-05), Receita
    'RODOVIA BR-116, 850, ATUBA, COLOMBO/PR, CEP 83413-000'. O CEP 83413-000 e
    do bairro Maua em Colombo e o Maps cadastra o posto em 'BR-116, 87 -
    Atuba, Curitiba/PR': o link por endereco cai a 1,8 km e o link pelo nome
    ('CONDOR AUTO POSTO ATUBA LTDA, COLOMBO, PR, Brasil') acerta o pino.
    """

    BASE = 'https://www.google.com/maps/search/?api=1&query='

    def test_usa_nome_fantasia_antes_da_razao_social(self):
        url = montar_maps_nome_url({
            'nm_fantasia': 'KOMPRAO KOCH ATACADISTA',
            'razao_social': 'KOCH HIPERMERCADO S/A',
            'municipio_descricao': 'TIJUCAS',
            'uf': 'SC',
        })
        self.assertEqual(
            url, self.BASE + 'KOMPRAO+KOCH+ATACADISTA%2C+TIJUCAS%2C+SC%2C+Brasil'
        )

    def test_sem_fantasia_usa_razao_social(self):
        """Condor: nm_fantasia vazio, o nome que acha o ponto e a razao social."""
        url = montar_maps_nome_url({
            'nm_fantasia': None,
            'razao_social': 'CONDOR AUTO POSTO ATUBA LTDA',
            'municipio_descricao': 'COLOMBO',
            'uf': 'PR',
        })
        self.assertEqual(
            url, self.BASE + 'CONDOR+AUTO+POSTO+ATUBA+LTDA%2C+COLOMBO%2C+PR%2C+Brasil'
        )

    def test_sem_municipio_nao_gera_link(self):
        """Nome sozinho e ambiguo: sem municipio nao ha segundo link."""
        self.assertEqual(montar_maps_nome_url({'razao_social': 'PADARIA CENTRAL'}), '')

    def test_sem_nome_nao_gera_link(self):
        self.assertEqual(montar_maps_nome_url({'nm_fantasia': ' ', 'razao_social': None,
                                               'municipio_descricao': 'COLOMBO'}), '')
        self.assertEqual(montar_maps_nome_url(None), '')

    def test_endereco_no_exterior_usa_cidade(self):
        url = montar_maps_nome_url({
            'razao_social': 'EMPRESA EXEMPLO',
            'nm_cidade_ext': 'BUENOS AIRES',
        })
        self.assertEqual(url, self.BASE + 'EMPRESA+EXEMPLO%2C+BUENOS+AIRES')


class GoogleMapsTemplateTests(SimpleTestCase):
    """Coluna Mapa na tabela de estabelecimentos e botao no card da matriz."""

    @staticmethod
    def _render(estabelecimentos, empresa=None):
        dados_empresa = {
            'razao_social': 'KOCH HIPERMERCADO S/A',
            'cnpj_completo': '02831172000190',
            'municipio_descricao': 'TIJUCAS',
            'uf': 'SC',
        }
        dados_empresa.update(empresa or {})
        return render_to_string('core/detalhe.html', {
            'empresa': dados_empresa,
            'estabelecimentos': estabelecimentos,
            'socios': [],
            'total_socios': 0,
            'total_filiais': 0,
            'total_estabelecimentos': len(estabelecimentos),
            'next_url': '/chat/',
        })

    @staticmethod
    def _filial(**extra):
        base = {
            'cnpj_completo': '02831172000270',
            'cnpj_ordem': '0002',
            'cnpj_dv': '70',
            'cd_matriz_filial': 2,
            'tipo': 'FILIAL',
            'nm_fantasia': 'KOCH PICARRAS',
            'situacao_descricao': 'ATIVA',
            'logradouro': 'AVENIDA DAS FLORES',
            'numero': '100',
            'bairro': 'CENTRO',
            'uf': 'SC',
            'data_abertura': None,
            'cnae_principal': '4711301',
        }
        base.update(extra)
        return base

    def test_cabecalho_tem_coluna_mapa(self):
        html = self._render([self._filial()])
        self.assertIn('data-label="Mapa"', html)

    def test_linha_com_coordenada_mostra_link_em_nova_aba(self):
        html = self._render([self._filial(
            maps_url='https://www.google.com/maps/search/?api=1&query=-26.9,-48.7'
        )])
        self.assertIn('query=-26.9,-48.7', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener"', html)

    def test_linha_sem_link_nao_mostra_icone(self):
        html = self._render([self._filial(maps_url='')])
        self.assertNotIn('google.com/maps', html)

    def test_card_da_matriz_mostra_botao(self):
        html = self._render(
            [self._filial()],
            empresa={'maps_url': 'https://www.google.com/maps/search/?api=1&query=-26.9,-48.7'},
        )
        self.assertIn('Abrir no Google Maps', html)

    def test_card_sem_link_nao_mostra_botao(self):
        html = self._render([self._filial()], empresa={'maps_url': ''})
        self.assertNotIn('Abrir no Google Maps', html)

    def test_card_mostra_busca_pelo_nome(self):
        """Segundo botao: usado quando o endereco fiscal cai no lugar errado."""
        html = self._render(
            [self._filial()],
            empresa={
                'maps_url': 'https://www.google.com/maps/search/?api=1&query=-26.9,-48.7',
                'maps_nome_url': 'https://www.google.com/maps/search/?api=1&query=CONDOR',
            },
        )
        self.assertIn('Buscar pelo nome', html)
        self.assertIn('query=CONDOR', html)

    def test_card_sem_nome_nao_mostra_busca_pelo_nome(self):
        html = self._render(
            [self._filial()],
            empresa={'maps_url': 'https://www.google.com/maps/search/?api=1&query=-26.9,-48.7'},
        )
        self.assertNotIn('Buscar pelo nome', html)

    def test_linha_mostra_busca_pelo_nome(self):
        html = self._render([self._filial(
            maps_url='https://www.google.com/maps/search/?api=1&query=-26.9,-48.7',
            maps_nome_url='https://www.google.com/maps/search/?api=1&query=KOCH+PICARRAS',
        )])
        self.assertIn('Buscar pelo nome no Google Maps', html)
        self.assertIn('query=KOCH+PICARRAS', html)



