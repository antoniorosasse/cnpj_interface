# cnpj_interface/chat/tests/test_sql_generator.py
"""
Testes do gerador de SQL (sql_generator.py).

Usa mock para nao chamar a API DeepSeek de verdade.

Roda com:
    python manage.py test chat.tests.test_sql_generator
"""

from unittest.mock import patch

from django.test import TestCase

from chat.services.sql_generator import (
    gerar_sql,
    _limpar_sql,
    _montar_user_prompt,
)
from chat.services.llm_client import LLMError


# Caminho do alvo a ser mockado
ALVO_LLM = 'chat.services.sql_generator.gerar_resposta'


class LimparSqlTests(TestCase):
    """Testes da funcao _limpar_sql (nao depende do LLM)."""

    def test_remove_cerca_markdown_sql(self):
        bruto = '```sql\nSELECT 1\n```'
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')

    def test_remove_cerca_markdown_simples(self):
        bruto = '```\nSELECT 1\n```'
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')

    def test_remove_prefixo_sql(self):
        bruto = 'SQL: SELECT 1'
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')

    def test_remove_ponto_virgula_final(self):
        bruto = 'SELECT 1;'
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')

    def test_string_vazia(self):
        self.assertEqual(_limpar_sql(''), '')

    def test_none(self):
        self.assertEqual(_limpar_sql(None), '')

    def test_espacos_extras(self):
        bruto = '   SELECT 1   '
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')


class MontarUserPromptTests(TestCase):
    """Testes da montagem do prompt do usuario."""

    def test_sem_historico(self):
        out = _montar_user_prompt('Quantas empresas?')
        self.assertIn('Pergunta atual: Quantas empresas?', out)
        self.assertNotIn('Historico', out)

    def test_com_historico(self):
        historico = [
            {'role': 'user', 'content': 'ola'},
            {'role': 'assistant', 'content': 'SELECT 1'},
        ]
        out = _montar_user_prompt('e agora?', historico)
        self.assertIn('Historico da conversa', out)
        self.assertIn('Usuario: ola', out)
        self.assertIn('Assistente: SELECT 1', out)
        self.assertIn('Pergunta atual: e agora?', out)

    def test_historico_ignora_papeis_invalidos(self):
        historico = [
            {'role': 'system', 'content': 'ignorar'},
            {'role': 'user', 'content': 'manter'},
        ]
        out = _montar_user_prompt('pergunta', historico)
        self.assertNotIn('ignorar', out)
        self.assertIn('manter', out)


class GerarSqlTests(TestCase):
    """Testes da funcao principal gerar_sql (com mock do LLM)."""

    def test_pergunta_vazia(self):
        r = gerar_sql('')
        self.assertFalse(r['ok'])
        self.assertEqual(r['tentativas'], 0)
        self.assertIn('vazia', r['erro'].lower())

    def test_pergunta_so_espacos(self):
        r = gerar_sql('   ')
        self.assertFalse(r['ok'])
        self.assertEqual(r['tentativas'], 0)

    @patch(ALVO_LLM)
    def test_sucesso_primeira_tentativa(self, mock_llm):
        mock_llm.return_value = 'SELECT 1'
        r = gerar_sql('pergunta qualquer')
        self.assertTrue(r['ok'])
        # O validador adiciona LIMIT 1000 quando o SQL nao tem LIMIT
        self.assertEqual(r['sql'], 'SELECT 1 LIMIT 1000')
        self.assertEqual(r['tentativas'], 1)
        self.assertIsNotNone(r['validation'])
        self.assertEqual(mock_llm.call_count, 1)

    @patch(ALVO_LLM)
    def test_llm_devolve_com_markdown(self, mock_llm):
        mock_llm.return_value = '```sql\nSELECT 1\n```'
        r = gerar_sql('pergunta')
        self.assertTrue(r['ok'])
        # O markdown foi removido E o validador adicionou LIMIT
        self.assertEqual(r['sql'], 'SELECT 1 LIMIT 1000')

    @patch(ALVO_LLM)
    def test_llm_devolve_vazio(self, mock_llm):
        # Duas respostas vazias -> falha
        mock_llm.return_value = ''
        r = gerar_sql('pergunta', max_tentativas=2)
        self.assertFalse(r['ok'])
        self.assertEqual(r['tentativas'], 2)
        self.assertIn('vazia', r['erro'].lower())
        self.assertEqual(mock_llm.call_count, 2)

    @patch(ALVO_LLM)
    def test_sql_invalido_retorna_falha(self, mock_llm):
        # LLM sempre devolve DROP -> validador rejeita em todas as tentativas
        mock_llm.return_value = 'DROP TABLE c_empresas'
        r = gerar_sql('pergunta', max_tentativas=2)
        self.assertFalse(r['ok'])
        self.assertEqual(r['tentativas'], 2)
        self.assertIn('proibida', r['erro'].lower())
        self.assertEqual(mock_llm.call_count, 2)

    @patch(ALVO_LLM)
    def test_retry_apos_sql_invalido(self, mock_llm):
        # Primeira resposta invalida, segunda valida
        mock_llm.side_effect = [
            'DROP TABLE c_empresas',
            'SELECT 1',
        ]
        r = gerar_sql('pergunta', max_tentativas=2)
        self.assertTrue(r['ok'])
        # SQL passa pelo validador, que adiciona LIMIT
        self.assertEqual(r['sql'], 'SELECT 1 LIMIT 1000')
        self.assertEqual(r['tentativas'], 2)
        self.assertEqual(mock_llm.call_count, 2)

    @patch(ALVO_LLM)
    def test_erro_llm_retorna_falha(self, mock_llm):
        mock_llm.side_effect = LLMError('timeout')
        r = gerar_sql('pergunta')
        self.assertFalse(r['ok'])
        self.assertIn('LLM', r['erro'])
        self.assertEqual(r['tentativas'], 1)

    @patch(ALVO_LLM)
    def test_sql_com_limit_existente(self, mock_llm):
        mock_llm.return_value = 'SELECT * FROM c_empresas LIMIT 10'
        r = gerar_sql('pergunta')
        self.assertTrue(r['ok'])
        # O validador mantem o LIMIT 10 (menor que o maximo)
        self.assertIn('LIMIT 10', r['sql'])
        self.assertFalse(r['validation'].teve_limit)

    @patch(ALVO_LLM)
    def test_sql_sem_limit_ganha_limit(self, mock_llm):
        mock_llm.return_value = 'SELECT * FROM c_empresas'
        r = gerar_sql('pergunta')
        self.assertTrue(r['ok'])
        self.assertIn('LIMIT', r['sql'].upper())
        self.assertTrue(r['validation'].teve_limit)
        
class HistoricoNoPromptTests(TestCase):
    """Verifica que o historico e passado ao LLM via prompt."""

    @patch(ALVO_LLM)
    def test_historico_aparece_no_prompt(self, mock_llm):
        mock_llm.return_value = 'SELECT 1'
        historico = [
            {'role': 'user', 'content': 'pergunta antiga'},
            {'role': 'assistant', 'content': 'SELECT id FROM x'},
        ]
        gerar_sql('pergunta nova', historico=historico)

        # Pega o user_prompt que foi passado ao mock
        args, kwargs = mock_llm.call_args
        user_prompt = kwargs.get('user_prompt') or args[1]

        self.assertIn('pergunta antiga', user_prompt)
        self.assertIn('SELECT id FROM x', user_prompt)
        self.assertIn('pergunta nova', user_prompt)
        self.assertIn('Historico da conversa', user_prompt)

    @patch(ALVO_LLM)
    def test_sem_historico_nao_tem_secao(self, mock_llm):
        mock_llm.return_value = 'SELECT 1'
        gerar_sql('pergunta nova', historico=None)

        args, kwargs = mock_llm.call_args
        user_prompt = kwargs.get('user_prompt') or args[1]

        self.assertNotIn('Historico da conversa', user_prompt)        