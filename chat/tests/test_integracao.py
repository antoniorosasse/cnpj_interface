# cnpj_interface/chat/tests/test_integracao.py
"""
Testes de integracao fim-a-fim do chat.

Simula o fluxo real do usuario:
  - Abre o chat (cria conversa)
  - Faz uma pergunta (mocka LLM + executor)
  - Verifica que salvou no backend ativo
  - Recarrega (le a conversa)
  - Deleta a conversa

Roda com os dois backends:
    python manage.py test chat.tests.test_integracao                 # Postgres
    CHAT_USE_MONGO=1 python manage.py test chat.tests.test_integracao  # Mongo

ATENCAO: quando roda no Mongo, APAGA a collection chat_conversas
antes/depois. Nao rodar com dados reais.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import json
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from chat import repo
from chat import mongo_models as mm


User = get_user_model()


# Caminhos dos alvos a mockar
ALVO_GERAR_SQL = 'chat.views.gerar_sql'
ALVO_EXECUTAR_SQL = 'chat.views.executar_sql'


def _fake_validation():
    """Cria um ValidationResult fake (mesmo formato do sql_validator)."""
    from chat.services.sql_validator import ValidationResult
    return ValidationResult(
        sql='SELECT COUNT(*) AS total FROM c_empresas LIMIT 1000',
        original_sql='SELECT COUNT(*) AS total FROM c_empresas',
        teve_limit=True,
        limit_valor=1000,
    )


def _fake_geracao_ok():
    """Retorno fake do gerar_sql (sucesso)."""
    return {
        'ok': True,
        'sql': 'SELECT COUNT(*) AS total FROM c_empresas LIMIT 1000',
        'erro': None,
        'tentativas': 1,
        'resposta_bruta': 'SELECT COUNT(*) AS total FROM c_empresas',
        'validation': _fake_validation(),
    }


def _fake_execucao_ok():
    """Retorno fake do executar_sql (sucesso)."""
    return {
        'ok': True,
        'rows': [{'total': 36905702}],
        'colunas': ['total'],
        'erro': None,
        'tempo_ms': 42,
        'total_linhas': 1,
        'truncado': False,
    }


def _fake_execucao_truncado():
    """Retorno fake do executar_sql com truncado=True."""
    return {
        'ok': True,
        'rows': [{'total': i} for i in range(5)],
        'colunas': ['total'],
        'erro': None,
        'tempo_ms': 100,
        'total_linhas': 1000,
        'truncado': True,
    }


def _fake_geracao_erro():
    """Retorno fake do gerar_sql (falha)."""
    return {
        'ok': False,
        'sql': '',
        'erro': 'Erro simulado na geracao',
        'tentativas': 1,
        'resposta_bruta': '',
        'validation': None,
    }


def _fake_execucao_erro():
    """Retorno fake do executar_sql (falha)."""
    return {
        'ok': False,
        'rows': [],
        'colunas': [],
        'erro': 'Erro simulado na execucao',
        'tempo_ms': 5,
        'total_linhas': 0,
        'truncado': False,
    }


class IntegracaoBaseTests(TestCase):
    """Base: cria usuario, limpa o backend antes/depois."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Se for Mongo, confirma conexao e limpa
        if repo.backend_ativo() == 'mongo':
            try:
                mm._get_collection().database.client.admin.command('ping')
            except Exception as e:
                raise RuntimeError(f'Nao foi possivel conectar ao MongoDB: {e}')

    def setUp(self):
        self.user = User.objects.create_user(
            username='teste_integracao',
            password='test-only',
        )
        self.client = Client()
        self.client.force_login(self.user)
        self._limpar_backend()

    def tearDown(self):
        self._limpar_backend()

    def _limpar_backend(self):
        """Limpa o backend ativo (so conversas do usuario de teste)."""
        if repo.backend_ativo() == 'mongo':
            # No Mongo, apaga TUDO (nao tem isolamento por usuario no teste)
            mm._get_collection().delete_many({})
        else:
            # No Postgres, apaga so do usuario de teste
            from chat.models import Conversa
            Conversa.objects.filter(usuario=self.user).delete()


# ============================================================
# Fluxo completo: pergunta -> salva -> recarrega -> deleta
# ============================================================

class FluxoCompletoTests(IntegracaoBaseTests):

    @patch(ALVO_EXECUTAR_SQL)
    @patch(ALVO_GERAR_SQL)
    def test_fluxo_pergunta_sucesso(self, mock_gerar, mock_exec):
        """Pergunta OK -> salva 2 mensagens -> aparece no historico."""
        mock_gerar.return_value = _fake_geracao_ok()
        mock_exec.return_value = _fake_execucao_ok()

        # 1) Faz a pergunta
        resp = self.client.post(
            reverse('chat:api'),
            data=json.dumps({'pergunta': 'Quantas empresas existem?'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertTrue(dados['ok'])
        self.assertEqual(dados['total_linhas'], 1)
        self.assertEqual(dados['colunas'], ['total'])
        self.assertIn('SELECT COUNT(*)', dados['sql'])

        # 2) Confirma que a conversa foi criada
        conversas = repo.listar_conversas(user=self.user)
        self.assertEqual(len(conversas), 1)
        self.assertEqual(conversas[0]['total_mensagens'], 2)
        self.assertEqual(conversas[0]['titulo'], 'Quantas empresas existem?')

        # 3) Confirma que as mensagens foram salvas com os extras
        cid = conversas[0]['id']
        msgs = repo.mensagens_da_conversa(cid)
        self.assertEqual(len(msgs), 2)

        # Mensagem [0] = user
        m0 = msgs[0] if isinstance(msgs[0], dict) else msgs[0].__dict__
        papel0 = m0.get('papel')
        self.assertEqual(papel0, 'user')

        # Mensagem [1] = assistant, com sql/colunas/rows
        m1 = msgs[1] if isinstance(msgs[1], dict) else msgs[1].__dict__
        self.assertEqual(m1.get('papel'), 'assistant')
        self.assertIn('SELECT COUNT(*)', m1.get('sql', ''))

        # 4) Recarrega a pagina do chat (deve achar as mensagens)
        resp2 = self.client.get(reverse('chat:chat'))
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, 'Quantas empresas existem?')

    @patch(ALVO_EXECUTAR_SQL)
    @patch(ALVO_GERAR_SQL)
    def test_fluxo_pergunta_erro_geracao(self, mock_gerar, mock_exec):
        """Pergunta que falha na geracao -> salva so a mensagem do user."""
        mock_gerar.return_value = _fake_geracao_erro()

        resp = self.client.post(
            reverse('chat:api'),
            data=json.dumps({'pergunta': 'pergunta ruim'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertFalse(dados['ok'])
        self.assertEqual(dados['etapa'], 'geracao')

        # Confirma que SO a mensagem do user foi salva
        conversas = repo.listar_conversas(user=self.user)
        self.assertEqual(len(conversas), 1)
        self.assertEqual(conversas[0]['total_mensagens'], 1)

    @patch(ALVO_EXECUTAR_SQL)
    @patch(ALVO_GERAR_SQL)
    def test_fluxo_pergunta_erro_execucao(self, mock_gerar, mock_exec):
        """Pergunta que falha na execucao -> salva 2 mensagens (uma com erro)."""
        mock_gerar.return_value = _fake_geracao_ok()
        mock_exec.return_value = _fake_execucao_erro()

        resp = self.client.post(
            reverse('chat:api'),
            data=json.dumps({'pergunta': 'pergunta que quebra'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertFalse(dados['ok'])
        self.assertEqual(dados['etapa'], 'execucao')

        # 2 mensagens: user + assistant (com erro)
        conversas = repo.listar_conversas(user=self.user)
        self.assertEqual(conversas[0]['total_mensagens'], 2)

    def test_pergunta_vazia_retorna_400(self):
        resp = self.client.post(
            reverse('chat:api'),
            data=json.dumps({'pergunta': '   '}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_json_invalido_retorna_400(self):
        resp = self.client.post(
            reverse('chat:api'),
            data='isso nao e json',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    # ============================================================
    # RESPOSTA_TEXTO NO JSON
    # ============================================================

    @patch('chat.views.gerar_texto_resposta')
    @patch(ALVO_EXECUTAR_SQL)
    @patch(ALVO_GERAR_SQL)
    def test_json_tem_resposta_texto_quando_sucesso(
        self, mock_gerar, mock_exec, mock_texto
    ):
        mock_gerar.return_value = _fake_geracao_ok()
        mock_exec.return_value = _fake_execucao_ok()
        mock_texto.return_value = 'texto gerado pelo LLM'

        resp = self.client.post(
            reverse('chat:api'),
            data=json.dumps({'pergunta': 'Quantas empresas?'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertTrue(dados['ok'])
        self.assertEqual(dados['resposta_texto'], 'texto gerado pelo LLM')

    @patch('chat.views.gerar_texto_resposta')
    @patch(ALVO_EXECUTAR_SQL)
    @patch(ALVO_GERAR_SQL)
    def test_json_resposta_texto_quando_truncado(
        self, mock_gerar, mock_exec, mock_texto
    ):
        mock_gerar.return_value = _fake_geracao_ok()
        mock_exec.return_value = _fake_execucao_truncado()
        mock_texto.return_value = 'lista parcial'

        resp = self.client.post(
            reverse('chat:api'),
            data=json.dumps({'pergunta': 'Liste tudo'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        # Confirma que gerar_texto_resposta foi chamado com truncado=True
        self.assertTrue(mock_texto.called)
        kwargs = mock_texto.call_args.kwargs
        self.assertTrue(kwargs['truncado'])
        self.assertEqual(kwargs['total_linhas_exibidas'], 1000) 



# ============================================================
# chat_view: cria conversa, lista, limpa vazias
# ============================================================
    @patch(ALVO_GERAR_SQL)
    def test_recusa_grava_assistente_no_historico(self, mock_gerar):
        mock_gerar.return_value = {
            'ok': False,
            'sql': '',
            'erro': 'o banco nao tem coluna X',
            'tipo_erro': 'recusa',
            'tentativas': 1,
            'resposta_bruta': 'RECUSAR: ...',
            'validation': None,
        }
        resp = self.client.post(
            reverse('chat:api'),
            data=json.dumps({'pergunta': 'Pergunta impossivel'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        dados = resp.json()
        self.assertEqual(dados['tipo_erro'], 'recusa')

        # Confirma que gravou 2 mensagens: user + assistant
        conversas = repo.listar_conversas(user=self.user)
        self.assertEqual(conversas[0]['total_mensagens'], 2)


class ChatViewTests(IntegracaoBaseTests):

    def test_chat_view_cria_conversa_se_nao_tiver(self):
        resp = self.client.get(reverse('chat:chat'))
        self.assertEqual(resp.status_code, 200)

        conversas = repo.listar_conversas(user=self.user)
        self.assertEqual(len(conversas), 1)

    def test_chat_view_limpa_conversas_vazias_antigas(self):
        """Se tem 2 vazias, a view mantem so a atual."""
        cid_antiga = repo.criar_conversa(user=self.user, titulo='antiga vazia')
        cid_atual = repo.criar_conversa(user=self.user, titulo='atual')

        # Simula a sessao apontando pra cid_atual
        session = self.client.session
        session['chat_conversa_id'] = cid_atual
        session.save()

        # Acessa o chat
        self.client.get(reverse('chat:chat'))

        # A antiga vazia deve ter sido removida
        self.assertIsNone(repo.buscar_conversa(cid_antiga, user=self.user))
        self.assertIsNotNone(repo.buscar_conversa(cid_atual, user=self.user))

    def test_chat_view_nao_limpa_conversa_com_mensagens(self):
        """Conversa com mensagens nunca e removida pelo housekeeping."""
        cid = repo.criar_conversa(user=self.user, titulo='com msg')
        repo.adicionar_mensagem(cid, 'user', 'oi')

        self.client.get(reverse('chat:chat'))

        self.assertIsNotNone(repo.buscar_conversa(cid, user=self.user))


# ============================================================
# chat_historico_view
# ============================================================

class HistoricoViewTests(IntegracaoBaseTests):

    def test_historico_lista_conversas(self):
        repo.criar_conversa(user=self.user, titulo='conversa 1')
        repo.criar_conversa(user=self.user, titulo='conversa 2')

        resp = self.client.get(reverse('chat:historico'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'conversa 1')
        self.assertContains(resp, 'conversa 2')


# ============================================================
# chat_nova_view
# ============================================================

class NovaViewTests(IntegracaoBaseTests):

    def test_nova_view_cria_conversa(self):
        resp = self.client.post(reverse('chat:nova'))
        self.assertEqual(resp.status_code, 302)

        conversas = repo.listar_conversas(user=self.user)
        self.assertEqual(len(conversas), 1)

    def test_nova_view_reusa_conversa_vazia(self):
        """Se a atual ja e vazia, nao cria outra."""
        cid = repo.criar_conversa(user=self.user)
        session = self.client.session
        session['chat_conversa_id'] = cid
        session.save()

        self.client.post(reverse('chat:nova'))

        conversas = repo.listar_conversas(user=self.user)
        self.assertEqual(len(conversas), 1)


# ============================================================
# chat_deletar_view
# ============================================================

class DeletarViewTests(IntegracaoBaseTests):

    def test_deletar_remove_conversa(self):
        cid = repo.criar_conversa(user=self.user, titulo='pra deletar')
        resp = self.client.post(reverse('chat:deletar', args=[cid]))
        self.assertEqual(resp.status_code, 302)

        self.assertIsNone(repo.buscar_conversa(cid, user=self.user))

    def test_deletar_limpa_sessao_se_for_atual(self):
        cid = repo.criar_conversa(user=self.user)
        session = self.client.session
        session['chat_conversa_id'] = cid
        session.save()

        self.client.post(reverse('chat:deletar', args=[cid]))

        self.assertNotIn('chat_conversa_id', self.client.session)

