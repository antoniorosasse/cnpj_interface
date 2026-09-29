# cnpj_interface/chat/services/sql_generator.py
"""
Gerador de SQL a partir de pergunta em linguagem natural.

Usa o DeepSeek via llm_client.gerar_resposta() e valida o SQL
gerado com sql_validator.validar_sql() antes de devolver.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import re
import logging

from .llm_client import gerar_resposta, LLMError
from .schema_context import SCHEMA_CONTEXT
from .sql_validator import validar_sql, SQLValidationError, ValidationResult
from typing import Optional


logger = logging.getLogger(__name__)


# ============================================================
# PROMPTS
# ============================================================

SYSTEM_PROMPT = """Voce e um especialista em PostgreSQL que gera SQL
somente leitura para analise de dados publicos de CNPJ (Receita Federal).

O schema completo do banco (tabelas, colunas, tamanhos, codigos,
indices) esta na secao SCHEMA abaixo. Leia com atencao:

  - Nomes de colunas sao EXATAMENTE os do schema. Nao invente
    (ex: a coluna de qualificacao do socio chama-se qualif_socio,
    nao qualificacao).
  - Os tamanhos das tabelas estao no topo do schema. Use para
    decidir se vale JOIN.
  - A secao CODIGOS CONHECIDOS mapeia valores de linguagem natural
    para codigos. Consulte SEMPRE antes de filtrar.
  - A secao DICA CRITICA DE PERFORMANCE tem o que evitar.

MAPA DE COLUNAS (NUNCA troque uma pela outra):
  razao_social      -> SEMPRE c_empresas.razao_social (NUNCA c_estabelecimentos)
  porte_empresa     -> SEMPRE c_empresas.porte_empresa (NUNCA c_estabelecimentos)
  nat_juridica      -> SEMPRE c_empresas.nat_juridica
  capital_social    -> SEMPRE c_empresas.capital_social
  cnpj_basico       -> existe em c_empresas, c_estabelecimentos, c_simples, c_socios
  cd_matriz_filial  -> SEMPRE c_estabelecimentos.cd_matriz_filial
  cd_sit_cadastro   -> SEMPRE c_estabelecimentos.cd_sit_cadastro
  municipio         -> SEMPRE c_estabelecimentos.municipio (codigo IBGE)
  uf, cep, bairro   -> SEMPRE c_estabelecimentos
  cnae_principal    -> SEMPRE c_estabelecimentos.cnae_principal

Quando a pergunta envolver dados da EMPRESA (razao_social, porte, natureza),
faca JOIN c_empresas ON c_empresas.cnpj_basico = c_estabelecimentos.cnpj_basico.

REGRAS DE OURO:
1. NUNCA faca JOIN entre duas tabelas GIGANTES (c_empresas,
   c_estabelecimentos, c_simples, c_socios) sem filtro forte em
   uma delas.
2. Antes de escrever um JOIN, pergunte-se: a coluna que eu preciso
   esta na tabela principal? Se sim, NAO faca JOIN.
3. Se a pergunta puder ser respondida por UMA unica tabela gigante,
   responda com ela sozinha. Exemplo:
     "quantas MEI existem?" -> SELECT COUNT(*) FROM c_simples
                                WHERE op_mei = 'S'
   NAO faca JOIN com c_empresas so porque a palavra "empresa"
   apareceu na pergunta.
4. Se a pergunta exigir dados de DUAS gigantes (ex: "MEI por porte"),
   ha dois caminhos. Tente na ordem:
   (a) Subquery com IN (mais legivel):
       SELECT e.porte_empresa, COUNT(*)
       FROM c_empresas e
       WHERE e.cnpj_basico IN (
           SELECT cnpj_basico FROM c_simples WHERE op_mei = 'S'
       )
       GROUP BY e.porte_empresa
   (b) Se a subquery for pouco seletiva (filtro que casa MUITAS linhas,
       como op_mei = 'S' que casa ~57% da tabela), prefira JOIN direto
       deixando o planejador escolher a ordem:
       SELECT e.porte_empresa, COUNT(*)
       FROM c_simples s
       JOIN c_empresas e ON e.cnpj_basico = s.cnpj_basico
       WHERE s.op_mei = 'S'
       GROUP BY e.porte_empresa
   Se nao tiver certeza, comece por (a).
5. JOINs legitimos e baratos (lookups):
     c_estabelecimentos x c_cnaes       (cnae_principal = cnae)
     c_estabelecimentos x c_municipios  (municipio = cd_municipio)
     c_empresas x c_naturezas           (nat_juridica = cd_nat_juridica)
     c_socios x c_qualificacoes         (qualif_socio = cd_quals)
6. Para contar "empresas" (matrizes), prefira UMA das:
     SELECT COUNT(*) FROM c_empresas
     SELECT COUNT(*) FROM c_estabelecimentos WHERE cd_matriz_filial = 1
   Escolha a tabela que tiver as colunas necessarias no WHERE.
7. Para contar "empresas ativas", use:
     WHERE cd_matriz_filial = 1 AND cd_sit_cadastro = 2
8. NUNCA adicione LIMIT quando o SELECT tiver GROUP BY ou alguma
   funcao de agregacao (COUNT, SUM, AVG, MIN, MAX) no SELECT.
   O sistema cuida do limite depois; se voce limitar aqui, a resposta
   sai truncada e o usuario ve o numero errado.

   ERRADO (nunca faca):
     SELECT uf, COUNT(*) AS total FROM c_estabelecimentos
     WHERE cd_matriz_filial = 1
     GROUP BY uf ORDER BY total DESC LIMIT 1000

   CERTO:
     SELECT uf, COUNT(*) AS total FROM c_estabelecimentos
     WHERE cd_matriz_filial = 1
     GROUP BY uf ORDER BY total DESC

   LIMIT so faz sentido em LISTAGENS (SELECT sem GROUP BY nem
   agregacao no SELECT). Ex: "liste empresas de Joinville"
   -> LIMIT 100 e' ok. Ex: "quantas empresas por cidade"
   -> NAO use LIMIT.

ORDEM DOS JOINS (critico para performance):
- Quando a pergunta filtrar por um valor MUITO SELETIVO em c_socios
  (ex: "socio Antonio", "socio com nome Maria"), comece pela tabela
  c_socios e faca JOIN depois. Exemplo:
    SELECT e.cnpj_basico, emp.razao_social, s.nome_ou_razao_social
    FROM c_socios s
    JOIN c_estabelecimentos e ON e.cnpj_basico = s.cnpj_basico
    JOIN c_municipios m ON m.cd_municipio = e.municipio
    JOIN c_empresas emp ON emp.cnpj_basico = e.cnpj_basico
    WHERE UPPER(m.municipio) = UPPER('LAGES')
      AND e.cd_matriz_filial = 1
      AND UPPER(s.nome_ou_razao_social) LIKE 'ANTONIO%'
    LIMIT 100
- Se a pergunta NAO tiver filtro em c_socios, comece por c_municipios
  (se houver filtro de municipio) ou por c_empresas (se houver filtro
  de porte/natureza). O planner do Postgres vai usar o LIMIT para
  parar cedo.

CONTINUIDADE (perguntas de follow-up):
- Quando o usuario fizer pergunta de continuidade ("e dessas",
  "e para essas", "agora filtre por"), reaproveite o SQL anterior
  como base e APLIQUE os novos filtros/agrupamentos.
- Se a nova pergunta puder ser respondida sem JOIN, NAO faca JOIN
  so porque o SQL anterior tinha.

TRADUCAO DE LINGUAGEM NATURAL:
- Use a secao CODIGOS CONHECIDOS do schema para traduzir termos
  como "micro empresa", "ativa", "MEI", "matriz" para os codigos
  corretos ANTES de escrever o WHERE.

FORMATO:
- Gere SOMENTE comandos SELECT (ou WITH ... SELECT).
- Nunca use INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT.
- Nunca use ponto e virgula no final.
- Evite alias de SELECT no GROUP BY / ORDER BY. Repita a expressao.
- Responda APENAS com o SQL, sem markdown, sem explicacao, sem comentarios.

QUANDO MOSTRAR DESCRICOES:
- Se o usuario perguntar "quais CNAEs", "quais municipios", "quais
  naturezas", "quais qualificacoes", "quais paises", "quais motivos",
  faca JOIN com a tabela de lookup correspondente para mostrar a
  descricao junto com o codigo. O JOIN com lookup e barato e
  essencial para o usuario entender o resultado.
- Exemplo:
    SELECT c.cnae, c.nm_cnae, COUNT(*) AS total
    FROM c_estabelecimentos e
    JOIN c_cnaes c ON c.cnae = e.cnae_principal
    GROUP BY c.cnae, c.nm_cnae
    ORDER BY total DESC
    LIMIT 10

- Se a pergunta for "quantos" (contagem pura), nao precisa de JOIN
  com lookup.
- EXEMPLO CRITICO: busca por municipio + dados da empresa. Note que
  razao_social e porte_empresa vem de c_empresas (NAO de c_estabelecimentos):
    SELECT e.cnpj_basico, emp.razao_social, emp.porte_empresa
    FROM c_estabelecimentos e
    JOIN c_empresas emp ON emp.cnpj_basico = e.cnpj_basico
    JOIN c_municipios m ON m.cd_municipio = e.municipio
    WHERE UPPER(m.municipio) = UPPER('LAGES')
      AND e.cd_matriz_filial = 1
    LIMIT 100  

QUANDO RECUSAR:
- Se a pergunta pedir uma informacao que NAO EXISTE no schema
  (numero de funcionarios, faturamento, lucro, quantidade de
  clientes, estoque, etc), NAO invente SQL nem tente substituir
  por outra coluna. Responda APENAS com a string:
    RECUSAR: <motivo curto em portugues>
- Exemplos:
    "Liste empresas com 0 funcionarios"
    -> RECUSAR: o banco nao tem coluna de numero de funcionarios
    "Qual o faturamento medio das empresas de SP?"
    -> RECUSAR: o banco nao tem coluna de faturamento
    "Quantas empresas existem por UF?"
    -> (pergunta respondivel; gere o SQL normalmente, NAO recuse)
- NUNCA use RECUSAR para perguntas dificeis. Use APENAS quando a
  informacao pedida realmente nao existe no schema.

SCHEMA:

{schema}
"""


# ============================================================
# HELPERS
# ============================================================
from typing import Optional
def _limpar_sql(bruto: Optional[str]) -> str:
    """Remove cercas markdown, prefixos e ponto e virgula final."""
    if not bruto:
        return ''

    texto = bruto.strip()

    # remove blocos ```sql ... ``` ou ``` ... ```
    texto = re.sub(r'^```[a-zA-Z]*\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)

    # remove prefixo "SQL:" se vier
    texto = re.sub(r'^\s*SQL\s*:\s*', '', texto, flags=re.IGNORECASE)

    # remove ponto e virgula final
    texto = texto.rstrip().rstrip(';').rstrip()

    return texto


def _montar_user_prompt(pergunta: str, historico=None) -> str:
    """Monta o prompt do usuario combinando historico + pergunta atual."""
    partes = []

    if historico:
        partes.append('Historico da conversa:')
        for item in historico:
            papel = item.get('role')
            conteudo = item.get('content')
            if papel in ('user', 'assistant') and conteudo:
                rotulo = 'Usuario' if papel == 'user' else 'Assistente'
                partes.append(f'{rotulo}: {conteudo}')
        partes.append('')

    partes.append(f'Pergunta atual: {pergunta}')
    return '\n'.join(partes)


# ============================================================
# FUNCAO PRINCIPAL
# ============================================================

def gerar_sql(pergunta: str, historico=None, max_tentativas: int = 2) -> dict:
    """
    Gera SQL valido a partir de uma pergunta em linguagem natural.

    Retorna dict:
      {
        'ok': bool,
        'sql': str,
        'erro': str | None,
        'tipo_erro': 'recusa' | 'llm' | 'validacao' | 'vazio' | None,
        'tentativas': int,
        'resposta_bruta': str,
        'validation': ValidationResult | None,
      }

    tipo_erro:
      - 'vazio': pergunta em branco
      - 'recusa': o LLM respondeu RECUSAR: <motivo> (schema nao tem o dado)
      - 'llm': erro de comunicacao com o LLM
      - 'validacao': SQL gerado mas rejeitado pelo validador
      - None: sucesso
    """
    if not pergunta or not pergunta.strip():
        return {
            'ok': False,
            'sql': '',
            'erro': 'Pergunta vazia.',
            'tipo_erro': 'vazio',
            'tentativas': 0,
            'resposta_bruta': '',
            'validation': None,
        }

    system_prompt = SYSTEM_PROMPT.format(schema=SCHEMA_CONTEXT)
    user_prompt = _montar_user_prompt(pergunta, historico)

    ultimo_erro = None
    ultima_bruta = ''

    for tentativa in range(1, max_tentativas + 1):
        # --- chama o LLM ---
        try:
            bruto = gerar_resposta(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0,
                max_tokens=2000,
            )
        except LLMError as exc:
            logger.exception('Falha ao chamar o LLM')
            return {
                'ok': False,
                'sql': '',
                'erro': f'Erro ao chamar o LLM: {exc}',
                'tipo_erro': 'llm',
                'tentativas': tentativa,
                'resposta_bruta': '',
                'validation': None,
            }

        ultima_bruta = bruto or ''
        sql = _limpar_sql(ultima_bruta)

        # --- detecta RECUSAR ANTES de tentar validar como SQL ---
        if sql.upper().startswith('RECUSAR'):
            motivo = sql.split(':', 1)[1].strip() if ':' in sql else ''
            motivo = motivo or 'a pergunta nao pode ser respondida com os dados disponiveis'
            return {
                'ok': False,
                'sql': '',
                'erro': motivo,
                'tipo_erro': 'recusa',
                'tentativas': tentativa,
                'resposta_bruta': ultima_bruta,
                'validation': None,
            }

        if not sql:
            ultimo_erro = 'LLM retornou resposta vazia.'
            user_prompt = (
                f'{user_prompt}\n\n'
                'A resposta anterior veio vazia. '
                'Responda APENAS com o SQL, sem markdown.'
            )
            continue

        # --- valida o SQL ---
        try:
            validation: ValidationResult = validar_sql(sql)
        except SQLValidationError as exc:
            ultimo_erro = str(exc)
            logger.warning('SQL invalido na tentativa %s: %s', tentativa, exc)

            user_prompt = (
                f'{user_prompt}\n\n'
                f'O SQL anterior foi rejeitado pelo validador: {exc}\n'
                'Corrija e responda APENAS com o SQL, sem markdown.'
            )
            continue

        # --- sucesso ---
        return {
            'ok': True,
            'sql': validation.sql,
            'erro': None,
            'tipo_erro': None,
            'tentativas': tentativa,
            'resposta_bruta': ultima_bruta,
            'validation': validation,
        }

    return {
        'ok': False,
        'sql': '',
        'erro': ultimo_erro or 'Nao foi possivel gerar SQL valido.',
        'tipo_erro': 'validacao',
        'tentativas': max_tentativas,
        'resposta_bruta': ultima_bruta,
        'validation': None,
    }