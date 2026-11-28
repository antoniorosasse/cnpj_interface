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
import re

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

def executar_sql(validation: Optional[ValidationResult],
                 max_rows: Optional[int] = None) -> dict:
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

    max_rows: teto de linhas desta execucao. None = CHAT_SQL_MAX_ROWS (tela).
    O export do chat passa um teto maior, sem mudar o comportamento da tela.
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
    max_rows = max_rows or _get_max_rows()
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


def _pos_top_level(sql, palavra):
    """Posicao do primeiro keyword no nivel zero (fora de strings/parenteses)."""
    alvo = palavra.upper()
    i = 0
    profundidade = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif ch == '(':
            profundidade += 1
        elif ch == ')':
            profundidade = max(0, profundidade - 1)
        elif profundidade == 0 and sql[i:i + len(alvo)].upper() == alvo:
            antes = sql[i - 1] if i > 0 else ' '
            depois = sql[i + len(alvo)] if i + len(alvo) < len(sql) else ' '
            if not (antes.isalnum() or antes == '_') and not (
                    depois.isalnum() or depois == '_'):
                return i
        i += 1
    return -1


def _simples_in_para_exists(sql):
    """Troca `cnpj_basico IN (SELECT ... c_simples op_mei='S')` por EXISTS."""
    padrao = re.compile(
        r'\b([\w.]+)\.cnpj_basico\s+IN\s*\(\s*SELECT\s+cnpj_basico\s+'
        r'FROM\s+c_simples\s+WHERE\s+op_mei\s*=\s*\'S\'\s*\)',
        re.IGNORECASE)
    return padrao.sub(
        lambda m: (f'EXISTS (SELECT 1 FROM c_simples s '
                   f'WHERE s.cnpj_basico = {m.group(1)}.cnpj_basico '
                   f'AND s.op_mei = \'S\')'),
        sql)


def _simples_join_para_exists(sql):
    """No COUNT, troca JOIN c_simples por EXISTS (MEI).

    O JOIN forca o COUNT a passar por 26M de linhas de simples; o EXISTS
    permite nested-loop pelo indice de cnpj_basico e cai de ~34 s para ~3 s.
    """
    m = re.search(
        r'\bJOIN\s+c_simples\s+(\w+)\s+ON\s+\1\.cnpj_basico\s*=\s*([\w.]+)\.cnpj_basico',
        sql, re.IGNORECASE)
    if not m:
        return sql
    alias, base = m.group(1), m.group(2)
    sql = sql[:m.start()] + sql[m.end():]
    exists = (f"EXISTS (SELECT 1 FROM c_simples {alias} "
              f"WHERE {alias}.cnpj_basico = {base}.cnpj_basico "
              f"AND {alias}.op_mei = 'S')")
    # Substitui a condicao do alias removido pelo EXISTS.
    sql = re.sub(
        r"\bAND\s+" + re.escape(alias) + r"\.op_mei\s*=\s*'S'",
        'AND ' + exists, sql, count=1, flags=re.IGNORECASE)
    return sql


def _limpar_para_contagem(sql):
    """Transforma um SELECT de listagem em SELECT COUNT(*), sem ORDER/LIMIT."""
    if not sql or not sql.lstrip().upper().startswith('SELECT'):
        return None
    upper = sql.upper()
    if ' GROUP BY ' in upper or ' DISTINCT ' in upper or ' UNION ' in upper:
        return None
    pos_from = _pos_top_level(sql, 'FROM')
    if pos_from < 0:
        return None
    pos_order = _pos_top_level(sql, 'ORDER BY')
    pos_limit = _pos_top_level(sql, 'LIMIT')
    fim = len(sql)
    for pos in (pos_order, pos_limit):
        if pos >= 0:
            fim = min(fim, pos)
    corpo = sql[pos_from:fim].strip()
    if not corpo:
        return None

    # Se o WHERE so' usa a tabela base, remove JOINs de exibicao do COUNT.
    # Ex.: listagem de Curitiba faz JOIN em c_empresas/c_cnaes/c_municipios
    # apenas para as colunas; o COUNT fica 0,2 s em vez de 40 s.
    pos_where = _pos_top_level(corpo, 'WHERE')
    pos_join = _pos_top_level(corpo, 'JOIN')
    if pos_where > 0 and pos_join > pos_where:
        pass
    if pos_where > 0 and pos_join > 0 and pos_join < pos_where:
        where_sql = corpo[pos_where:]
        aliases = re.findall(r'\bJOIN\s+[\w.]+\s+(\w+)\b', corpo[pos_join:pos_where],
                             re.IGNORECASE)
        if aliases and not any(re.search(r'\b' + re.escape(a) + r'\.',
                                         where_sql) for a in aliases):
            corpo = corpo[:pos_join] + where_sql
    # Cidade por codigo IBGE: o UF e' redundante no COUNT e atrapalha o
    # indice (municipio, sit, dt_ini). Remove so' no COUNT.
    if re.search(r'\.municipio\s*=\s*\(\s*SELECT', corpo, re.IGNORECASE):
        corpo = re.sub(r"\bAND\s+\w+\.uf\s*=\s*'[A-Z]{2}'",
                       '', corpo, count=1, flags=re.IGNORECASE)
    corpo = _simples_in_para_exists(corpo)
    corpo = _simples_join_para_exists(corpo)
    return 'SELECT COUNT(*) AS total ' + corpo


def contar_linhas(sql, timeout_ms=5000):
    """Conta as linhas de um SELECT de listagem; None se nao der para contar."""
    count_sql = _limpar_para_contagem(sql)
    if not count_sql:
        return None
    cfg = _get_config()
    if not cfg['password']:
        return None
    conn = None
    try:
        conn = psycopg2.connect(**cfg)
        conn.set_session(readonly=True, autocommit=False)
        cur = conn.cursor()
        cur.execute(f'SET LOCAL statement_timeout = {int(timeout_ms)}')
        cur.execute(count_sql)
        valor = cur.fetchone()[0]
        conn.rollback()
        return int(valor) if valor is not None else None
    except Exception:
        logger.exception('Falha ao contar o total real da listagem')
        return None
    finally:
        if conn is not None:
            conn.close()
