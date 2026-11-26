# cnpj_interface/chat/tests/test_planner.py
"""
Testes do planejador de consultas (chat/services/planner.py).

Sem LLM real: `gerar_resposta` e' mockado.
"""
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from chat.services import planner


ALVO_LLM = 'chat.services.planner.gerar_resposta'


class HeuristicaProximidadeTests(SimpleTestCase):
    """parece_proximidade: roteamento barato antes de chamar o LLM."""

    def test_perguntas_geograficas(self):
        for pergunta in (
            'Encontre 248 indústrias ativas em um raio de 10 km',
            'Clínicas em um raio de 5 km da Av. Paulista',
            'Supermercados a até 5 km deste endereço',
            'Empresas perto daqui',
            'Transportadoras num raio de 20 km de Joinville/SC',
        ):
            self.assertTrue(planner.parece_proximidade(pergunta), pergunta)

    def test_perguntas_nao_geograficas(self):
        for pergunta in (
            'Indústrias metalúrgicas em Santa Catarina abertas há mais de 3 anos',
            'Transportadoras com capital social acima de R$ 500 mil no Paraná',
            'Quantas MEIs existem?',
            'Liste os mercados KOCH',
        ):
            self.assertFalse(planner.parece_proximidade(pergunta), pergunta)


class NormalizarPlanoTests(SimpleTestCase):
    """normalizar_plano: clampa e completa o JSON do LLM."""

    def test_clampa_valores(self):
        plano = planner.normalizar_plano({
            'intencao': 'proximidade',
            'origem': '  Joinville/SC  ',
            'raio_km': 500,
            'atividades': ['indústrias', 'metalúrgicas', 123],
            'situacao': 'ATIVA',
            'uf': 'sc',
            'idade_min_anos': -2,
            'capital_min': '500000',
            'limite': 9999,
        })
        self.assertEqual(plano['intencao'], 'proximidade')
        self.assertEqual(plano['origem'], 'Joinville/SC')
        self.assertEqual(plano['raio_km'], 100)
        self.assertEqual(plano['atividades'], ['indústrias', 'metalúrgicas'])
        self.assertEqual(plano['situacao'], 'ativa')
        self.assertEqual(plano['uf'], 'SC')
        self.assertEqual(plano['idade_min_anos'], 0)
        self.assertEqual(plano['capital_min'], 500000.0)
        self.assertEqual(plano['limite'], 1000)

    def test_defaults(self):
        plano = planner.normalizar_plano({})
        self.assertEqual(plano['intencao'], 'sql')
        self.assertEqual(plano['raio_km'], 10)
        self.assertEqual(plano['situacao'], 'todas')
        self.assertIsNone(plano['limite'])
        self.assertIsNone(plano['origem'])
        self.assertEqual(plano['atividades'], [])

    def test_intencao_invalida_vira_sql(self):
        plano = planner.normalizar_plano({'intencao': 'qualquer'})
        self.assertEqual(plano['intencao'], 'sql')


class PlanejarTests(SimpleTestCase):
    """planejar: chama o LLM, extrai JSON e devolve plano normalizado."""

    def test_json_com_cerca_markdown(self):
        resposta = '```json\n' + json.dumps({
            'intencao': 'proximidade',
            'origem': 'Av. Paulista',
            'usar_contexto': False,
            'raio_km': 5,
            'atividades': ['clínicas médicas e odontológicas'],
            'situacao': 'todas',
            'uf': None,
            'idade_min_anos': None,
            'capital_min': None,
            'limite': None,
            'observacao': 'falta cidade',
        }) + '\n```'
        with patch(ALVO_LLM, return_value=resposta):
            resultado = planner.planejar('Clínicas num raio de 5 km da Av. Paulista')
        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['intencao'], 'proximidade')
        self.assertEqual(resultado['plano']['raio_km'], 5)
        self.assertEqual(resultado['plano']['origem'], 'Av. Paulista')

    def test_llm_fora_do_ar(self):
        from chat.services.llm_client import LLMError
        with patch(ALVO_LLM, side_effect=LLMError('timeout')):
            resultado = planner.planejar('Clínicas num raio de 5 km')
        self.assertFalse(resultado['ok'])
        self.assertEqual(resultado['intencao'], 'sql')
        self.assertIn('timeout', resultado['erro'])

    def test_json_invalido(self):
        with patch(ALVO_LLM, return_value='nao sei responder'):
            resultado = planner.planejar('Clínicas num raio de 5 km')
        self.assertFalse(resultado['ok'])
        self.assertEqual(resultado['intencao'], 'sql')

    def test_contexto_geo_vai_no_prompt(self):
        resposta = json.dumps({'intencao': 'proximidade', 'usar_contexto': True})
        capturado = {}

        def _fake(system_prompt, user_prompt, **kwargs):
            capturado['user'] = user_prompt
            return resposta

        with patch(ALVO_LLM, side_effect=_fake):
            planner.planejar(
                'mais 5 km deste endereço',
                contexto_geo={'latitude': -26.3, 'longitude': -48.8,
                              'rotulo': 'Joinville/SC'},
            )
        self.assertIn('Joinville/SC', capturado['user'])
        self.assertIn('latitude -26.3', capturado['user'])

    def test_pergunta_vazia_nao_chama_llm(self):
        with patch(ALVO_LLM) as mock_llm:
            resultado = planner.planejar('   ')
        self.assertFalse(resultado['ok'])
        mock_llm.assert_not_called()
