"""Testes do app core (pagina de detalhe da empresa)."""
from django.template.loader import render_to_string
from django.test import SimpleTestCase


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
