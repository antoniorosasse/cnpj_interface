# /home/neto/ProjetosPython/cnpj_interface/core/tests_estatisticas_filtros.py
from django.test import RequestFactory, SimpleTestCase

from core.estatisticas_filtros import filtros_do_request


class FiltrosDoRequestTests(SimpleTestCase):
    def test_le_e_normaliza_uf(self):
        req = RequestFactory().get('/estatisticas/', {'uf': 'sc'})
        self.assertEqual(filtros_do_request(req)['uf'], 'SC')

    def test_uf_invalida_ignorada(self):
        req = RequestFactory().get('/estatisticas/', {'uf': 'SANTA'})
        self.assertIsNone(filtros_do_request(req)['uf'])

    def test_campos_numericos(self):
        req = RequestFactory().get('/estatisticas/', {
            'uf': 'SC', 'cidade': '8105', 'cnae': '4711302',
            'situacao': '2', 'porte': '1',
        })
        filtros = filtros_do_request(req)
        self.assertEqual(filtros['cidade'], 8105)
        self.assertEqual(filtros['cnaes'], [4711302])
        self.assertEqual(filtros['situacao'], 2)
        self.assertEqual(filtros['porte'], '1')

    def test_situacao_e_porte_invalidos(self):
        req = RequestFactory().get('/estatisticas/', {
            'situacao': '99', 'porte': '9',
        })
        filtros = filtros_do_request(req)
        self.assertIsNone(filtros['situacao'])
        self.assertIsNone(filtros['porte'])

    def test_sem_filtros(self):
        req = RequestFactory().get('/estatisticas/')
        self.assertEqual(filtros_do_request(req), {
            'uf': None, 'cidade': None, 'cnaes': [],
            'situacao': None, 'porte': None,
        })

    def test_varios_cnaes_repetidos_e_com_virgula(self):
        req = RequestFactory().get('/estatisticas/', {
            'cnae': ['4711302,1091102', '4721101'],
        })
        self.assertEqual(filtros_do_request(req)['cnaes'],
                         [1091102, 4711302, 4721101])
