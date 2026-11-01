# chat/services/embedding_service.py
"""
Servico de embedding para o chat IA.

Gera o vetor de uma pergunta em portugues usando o modelo Braza
e formata como string para injetar no SQL (halfvec(384)).
"""

import logging
import os
import re
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

MODELO_NOME = 'calneymgp/braza-embedding-ptbr-v1'

# Arquivo de sinônimos (na raiz do projeto, ao lado do manage.py).
# Formato: termo_coloquial -> texto_para_o_modelo
ARQUIVO_SINONIMOS = os.path.join(settings.BASE_DIR, 'cnae_sinonimos.txt')
ARQUIVO_IGNORAR = os.path.join(settings.BASE_DIR, 'cnae_ignorar.txt')


@lru_cache(maxsize=1)
def _carregar_sinonimos() -> dict:
    """
    Le o arquivo cnae_sinonimos.txt e devolve um dict
    {termo_coloquial_lower: texto_para_o_modelo}.

    NUNCA levanta excecao: se o arquivo nao existir ou estiver
    mal formatado, devolve {} (o embedding funciona sem sinonimos).
    """
    sinonimos = {}
    try:
        with open(ARQUIVO_SINONIMOS, 'r', encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith('#'):
                    continue
                if '->' not in linha:
                    logger.warning('Linha invalida em cnae_sinonimos.txt: %r', linha)
                    continue
                termo, substituto = linha.split('->', 1)
                termo = termo.strip().lower()
                substituto = substituto.strip()
                if termo and substituto:
                    sinonimos[termo] = substituto
    except FileNotFoundError:
        logger.info('cnae_sinonimos.txt nao encontrado (embedding sem sinonimos).')
    except Exception:
        logger.exception('Erro ao carregar cnae_sinonimos.txt')
    return sinonimos


def _aplicar_sinonimos(pergunta: str) -> str:
    """
    Substitui termos coloquiais pelo texto do CNAE antes de gerar o vetor.

    Case-insensitive. Substitui o PRIMEIRO termo que casar (nao
    substitui todos, pra nao virar bagunca se dois termos casarem).
    """
    if not pergunta:
        return pergunta
    pergunta_lower = pergunta.lower()
    for termo, substituto in _carregar_sinonimos().items():
        if termo in pergunta_lower:
            return pergunta_lower.replace(termo, substituto, 1)
    return pergunta

@lru_cache(maxsize=1)
def _carregar_ignorar() -> set:
    """
    Le cnae_ignorar.txt e devolve um set de palavras (lower)
    que devem ser removidas da pergunta antes do embedding.

    NUNCA levanta excecao: se o arquivo nao existir, devolve set().
    """
    ignorar = set()
    try:
        with open(ARQUIVO_IGNORAR, 'r', encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip().lower()
                if linha and not linha.startswith('#'):
                    ignorar.add(linha)
    except FileNotFoundError:
        logger.info('cnae_ignorar.txt nao encontrado (sem remocao de cidade).')
    except Exception:
        logger.exception('Erro ao carregar cnae_ignorar.txt')
    return ignorar


def _remover_ignorar(pergunta: str) -> str:
    """
    Remove palavras de cnae_ignorar.txt da pergunta.

    Ex: "padarias de lages" -> "padarias"
    (se "de" e "lages" estiverem no cnae_ignorar.txt).
    """
    if not pergunta:
        return pergunta
    palavras = pergunta.split()
    palavras_filtradas = [
        p for p in palavras
        if p.lower() not in _carregar_ignorar()
    ]
    return ' '.join(palavras_filtradas)

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

    Antes de gerar o vetor, aplica:
      1. Sinonimos de cnae_sinonimos.txt ("padarias" -> "panificacoes padarias")
      2. Remocao de palavras de cnae_ignorar.txt ("de", "lages")

    Ex: "padarias de lages" -> "panificacoes padarias"
        (cidade/stopwords removidas, sinonimo aplicado)

    NUNCA levanta excecao. Se falhar, retorna None.
    """
    try:
        pergunta_para_modelo = _aplicar_sinonimos(pergunta)
        pergunta_para_modelo = _remover_ignorar(pergunta_para_modelo)
        if pergunta_para_modelo != pergunta:
            logger.debug('Pergunta ajustada: %r -> %r', pergunta, pergunta_para_modelo)
        if not pergunta_para_modelo.strip():
            # Se a remocao esvaziou a pergunta (so tinha cidade), usa a original
            logger.warning('Pergunta vazia apos remocao; usando original: %r', pergunta)
            pergunta_para_modelo = pergunta
        modelo = _get_modelo()
        emb = modelo.encode([pergunta_para_modelo], normalize_embeddings=True)[0]
        # Se o modelo retornar um numpy array, usa .tolist()
        # Se retornar uma lista (ex: em testes), usa direto
        valores = emb.tolist() if hasattr(emb, 'tolist') else emb
        return '[' + ','.join(f'{x:.6f}' for x in valores) + ']'
    except Exception as exc:
        logger.exception('Falha ao gerar embedding da pergunta')
        return None


def _posicoes_placeholder(sql: str) -> list[int]:
    """
    Devolve as posicoes dos placeholders %s REAIS do SQL.

    Percorre o SQL ignorando:
      - literais de string ('...', com '' como escape)
      - comentarios de linha (-- ...)
      - comentarios de bloco (/* ... */)

    Isso evita o bug em que "%s" dentro de um LIKE (ex:
    '%supermercado%') era confundido com o placeholder do vetor.
    """
    posicoes = []
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]

        # Literal de string: pula ate o ' de fechamento.
        if ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    # '' e' um ' escapado dentro da string
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        # Comentario de linha
        if ch == '-' and i + 1 < n and sql[i + 1] == '-':
            fim_linha = sql.find('\n', i)
            if fim_linha == -1:
                break
            i = fim_linha + 1
            continue

        # Comentario de bloco
        if ch == '/' and i + 1 < n and sql[i + 1] == '*':
            fim_bloco = sql.find('*/', i + 2)
            if fim_bloco == -1:
                break
            i = fim_bloco + 2
            continue

        # Placeholder de verdade (fora de string/comentario)
        if ch == '%' and i + 1 < n and sql[i + 1] == 's':
            posicoes.append(i)
            i += 2
            continue

        i += 1

    return posicoes


def tem_placeholder(sql: str) -> bool:
    """Retorna True se o SQL contem o placeholder %s fora de literais."""
    if not sql:
        return False
    return bool(_posicoes_placeholder(sql))


def injetar_vetor(sql: str, pergunta: str) -> tuple[str, str | None]:
    """
    Substitui os placeholders %s REAIS do SQL pelo vetor da pergunta.

    Placeholders dentro de literais (ex: ILIKE '%supermercado%', que
    contem a substring "%s") sao ignorados. O cast ::halfvec(384) e'
    adicionado apenas quando o SQL nao o traz logo apos o placeholder.

    Retorna (sql_final, erro). Se erro for None, deu certo.
    """
    posicoes = _posicoes_placeholder(sql)
    if not posicoes:
        return sql, None

    vetor = gerar_vetor(pergunta)
    if vetor is None:
        return sql, 'Falha ao gerar o vetor da pergunta (modelo indisponivel).'

    literal = f"'{vetor}'"
    sql_final = sql

    # Da direita para a esquerda, para as posicoes anteriores continuarem
    # validas depois de cada substituicao.
    for inicio in reversed(posicoes):
        fim = inicio + 2
        # A IA pode ja ter copiado o cast do schema_context.
        tem_cast = re.match(r'\s*::', sql[fim:]) is not None
        trecho = literal if tem_cast else literal + '::halfvec(384)'
        sql_final = sql_final[:inicio] + trecho + sql_final[fim:]

    return sql_final, None
