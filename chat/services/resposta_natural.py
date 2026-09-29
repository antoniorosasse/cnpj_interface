# cnpj_interface/chat/services/resposta_natural.py
"""
Gera uma resposta em linguagem natural a partir dos dados
retornados pelo sql_executor.

Fluxo:
    pergunta + colunas + rows  ->  gerar_texto_resposta()  ->  str

Usa o mesmo DeepSeek via llm_client.gerar_resposta.

Regras:
- Recebe apenas as primeiras N linhas (LIMITE_LINHAS), pra nao
  estourar o contexto do LLM.
- O prompt informa o total real (len(rows)) ao LLM, pra ele nao
  dizer "so recebi N" e sim "sao X no total, top 5 sao ...".
- Prompt rigido: "use APENAS os dados fornecidos".
- Temperatura baixa (0.2), pra reduzir alucinacao.
- Se qualquer coisa der errado (LLMError, timeout, etc), devolve
  uma string de fallback simples, NUNCA levanta excecao. O chat
  nao pode quebrar porque o texto amigavel falhou.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import json
import logging

from .llm_client import gerar_resposta, LLMError


logger = logging.getLogger(__name__)


# Quantas linhas enviar pro LLM (as primeiras). O resto fica na tabela.
LIMITE_LINHAS = 20

# Quantos itens o LLM deve destacar no texto natural (top N).
TOP_N = 5

# Tamanho maximo da resposta em tokens.
MAX_TOKENS_RESPOSTA = 300


SYSTEM_PROMPT = (
    "Voce e um analista de dados que responde em portugues do Brasil. "
    "Voce recebe uma pergunta, o total de linhas que a consulta retornou "
    "e uma amostra dos primeiros itens em JSON. "

    "REGRAS OBRIGATORIAS:\n"
    "1. Responda APENAS com base nos dados fornecidos. "
    "NUNCA invente numeros que nao aparecem nos dados.\n"
    "2. Se a consulta retornou MAIS linhas do que voce esta vendo, "
    f"destaque no maximo os {TOP_N} primeiros itens no texto e diga "
    "explicitamente o TOTAL REAL (o numero informado no campo "
    "'Total de linhas'). NUNCA diga 'apenas 20 foram fornecidos' ou "
    "'apenas uma amostra foi fornecida'. Diga, por exemplo: "
    "'Sao 296 municipios no total; os 5 maiores sao...'\n"
    "3. Se a pergunta pediu uma coluna ou metrica que NAO esta nos dados "
    "(ex: numero de funcionarios, faturamento, lucro), diga claramente "
    "que essa informacao nao esta disponivel na base, sem tentar "
    "substituir por outra coisa.\n"
    "4. Seja claro, objetivo e profissional. Use 1 a 3 frases.\n"
    "5. Nao use markdown, nao use blocos de codigo, nao repita a pergunta.\n"
    "6. Se o sistema te avisar EXPLICITAMENTE que a consulta foi TRUNCADA, "
    "mencione isso no final da resposta, de forma natural, por exemplo: "
    "'esta e uma lista parcial; use filtros para refinar'. "
    "NAO invente o numero total se ele nao foi informado.\n"
    "7. Se o sistema NAO te avisar que a consulta foi TRUNCADA, "
    "NAO mencione 'lista parcial', 'amostra', 'truncado' nem 'apenas X de Y'. "
    "Apenas diga o total real e destaque os primeiros itens. "
    "Nunca use as palavras 'parcial', 'amostra' ou 'truncado' por conta propria."
)


def _montar_user_prompt(
    pergunta: str,
    colunas: list,
    rows: list,
    truncado: bool = False,
    total_linhas_exibidas: int | None = None,
) -> str:
    """Monta o prompt do usuario com pergunta + dados limitados."""
    linhas = rows[:LIMITE_LINHAS]
    total = len(rows)

    dados_json = json.dumps(linhas, ensure_ascii=False, indent=2, default=str)

    # Sempre diz o total real. NAO menciona quantas linhas o LLM ve,
    # exceto quando for truncado (aí o aviso_truncado cuida disso).
    # Motivo: dizer "voce esta vendo as primeiras 20" faz o LLM
    # alucinar que a lista esta truncada e inventar "lista parcial".
    aviso_total = f"\n\nTotal de linhas: {total}."

    aviso_truncado = ''
    if truncado:
        aviso_truncado = (
            "\n\nATENCAO: a consulta foi TRUNCADA pelo sistema. "
            f"Exibindo apenas {total_linhas_exibidas or total} linhas "
            "no total. Avise o usuario no final da resposta de que "
            "esta e uma lista parcial (sem inventar numeros)."
        )

    return (
        f"Pergunta do usuario:\n{pergunta}\n\n"
        f"Colunas retornadas: {', '.join(colunas)}\n\n"
        f"Dados (JSON):\n{dados_json}"
        f"{aviso_total}"
        f"{aviso_truncado}\n\n"
        f"Responda agora, em portugues, com 1 a 3 frases. "
        f"Use APENAS os numeros que aparecem nos dados acima. "
        f"Destaque no maximo os {TOP_N} primeiros itens e SEMPRE cite "
        f"o total real quando a consulta tiver mais linhas do que voce ve."
    )


def gerar_texto_resposta(
    pergunta: str,
    colunas: list,
    rows: list | None,
    truncado: bool = False,
    total_linhas_exibidas: int | None = None,
) -> str:
    """
    Gera 1-3 frases em PT-BR resumindo os dados.

    - Se rows estiver vazio, devolve string vazia (o chamador decide o que fazer).
    - Se o LLM falhar, devolve uma string de fallback simples.
    - NUNCA levanta excecao.
    - truncado: True se o sql_executor cortou a query em CHAT_SQL_MAX_ROWS.
    - total_linhas_exibidas: quantas linhas foram efetivamente exibidas.
    """
    if not rows:
        return ''

    if not colunas:
        return ''

    try:
        texto = gerar_resposta(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=_montar_user_prompt(
                pergunta, colunas, rows,
                truncado=truncado,
                total_linhas_exibidas=total_linhas_exibidas,
            ),
            temperature=0.2,
            max_tokens=MAX_TOKENS_RESPOSTA,
        )
    except LLMError as exc:
        logger.warning('Falha ao gerar texto natural: %s', exc)
        return _fallback_texto(colunas, rows)
    except Exception as exc:
        logger.exception('Erro inesperado ao gerar texto natural')
        return _fallback_texto(colunas, rows)

    texto = (texto or '').strip()
    if not texto:
        return _fallback_texto(colunas, rows)

    return texto


def _fallback_texto(colunas: list, rows: list) -> str:
    """Texto simples quando o LLM falha. Nao inventa nada."""
    total = len(rows)
    plural = 'linha' if total == 1 else 'linhas'
    return f"A consulta retornou {total} {plural}. Veja os detalhes na tabela abaixo."