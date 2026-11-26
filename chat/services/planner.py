# cnpj_interface/chat/services/planner.py
"""
Planejador de consultas do chat: decide entre SQL comum e busca por proximidade.

O pipeline SQL (sql_generator -> validator -> executor -> resposta_natural)
continua sendo o caminho para perguntas de atributo (CNAE, UF, capital, data).
Perguntas com raio/distancia/origem geografica NAO passam pelo gerador de SQL:
elas viram um plano JSON (origem, raio, atividades, filtros) que e' executado
pela ferramenta deterministica `chat.services.proximidade_service`, reusando
`core.proximidade.resolver_origem` + `buscar_proximos_filtrado`.

O roteamento e' heuristico (`parece_proximidade`) para nao pagar uma chamada
de LLM em toda pergunta; quando a heuristica acerta, o planner extrai o JSON.
Se o planner disser que a pergunta nao e' geografica, a view cai no pipeline
SQL normal.

Sem acentos no codigo; o prompt (texto para o LLM) pode ter acento.
"""
import json
import re

from .llm_client import gerar_resposta, LLMError


# Termos que disparam o planejador. "a partir de" e' generico, mas se o planner
# concluir que nao ha geo, a view volta ao SQL - entao o falso positivo custa
# uma chamada e nao uma resposta errada.
RE_PROXIMIDADE = re.compile(
    r'(?i)(\braio\b|\bproximidade\b|\bproxim[oa]s?\b|\bperto\b'
    r'|\bdist[aâ]ncia\b|\bkm\b|\bquil[oô]metr'
    r'|deste\s+endere[cç]o|desse\s+endere[cç]o|deste\s+local|desse\s+local'
    r'|desta\s+empresa|dessa\s+empresa|a\s+partir\s+de|coordenadas?\b)'
)

# Heuristica de "tem cara de geo": usada so para decidir se vale chamar o
# planner. Nao decide a intencao final - quem decide e' o JSON do LLM.
def parece_proximidade(pergunta):
    """True quando a pergunta provavelmente pede busca por raio/localizacao."""
    if not pergunta:
        return False
    return bool(RE_PROXIMIDADE.search(pergunta))


SYSTEM_PROMPT = """Voce e o planejador de consultas do BusqueCNPJ, um site de
consulta de empresas da Receita Federal. Sua unica funcao e' transformar a
pergunta do usuario em um JSON de plano. Responda APENAS com o JSON, sem
markdown, sem comentarios, sem explicacao.

O sistema tem duas ferramentas:

1. "sql": consulta comum de atributos (atividade/CNAE, cidade, UF, situacao,
   capital social, data de abertura, nome). Use quando a pergunta NAO pedir
   distancia/raio a partir de um ponto.
2. "proximidade": busca por raio (distancia em km) a partir de uma origem
   (CNPJ, CEP, cidade, endereco ou coordenada). Use sempre que a pergunta
   pedir "num raio de X km", "a X km de", "perto de", "proximo a",
   "a partir de <empresa/endereco>", "deste endereco", "deste local".

Formato do JSON (todas as chaves sao obrigatorias; use null quando nao houver):

{
  "intencao": "proximidade" | "sql",
  "origem": "texto da origem (CNPJ/CEP/cidade/endereco/coordenada) ou null",
  "usar_contexto": true | false,
  "raio_km": inteiro (1 a 100; default 10),
  "atividades": ["expressoes de atividade citadas"] ou [],
  "situacao": "ativa" | "todas",
  "uf": "sigla" ou null,
  "idade_min_anos": inteiro ou null,
  "capital_min": numero em reais ou null,
  "limite": inteiro ou null,
  "observacao": "texto curto ou null"
}

REGRAS:
- "intencao" = "proximidade" somente se houver raio/distancia/localizacao;
  caso contrario "sql".
- "origem": copie o texto que o usuario deu para o ponto de partida. NUNCA
  invente CNPJ, CEP ou endereco. Se o usuario disser "uma empresa" sem
  informar o CNPJ, use origem=null.
- "usar_contexto" = true quando o usuario disser "deste endereco", "deste
  local", "dessa empresa", "aqui" - isto e', reaproveitar a origem da
  pergunta anterior da conversa. Nesse caso origem=null.
- "raio_km": se o usuario nao disser, use 10. Nunca invente um raio absurdo.
- "atividades": copie as expressoes de atividade ("industrias metalurgicas",
  "clinicas medicas e odontologicas", "transportadoras", "supermercados e
  padarias"). Nao traduza para CNAE; outra camada faz isso.
- "situacao": "ativa" somente se o usuario disser ativa/ativas/em atividade;
  senao "todas".
- "uf": sigla de 2 letras se o usuario citar estado ("Parana" -> "PR",
  "Santa Catarina" -> "SC"). Cidade NAO vira UF.
- "idade_min_anos": "abertas ha mais de 3 anos" -> 3; "com mais de 2 anos de
  abertura" -> 2; senao null.
- "capital_min": "capital social acima de R$ 500 mil" -> 500000;
  "1 milhao" -> 1000000; senao null.
- "limite": "encontre 248 empresas" / "liste 300" -> 248/300; senao null.
- "observacao": use para registrar algo que muda a resposta (ex.: "usuario
  pediu apenas matrizes") ou null.

EXEMPLOS:
"Encontre 248 industrias ativas em um raio de 10 km a partir de uma empresa"
-> {"intencao":"proximidade","origem":null,"usar_contexto":false,"raio_km":10,
    "atividades":["industrias"],"situacao":"ativa","uf":null,
    "idade_min_anos":null,"capital_min":null,"limite":248,
    "observacao":"origem nao informada"}

"Clinicas medicas e odontologicas em um raio de 5 km da Av. Paulista"
-> {"intencao":"proximidade","origem":"Av. Paulista","usar_contexto":false,
    "raio_km":5,"atividades":["clinicas medicas e odontologicas"],
    "situacao":"todas","uf":null,"idade_min_anos":null,"capital_min":null,
    "limite":null,"observacao":"falta cidade/UF para desambiguar"}

"Supermercados e padarias a ate 5 km deste endereco com mais de 2 anos de abertura"
-> {"intencao":"proximidade","origem":null,"usar_contexto":true,"raio_km":5,
    "atividades":["supermercados e padarias"],"situacao":"todas","uf":null,
    "idade_min_anos":2,"capital_min":null,"limite":null,"observacao":null}

"Industrias metalurgicas em Santa Catarina abertas ha mais de 3 anos"
-> {"intencao":"sql","origem":null,"usar_contexto":false,"raio_km":10,
    "atividades":["industrias metalurgicas"],"situacao":"ativa","uf":"SC",
    "idade_min_anos":3,"capital_min":null,"limite":null,"observacao":null}
"""


def _extrair_json(texto):
    """Extrai o primeiro objeto JSON do texto (tolera cercas markdown)."""
    if not texto:
        return None
    texto = texto.strip()
    texto = re.sub(r'^```[a-zA-Z]*\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    inicio = texto.find('{')
    fim = texto.rfind('}')
    if inicio < 0 or fim <= inicio:
        return None
    try:
        return json.loads(texto[inicio:fim + 1])
    except (ValueError, TypeError):
        return None


def _inteiro(valor, minimo, maximo, padrao=None):
    try:
        numero = int(float(valor))
    except (TypeError, ValueError):
        return padrao
    return max(minimo, min(maximo, numero))


def _numero(valor, minimo=0):
    try:
        return max(minimo, float(valor))
    except (TypeError, ValueError):
        return None


def _texto(valor, limite=200):
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto[:limite] if texto else None


def normalizar_plano(bruto):
    """
    Valida/clampa o JSON do LLM. Devolve um plano sempre utilizavel.

    Nunca levanta excecao: campo invalido vira default e a view decide pelo
    campo 'intencao'.
    """
    if not isinstance(bruto, dict):
        bruto = {}

    intencao = str(bruto.get('intencao') or '').strip().lower()
    if intencao not in ('proximidade', 'sql'):
        intencao = 'sql'

    atividades = bruto.get('atividades')
    if isinstance(atividades, str):
        atividades = [atividades]
    if not isinstance(atividades, list):
        atividades = []
    atividades = [
        _texto(item, 120) for item in atividades
        if isinstance(item, str) and _texto(item, 120)
    ][:5]

    situacao = str(bruto.get('situacao') or '').strip().lower()
    if situacao not in ('ativa', 'todas'):
        situacao = 'todas'

    uf = _texto(bruto.get('uf'), 2)
    if uf:
        uf = uf.upper()
        if not re.match(r'^[A-Z]{2}$', uf):
            uf = None

    limite = bruto.get('limite')
    if limite is None or limite == '':
        limite = None
    else:
        limite = _inteiro(limite, 1, 1000, None)

    return {
        'intencao': intencao,
        'origem': _texto(bruto.get('origem')),
        'usar_contexto': bool(bruto.get('usar_contexto')),
        'raio_km': _inteiro(bruto.get('raio_km'), 1, 100, 10),
        'atividades': atividades,
        'situacao': situacao,
        'uf': uf,
        'idade_min_anos': _inteiro(bruto.get('idade_min_anos'), 0, 100, None),
        'capital_min': _numero(bruto.get('capital_min')),
        'limite': limite,
        'observacao': _texto(bruto.get('observacao'), 300),
    }


def _montar_user_prompt(pergunta, historico=None, contexto_geo=None):
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
    if contexto_geo:
        rotulo = contexto_geo.get('rotulo') or ''
        partes.append(
            'Ultima origem geografica usada na conversa: '
            f'{rotulo} (latitude {contexto_geo.get("latitude")}, '
            f'longitude {contexto_geo.get("longitude")}).'
        )
        partes.append('')
    partes.append(f'Pergunta atual: {pergunta}')
    return '\n'.join(partes)


def planejar(pergunta, historico=None, contexto_geo=None):
    """
    Chama o LLM e devolve o plano normalizado.

    Retorno:
      {
        'ok': bool,
        'intencao': 'proximidade' | 'sql',
        'plano': dict,          # sempre normalizado
        'erro': str | None,
        'resposta_bruta': str,
      }

    Em qualquer falha (LLM fora, JSON invalido) devolve ok=False e
    intencao='sql' - a view segue pelo pipeline SQL, que e' o comportamento
    antigo. Nunca levanta excecao.
    """
    if not pergunta or not pergunta.strip():
        return {'ok': False, 'intencao': 'sql', 'plano': {},
                'erro': 'Pergunta vazia.', 'resposta_bruta': ''}

    user_prompt = _montar_user_prompt(pergunta, historico, contexto_geo)

    try:
        bruto = gerar_resposta(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=600,
        )
    except LLMError as exc:
        return {'ok': False, 'intencao': 'sql', 'plano': {},
                'erro': f'Erro ao chamar o LLM: {exc}', 'resposta_bruta': ''}

    dados = _extrair_json(bruto)
    if dados is None:
        return {'ok': False, 'intencao': 'sql', 'plano': {},
                'erro': 'O planejador nao devolveu um JSON valido.',
                'resposta_bruta': bruto or ''}

    plano = normalizar_plano(dados)
    return {'ok': True, 'intencao': plano['intencao'], 'plano': plano,
            'erro': None, 'resposta_bruta': bruto or ''}
