# cnpj_interface/core/tests_cnae_categorias.py
"""
Testes da taxonomia CNAE e da KB de categorias de negocio (25/11/2026).

Nao tocam o banco: a taxonomia e' estatica e a KB e' um dicionario.
"""
from django.test import SimpleTestCase

from core import cnae_categorias, cnae_taxonomia


class TaxonomiaCnaeTests(SimpleTestCase):
    """Hierarquia derivada do codigo (com o zero a esquerda das divisoes 01-09)."""

    def test_normaliza_codigo_de_7_digitos(self):
        self.assertEqual(cnae_taxonomia.normalizar_cnae(2411300), '2411300')
        self.assertEqual(cnae_taxonomia.normalizar_cnae(111301), '0111301')
        self.assertEqual(cnae_taxonomia.normalizar_cnae('2411-3/00'), '2411300')
        self.assertEqual(cnae_taxonomia.normalizar_cnae(None), '')

    def test_divisao_de_codigo_com_zero_a_esquerda(self):
        # 111301 e' a subclasse 0111-3/01 (agricultura), nao a divisao 11.
        self.assertEqual(cnae_taxonomia.divisao(111301), 1)
        self.assertEqual(cnae_taxonomia.divisao(2411300), 24)
        self.assertEqual(cnae_taxonomia.divisao(9900800), 99)

    def test_secao_do_codigo(self):
        self.assertEqual(cnae_taxonomia.secao(111301), 'A')
        self.assertEqual(cnae_taxonomia.secao(2411300), 'C')
        self.assertEqual(cnae_taxonomia.secao(4930201), 'H')
        self.assertEqual(cnae_taxonomia.secao(4711302), 'G')
        self.assertEqual(cnae_taxonomia.secao(8630501), 'Q')
        self.assertEqual(cnae_taxonomia.secao(9900800), 'U')

    def test_faixa_de_uma_divisao(self):
        self.assertEqual(cnae_taxonomia.faixa_divisao(24), (2400000, 2499999))
        self.assertEqual(cnae_taxonomia.faixa_divisao(5), (500000, 599999))
        self.assertEqual(cnae_taxonomia.faixa_divisao(1), (100000, 199999))

    def test_faixas_une_divisoes_consecutivas(self):
        self.assertEqual(cnae_taxonomia.faixas_divisoes([24, 25]),
                         [(2400000, 2599999)])
        self.assertEqual(cnae_taxonomia.faixas_divisoes(range(10, 34)),
                         [(1000000, 3399999)])
        self.assertEqual(cnae_taxonomia.faixas_divisoes([5, 6, 7, 8, 9]),
                         [(500000, 999999)])
        # Nao consecutivas viram duas faixas.
        self.assertEqual(cnae_taxonomia.faixas_divisoes([6, 9]),
                         [(600000, 699999), (900000, 999999)])
        self.assertEqual(cnae_taxonomia.faixas_divisoes([]), [])

    def test_nomes_oficiais(self):
        self.assertEqual(cnae_taxonomia.nome_divisao(24), 'Metalurgia')
        self.assertIn('transformação', cnae_taxonomia.nome_secao('C'))
        self.assertEqual(len(cnae_taxonomia.divisoes_da_secao('C')), 24)
        self.assertEqual(cnae_taxonomia.divisoes_da_secao('X'), [])


class CategoriasCnaeTests(SimpleTestCase):
    """KB de categorias de negocio -> CNAE (decisoes de 25/11/2026)."""

    def test_industria_e_secao_c(self):
        filtro = cnae_categorias.resolver_filtro_cnae('indústrias ativas')
        self.assertEqual(filtro['categorias'], ['industria'])
        self.assertEqual(filtro['faixas'], [(1000000, 3399999)])
        self.assertEqual(filtro['cnaes'], [])

    def test_industria_extrativa_e_categoria_a_parte(self):
        filtro = cnae_categorias.resolver_filtro_cnae('indústrias extrativas')
        self.assertEqual(filtro['categorias'], ['industria_extrativa'])
        self.assertEqual(filtro['faixas'], [(500000, 999999)])

    def test_metalurgicas_sao_24_mais_25(self):
        filtro = cnae_categorias.resolver_filtro_cnae('indústrias metalúrgicas')
        self.assertEqual(filtro['categorias'], ['metalurgica'])
        self.assertEqual(filtro['faixas'], [(2400000, 2599999)])
        self.assertEqual(filtro['rotulo'], 'indústrias metalúrgicas')

    def test_categoria_especifica_vence_a_generica(self):
        # "industrias metalurgicas" casa industria (generica) e metalurgica;
        # o filtro tem que ficar em 24+25, nao em 10..33.
        slugs = cnae_categorias.resolver_categorias('indústrias metalúrgicas')
        self.assertIn('industria', slugs)
        self.assertIn('metalurgica', slugs)
        filtro = cnae_categorias.resolver_filtro_cnae('indústrias metalúrgicas')
        self.assertEqual(filtro['categorias'], ['metalurgica'])

    def test_clinicas_medicas_e_odontologicas(self):
        filtro = cnae_categorias.resolver_filtro_cnae(
            'clínicas médicas e odontológicas')
        self.assertEqual(filtro['categorias'],
                         ['clinicas_medicas_odontologicas'])
        self.assertEqual(filtro['faixas'], [])
        self.assertEqual(set(filtro['cnaes']), {
            8630501, 8630502, 8630503, 8630504, 8630505, 8640202,
        })

    def test_clinica_de_estetica_nao_entra(self):
        # "clinica" sozinho casaria "Clinicas de estetica" no ILIKE; a KB nao
        # mapeia esse termo para a categoria medica de proposito.
        self.assertEqual(
            cnae_categorias.resolver_categorias('clínicas de estética'), [])
        self.assertEqual(
            cnae_categorias.resolver_filtro_cnae('clínicas de estética')['cnaes'],
            [])

    def test_transportadoras_de_carga(self):
        filtro = cnae_categorias.resolver_filtro_cnae('transportadoras')
        self.assertEqual(filtro['categorias'], ['transportadoras'])
        self.assertEqual(set(filtro['cnaes']),
                         {4930201, 4930202, 4930203, 4930204})

    def test_supermercados_e_padarias_juntos(self):
        filtro = cnae_categorias.resolver_filtro_cnae(
            'supermercados e padarias')
        self.assertEqual(filtro['categorias'], ['supermercados', 'padarias'])
        self.assertEqual(set(filtro['cnaes']),
                         {4711302, 1091102, 4721101, 4721102})
        self.assertEqual(filtro['rotulo'], 'supermercados e padarias')

    def test_acento_e_caixa_nao_importam(self):
        for termo in ('METALURGICAS', 'metalúrgicas', 'Metalurgicas',
                      'metalurgia'):
            filtro = cnae_categorias.resolver_filtro_cnae(termo)
            self.assertEqual(filtro['categorias'], ['metalurgica'], termo)

    def test_termo_fora_da_kb_nao_inventa_categoria(self):
        self.assertEqual(
            cnae_categorias.resolver_filtro_cnae('empresas de energia solar'),
            {'categorias': [], 'faixas': [], 'cnaes': [], 'rotulo': ''})

    def test_descrever_categorias(self):
        self.assertEqual(
            cnae_categorias.descrever_categorias(['supermercados', 'padarias']),
            'supermercados e padarias')
        self.assertEqual(
            cnae_categorias.descrever_categorias(['industria']),
            'indústrias de transformação')
        self.assertEqual(cnae_categorias.descrever_categorias([]), '')
