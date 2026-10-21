# chat/services/embedding_service.py
"""
Servico de embedding para o chat IA.

Gera o vetor de uma pergunta em portugues usando o modelo Braza
e formata como string para injetar no SQL (halfvec(384)).
"""

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

MODELO_NOME = 'calneymgp/braza-embedding-ptbr-v1'

@lru_cache(maxsize=1)
def _get_modelo():
    """Carrega o modelo uma vez (singleton)."""
    from sentence_transformers import SentenceTransformer
    logger.info('Carregando modelo de embedding: %s', MODELO_NOME)
    return SentenceTransformer(MODELO_NOME)


def gerar_vetor(pergunta: str) -> str | None:
    """
    Gera o vetor da pergunta e retorna como string formatada
    para o Postgres (ex: '[0.1, 0.2, ...]').

    NUNCA levanta excecao. Se falhar, retorna None.
    """
    try:
        modelo = _get_modelo()
        emb = modelo.encode([pergunta], normalize_embeddings=True)[0]
        # Se o modelo retornar um numpy array, usa .tolist()
        # Se retornar uma lista (ex: em testes), usa direto
        valores = emb.tolist() if hasattr(emb, 'tolist') else emb
        return '[' + ','.join(f'{x:.6f}' for x in valores) + ']'
    except Exception as exc:
        logger.exception('Falha ao gerar embedding da pergunta')
        return None


def tem_placeholder(sql: str) -> bool:
    """Retorna True se o SQL contem o placeholder %s."""
    return '%s' in sql


def injetar_vetor(sql: str, pergunta: str) -> tuple[str, str | None]:
    """
    Substitui o primeiro %s do SQL pelo vetor da pergunta.

    Retorna (sql_final, erro). Se erro for None, deu certo.
    """
    if not tem_placeholder(sql):
        return sql, None

    vetor = gerar_vetor(pergunta)
    if vetor is None:
        return sql, 'Falha ao gerar o vetor da pergunta (modelo indisponivel).'

    # Substitui o PRIMEIRO %s pelo vetor, sem duplicar o cast
    if '::halfvec' in sql:
        # A IA já incluiu o cast no SQL; só substitui o %s pelo vetor cru
        sql_final = sql.replace('%s', f"'{vetor}'", 1)
    else:
        sql_final = sql.replace('%s', f"'{vetor}'::halfvec(384)", 1)
    return sql_final, None
