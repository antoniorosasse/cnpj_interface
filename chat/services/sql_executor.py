# cnpj_interface/chat/services/sql_executor.py
"""
Executor de SQL do chat IA.

Conecta no Postgres como usuario read-only (chat_ia), roda o SQL
ja validado pelo sql_validator e devolve as linhas como list[dict].

Camadas de seguranca:
  - Conexao com readonly=True (redundante com GRANT SELECT do Postgres)
  - statement_timeout por query (CHAT_SQL_TIMEOUT)
  - Limite de linhas (CHAT_SQL_MAX_ROWS)
  - Fecha conexao sempre (finally)

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import os
import time
import logging

import psycopg2
import psycopg2.extras
from django.conf import settings

from .sql_validator import ValidationResult
from typing import Optional


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURACAO (lida do .env / settings)
# ============================================================

def _get_config() -> dict:
    """Le a config do usuario read-only, com fallback para PG_*."""
    return {
        'host': os.getenv('CHAT_PG_HOST') or getattr(settings, 'PG_HOST', None) or 'localhost',
        'port': int(os.getenv('CHAT_PG_PORT') or getattr(settings, 'PG_PORT', None) or 5432),
        'dbname': os.getenv('CHAT_PG_DB') or getattr(settings, 'PG_DB', None) or 'bancocnpj',
        'user': os.getenv('CHAT_PG_USER') or 'chat_ia',
        'password': os.getenv('CHAT_PG_PASSWORD') or '',
        'connect_timeout': 5,
    }


def _get_timeout() -> int:
    return int(os.getenv('CHAT_SQL_TIMEOUT', '10'))


def _get_max_rows() -> int:
    return int(os.getenv('CHAT_SQL_MAX_ROWS', '1000'))


# ============================================================
# EXCECOES
# ============================================================

class SQLExecutionError(Exception):
    """Erro ao executar SQL no Postgres."""
    pass


# ============================================================
# FUNCAO PRINCIPAL
# ============================================================

def executar_sql(validation: Optional[ValidationResult]) -> dict:
    """
    Executa o SQL de um ValidationResult e devolve as linhas.

    Retorna dict:
      {
        'ok': bool,
        'rows': list[dict],
        'colunas': list[str],
        'erro': str | None,
        'tempo_ms': int,
        'total_linhas': int,
        'truncado': bool,
      }
    """
    if validation is None or not validation.sql:
        return {
            'ok': False,
            'rows': [],
            'colunas': [],
            'erro': 'ValidationResult vazio.',
            'tempo_ms': 0,
            'total_linhas': 0,
            'truncado': False,
        }

    sql = validation.sql
    max_rows = _get_max_rows()
    timeout_ms = _get_timeout() * 1000
    cfg = _get_config()

    if not cfg['password']:
        return {
            'ok': False,
            'rows': [],
            'colunas': [],
            'erro': 'CHAT_PG_PASSWORD nao configurada no .env.',
            'tempo_ms': 0,
            'total_linhas': 0,
            'truncado': False,
        }

    conn = None
    cursor = None
    inicio = time.monotonic()

    try:
        conn = psycopg2.connect(**cfg)
        # Modo read-only: qualquer tentativa de escrita falha no proprio PG
        conn.set_session(readonly=True, autocommit=False)

        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # statement_timeout so vale para a transacao atual
        cursor.execute(f'SET LOCAL statement_timeout = {timeout_ms}')

        # Executa o SELECT
        cursor.execute(sql)

        # Nome das colunas
        colunas = [d.name for d in cursor.description] if cursor.description else []

        # Pega ate max_rows+1 para detectar truncamento
        rows_raw = cursor.fetchmany(max_rows + 1)
        truncado = len(rows_raw) > max_rows
        if truncado:
            rows_raw = rows_raw[:max_rows]

        # RealDictRow -> dict comum (mais facil de serializar)
        rows = [dict(r) for r in rows_raw]

        conn.rollback()  # nada para commitar, mas fecha a transacao limpa

        tempo_ms = int((time.monotonic() - inicio) * 1000)

        return {
            'ok': True,
            'rows': rows,
            'colunas': colunas,
            'erro': None,
            'tempo_ms': tempo_ms,
            'total_linhas': len(rows),
            'truncado': truncado,
        }

    except psycopg2.errors.QueryCanceled as exc:
        logger.warning('Query cancelada por timeout: %s', exc)
        return {
            'ok': False,
            'rows': [],
            'colunas': [],
            'erro': f'Query excedeu o tempo limite de {_get_timeout()}s.',
            'tempo_ms': int((time.monotonic() - inicio) * 1000),
            'total_linhas': 0,
            'truncado': False,
        }

    except psycopg2.Error as exc:
        logger.exception('Erro do Postgres ao executar SQL')
        return {
            'ok': False,
            'rows': [],
            'colunas': [],
            'erro': f'Erro no Postgres: {exc.pgerror or exc}',
            'tempo_ms': int((time.monotonic() - inicio) * 1000),
            'total_linhas': 0,
            'truncado': False,
        }

    except Exception as exc:
        logger.exception('Erro inesperado ao executar SQL')
        return {
            'ok': False,
            'rows': [],
            'colunas': [],
            'erro': f'Erro inesperado: {exc}',
            'tempo_ms': int((time.monotonic() - inicio) * 1000),
            'total_linhas': 0,
            'truncado': False,
        }

    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
               