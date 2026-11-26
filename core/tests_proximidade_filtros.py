# cnpj_interface/core/tests_proximidade_filtros.py
"""
Testes dos filtros da /proximidade/ (Fase 3 - 25/11/2026).

Sem banco: `filtros_proximidade` e' parsing puro e o template e' renderizado
com contexto manual (mesmo padrao de ProximidadeTemplateTests).
"""
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from core.views import filtros_proximidade


class FiltrosProximidadeTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _filtros(self, **params):
        return filtros_proximidade(self.factory.get('/proximidade/', params))

    def test_sem_filtros(self):
        filtros = self._filtros()
        self.assertFalse(filtros['filtrado'])
        self.assertEqual(filtros['faixas'], [])
        self.assertEqual(filtros['cnaes'], [])
        self.assertEqual(filtros['erro'], '')

    def test_atividade_da_kb(self):
        filtros = self._filtros(atividade='indústrias metalúrgicas')
        self.assertTrue(filtros['filtrado'])
        self.assertEqual(filtros['faixas'], [(2400000, 2599999)])
        self.assertIn('metalúrgicas', filtros['rotulo_cnae'])

    def test_atividade_por_codigo_cnae(self):
        filtros = self._filtros(atividade='8630504')
        self.assertTrue(filtros['filtrado'])
        self.assertEqual(filtros['cnaes'], [8630504])

    def test_atividade_desconhecida(self):
        filtros = self._filtros(atividade='naves espaciais')
        self.assertTrue(filtros['erro'])
        self.assertFalse(filtros['filtrado'])

    def test_uf_idade_capital(self):
        filtros = self._filtros(uf='sc', idade='3', capital='500.000')
        self.assertTrue(filtros['filtrado'])
        self.assertEqual(filtros['uf'], 'SC')
        self.assertEqual(filtros['idade'], 3)
        self.assertEqual(filtros['capital'], 500000.0)
        self.assertIn('UF SC', filtros['rotulo_filtros'])
        self.assertIn('500.000', filtros['rotulo_filtros'])

    def test_capital_com_virgula_decimal(self):
        filtros = self._filtros(capital='500.000,50')
        self.assertEqual(filtros['capital'], 500000.5)

    def test_uf_invalida_e_ignorada(self):
        filtros = self._filtros(uf='SCX')
        self.assertEqual(filtros['uf'], '')


class ProximidadeFiltrosTemplateTests(SimpleTestCase):
    def _render(self, **contexto):
        base = {
            'origem_txt': 'Joinville/SC',
            'origem': {'rotulo': 'Joinville/SC (centro do município)'},
            'erro': None, 'aviso': None, 'raio': 10, 'raio_opcoes': [1, 10, 100],
            'raio_min': 1, 'raio_max': 100,
            'somente_matrizes': False, 'resultados': [], 'tem_mais': False,
            'situacao': '', 'situacoes': [('ATIVA', 'Ativa'), ('INAPTA', 'Inapta')],
            'lista_ufs': [('SC', 'Santa Catarina'), ('PR', 'Paraná')],
            'ativas': False,
            'total': 0, 'pagina': 1, 'total_paginas': 1,
            'link_primeira': '?a=1', 'link_anterior': None, 'link_proxima': None,
            'filtros': {'atividade': '', 'uf': '', 'idade': None,
                        'capital': None, 'rotulo_filtros': ''},
            'filtrado': False, 'filtros_rotulo': '',
        }
        base.update(contexto)
        return render_to_string('core/proximidade.html', base)

    def test_form_tem_campos_de_filtro(self):
        html = self._render()
        self.assertIn('name="atividade"', html)
        self.assertIn('name="uf"', html)
        self.assertIn('name="idade"', html)
        self.assertIn('name="capital"', html)
        self.assertNotIn('name="ativas"', html)
        self.assertIn('name="matrizes"', html)
        self.assertIn('<select name="uf"', html)

    def test_situacao_inclui_ativa(self):
        html = self._render()
        trecho = html[html.index('<select name="situacao"'):html.index('</select>', html.index('<select name="situacao"'))]
        self.assertIn('value="ATIVA"', trecho)

    def test_painel_da_consulta_do_chat(self):
        html = self._render(
            pergunta_chat='Metalúrgicas num raio de 10 km de Joinville/SC',
            resposta_chat='Encontrei 1.202 empresas...',
        )
        self.assertIn('Consulta feita no chat', html)
        self.assertIn('Metalúrgicas num raio de 10 km', html)
        self.assertIn('Encontrei 1.202 empresas', html)

    def test_modo_filtrado_mostra_rotulo_e_sem_densidade(self):
        html = self._render(
            filtrado=True,
            filtros={'atividade': 'metalúrgicas', 'uf': 'SC', 'idade': 3,
                     'capital': 500000, 'rotulo_filtros': 'metalúrgicas · UF SC'},
            filtros_rotulo='metalúrgicas · UF SC',
            mapa_dados={'origem': {'lat': -26.3, 'lon': -48.8,
                                   'rotulo': 'Joinville/SC'},
                        'raio': 10, 'pontos': [], 'ufs': ['SC'],
                        'filtrado': True},
        )
        self.assertIn('Filtros:', html)
        self.assertIn('metalúrgicas · UF SC', html)
        self.assertIn('data-filtrado="1"', html)
        self.assertNotIn('id="prox-mapa-densidade"', html)
        # "Ir para" fica escondido no modo filtrado
        self.assertNotIn('consulta-ir-para', html)
