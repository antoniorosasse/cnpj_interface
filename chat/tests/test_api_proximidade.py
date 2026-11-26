# cnpj_interface/chat/tests/test_api_proximidade.py
"""
Testes da rota de proximidade no chat (/chat/api/).

LLM e ferramenta de proximidade sao mockados: o objetivo e' testar a
integracao da view (roteamento, persistencia do plano, JSON e mensagens).
"""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from chat import repo
from chat.models import Conversa


User = get_user_model()

ALVO_PLANEJAR = 'chat.views.planejar'
ALVO_CONSULTAR = 'chat.views.consultar_proximidade'


def _plano_resultado(intencao='proximidade'):
    return {
        'ok': True,
        'intencao': intencao,
        'plano': {
            'intencao': intencao,
            'origem': 'Joinville/SC',
            'usar_contexto': False,
            'raio_km': 10,
            'atividades': ['indústrias metalúrgicas'],
            'situacao': 'ativa',
            'uf': 'SC',
            'idade_min_anos': 3,
            'capital_min': None,
            'limite': 248,
            'observacao': None,
        },
        'erro': None,
        'resposta_bruta': '',
    }


def _consulta_ok():
    plano = {
        'origem': {
            'texto': 'Joinville/SC',
            'rotulo': 'Joinville/SC',
            'latitude': -26.3,
            'longitude': -48.8,
        },
        'raio_km': 10,
        'filtros_resumo': 'UF SC, somente ativas, abertas há mais de 3 ano(s)',
        'categorias': ['metalurgica'],
        'atividades': ['indústrias metalúrgicas'],
        'link_proximidade': '/proximidade/?origem=Joinville/SC&raio=10&situacao=ATIVA',
    }
    return {
        'ok': True,
        'rows': [{
            'cnpj_completo': '84709955000102',
            'razao_social': 'CIA INDUSTRIAL H. CARLOS SCHNEIDER',
            'nm_fantasia': 'SCHNEIDER',
            'municipio_nome': 'Joinville',
            'uf': 'SC',
            'atividade_principal': 'Metalurgia de outros metais',
            'situacao_descricao': 'ATIVA',
            'km': 0.45,
            'telefone_1': '4733334444',
            'email': 'contato@exemplo.com',
            'dt_ini': '1969-01-02',
        }],
        'colunas': [
            'cnpj_completo', 'razao_social', 'nm_fantasia', 'municipio_nome',
            'uf', 'atividade_principal', 'situacao_descricao', 'km',
            'telefone_1', 'email', 'dt_ini'],
        'total': 1228,
        'exibidas': 1,
        'tem_mais': True,
        'resumo': 'Encontrei 1.228 indústrias metalúrgicas ativas num raio de 10 km de Joinville/SC.',
        'link': '/proximidade/?origem=Joinville/SC&raio=10&situacao=ATIVA',
        'origem': plano['origem'],
        'raio_km': 10,
        'filtros': {'uf': 'SC', 'situacao': 2, 'categorias': ['metalurgica']},
        'filtros_resumo': plano['filtros_resumo'],
        'tempo_ms': 1200,
        'plano': plano,
    }


def _consulta_esclarecimento():
    return {
        'ok': False,
        'etapa': 'esclarecimento',
        'erro': 'Para buscar por proximidade eu preciso da origem.',
    }


def _consulta_erro():
    return {
        'ok': False,
        'etapa': 'execucao',
        'erro': 'A consulta ficou pesada demais e foi cancelada.',
    }


class ApiProximidadeBaseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='teste_proximidade', password='test-only')
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        Conversa.objects.filter(usuario=self.user).delete()

    def _post(self, pergunta):
        return self.client.post(
            reverse('chat:api'),
            data=json.dumps({'pergunta': pergunta}),
            content_type='application/json',
        )

    def _mensagens(self):
        conversas = repo.listar_conversas(user=self.user)
        self.assertTrue(conversas)
        return repo.mensagens_da_conversa(conversas[0]['id'])


class FluxoProximidadeTests(ApiProximidadeBaseTests):

    @patch(ALVO_CONSULTAR)
    @patch(ALVO_PLANEJAR)
    def test_consulta_geografica_responde_tabela_e_link(self, mock_planejar, mock_consultar):
        mock_planejar.return_value = _plano_resultado()
        mock_consultar.return_value = _consulta_ok()

        resp = self._post(
            'Indústrias metalúrgicas ativas abertas há mais de 3 anos '
            'num raio de 10 km de Joinville/SC')

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertTrue(dados['ok'])
        self.assertEqual(dados['tipo'], 'proximidade')
        self.assertEqual(dados['total_linhas'], 1228)
        self.assertEqual(dados['rows'][0]['cnpj_completo'], '84709955000102')
        self.assertEqual(dados['link_proximidade'],
                         '/proximidade/?origem=Joinville/SC&raio=10&situacao=ATIVA')
        self.assertIn('1.228', dados['resposta_texto'])

        # Persistiu pergunta + resposta (com plano) na conversa
        msgs = self._mensagens()
        self.assertEqual(len(msgs), 2)
        assistente = msgs[1]
        plano = (assistente.get('plano') if isinstance(assistente, dict)
                 else getattr(assistente, 'plano', None))
        self.assertIsInstance(plano, dict)
        self.assertEqual(plano['origem']['latitude'], -26.3)
        # F5: a tabela precisa voltar do banco (colunas/rows persistidos)
        colunas = (assistente.get('colunas') if isinstance(assistente, dict)
                   else getattr(assistente, 'colunas', None))
        rows = (assistente.get('rows') if isinstance(assistente, dict)
                else getattr(assistente, 'rows', None))
        self.assertTrue(colunas)
        self.assertEqual(rows[0]['cnpj_completo'], '84709955000102')
        recarregada = self.client.get(reverse('chat:chat'))
        self.assertContains(recarregada, '84709955000102')

    @patch('chat.views.executar_sql')
    @patch('chat.views.gerar_sql')
    @patch(ALVO_CONSULTAR)
    @patch(ALVO_PLANEJAR)
    def test_planner_diz_sql_e_cai_no_pipeline_antigo(
            self, mock_planejar, mock_consultar, mock_gerar, mock_exec):
        mock_planejar.return_value = _plano_resultado(intencao='sql')
        from chat.services.sql_validator import ValidationResult
        mock_gerar.return_value = {
            'ok': True,
            'sql': 'SELECT COUNT(*) AS total FROM c_empresas LIMIT 1000',
            'erro': None,
            'tentativas': 1,
            'resposta_bruta': 'SELECT COUNT(*) AS total FROM c_empresas',
            'validation': ValidationResult(
                sql='SELECT COUNT(*) AS total FROM c_empresas LIMIT 1000',
                original_sql='SELECT COUNT(*) AS total FROM c_empresas',
                teve_limit=True,
                limit_valor=1000,
            ),
        }
        mock_exec.return_value = {
            'ok': True, 'rows': [{'total': 10}], 'colunas': ['total'],
            'erro': None, 'tempo_ms': 5, 'total_linhas': 1, 'truncado': False,
        }
        with patch('chat.views.gerar_texto_resposta', return_value='10 empresas.'):
            resp = self._post('A partir de 2020, quantas empresas existem?')

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertTrue(dados['ok'])
        self.assertEqual(dados['rows'], [{'total': 10}])
        mock_consultar.assert_not_called()

    @patch(ALVO_CONSULTAR)
    @patch(ALVO_PLANEJAR)
    def test_esclarecimento_vira_texto_do_assistente(self, mock_planejar, mock_consultar):
        mock_planejar.return_value = _plano_resultado()
        mock_consultar.return_value = _consulta_esclarecimento()

        resp = self._post('Clínicas num raio de 5 km de algum lugar')

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertTrue(dados['ok'])
        self.assertEqual(dados['tipo'], 'proximidade')
        self.assertEqual(dados['etapa'], 'esclarecimento')
        self.assertIn('preciso da origem', dados['resposta_texto'])
        self.assertEqual(dados['rows'], [])
        self.assertEqual(len(self._mensagens()), 2)

    @patch(ALVO_CONSULTAR)
    @patch(ALVO_PLANEJAR)
    def test_erro_de_execucao_devolve_ok_false(self, mock_planejar, mock_consultar):
        mock_planejar.return_value = _plano_resultado()
        mock_consultar.return_value = _consulta_erro()

        resp = self._post('Clínicas num raio de 5 km de Curitiba/PR')

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertFalse(dados['ok'])
        self.assertEqual(dados['etapa'], 'execucao')
        self.assertIn('pesada', dados['resposta_texto'])

    @patch(ALVO_CONSULTAR)
    @patch(ALVO_PLANEJAR)
    def test_followup_recebe_contexto_geo_da_conversa(self, mock_planejar, mock_consultar):
        # 1a pergunta: grava o plano com a origem
        mock_planejar.return_value = _plano_resultado()
        mock_consultar.return_value = _consulta_ok()
        self._post('Metalúrgicas num raio de 10 km de Joinville/SC')

        # 2a pergunta: "deste endereço" -> a view passa o contexto para o planner
        mock_planejar.reset_mock()
        mock_planejar.return_value = _plano_resultado()
        mock_consultar.return_value = _consulta_ok()
        self._post('Supermercados a até 5 km deste endereço')

        contexto = mock_planejar.call_args.kwargs.get('contexto_geo')
        self.assertIsInstance(contexto, dict)
        self.assertEqual(contexto['latitude'], -26.3)
        self.assertEqual(contexto['longitude'], -48.8)
