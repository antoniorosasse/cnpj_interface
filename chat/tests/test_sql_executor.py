# cnpj_interface/chat/tests/test_sql_executor.py
"""
Testes do executor de SQL (sql_executor.py).

Usa mock do psycopg2.connect para nao depender do Postgres real.

Roda com:
    python manage.py test chat.tests.test_sql_executor
"""

from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase

from chat.services.sql_executor import (
    executar_sql,
    SQLExecutionError,
    _get_config,
    _get_timeout,
    _get_max_rows,
)
from chat.services.sql_validator import ValidationResult


ALVO_CONNECT = 'chat.services.sql_executor.psycopg2.connect'


def _vr(sql='SELECT 1'):
    """Atalho para criar um ValidationResult valido."""
    return ValidationResult(
        sql=sql,
        original_sql=sql,
        teve_limit=False,
        limit_valor=1000,
    )


def _mock_conn(colunas, linhas):
    """
    Cria um mock de conexao psycopg2 que devolve as colunas/linhas dadas.

    colunas: lista de nomes (ex: ['total', 'uf'])
    linhas:  lista de tuplas/dicts
    """
    conn = MagicMock()
    cursor = MagicMock()

    # cursor.description: lista de objetos com .name
    descricoes = [MagicMock(name=c) for c in colunas]
    for d, nome in zip(descricoes, colunas):
        d.name = nome
    cursor.description = descricoes

    # fetchmany(max_rows + 1) -> linhas
    cursor.fetchmany.return_value = linhas

    conn.cursor.return_value = cursor
    return conn


class ConfigTests(TestCase):
    """Testes das funcoes de configuracao."""

    @patch.dict('os.environ', {
        'CHAT_PG_HOST': 'meuhost',
        'CHAT_PG_PORT': '5555',
        'CHAT_PG_DB': 'meudb',
        'CHAT_PG_USER': 'meuuser',
        'CHAT_PG_PASSWORD': 'minhasenha',
    }, clear=False)
    def test_get_config_le_do_ambiente(self):
        cfg = _get_config()
        self.assertEqual(cfg['host'], 'meuhost')
        self.assertEqual(cfg['port'], 5555)
        self.assertEqual(cfg['dbname'], 'meudb')
        self.assertEqual(cfg['user'], 'meuuser')
        self.assertEqual(cfg['password'], 'minhasenha')

    @patch.dict('os.environ', {'CHAT_SQL_TIMEOUT': '42'}, clear=False)
    def test_get_timeout(self):
        self.assertEqual(_get_timeout(), 42)

    @patch.dict('os.environ', {'CHAT_SQL_MAX_ROWS': '77'}, clear=False)
    def test_get_max_rows(self):
        self.assertEqual(_get_max_rows(), 77)


class ExecutarSqlValidacaoTests(TestCase):
    """Testes das validacoes antes de conectar no banco."""

    def test_validation_none(self):
        r = executar_sql(None)
        self.assertFalse(r['ok'])
        self.assertIn('vazio', r['erro'].lower())
        self.assertEqual(r['rows'], [])

    def test_validation_sql_vazio(self):
        vr = ValidationResult(sql='', original_sql='', teve_limit=False, limit_valor=0)
        r = executar_sql(vr)
        self.assertFalse(r['ok'])
        self.assertIn('vazio', r['erro'].lower())

    @patch('chat.services.sql_executor._get_config')
    def test_sem_password(self, mock_cfg):
        mock_cfg.return_value = {
            'host': 'x', 'port': 5432, 'dbname': 'x',
            'user': 'x', 'password': '', 'connect_timeout': 5,
        }
        r = executar_sql(_vr())
        self.assertFalse(r['ok'])
        self.assertIn('CHAT_PG_PASSWORD', r['erro'])


class ExecutarSqlSucessoTests(TestCase):
    """Testes do caminho feliz, com mock do psycopg2."""

    @patch(ALVO_CONNECT)
    def test_sucesso_basico(self, mock_connect):
        linhas = [
            {'total': 36905702},
        ]
        mock_connect.return_value = _mock_conn(['total'], linhas)

        r = executar_sql(_vr('SELECT COUNT(*) AS total FROM c_empresas'))

        self.assertTrue(r['ok'])
        self.assertEqual(r['colunas'], ['total'])
        self.assertEqual(r['total_linhas'], 1)
        self.assertEqual(r['rows'][0]['total'], 36905702)
        self.assertIsNone(r['erro'])
        self.assertFalse(r['truncado'])
        self.assertGreaterEqual(r['tempo_ms'], 0)

    @patch(ALVO_CONNECT)
    def test_multiplas_colunas_e_linhas(self, mock_connect):
        linhas = [
            {'uf': 'SP', 'total': 8201579},
            {'uf': 'MG', 'total': 2808742},
            {'uf': 'RJ', 'total': 2203664},
        ]
        mock_connect.return_value = _mock_conn(['uf', 'total'], linhas)

        r = executar_sql(_vr('SELECT uf, COUNT(*) ...'))

        self.assertTrue(r['ok'])
        self.assertEqual(r['colunas'], ['uf', 'total'])
        self.assertEqual(r['total_linhas'], 3)
        self.assertEqual(r['rows'][1]['uf'], 'MG')

    @patch(ALVO_CONNECT)
    def test_resultado_vazio(self, mock_connect):
        mock_connect.return_value = _mock_conn(['total'], [])

        r = executar_sql(_vr())

        self.assertTrue(r['ok'])
        self.assertEqual(r['rows'], [])
        self.assertEqual(r['total_linhas'], 0)
        self.assertEqual(r['colunas'], ['total'])

    @patch(ALVO_CONNECT)
    def test_valores_decimal_sao_convertidos(self, mock_connect):
        # Em producao o RealDictCursor pode devolver Decimal
        linhas = [{'capital_social': Decimal('8013289.00')}]
        mock_connect.return_value = _mock_conn(['capital_social'], linhas)

        r = executar_sql(_vr())
        # O valor bruto e mantido como Decimal (serializacao e' problema do views)
        self.assertIsInstance(r['rows'][0]['capital_social'], Decimal)


class ExecutarSqlTruncamentoTests(TestCase):
    """Testes do limite de linhas (truncado)."""

    @patch(ALVO_CONNECT)
    @patch('chat.services.sql_executor._get_max_rows')
    def test_truncado_quando_passam_mais_linhas(self, mock_max, mock_connect):
        mock_max.return_value = 3
        # fetchmany(4) devolve 4 linhas -> truncado
        linhas = [{'i': 1}, {'i': 2}, {'i': 3}, {'i': 4}]
        mock_connect.return_value = _mock_conn(['i'], linhas)

        r = executar_sql(_vr())

        self.assertTrue(r['ok'])
        self.assertTrue(r['truncado'])
        self.assertEqual(r['total_linhas'], 3)  # cortou pra max

    @patch(ALVO_CONNECT)
    @patch('chat.services.sql_executor._get_max_rows')
    def test_nao_truncado_quando_exatamente_max(self, mock_max, mock_connect):
        mock_max.return_value = 3
        linhas = [{'i': 1}, {'i': 2}, {'i': 3}]
        mock_connect.return_value = _mock_conn(['i'], linhas)

        r = executar_sql(_vr())

        self.assertTrue(r['ok'])
        self.assertFalse(r['truncado'])
        self.assertEqual(r['total_linhas'], 3)


class ExecutarSqlErrosTests(TestCase):
    """Testes dos caminhos de erro."""

    @patch(ALVO_CONNECT)
    def test_erro_psycopg2(self, mock_connect):
        import psycopg2
        mock_connect.side_effect = psycopg2.OperationalError('conexao falhou')

        r = executar_sql(_vr())

        self.assertFalse(r['ok'])
        self.assertIn('Erro no Postgres', r['erro'])
        self.assertEqual(r['rows'], [])

    @patch(ALVO_CONNECT)
    def test_timeout_query_canceled(self, mock_connect):
        import psycopg2.errors
        # Simula a excecao de timeout
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = psycopg2.errors.QueryCanceled('canceling statement')
        conn.cursor.return_value = cursor
        mock_connect.return_value = conn

        r = executar_sql(_vr())

        self.assertFalse(r['ok'])
        self.assertIn('tempo limite', r['erro'].lower())

    @patch(ALVO_CONNECT)
    def test_erro_generico(self, mock_connect):
        mock_connect.side_effect = RuntimeError('boom')

        r = executar_sql(_vr())

        self.assertFalse(r['ok'])
        self.assertIn('inesperado', r['erro'].lower())


class FechamentoConexaoTests(TestCase):
    """Garante que a conexao e o cursor sao sempre fechados."""

    @patch(ALVO_CONNECT)
    def test_fecha_no_sucesso(self, mock_connect):
        conn = _mock_conn(['total'], [{'total': 1}])
        mock_connect.return_value = conn

        executar_sql(_vr())

        conn.close.assert_called_once()
        conn.cursor.return_value.close.assert_called_once()

    @patch(ALVO_CONNECT)
    def test_fecha_no_erro(self, mock_connect):
        import psycopg2
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = psycopg2.OperationalError('boom')
        conn.cursor.return_value = cursor
        mock_connect.return_value = conn

        executar_sql(_vr())

        conn.close.assert_called_once()