# cnpj_interface/chat/services/sql_validator.py
"""
Validador de SQL do chat IA.

Aplica 4 camadas de seguranca:
  1. So permite SELECT (bloqueia DDL/DML)
  2. Nao permite multiplas queries (bloqueia ';' fora de strings)
  3. Forca LIMIT (garante que nenhuma query retorne mais que o limite)
  4. Nao permite funcoes perigosas do Postgres (pg_sleep, dblink, etc)
"""

import re
from dataclasses import dataclass

# ============================================================
# CONSTANTES
# ============================================================

DEFAULT_MAX_ROWS = 1000

PALAVRAS_PROIBIDAS = [
    'DROP', 'CREATE', 'ALTER', 'TRUNCATE', 'RENAME',
    'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'UPSERT',
    'GRANT', 'REVOKE',
    'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'BEGIN', 'START',
    'COPY', 'VACUUM', 'ANALYZE', 'CLUSTER', 'REINDEX',
    'EXPLAIN', 'DO', 'CALL', 'LISTEN', 'NOTIFY', 'LOAD',
    'SET', 'RESET', 'DISCARD',
    'PG_READ_FILE', 'PG_WRITE_FILE', 'PG_LS_DIR', 'PG_STAT_FILE',
    'LO_IMPORT', 'LO_EXPORT',
    'DBLINK', 'POSTGRES_FDW', 'PG_SLEEP',
]

FUNCOES_PERIGOSAS = [
    'PG_SLEEP',
    'PG_TERMINATE_BACKEND',
    'PG_CANCEL_BACKEND',
    'SET_CONFIG',
]

REGEX_SELECT_INICIO = re.compile(r'^\s*SELECT\b', re.IGNORECASE)
REGEX_LIMIT = re.compile(r'\bLIMIT\s+(\d+)\b', re.IGNORECASE)
REGEX_COMENTARIO_LINHA = re.compile(r'--[^\n]*')
REGEX_COMENTARIO_BLOCO = re.compile(r'/\*.*?\*/', re.DOTALL)

# ============================================================
# EXCECOES
# ============================================================

class SQLValidationError(Exception):
    """Levantada quando a query falha na validacao."""
    pass


@dataclass
class ValidationResult:
    """Resultado da validacao."""
    sql: str
    original_sql: str
    teve_limit: bool
    limit_valor: int

# ============================================================
# HELPERS
# ============================================================

def _remover_comentarios(sql: str) -> str:
    """Remove comentarios de linha e de bloco."""
    sql = REGEX_COMENTARIO_BLOCO.sub(' ', sql)
    sql = REGEX_COMENTARIO_LINHA.sub(' ', sql)
    return sql


def _normalizar_espacos(sql: str) -> str:
    """Colapsa espacos em branco multiplos em um unico espaco."""
    return re.sub(r'\s+', ' ', sql).strip()


def _tem_multiplas_queries(sql: str) -> bool:
    """Camada 2: detecta ';' que separa queries (ignora ';' dentro de strings)."""
    # remove strings literais para nao confundir ';' dentro delas
    sql_sem_strings = re.sub(r"'(?:[^']|'')*'", "''", sql)
    # remove ponto e virgula final (permitido)
    sql_sem_ponto_final = sql_sem_strings.rstrip().rstrip(';')
    return ';' in sql_sem_ponto_final


def _validar_select_only(sql: str) -> None:
    """Camada 1 + 4: so permite SELECT e bloqueia funcoes/palavras perigosas."""
    sql_normalizado = _normalizar_espacos(sql).upper()

    for palavra in PALAVRAS_PROIBIDAS:
        padrao = rf'\b{palavra}\b'
        if re.search(padrao, sql_normalizado):
            raise SQLValidationError(
                f'Palavra proibida detectada: {palavra}. '
                f'Somente consultas SELECT sao permitidas.'
            )

    if not REGEX_SELECT_INICIO.match(sql):
        raise SQLValidationError(
            'A query deve comecar com SELECT.'
        )

    for funcao in FUNCOES_PERIGOSAS:
        padrao = rf'\b{funcao}\s*\('
        if re.search(padrao, sql_normalizado):
            raise SQLValidationError(
                f'Funcao proibida detectada: {funcao}().'
            )


# Regex: GROUP BY ou funcao de agregacao no SELECT.
# Usado por _aplicar_limit pra decidir se pode forcar LIMIT.
#
# Motivo: GROUP BY / COUNT / SUM / AVG / MIN / MAX sao "agregacoes",
# e o usuario costuma querer o resultado COMPLETO (ex: "quantos
# municipios tem empresa no Brasil?" -> 5570). Forcar LIMIT 1000
# aqui trunca a resposta sem o usuario saber.
REGEX_AGREGACAO = re.compile(
    r'\bGROUP\s+BY\b'
    r'|\bCOUNT\s*\('
    r'|\bSUM\s*\('
    r'|\bAVG\s*\('
    r'|\bMIN\s*\('
    r'|\bMAX\s*\(',
    re.IGNORECASE,
)


def _tem_agregacao(sql: str) -> bool:
    """
    Retorna True se o SQL tem GROUP BY ou funcao de agregacao
    no SELECT (COUNT, SUM, AVG, MIN, MAX).

    Nesse caso, o validador NAO deve forcar LIMIT: a query ja
    retorna poucas linhas por natureza (1 por grupo), e o usuario
    quer o resultado completo.
    """
    return bool(REGEX_AGREGACAO.search(sql))


def _aplicar_limit(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> tuple:
    """
    Camada 3: garante que a query tenha LIMIT <= max_rows.

    EXCECAO: se o SQL for uma agregacao (GROUP BY ou COUNT/SUM/AVG/
    MIN/MAX) e NAO tiver LIMIT explicito, NAO adiciona LIMIT. Isso
    evita truncar respostas como "quantos municipios tem empresa no
    Brasil?" (o usuario quer o total real, nao os 1000 primeiros).

    Retorna (sql_final, teve_limit, limit_valor).
    teve_limit = True significa que o validador PRECISOU adicionar/ajustar LIMIT.
    limit_valor = 0 significa "nenhum LIMIT aplicado".
    """
    match = REGEX_LIMIT.search(sql)

    if match:
        limit_atual = int(match.group(1))
        if limit_atual <= max_rows:
            # LIMIT ja existe e e valido: nao precisou mexer
            return sql, False, limit_atual

        # LIMIT maior que o permitido: reduz para max_rows
        sql_novo = REGEX_LIMIT.sub(f'LIMIT {max_rows}', sql, count=1)
        return sql_novo, True, max_rows

    # Sem LIMIT no SQL. Se for agregacao, respeita (nao adiciona).
    if _tem_agregacao(sql):
        return sql, False, 0

    # Listagem sem LIMIT: adiciona
    sql_novo = sql.rstrip().rstrip(';').rstrip() + f' LIMIT {max_rows}'
    return sql_novo, True, max_rows

# ============================================================
# FUNCAO PUBLICA
# ============================================================

def validar_sql(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> ValidationResult:
    """
    Valida e sanitiza uma query SQL.

    Retorna ValidationResult em caso de sucesso.
    Levanta SQLValidationError em caso de falha.
    """
    if not sql or not sql.strip():
        raise SQLValidationError('SQL vazio.')

    original_sql = sql.strip()

    # normaliza para analise (sem comentarios)
    sql_sem_comentarios = _remover_comentarios(original_sql)
    sql_limpo = _normalizar_espacos(sql_sem_comentarios)

    if not sql_limpo:
        raise SQLValidationError('SQL vazio apos remover comentarios.')

    # camada 1 + 4: SELECT only + funcoes perigosas
    _validar_select_only(sql_limpo)

    # camada 2: multiplas queries
    if _tem_multiplas_queries(sql_limpo):
        raise SQLValidationError(
            'Multiplas queries detectadas. Envie apenas um SELECT.'
        )

    # camada 3: LIMIT obrigatorio
    sql_final, teve_limit, limit_valor = _aplicar_limit(sql_limpo, max_rows)

    return ValidationResult(
        sql=sql_final,
        original_sql=original_sql,
        teve_limit=teve_limit,
        limit_valor=limit_valor,
    )