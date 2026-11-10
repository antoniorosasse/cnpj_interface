"""Testes do app core (pagina de detalhe da empresa e log de acesso)."""
import re
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import SimpleTestCase, RequestFactory, override_settings

from core.log_acesso import (
    TIPO_DETALHE_EMPRESA,
    TIPO_LISTA_EMPRESAS,
    pegar_ip,
    registrar_acesso,
)
from core.maps import montar_maps_nome_url, montar_maps_url
from core import proximidade
from core.proximidade import (
    HIST_MAXIMO,
    KM_POR_GRAU_LAT,
    caixa_geografica,
    cursor_do_token,
    hist_de_texto,
    hist_para_texto,
    resolver_origem,
    sem_acento,
    sql_sem_acento,
    token_cursor,
)
from core.telefone import montar_whatsapp, montar_whatsapp_url
from core.views import consultar_com_salto
from chat.models import LogAcesso


class CursorFakeProximidade:
    """
    Cursor minimo: devolve o que o teste mandar, guarda o SQL executado.

    Com uma lista em `resultado`, cada fetch devolve a lista inteira (e
    fetchone, o primeiro item) - util para resolver_origem, que faz no maximo
    duas consultas e usa o mesmo retorno.
    """

    def __init__(self, resultado=None):
        self.resultado = resultado
        self.executados = []

    def execute(self, sql, params=None):
        self.executados.append((sql, params))

    def fetchall(self):
        return self.resultado if isinstance(self.resultado, list) else [self.resultado]

    def fetchone(self):
        if isinstance(self.resultado, list):
            return self.resultado[0] if self.resultado else None
        return self.resultado


class CursorFakeSequencia(CursorFakeProximidade):
    """Cursor que devolve um resultado diferente a cada execute (nao-SET)."""

    def __init__(self, resultados):
        super().__init__(None)
        self.fila = list(resultados)
        self.atual = None

    def execute(self, sql, params=None):
        self.executados.append((sql, params))
        if sql.strip().upper().startswith('SET'):
            return
        self.atual = self.fila.pop(0) if self.fila else None

    def fetchall(self):
        if self.atual is None:
            return []
        return self.atual if isinstance(self.atual, list) else [self.atual]

    def fetchone(self):
        if isinstance(self.atual, list):
            return self.atual[0] if self.atual else None
        return self.atual


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




class ListaEmpresasTemplateTests(SimpleTestCase):
    """
    Lista de empresas: cidade, quantidade de filiais e fim da ordenacao
    por nome fantasia (o filtro por fantasia continua).
    """

    @staticmethod
    def _empresa(**extra):
        base = {
            'cnpj_completo': '02831172000190',
            'razao_social': 'KOCH HIPERMERCADO S/A',
            'nm_fantasia': 'KOCH',
            'municipio_nome': 'Tijucas',
            'uf': 'SC',
            'total_filiais': 3,
            'situacao_descricao': 'ATIVA',
            'op_simples': 'N',
            'op_mei': 'N',
            'data_inicio_atividade': None,
        }
        base.update(extra)
        return base

    def _render(self, empresas=None, **extra):
        contexto = {
            'empresas': [self._empresa()] if empresas is None else empresas,
            'lista_ufs': [('SC', 'SC - Santa Catarina')],
            'lista_municipios': [{'cd_municipio': 7535, 'nome': 'Curitiba'}],
            'uf': 'SC',
            'municipio': '7535',
            'ordem': 'cnpj',
            'razao_social': '',
            'nome_fantasia': '',
            'cnpj': '',
            'situacao': '',
            'simples': '',
            'mei': '',
            'total': 1,
            'total_paginas': 3,
            'page': 1,
            'has_next': True,
            'has_previous': False,
            'last_cnpj': '02831172000190',
            'first_cnpj': '02831172000190',
            'last_razao': '',
            'first_razao': '',
            'total_estimado': False,
        }
        contexto.update(extra)
        return render_to_string('core/lista.html', contexto)

    # -------------------------------------------------- colunas novas
    def test_cabecalho_tem_cidade_e_filiais(self):
        html = self._render()
        self.assertIn('>Cidade</th>', html)
        self.assertIn('>Filiais</th>', html)

    def test_linha_mostra_o_nome_do_municipio(self):
        html = self._render()
        self.assertIn('Tijucas', html)

    def test_linha_mostra_a_quantidade_de_filiais(self):
        html = self._render()
        self.assertIn('3 filial(is)', html)

    def test_empresa_sem_filial_mostra_traco(self):
        html = self._render([self._empresa(total_filiais=0)])
        self.assertNotIn('0 filial(is)', html)

    # -------------------------------------------------- filtro de cidade
    def test_cidade_escolhida_fica_selecionada(self):
        html = self._render()
        self.assertIn('<option value="7535" selected>Curitiba</option>', html)

    def test_cidade_sem_uf_fica_desabilitada(self):
        html = self._render(uf='', municipio='', lista_municipios=[])
        self.assertIn('disabled', html)
        self.assertIn('escolha a UF', html)

    # -------------------------------------------------- ordenacao
    def test_ordenacao_nao_oferece_mais_nome_fantasia(self):
        html = self._render()
        self.assertNotIn('value="nome_fantasia"', html)
        self.assertIn('value="razao_social"', html)
        self.assertIn('value="cnpj"', html)

    def test_paginacao_leva_a_cidade(self):
        html = self._render()
        self.assertIn('&municipio=7535', html)

    def test_paginacao_nao_leva_mais_last_fantasia(self):
        html = self._render()
        self.assertNotIn('last_fantasia', html)

    # -------------------------------------------------- ir para a pagina
    def test_paginacao_tem_campo_ir_para(self):
        html = self._render()
        self.assertIn('<input type="number" name="page"', html)
        self.assertIn('max="3"', html)
        self.assertIn('<input type="hidden" name="ordem" value="cnpj">', html)
        self.assertIn('<input type="hidden" name="uf" value="SC">', html)
        self.assertIn('<input type="hidden" name="municipio" value="7535">', html)

    def test_ir_para_leva_os_filtros_preenchidos(self):
        html = self._render(razao_social='KOCH', cnpj='02831172',
                            situacao='ATIVA', simples='S')
        self.assertIn('<input type="hidden" name="razao_social" value="KOCH">', html)
        self.assertIn('<input type="hidden" name="cnpj" value="02831172">', html)
        self.assertIn('<input type="hidden" name="situacao" value="ATIVA">', html)
        self.assertIn('<input type="hidden" name="simples" value="S">', html)

    def test_total_estimado_nao_limita_o_campo(self):
        html = self._render(total=100000, total_paginas=5000, total_estimado=True)
        self.assertIn('<input type="number" name="page"', html)
        self.assertNotIn('max="5000"', html)

    def test_sem_total_de_paginas_nao_mostra_o_campo(self):
        # Sem total (busca sem resultado / total indisponivel) nao ha para onde ir.
        html = self._render(total=0, total_paginas=None, empresas=[])
        self.assertNotIn('name="page"', html)

    # -------------------------------------------------- ver detalhes
    def test_ver_detalhes_abre_em_nova_aba(self):
        html = self._render()
        self.assertIn('title="Ver detalhes (nova aba)"', html)
        self.assertIn('target="_blank" rel="noopener"', html)
        self.assertIn('/empresa/02831172000190/', html)



class MenuPrincipalTemplateTests(SimpleTestCase):
    """Menu principal: Proximidade abre em outra aba, o resto continua igual."""

    def _render(self):
        return render_to_string('core/base.html', {},
                                request=RequestFactory().get('/'))

    def test_proximidade_abre_em_nova_aba(self):
        html = self._render()
        self.assertIn('href="/proximidade/" target="_blank" rel="noopener"', html)

    def test_chat_ia_abre_em_nova_aba(self):
        """Chat IA abre em outra aba, como Proximidade e Estatisticas."""
        html = re.sub(r'\s+', ' ', self._render())
        self.assertIn('href="/chat/" target="_blank" rel="noopener"', html)

    @override_settings(ALLOWED_HOSTS=['testserver', 'chat.busquecnpj.com.br'])
    def test_chat_ia_fica_na_mesma_aba_no_host_do_chat(self):
        """Dentro do proprio chat.busquecnpj.com.br o link nao abre outra aba."""
        request = RequestFactory().get('/', HTTP_HOST='chat.busquecnpj.com.br')
        html = re.sub(r'\s+', ' ', render_to_string('core/base.html', {}, request=request))
        self.assertIn('href="/chat/"', html)
        self.assertNotIn('href="/chat/" target="_blank"', html)

    def test_empresas_continua_na_mesma_aba(self):
        html = self._render()
        self.assertNotIn('href="/" target="_blank"', html)


class WhatsAppTests(SimpleTestCase):
    """Link de WhatsApp a partir do telefone da RFB (core/telefone.py)."""

    def test_celular_de_8_digitos_gera_link(self):
        self.assertEqual(montar_whatsapp_url('41', '99998888'),
                         'https://wa.me/554199998888')

    def test_celular_de_9_digitos_gera_link(self):
        self.assertEqual(montar_whatsapp_url('11', '999998888'),
                         'https://wa.me/5511999998888')

    def test_telefone_fixo_nao_gera_link(self):
        for fixo in ('33334444', '22223333', '44445555', '55556666'):
            with self.subTest(fixo=fixo):
                self.assertEqual(montar_whatsapp_url('41', fixo), '')

    def test_ddd_invalido_nao_gera_link(self):
        for ddd in ('', None, '0', '00', '10', '100'):
            with self.subTest(ddd=ddd):
                self.assertEqual(montar_whatsapp_url(ddd, '99998888'), '')

    def test_numero_curto_ou_vazio_nao_gera_link(self):
        for tel in ('', None, '999988', '9999888877'):
            with self.subTest(tel=tel):
                self.assertEqual(montar_whatsapp_url('41', tel), '')

    def test_aceita_ddd_e_telefone_com_mascara(self):
        self.assertEqual(montar_whatsapp_url('(41)', '9999-8888'),
                         'https://wa.me/554199998888')

    def test_mensagem_vai_codificada(self):
        url = montar_whatsapp_url('41', '99998888', 'Olá, tudo bem?')
        self.assertTrue(url.startswith('https://wa.me/554199998888?text='))
        self.assertIn('Ol%C3%A1%2C%20tudo%20bem%3F', url)

    def test_usa_o_segundo_telefone_quando_o_primeiro_e_fixo(self):
        url = montar_whatsapp('41', '33334444', '11', '988887777')
        self.assertEqual(url, 'https://wa.me/5511988887777')

    def test_sem_celular_nenhum_retorna_vazio(self):
        self.assertEqual(montar_whatsapp('41', '33334444', '41', '32223333'), '')


class WhatsAppTemplateTests(SimpleTestCase):
    """Botao do WhatsApp no detalhe: card da matriz e tabela de estabelecimentos."""

    @staticmethod
    def _render(empresa_whatsapp='', est_whatsapp=''):
        return render_to_string('core/detalhe.html', {
            'empresa': {
                'razao_social': 'KOCH HIPERMERCADO S/A',
                'cnpj_completo': '02831172000190',
                'municipio_descricao': 'TIJUCAS',
                'uf': 'SC',
                'maps_url': '',
                'maps_nome_url': '',
                'ddd_1': '41',
                'tel_1': '99998888',
                'whatsapp_url': empresa_whatsapp,
            },
            'estabelecimentos': [{
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
                'ddd_1': '47',
                'tel_1': '988887777',
                'whatsapp_url': est_whatsapp,
            }],
            'socios': [],
            'total_socios': 0,
            'total_filiais': 1,
            'total_estabelecimentos': 1,
            'next_url': '/chat/',
        })

    def test_card_mostra_botao_quando_ha_celular(self):
        html = self._render(empresa_whatsapp='https://wa.me/554199998888')
        self.assertIn('https://wa.me/554199998888', html)
        self.assertIn('bi-whatsapp', html)

    def test_card_sem_celular_nao_mostra_botao(self):
        html = self._render(empresa_whatsapp='')
        self.assertNotIn('whatsapp', html.lower())

    def test_tabela_de_filiais_mostra_icone(self):
        html = self._render(est_whatsapp='https://wa.me/5547988887777')
        self.assertIn('https://wa.me/5547988887777', html)


class ProximidadeOrigemTests(SimpleTestCase):
    """Traducao do ponto de partida (core/proximidade.py) sem tocar no banco."""

    CursorFake = CursorFakeProximidade

    def test_coordenada_literal(self):
        origem, erro = resolver_origem(self.CursorFake(), '-26.3045,-48.8487')
        self.assertIsNone(erro)
        self.assertAlmostEqual(origem['latitude'], -26.3045, places=4)
        self.assertAlmostEqual(origem['longitude'], -48.8487, places=4)
        self.assertIn('coordenada', origem['rotulo'])

    def test_coordenada_com_virgula_decimal(self):
        """' -26,3045,-48,8487' tambem e aceita (virgula decimal)."""
        origem, erro = resolver_origem(self.CursorFake(), '-26,3045,-48,8487')
        self.assertIsNone(erro)
        self.assertAlmostEqual(origem['latitude'], -26.3045, places=4)
        self.assertAlmostEqual(origem['longitude'], -48.8487, places=4)

    def test_coordenada_fora_da_faixa(self):
        origem, erro = resolver_origem(self.CursorFake(), '120,200')
        self.assertIsNone(origem)
        self.assertIn('Coordenada inválida', erro)

    def test_cep_existente(self):
        cur = self.CursorFake({'cep': '89201000', 'latitude': -26.28, 'longitude': -48.81,
                              'nome_municipio': 'Joinville', 'uf': 'SC'})
        origem, erro = resolver_origem(cur, '89201-000')
        self.assertIsNone(erro)
        self.assertAlmostEqual(origem['latitude'], -26.28, places=2)
        self.assertIn('Joinville/SC', origem['rotulo'])
        self.assertIn('89201000', cur.executados[0][1]['cep'])

    def test_cep_sem_coordenada_orienta_usar_cidade(self):
        origem, erro = resolver_origem(self.CursorFake(None), '89201-000')
        self.assertIsNone(origem)
        self.assertIn('não tem coordenada', erro)

    def test_cidade_com_uf(self):
        cur = self.CursorFake([{'cd_municipio': 8105, 'nome': 'Joinville', 'uf': 'SC',
                                'latitude': Decimal('-26.3'), 'longitude': Decimal('-48.8'),
                                'n_ceps': 1200}])
        origem, erro = resolver_origem(cur, 'Joinville/SC')
        self.assertIsNone(erro)
        self.assertIn('Joinville/SC', origem['rotulo'])
        self.assertEqual(cur.executados[0][1], {'nome': 'JOINVILLE', 'uf': 'SC'})

    def test_cidade_ambigua_pede_uf(self):
        cur = self.CursorFake([
            {'cd_municipio': 1, 'nome': 'Bom Jesus', 'uf': 'PI',
             'latitude': Decimal('-9.0'), 'longitude': Decimal('-44.0'), 'n_ceps': 10},
            {'cd_municipio': 2, 'nome': 'Bom Jesus', 'uf': 'RS',
             'latitude': Decimal('-28.0'), 'longitude': Decimal('-50.0'), 'n_ceps': 20},
        ])
        origem, erro = resolver_origem(cur, 'Bom Jesus')
        self.assertIsNone(origem)
        self.assertIn('ambíguo', erro)
        self.assertIn('Bom Jesus/PI', erro)

    def test_cidade_inexistente(self):
        cur = self.CursorFake([])
        origem, erro = resolver_origem(cur, 'Cidade Que Nao Existe/XX')
        self.assertIsNone(origem)
        self.assertIn('não encontrada', erro)

    def test_cnpj_usa_a_coordenada_do_cep(self):
        cur = self.CursorFake({'cnpj_completo': '02831172000190', 'uf': 'SC',
                               'latitude': Decimal('-26.28'), 'longitude': Decimal('-48.81'),
                               'municipio_nome': 'Joinville',
                               'mun_latitude': Decimal('-26.30'),
                               'mun_longitude': Decimal('-48.84')})
        origem, erro = resolver_origem(cur, '02.831.172/0001-90')
        self.assertIsNone(erro)
        self.assertAlmostEqual(float(origem['latitude']), -26.28, places=2)
        self.assertEqual(cur.executados[0][1],
                         {'basico': '02831172', 'ordem': '0001', 'dv': '90'})

    def test_cnpj_sem_cep_cai_no_centroide_do_municipio(self):
        cur = self.CursorFake({'cnpj_completo': '02831172000190', 'uf': 'SC',
                               'latitude': None, 'longitude': None,
                               'municipio_nome': 'Joinville',
                               'mun_latitude': Decimal('-26.30'),
                               'mun_longitude': Decimal('-48.84')})
        origem, erro = resolver_origem(cur, '02831172000190')
        self.assertIsNone(erro)
        self.assertIn('centro do município', origem['rotulo'])

    def test_entrada_vazia(self):
        origem, erro = resolver_origem(None, '   ')
        self.assertIsNone(origem)
        self.assertIn('Informe', erro)


class CaixaGeograficaTests(SimpleTestCase):
    """bbox da busca por proximidade."""

    def test_um_grau_de_latitude(self):
        caixa = caixa_geografica(0, 0, KM_POR_GRAU_LAT)
        self.assertAlmostEqual(caixa['lat_max'] - caixa['lat_min'], 2.0, places=6)

    def test_latitude_alta_estreita_a_longitude(self):
        perto_equador = caixa_geografica(0, -48, 10)
        longe = caixa_geografica(-60, -48, 10)
        largura_equador = perto_equador['lon_max'] - perto_equador['lon_min']
        largura_sul = longe['lon_max'] - longe['lon_min']
        self.assertGreater(largura_sul, largura_equador)

    def test_caixa_contem_o_ponto(self):
        caixa = caixa_geografica(-26.3, -48.8, 5)
        self.assertLess(caixa['lat_min'], -26.3)
        self.assertGreater(caixa['lat_max'], -26.3)
        self.assertLess(caixa['lon_min'], -48.8)
        self.assertGreater(caixa['lon_max'], -48.8)


class ProximidadeAcentoTests(SimpleTestCase):
    """Cidade digitada sem acento tem de achar o municipio (banco sem unaccent)."""

    def test_sem_acento_tira_acento_e_sobe_caixa(self):
        self.assertEqual(sem_acento('Balneário Piçarras'), 'BALNEARIO PICARRAS')

    def test_sem_acento_colapsa_espacos(self):
        self.assertEqual(sem_acento('  Rua   do Príncipe '), 'RUA DO PRINCIPE')

    def test_sem_acento_de_vazio(self):
        self.assertEqual(sem_acento(None), '')
        self.assertEqual(sem_acento(''), '')

    def test_sql_normaliza_no_banco(self):
        expressao = sql_sem_acento('nome')
        self.assertTrue(expressao.startswith('upper(translate(nome'))
        self.assertIn('ÁÀÂÃÄ', expressao)

    def test_picarras_sem_acento_consulta_normalizada(self):
        cur = CursorFakeProximidade([])
        origem, erro = resolver_origem(cur, 'Picarras')
        self.assertIsNone(origem)
        self.assertIn('não encontrada', erro)
        sql, params = cur.executados[0]
        self.assertEqual(params['nome'], 'PICARRAS')
        self.assertIn('translate(', sql)

    def test_picarras_acha_balneario_picarras(self):
        cur = CursorFakeProximidade([{
            'cd_municipio': 8002, 'nome': 'Balneário Piçarras', 'uf': 'SC',
            'latitude': Decimal('-26.76'), 'longitude': Decimal('-48.75'),
            'n_ceps': 40,
        }])
        origem, erro = resolver_origem(cur, 'Picarras')
        self.assertIsNone(erro)
        self.assertIn('Balneário Piçarras/SC', origem['rotulo'])
        self.assertEqual(origem['nome'], 'BALNEARIO PICARRAS')
        self.assertEqual(origem['uf'], 'SC')


class ProximidadeEnderecoTests(SimpleTestCase):
    """Ponto de partida em endereco (logradouro + cidade ou CEP)."""

    CIDADE = {'cd_municipio': 8105, 'cd_ibge': 4209102, 'nome': 'Joinville',
              'uf': 'SC', 'latitude': Decimal('-26.3'),
              'longitude': Decimal('-48.8'), 'n_ceps': 1200}

    def test_rua_numero_e_cidade(self):
        cur = CursorFakeSequencia([
            self.CIDADE,
            {'n_ceps': 6, 'latitude': Decimal('-26.305118'),
             'longitude': Decimal('-48.836441'), 'nome_municipio': 'Joinville',
             'uf': 'SC'},
        ])
        origem, erro = resolver_origem(cur, 'Rua do Príncipe, 500, Joinville/SC')
        self.assertIsNone(erro)
        self.assertAlmostEqual(float(origem['latitude']), -26.305118, places=5)
        self.assertIn('Rua do Príncipe', origem['rotulo'])
        self.assertIn('6 CEPs', origem['rotulo'])
        # a busca do logradouro ja restringe ao municipio (IBGE) e ignora acento
        sql_logradouro, params = cur.executados[1]
        self.assertIn('c_ceps', sql_logradouro)
        self.assertIn('translate(', sql_logradouro)
        self.assertIn('id_municipio = %(ibge)s', sql_logradouro)
        self.assertEqual(params['termo'], '%RUA DO PRINCIPE%')
        self.assertEqual(params['ibge'], 4209102)
        self.assertEqual(params['uf'], 'SC')

    def test_cep_no_texto_usa_o_cep(self):
        cur = CursorFakeSequencia([{
            'cep': '89201000', 'latitude': Decimal('-26.287697'),
            'longitude': Decimal('-48.817937'), 'nome_municipio': 'Joinville',
            'uf': 'SC',
        }])
        origem, erro = resolver_origem(cur, 'Rua do Príncipe, 89201-000')
        self.assertIsNone(erro)
        self.assertAlmostEqual(float(origem['latitude']), -26.287697, places=5)
        self.assertIn('CEP 89201-000', origem['rotulo'])
        self.assertEqual(cur.executados[0][1], {'cep': '89201000'})

    def test_endereco_sem_cidade_pede_cidade(self):
        cur = CursorFakeSequencia([])
        origem, erro = resolver_origem(cur, 'Rua das Flores, 100')
        self.assertIsNone(origem)
        self.assertIn('cidade', erro)

    def test_logradouro_de_outra_cidade_nao_e_usado(self):
        """Só o município informado é consultado (id_municipio IBGE)."""
        cur = CursorFakeSequencia([self.CIDADE])
        origem, erro = resolver_origem(cur, 'Rua das Palmeiras, Joinville/SC')
        self.assertIsNone(origem)
        self.assertIn('logradouro', erro)
        consultas = [p for s, p in cur.executados if p and 'ibge' in p]
        for params in consultas:
            self.assertEqual(params['ibge'], 4209102)
            self.assertEqual(params['uf'], 'SC')

    def test_logradouro_inexistente_orienta_cidade_ou_cep(self):
        cur = CursorFakeSequencia([self.CIDADE])
        origem, erro = resolver_origem(cur, 'Rua Que Nao Existe, Joinville/SC')
        self.assertIsNone(origem)
        self.assertIn('logradouro', erro)
        self.assertIn('Tente só a cidade ou um CEP', erro)

    def test_remove_o_tipo_do_logradouro_na_segunda_tentativa(self):
        vazio = {'n_ceps': 0, 'latitude': None, 'longitude': None,
                 'nome_municipio': None, 'uf': None}
        cur = CursorFakeSequencia([
            self.CIDADE, vazio,
            {'n_ceps': 3, 'latitude': Decimal('-26.29'),
             'longitude': Decimal('-48.81'), 'nome_municipio': 'Joinville',
             'uf': 'SC'},
        ])
        origem, erro = resolver_origem(cur, 'Rua Sete de Setembro, Joinville/SC')
        self.assertIsNone(erro)
        self.assertEqual(cur.executados[-1][1]['termo'], '%SETE DE SETEMBRO%')

    def test_tipo_nao_e_removido_quando_sobra_uma_palavra(self):
        """"Avenida Brasil" nao vira "%BRASIL%" (casaria Rua Brasil, Assis Brasil...)."""
        cur = CursorFakeSequencia([self.CIDADE])
        origem, erro = resolver_origem(cur, 'Avenida Brasil, Joinville/SC')
        self.assertIsNone(origem)
        self.assertIn('logradouro', erro)
        termos = [p['termo'] for _s, p in cur.executados if p and 'termo' in p]
        self.assertEqual(termos, ['%AVENIDA BRASIL%'])


class ProximidadePaginacaoTests(SimpleTestCase):
    """Token do historico: avanco e retorno sem OFFSET."""

    def test_primeiro_cursor_vira_traco(self):
        self.assertEqual(token_cursor(None), '-')

    def test_token_ida_e_volta(self):
        token = token_cursor((0.123456, '06382126000154'))
        self.assertEqual(token, '0.123456|06382126000154')
        self.assertEqual(cursor_do_token(token), (0.123456, '06382126000154'))

    def test_token_preserva_o_km_sem_arredondar(self):
        # Regressao: f'{km:.6f}' virava 8.393042 e a pagina se repetia para
        # sempre quando varias empresas empatavam no km do mesmo CEP
        # (Picarras/SC, pagina 9, 2026-11).
        km = 8.393042237528174
        token = token_cursor((km, '26726811000168'))
        self.assertEqual(token, '8.393042237528174|26726811000168')
        self.assertEqual(cursor_do_token(token), (km, '26726811000168'))

    def test_token_invalido_vira_inicio(self):
        self.assertIsNone(cursor_do_token('lixo'))
        self.assertIsNone(cursor_do_token(''))
        self.assertIsNone(cursor_do_token('-'))

    def test_hist_de_texto_limita_o_tamanho(self):
        texto = ';'.join(['-'] * (HIST_MAXIMO + 5))
        self.assertEqual(len(hist_de_texto(texto)), HIST_MAXIMO)

    def test_hist_ida_e_volta(self):
        hist = ['-', '0.100000|12345678000190']
        self.assertEqual(hist_de_texto(hist_para_texto(hist)), hist)


class ProximidadeTemplateTests(SimpleTestCase):
    """Rodape: 'Pagina X de Y', igual a lista de empresas."""

    def _render(self, **contexto):
        base = {
            'origem_txt': 'Joinville/SC',
            'origem': {'rotulo': 'Joinville/SC (centro do município)'},
            'erro': None, 'raio': 5, 'raio_opcoes': [1, 5, 10],
            'somente_matrizes': True, 'resultados': [], 'tem_mais': False,
            'situacao': '',
            'situacoes': [('ATIVA', 'Ativa'), ('INAPTA', 'Inapta')],
            'total': None, 'pagina': 1, 'total_paginas': None,
            'link_primeira': '?origem=Joinville/SC&raio=5',
            'link_anterior': None, 'link_proxima': None,
        }
        base.update(contexto)
        return render_to_string('core/proximidade.html', base)

    def test_mostra_pagina_e_total(self):
        html = self._render(pagina=2, total=240, total_paginas=12,
                            link_anterior='?a=1', link_proxima='?p=3')
        self.assertIn('Página 2 de 12', html)
        self.assertIn('Mostrando', html)
        self.assertIn('de 240', html)
        self.assertIn('href="?a=1"', html)
        self.assertIn('href="?p=3"', html)

    def test_sem_total_mostra_so_a_pagina(self):
        html = self._render(pagina=3, total=None, total_paginas=None)
        self.assertIn('Página 3', html)
        self.assertNotIn('Página 3 de', html)

    def test_total_zero_aparece(self):
        html = self._render(total=0, total_paginas=1)
        self.assertIn('Página 1 de 1', html)
        self.assertIn('de 0', html)

    def test_form_aceita_endereco(self):
        html = self._render()
        self.assertIn('Rua do Príncipe, 500, Joinville/SC', html)

    def test_sem_proxima_pagina_botao_fica_desabilitado(self):
        html = self._render(pagina=1)
        self.assertIn('page-item disabled', html)

    # -------------------------------------------------- filtro de situacao
    def test_form_tem_o_filtro_de_situacao(self):
        html = self._render()
        self.assertIn('<select name="situacao"', html)
        self.assertIn('<option value="">Todas</option>', html)
        self.assertIn('<option value="INAPTA"', html)

    def test_situacao_escolhida_fica_selecionada(self):
        html = self._render(situacao='INAPTA')
        self.assertIn('<option value="INAPTA" selected>Inapta</option>', html)
        self.assertIn('(situação: Inapta)', html)

    def test_aviso_quando_nao_ha_tabela_de_total_por_situacao(self):
        html = self._render(situacao='INAPTA')
        self.assertIn('c_ceps_estab_sit', html)
        self.assertIn('contar_estab_por_cep_situacao', html)

    def test_sem_filtro_nao_mostra_o_aviso(self):
        html = self._render()
        self.assertNotIn('c_ceps_estab_sit', html)

    def test_com_filtro_e_total_nao_mostra_o_aviso(self):
        html = self._render(situacao='INAPTA', total=120, total_paginas=6)
        self.assertNotIn('c_ceps_estab_sit', html)
        self.assertIn('Página 1 de 6', html)

    # --------------------------------------- contador acima da lista / limpar
    def test_contador_de_empresas_aparece_acima_da_lista(self):
        html = self._render(total=1341417, total_paginas=67071)
        antes_da_tabela = html.split('<table', 1)[0]
        self.assertIn('1.341.417</strong> empresas', antes_da_tabela)
        self.assertIn('em 67.071 páginas', antes_da_tabela)

    def test_contador_sem_total_mostra_o_da_pagina(self):
        linha = {
            'km': Decimal('0.42'), 'cnpj_completo': '02831172000190',
            'razao_social': 'KOCH HIPERMERCADO S/A', 'nm_fantasia': 'KOCH',
            'logradouro': 'Rua Blumenau', 'numero': '100', 'bairro': 'Centro',
            'municipio_nome': 'Joinville', 'uf': 'SC',
            'cd_matriz_filial': 1, 'situacao_descricao': 'ATIVA',
            'maps_url': None,
        }
        html = self._render(resultados=[linha], situacao='ATIVA')
        antes_da_tabela = html.split('<table', 1)[0]
        self.assertIn('1</strong> empresa', antes_da_tabela)
        self.assertIn('nesta página', antes_da_tabela)

    def test_botao_limpar_volta_para_a_pagina_limpa(self):
        html = self._render(situacao='INAPTA')
        self.assertIn('href="/proximidade/"', html)
        self.assertIn('Limpar', html)

    def test_linha_mostra_a_situacao_do_estabelecimento(self):
        linha = {
            'km': Decimal('0.42'), 'cnpj_completo': '02831172000190',
            'razao_social': 'KOCH HIPERMERCADO S/A', 'nm_fantasia': 'KOCH',
            'logradouro': 'Rua Blumenau', 'numero': '100', 'bairro': 'Centro',
            'municipio_nome': 'Joinville', 'uf': 'SC',
            'cd_matriz_filial': 1, 'situacao_descricao': 'INAPTA',
            'maps_url': None,
        }
        html = self._render(resultados=[linha], total=1, total_paginas=1)
        self.assertIn('Inapta', html)

    def test_ver_detalhes_abre_em_nova_aba(self):
        linha = {
            'km': Decimal('0.42'), 'cnpj_completo': '02831172000190',
            'razao_social': 'KOCH HIPERMERCADO S/A', 'nm_fantasia': 'KOCH',
            'logradouro': 'Rua Blumenau', 'numero': '100', 'bairro': 'Centro',
            'municipio_nome': 'Joinville', 'uf': 'SC',
            'cd_matriz_filial': 1, 'situacao_descricao': 'ATIVA',
            'maps_url': None,
        }
        html = self._render(resultados=[linha], total=1, total_paginas=1)
        self.assertIn('title="Ver detalhes (nova aba)"', html)
        self.assertIn('target="_blank" rel="noopener"', html)
        self.assertIn('/empresa/02831172000190/', html)


class ProximidadeBuscaTests(SimpleTestCase):
    """buscar_proximos: total da tabela agregada e ordem (km, CNPJ)."""

    def setUp(self):
        proximidade._tabela_total_cache = None

    @staticmethod
    def _linha(km, cnpj):
        return {
            'km': Decimal(str(km)), 'cnpj_completo': cnpj, 'cnpj_basico': cnpj[:8],
            'cnpj_ordem': cnpj[8:12], 'cnpj_dv': cnpj[12:14],
            'cd_matriz_filial': 1, 'nm_fantasia': 'X', 'logradouro': 'Rua A',
            'numero': '10', 'bairro': 'Centro', 'uf': 'SC',
            'municipio_nome': 'Joinville', 'telefone_1': '4733334444',
            'cd_sit_cadastro': '2', 'latitude': Decimal('-26.3'),
            'longitude': Decimal('-48.8'), 'total_raio': 42,
        }

    @staticmethod
    def _sql_principal(cur):
        for sql, _params in cur.executados:
            if 'c_estabelecimentos' in sql:
                return sql
        raise AssertionError('SQL principal nao executado')

    def test_total_vem_da_tabela_agregada_e_respeita_o_filtro(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.1, '11111111000111'), self._linha(0.2, '22222222000122')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        linhas, tem_mais, total = proximidade.buscar_proximos(
            cur, -26.3, -48.8, 5, True, limite=1)
        self.assertEqual(total, 42)
        self.assertTrue(tem_mais)
        self.assertEqual(len(linhas), 1)
        sql = self._sql_principal(cur)
        self.assertIn('c_ceps_estab', sql)
        self.assertIn('n_matrizes', sql)

    def test_sem_matrizes_usa_o_total_geral(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, False, limite=1)
        sql = self._sql_principal(cur)
        self.assertIn('n_total', sql)
        self.assertNotIn('n_matrizes', sql)

    def test_sem_tabela_agregada_total_e_none(self):
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        _linhas, _tem_mais, total = proximidade.buscar_proximos(
            cur, -26.3, -48.8, 5, True, limite=1)
        self.assertIsNone(total)
        self.assertNotIn('c_ceps_estab', self._sql_principal(cur))

    def test_filtro_de_situacao_usa_a_tabela_por_situacao(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        _linhas, _tem_mais, total = proximidade.buscar_proximos(
            cur, -26.3, -48.8, 5, True, limite=1, situacao=2)
        self.assertEqual(total, 42)
        sql = self._sql_principal(cur)
        self.assertIn('c_ceps_estab_sit', sql)
        self.assertIn('cd_sit_cadastro = %(situacao)s', sql)
        params = [p for s, p in cur.executados if 'c_estabelecimentos' in s][0]
        self.assertEqual(params['situacao'], 2)

    def test_filtro_sem_tabela_por_situacao_nao_inventa_total(self):
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        _linhas, _tem_mais, total = proximidade.buscar_proximos(
            cur, -26.3, -48.8, 5, True, limite=1, situacao=2)
        self.assertIsNone(total)
        self.assertNotIn('c_ceps_estab_sit', self._sql_principal(cur))

    def test_ordem_por_distancia_e_depois_cnpj(self):
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True, limite=1)
        sql = self._sql_principal(cur)
        self.assertIn('ORDER BY c.km, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv', sql)

    def test_keyset_usa_a_ultima_linha_da_pagina(self):
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.5, '99999999000199')],
            [{'cnpj_basico': '99999999', 'razao_social': 'Z'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True,
                                    depois=(0.4, '12345678000190'), limite=1)
        sql = self._sql_principal(cur)
        params = [p for s, p in cur.executados if 'c_estabelecimentos' in s][0]
        self.assertIn('depois_km', params)
        self.assertEqual(params['depois_basico'], '12345678')
        self.assertIn('(c.km, e.cnpj_basico', sql)

    # -------------------------------------------------- poda da CTE de CEPs
    # Sem ela o custo da pagina crescia com a posicao do cursor: Joinville/10
    # km ia de 0,4 s (pagina 2) para 9 s (ultima pagina), porque o planner
    # percorria todos os CEPs do raio e descartava o que ficava antes do
    # cursor. A poda por km >= depois_km nao muda o resultado (quem passa no
    # keyset tem km >= depois_km), so evita a varredura.
    def test_cursor_poda_os_ceps_mais_proximos_que_ele(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.5, '99999999000199')],
            [{'cnpj_basico': '99999999', 'razao_social': 'Z'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True,
                                    depois=(0.4, '12345678000190'), limite=1)
        sql = self._sql_principal(cur)
        self.assertIn('WHERE km >= %(depois_km)s', sql)
        params = [p for s, p in cur.executados if 'c_estabelecimentos' in s][0]
        self.assertEqual(params['depois_km'], 0.4)

    def test_primeira_pagina_nao_poda(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True, limite=1)
        self.assertNotIn('WHERE km >= %(depois_km)s', self._sql_principal(cur))

    def test_total_do_raio_nao_usa_a_cte_podada(self):
        """O total precisa de todos os CEPs do raio: ceps_todos, nao ceps."""
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.5, '99999999000199')],
            [{'cnpj_basico': '99999999', 'razao_social': 'Z'}],
        ])
        _linhas, _tem_mais, total = proximidade.buscar_proximos(
            cur, -26.3, -48.8, 5, True,
            depois=(0.4, '12345678000190'), limite=1)
        self.assertEqual(total, 42)
        sql = self._sql_principal(cur)
        self.assertIn('WHERE ce.cep = ANY(ARRAY(SELECT c.cep FROM ceps_todos c',
                      sql)
        self.assertIn('SELECT * FROM ceps_todos', sql)

    def test_primeira_pagina_vazia_tem_total_zero(self):
        cur = CursorFakeSequencia([{'existe': True}, []])
        linhas, tem_mais, total = proximidade.buscar_proximos(
            cur, -26.3, -48.8, 2, True, limite=1)
        self.assertEqual(linhas, [])
        self.assertFalse(tem_mais)
        self.assertEqual(total, 0)

    def test_pagina_alem_do_fim_nao_inventa_total(self):
        cur = CursorFakeSequencia([{'existe': True}, []])
        _linhas, _tem_mais, total = proximidade.buscar_proximos(
            cur, -26.3, -48.8, 2, True,
            depois=(9.0, '12345678000190'), limite=1)
        self.assertIsNone(total)

    # -------------------------------------------------- filtro de situacao
    def test_situacao_entra_no_where_com_o_codigo(self):
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True, limite=1,
                                    situacao=2)
        sql = self._sql_principal(cur)
        self.assertIn('e.cd_sit_cadastro = %(situacao)s', sql)
        params = [p for s, p in cur.executados if 'c_estabelecimentos' in s][0]
        self.assertEqual(params['situacao'], 2)

    def test_situacao_nao_soma_o_total_da_tabela_sem_situacao(self):
        """c_ceps_estab nao guarda situacao: o total deixaria de bater."""
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        _linhas, _tem_mais, total = proximidade.buscar_proximos(
            cur, -26.3, -48.8, 5, True, limite=1, situacao=4)
        self.assertIsNone(total)
        self.assertNotIn('FROM c_ceps_estab ce', self._sql_principal(cur))
        # a consulta de existencia e a da tabela por situacao
        consultas = [p for s, p in cur.executados if 'to_regclass' in s]
        self.assertEqual(consultas, [['c_ceps_estab_sit']])

    def test_sem_situacao_o_where_nao_tem_o_filtro(self):
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True, limite=1)
        sql = self._sql_principal(cur)
        self.assertNotIn('%(situacao)s', sql)

    def test_situacao_conserva_o_keyset(self):
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.5, '99999999000199')],
            [{'cnpj_basico': '99999999', 'razao_social': 'Z'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True,
                                    depois=(0.4, '12345678000190'), limite=1,
                                    situacao=2)
        sql = self._sql_principal(cur)
        self.assertIn('e.cd_sit_cadastro = %(situacao)s', sql)
        self.assertIn('(c.km, e.cnpj_basico', sql)

    # -------------------------------------------------- janela por km
    # Com a tabela agregada, a CTE de CEPs usa a janela por km para o PG16 nao
    # ordenar o raio inteiro (Curitiba/10 km passava de 60 s). E um superset do
    # resultado; o ORDER BY/LIMIT da query principal decide as linhas.
    def test_janela_por_km_entra_no_sql_principal(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True, limite=1)
        sql = self._sql_principal(cur)
        self.assertIn('ceps_contagem', sql)
        self.assertIn('acum_antes', sql)
        self.assertIn('JOIN c_ceps_estab ce', sql)
        # pagina 1: janela cobre limite+1 linhas + margem
        self.assertIn('acum_antes < %(limite)s + {}'.format(
            proximidade.MARGEM_JANELA_KM), sql)
        # o total continua saindo de ceps_todos, nao da janela
        self.assertIn('WHERE ce.cep = ANY(ARRAY(SELECT c.cep FROM ceps_todos c',
                      sql)

    def test_janela_com_cursor_soma_o_km_do_cursor(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.5, '99999999000199')],
            [{'cnpj_basico': '99999999', 'razao_social': 'Z'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True,
                                    depois=(0.4, '12345678000190'), limite=1)
        sql = self._sql_principal(cur)
        # a janela soma a contagem do km do cursor (tb.km = depois_km)
        self.assertIn('WHERE tb.km = %(depois_km)s', sql)
        self.assertIn('acum_antes', sql)
        params = [p for s, p in cur.executados if 'c_estabelecimentos' in s][0]
        self.assertEqual(params['depois_km'], 0.4)

    def test_sem_tabela_agregada_nao_usa_janela(self):
        cur = CursorFakeSequencia([
            {'existe': False},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True, limite=1)
        sql = self._sql_principal(cur)
        self.assertNotIn('acum_antes', sql)
        self.assertIn('SELECT * FROM ceps_todos', sql)

    def test_janela_respeita_a_situacao(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'A'}],
        ])
        proximidade.buscar_proximos(cur, -26.3, -48.8, 5, True, limite=1,
                                    situacao=2)
        sql = self._sql_principal(cur)
        self.assertIn('ceps_contagem', sql)
        self.assertIn('JOIN c_ceps_estab_sit ce', sql)
        self.assertIn('ce.cd_sit_cadastro = %(situacao)s', sql)








class ProximidadeSaltoTests(SimpleTestCase):
    """cursor_para_pagina: pular para a pagina N sem OFFSET no raio."""

    def setUp(self):
        proximidade._tabela_total_cache = None

    @staticmethod
    def _sql(cur, trecho):
        for sql, params in cur.executados:
            if trecho in sql:
                return sql, params
        raise AssertionError(f'consulta com {trecho!r} nao executada')

    def test_pagina_um_nao_consulta_o_banco(self):
        cur = CursorFakeSequencia([])
        self.assertIsNone(
            proximidade.cursor_para_pagina(cur, -26.3, -48.8, 5, True, 1))
        self.assertEqual(cur.executados, [])

    def test_sem_tabela_agregada_nao_inventa_cursor(self):
        cur = CursorFakeSequencia([{'existe': False}])
        self.assertEqual(
            proximidade.cursor_para_pagina(cur, -26.3, -48.8, 5, True, 3),
            proximidade.PAGINA_SEM_TABELA)

    def test_pagina_alem_do_fim(self):
        cur = CursorFakeSequencia([{'existe': True}, None])
        self.assertEqual(
            proximidade.cursor_para_pagina(cur, -26.3, -48.8, 5, True, 9999),
            proximidade.PAGINA_ALEM_DO_FIM)

    def test_cursor_da_linha_anterior_a_pagina(self):
        # 3a pagina com LIMITE_PAGINA=20: alvo (0-based) = 39.
        # No indice, 39 linhas antes do CEP alvo -> resto 0 dentro do CEP.
        cur = CursorFakeSequencia([
            {'existe': True},
            {'cep': '89216130', 'km': Decimal('2.33'), 'antes': 39},
            {'cnpj_completo': '11111111000111'},
        ])
        cursor = proximidade.cursor_para_pagina(
            cur, -26.3, -48.8, 5, True, 3, limite=20)
        self.assertEqual(cursor, (2.33, '11111111000111'))

        sql_cep, params_cep = self._sql(cur, 'c_ceps')
        self.assertIn('c_ceps_estab', sql_cep)
        self.assertIn('n_matrizes', sql_cep)
        self.assertEqual(params_cep['alvo'], 39)

        _sql_cnpj, params_cnpj = self._sql(cur, 'c_estabelecimentos')
        self.assertEqual(params_cnpj['cep'], '89216130')
        self.assertEqual(params_cnpj['resto'], 0)

    def test_offset_dentro_do_cep_e_o_que_sobra_do_alvo(self):
        # 5a pagina, LIMITE_PAGINA=20 -> alvo = 79; o CEP guarda 70 antes,
        # entao a linha pedida e a 9a (0-based) daquele CEP.
        cur = CursorFakeSequencia([
            {'existe': True},
            {'cep': '89216130', 'km': 1.0, 'antes': 70},
            {'cnpj_completo': '22222222000122'},
        ])
        proximidade.cursor_para_pagina(cur, -26.3, -48.8, 5, True, 5, limite=20)
        _sql_cnpj, params_cnpj = self._sql(cur, 'c_estabelecimentos')
        self.assertEqual(params_cnpj['resto'], 9)

    def test_sem_matrizes_usa_o_total_geral(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            {'cep': '89216130', 'km': 1.0, 'antes': 39},
            {'cnpj_completo': '33333333000133'},
        ])
        proximidade.cursor_para_pagina(cur, -26.3, -48.8, 5, False, 3, limite=20)
        sql_cep, _params = self._sql(cur, 'c_ceps')
        self.assertIn('n_total', sql_cep)
        self.assertNotIn('n_matrizes', sql_cep)
        # A posicao dentro do CEP nao filtra matriz.
        sql_cnpj, _p = self._sql(cur, 'c_estabelecimentos')
        self.assertNotIn('cd_matriz_filial', sql_cnpj)

    def test_filtro_de_situacao_usa_a_tabela_por_situacao(self):
        cur = CursorFakeSequencia([
            {'existe': True},
            {'cep': '89216130', 'km': 1.0, 'antes': 39},
            {'cnpj_completo': '44444444000144'},
        ])
        proximidade.cursor_para_pagina(
            cur, -26.3, -48.8, 5, True, 3, situacao=2, limite=20)
        sql_cep, params_cep = self._sql(cur, 'c_ceps_estab_sit')
        self.assertIn('ce.cd_sit_cadastro = %(situacao)s', sql_cep)
        self.assertEqual(params_cep['situacao'], 2)
        sql_cnpj, params_cnpj = self._sql(cur, 'c_estabelecimentos')
        self.assertIn('cd_sit_cadastro = %(situacao)s', sql_cnpj)
        self.assertEqual(params_cnpj['situacao'], 2)


class ProximidadeSaltoTemplateTests(SimpleTestCase):
    """Campo 'Ir para' do rodape da proximidade."""

    def _render(self, **contexto):
        base = {
            'origem_txt': 'Joinville/SC',
            'origem': {'rotulo': 'Joinville/SC (centro do município)'},
            'erro': None, 'aviso': None, 'raio': 5, 'raio_opcoes': [1, 5, 10],
            'somente_matrizes': True, 'resultados': [], 'tem_mais': False,
            'situacao': '', 'situacoes': [('ATIVA', 'Ativa')],
            'total': 240, 'pagina': 2, 'total_paginas': 12,
            'link_primeira': '?origem=Joinville/SC&raio=5',
            'link_anterior': '?a=1', 'link_proxima': '?p=3',
        }
        base.update(contexto)
        return render_to_string('core/proximidade.html', base)

    def test_form_ir_para_leva_a_pagina_e_os_filtros(self):
        html = self._render(situacao='INAPTA')
        self.assertIn('<input type="number" name="page"', html)
        self.assertIn('max="12"', html)
        self.assertIn('value="2"', html)
        self.assertIn('<input type="hidden" name="origem" value="Joinville/SC">', html)
        self.assertIn('<input type="hidden" name="raio" value="5">', html)
        self.assertIn('<input type="hidden" name="situacao" value="INAPTA">', html)

    def test_sem_total_de_paginas_nao_mostra_o_campo(self):
        html = self._render(total=None, total_paginas=None)
        self.assertNotIn('name="page"', html)

    def test_pagina_unica_nao_mostra_o_campo(self):
        # Uma pagina so: o campo nao teria para onde ir.
        html = self._render(total=12, total_paginas=1, pagina=1)
        self.assertNotIn('name="page"', html)

    def test_aviso_do_salto_aparece(self):
        html = self._render(aviso='A página 99 passa do fim desta busca.')
        self.assertIn('A página 99 passa do fim desta busca.', html)


class CursorFakeSalto:
    """Cursor minimo para o OFFSET do campo 'Ir para' da lista."""

    def __init__(self, linhas=None, estourar_com_offset=False):
        self.linhas = linhas or []
        self.estourar_com_offset = estourar_com_offset
        self.executados = []

    def execute(self, sql, params=None):
        self.executados.append((sql, params))
        if self.estourar_com_offset and 'OFFSET' in sql:
            raise Exception('canceling statement due to statement timeout')

    def fetchall(self):
        return self.linhas


class ConexaoFakeSalto:
    """Conexao minima: registra o rollback feito ao recusar o salto."""

    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


class ConsultaComSaltoTests(SimpleTestCase):
    """
    consultar_com_salto: OFFSET do campo 'Ir para' com rede de seguranca.

    O salto por OFFSET so e barato quando a ordenacao sai do indice; quando o
    statement_timeout de 8 s estoura, a consulta tem de ser repetida sem OFFSET
    (depois do rollback, que tambem devolve o plano padrao) para a tela nao
    ficar vazia com um numero de pagina que nao abriu.
    """

    def test_sem_salto_nao_mexe_no_sql(self):
        cur = CursorFakeSalto([{'cnpj_completo': '11111111000111'}])
        linhas, recusou = consultar_com_salto(
            cur, ConexaoFakeSalto(), 'SELECT 1 LIMIT %s', [20], 0)
        self.assertEqual(linhas, [{'cnpj_completo': '11111111000111'}])
        self.assertFalse(recusou)
        self.assertEqual(cur.executados, [('SELECT 1 LIMIT %s', [20])])

    def test_salto_vira_offset_no_fim_dos_parametros(self):
        cur = CursorFakeSalto()
        _linhas, recusou = consultar_com_salto(
            cur, ConexaoFakeSalto(), 'SELECT 1 LIMIT %s', [20], 9980)
        self.assertFalse(recusou)
        sql, params = cur.executados[0]
        self.assertTrue(sql.endswith('OFFSET %s'))
        self.assertEqual(params, [20, 9980])

    def test_timeout_repete_sem_offset_e_avisa(self):
        conn = ConexaoFakeSalto()
        cur = CursorFakeSalto([{'cnpj_completo': '22222222000122'}],
                              estourar_com_offset=True)
        linhas, recusou = consultar_com_salto(
            cur, conn, 'SELECT 1 LIMIT %s', [20], 9980)
        self.assertTrue(recusou)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(linhas, [{'cnpj_completo': '22222222000122'}])
        # 1a tentativa com OFFSET, 2a sem (os mesmos params da pagina).
        self.assertIn('OFFSET', cur.executados[0][0])
        self.assertEqual(cur.executados[1], ('SELECT 1 LIMIT %s', [20]))

    def test_timeout_nao_engole_erro_quando_nao_havia_salto(self):
        class CursorQuebrado(CursorFakeSalto):
            def execute(self, sql, params=None):
                raise Exception('canceling statement due to statement timeout')

        with self.assertRaises(Exception):
            consultar_com_salto(CursorQuebrado(), ConexaoFakeSalto(),
                                'SELECT 1 LIMIT %s', [20], 0)


class RobotsTxtTests(SimpleTestCase):
    """robots.txt: libera o site e fecha as areas privadas (admin/chat/login)."""

    def test_robots_libera_o_site_e_fecha_as_areas_privadas(self):
        resposta = self.client.get('/robots.txt')

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta['Content-Type'].startswith('text/plain'))

        corpo = resposta.content.decode()
        self.assertIn('User-agent: *', corpo)
        self.assertIn('Allow: /', corpo)
        for caminho in ('/admin/', '/chat/', '/accounts/'):
            self.assertIn(f'Disallow: {caminho}', corpo)
        # a raiz continua indexavel: nao pode haver 'Disallow: /'
        self.assertNotIn('Disallow: /\n', corpo)

