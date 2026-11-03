"""Testes do app core (pagina de detalhe da empresa e log de acesso)."""
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

