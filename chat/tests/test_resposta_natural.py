# cnpj_interface/chat/tests/test_resposta_natural.py
"""
Testes da geracao de texto natural (chat/services/resposta_natural.py).

Usa mock para nao chamar a API DeepSeek de verdade.

Roda com:
    python manage.py test chat.tests.test_resposta_natural

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from unittest.mock import patch

from django.test import TestCase

from chat.services.resposta_natural import (
    gerar_texto_resposta,
    _montar_user_prompt,
    _fallback_texto,
    LIMITE_LINHAS,
)
from chat.services.llm_client import LLMError


ALVO_LLM = 'chat.services.resposta_natural.gerar_resposta'


class SemDadosTests(TestCase):
    """Quando nao tem rows/colunas, nao chama o LLM."""

    @patch(ALVO_LLM)
    def test_rows_vazio_retorna_string_vazia(self, mock_llm):
        r = gerar_texto_resposta('pergunta', ['a', 'b'], [])
        self.assertEqual(r, '')
        self.assertEqual(mock_llm.call_count, 0)

    @patch(ALVO_LLM)
    def test_colunas_vazio_retorna_string_vazia(self, mock_llm):
        r = gerar_texto_resposta('pergunta', [], [{'a': 1}])
        self.assertEqual(r, '')
        self.assertEqual(mock_llm.call_count, 0)

    @patch(ALVO_LLM)
    def test_rows_none_retorna_string_vazia(self, mock_llm):
        r = gerar_texto_resposta('pergunta', ['a'], None)
        self.assertEqual(r, '')
        self.assertEqual(mock_llm.call_count, 0)


class MontarUserPromptTests(TestCase):
    """Testes da montagem do prompt (sem chamar LLM)."""

    def test_inclui_pergunta_e_colunas(self):
        out = _montar_user_prompt('quantas empresas?', ['uf', 'total'], [{'uf': 'SP', 'total': 10}])
        self.assertIn('quantas empresas?', out)
        self.assertIn('uf, total', out)
        self.assertIn('SP', out)
        self.assertIn('10', out)

    def test_limita_a_LIMITE_LINHAS(self):
        rows = [{'n': i} for i in range(LIMITE_LINHAS + 10)]
        out = _montar_user_prompt('pergunta', ['n'], rows)
        # A ultima linha enviada deve ser a LIMITE_LINHAS-esima (indice LIMITE_LINHAS-1)
        self.assertIn(f'"n": {LIMITE_LINHAS - 1}', out)
        # A linha LIMITE_LINHAS nao deve aparecer
        self.assertNotIn(f'"n": {LIMITE_LINHAS},', out)

    def test_mostra_total_quando_truncou(self):
        """Sempre diz o total real, mas NAO menciona quantas linhas ve."""
        rows = [{'n': i} for i in range(LIMITE_LINHAS + 5)]
        out = _montar_user_prompt('pergunta', ['n'], rows)
        self.assertIn(f'Total de linhas: {LIMITE_LINHAS + 5}', out)
        # Nao deve mencionar "primeiras N" (isso e' papel do aviso_truncado)
        self.assertNotIn('primeiras', out.lower())

    def test_mostra_total_quando_nao_truncou(self):
        """Sempre diz o total real, sem 'voce esta vendo todas'."""
        rows = [{'n': i} for i in range(3)]
        out = _montar_user_prompt('pergunta', ['n'], rows)
        self.assertIn('Total de linhas: 3', out)
        # Nao deve mencionar "voce esta vendo todas"
        self.assertNotIn('voce esta vendo', out.lower())

    def test_nao_avisa_quando_nao_truncou(self):
        rows = [{'n': i} for i in range(3)]
        out = _montar_user_prompt('pergunta', ['n'], rows)
        self.assertNotIn('Observacao', out)


class GerarTextoSucessoTests(TestCase):
    """Testes do caminho feliz (LLM mockado)."""

    @patch(ALVO_LLM)
    def test_retorna_texto_do_llm(self, mock_llm):
        mock_llm.return_value = 'Sao Paulo lidera com 10 milhoes de empresas.'
        r = gerar_texto_resposta(
            'quantas por uf?',
            ['uf', 'total'],
            [{'uf': 'SP', 'total': 10000000}],
        )
        self.assertEqual(r, 'Sao Paulo lidera com 10 milhoes de empresas.')
        self.assertEqual(mock_llm.call_count, 1)

    @patch(ALVO_LLM)
    def test_strip_espacos(self, mock_llm):
        mock_llm.return_value = '   texto com espacos   \n\n'
        r = gerar_texto_resposta('p', ['a'], [{'a': 1}])
        self.assertEqual(r, 'texto com espacos')

    @patch(ALVO_LLM)
    def test_passa_temperature_baixa(self, mock_llm):
        mock_llm.return_value = 'ok'
        gerar_texto_resposta('p', ['a'], [{'a': 1}])
        kwargs = mock_llm.call_args.kwargs
        self.assertEqual(kwargs['temperature'], 0.2)


class GerarTextoFallbackTests(TestCase):
    """Quando o LLM falha, devolve fallback (nunca levanta)."""

    @patch(ALVO_LLM)
    def test_llm_error_retorna_fallback(self, mock_llm):
        mock_llm.side_effect = LLMError('timeout')
        r = gerar_texto_resposta('p', ['a'], [{'a': 1}, {'a': 2}])
        self.assertIn('2 linhas', r)
        self.assertIn('tabela', r.lower())

    @patch(ALVO_LLM)
    def test_llm_retorna_vazio_usa_fallback(self, mock_llm):
        mock_llm.return_value = '   '
        r = gerar_texto_resposta('p', ['a'], [{'a': 1}])
        self.assertIn('1 linha', r)

    @patch(ALVO_LLM)
    def test_excecao_generica_retorna_fallback(self, mock_llm):
        mock_llm.side_effect = RuntimeError('boom')
        r = gerar_texto_resposta('p', ['a'], [{'a': 1}])
        self.assertIn('1 linha', r)


class FallbackTextoTests(TestCase):
    """Testes do texto de fallback puro (sem LLM)."""

    def test_singular(self):
        r = _fallback_texto(['a'], [{'a': 1}])
        self.assertIn('1 linha', r)
        self.assertNotIn('1 linhas', r)

    def test_plural(self):
        r = _fallback_texto(['a'], [{'a': 1}, {'a': 2}])
        self.assertIn('2 linhas', r)

class TruncadoTests(TestCase):
    """Testes do parametro truncado em _montar_user_prompt e gerar_texto_resposta."""

    def test_prompt_sem_truncado_nao_menciona(self):
        out = _montar_user_prompt('p', ['a'], [{'a': 1}])
        self.assertNotIn('TRUNCADA', out.upper())

    def test_prompt_com_truncado_menciona(self):
        out = _montar_user_prompt(
            'p', ['a'], [{'a': 1}],
            truncado=True,
            total_linhas_exibidas=1000,
        )
        self.assertIn('TRUNCADA', out.upper())
        self.assertIn('1000', out)

    @patch(ALVO_LLM)
    def test_gerar_texto_resposta_aceita_truncado(self, mock_llm):
        mock_llm.return_value = 'texto ok'
        r = gerar_texto_resposta(
            'p', ['a'], [{'a': 1}],
            truncado=True,
            total_linhas_exibidas=1000,
        )
        self.assertEqual(r, 'texto ok')
        # Verifica que o prompt enviado ao LLM mencionou TRUNCADA
        kwargs = mock_llm.call_args.kwargs
        self.assertIn('TRUNCADA', kwargs['user_prompt'].upper())


class SystemPromptTests(TestCase):
    """Verifica que o SYSTEM_PROMPT contem as regras novas."""

    def test_prompt_manda_citar_total_real(self):
        from chat.services.resposta_natural import SYSTEM_PROMPT
        self.assertIn('Total de linhas', SYSTEM_PROMPT)
        self.assertIn('TOTAL REAL', SYSTEM_PROMPT)

    def test_prompt_manda_destacar_top_n(self):
        from chat.services.resposta_natural import SYSTEM_PROMPT, TOP_N
        self.assertIn(str(TOP_N), SYSTEM_PROMPT)

    def test_prompt_proibe_dizer_apenas_20(self):
        from chat.services.resposta_natural import SYSTEM_PROMPT
        # O prompt deve instruir explicitamente a NAO dizer "apenas 20"
        self.assertIn('NUNCA diga', SYSTEM_PROMPT)
        
class PromptNaoMencionaParcialTests(TestCase):
    """Garante que _montar_user_prompt nao sugere 'parcial' quando nao truncado."""

    def test_sem_truncado_nao_menciona_primeiras(self):
        rows = [{'a': i} for i in range(30)]  # > LIMITE_LINHAS, mas nao truncado
        out = _montar_user_prompt('p', ['a'], rows)
        self.assertNotIn('primeiras', out.lower())
        self.assertNotIn('TRUNCADA', out.upper())

    def test_com_truncado_ainda_menciona(self):
        rows = [{'a': i} for i in range(30)]
        out = _montar_user_prompt(
            'p', ['a'], rows,
            truncado=True,
            total_linhas_exibidas=30,
        )
        self.assertIn('TRUNCADA', out.upper())

                