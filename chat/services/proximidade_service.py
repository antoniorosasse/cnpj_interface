# cnpj_interface/chat/services/proximidade_service.py
"""
Ferramenta deterministica de busca por proximidade do chat.

Recebe o plano do `chat.services.planner` (origem, raio, atividades, filtros)
e executa a busca reusando o motor da pagina `/proximidade/`:

    resolver_origem          (core.proximidade)
    resolver_filtro_cnae     (core.cnae_categorias)
    buscar_proximos_filtrado (core.proximidade) - com CNAE/data/capital
    buscar_proximos          (core.proximidade) - sem filtro seletivo

A conexao e' aberta como o usuario de leitura do app (`PG_*` do
settings.DATABASES['postgres']), em sessao read-only e fechada no finally.
Nao usa a role `chat_ia` do executor SQL: ela nao tem GRANT nas tabelas geo.

Nada aqui chama o LLM: a resposta textual e' montada em Python (previsivel e
barata). O planner interpreta a pergunta; esta camada so executa.

Sem acentos no codigo; os textos de resposta tem acento.
"""
import logging
import time
from datetime import date
from urllib.parse import urlencode

import psycopg2
import psycopg2.extras
from django.conf import settings
from django.urls import reverse

from core.cnae_categorias import (
    CATEGORIAS,
    descrever_categorias,
    resolver_filtro_cnae,
)
from core.cnae_atividade import resolver_atividade
from core.proximidade import (
    buscar_proximos,
    buscar_proximos_endereco,
    buscar_proximos_filtrado,
    resolver_origem,
    tem_tabela_total,
)

logger = logging.getLogger(__name__)

RAIO_PADRAO_KM = 10
LIMITE_PADRAO = 248
LIMITE_MAX_CHAT = 1000

# Colunas mostradas na tabela do chat (mesmos nomes que o front-end ja usa).
COLUNAS = [
    'cnpj_completo',
    'razao_social',
    'nm_fantasia',
    'municipio_nome',
    'uf',
    'atividade_principal',
    'situacao_descricao',
    'km',
    'telefone_1',
    'email',
    'dt_ini',
]

ROTULOS = {
    'cnpj_completo': 'CNPJ',
    'razao_social': 'Razão social',
    'nm_fantasia': 'Nome fantasia',
    'municipio_nome': 'Município',
    'uf': 'UF',
    'atividade_principal': 'Atividade',
    'situacao_descricao': 'Situação',
    'km': 'km',
    'telefone_1': 'Telefone',
    'email': 'E-mail',
    'dt_ini': 'Data Abertura',
}


# ============================================================
# CONEXAO
# ============================================================

def _abrir_conexao():
    """Conexao read-only com o usuario do app (nao o chat_ia)."""
    cfg = settings.DATABASES['postgres']
    conn = psycopg2.connect(
        host=cfg.get('HOST') or 'localhost',
        port=cfg.get('PORT') or 5432,
        dbname=cfg.get('NAME') or 'bancocnpj',
        user=cfg.get('USER') or 'arosasse',
        password=cfg.get('PASSWORD') or '',
        connect_timeout=5,
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


# ============================================================
# HELPERS
# ============================================================

def _tem_endereco(cur):
    """True quando c_estab_geo e c_ceps_folga existem (km por endereco)."""
    try:
        return (tem_tabela_total(cur, 'c_estab_geo')
                and tem_tabela_total(cur, 'c_ceps_folga'))
    except Exception:
        return False


def data_ha_anos(anos):
    """
    Data de corte para "abertas ha mais de N anos" (dt_ini < data).

    Usa calendario (nao 365*N) para nao errar ano bissexto; 29/02 vira 28/02.
    """
    hoje = date.today()
    try:
        return hoje.replace(year=hoje.year - int(anos))
    except ValueError:
        return hoje.replace(year=hoje.year - int(anos), day=28)


def _float(valor, casas=2):
    """Decimal/str -> float arredondado (para o JSON e a tabela)."""
    if valor is None:
        return None
    try:
        return round(float(valor), casas)
    except (TypeError, ValueError):
        return None


def _data(valor):
    """date -> 'dd/mm/aaaa' (padrao brasileiro, como o resto da UI)."""
    if valor is None:
        return None
    try:
        return valor.strftime('%d/%m/%Y')
    except (AttributeError, ValueError):
        return str(valor)


def _formatar_linha(linha):
    """Normaliza uma linha dos dois motores para as colunas do chat."""
    linha = linha or {}
    return {
        'cnpj_completo': linha.get('cnpj_completo'),
        'razao_social': linha.get('razao_social'),
        'nm_fantasia': linha.get('nm_fantasia'),
        'municipio_nome': linha.get('municipio_nome'),
        'uf': linha.get('uf'),
        'atividade_principal': linha.get('atividade_principal'),
        'situacao_descricao': linha.get('situacao_descricao'),
        'km': _float(linha.get('km')),
        'telefone_1': linha.get('telefone_1'),
        'email': linha.get('email'),
        'dt_ini': _data(linha.get('dt_ini')),
    }


def _fmt_numero(valor):
    """Inteiro com separador de milhar pt-BR."""
    try:
        return f'{int(valor):,}'.replace(',', '.')
    except (TypeError, ValueError):
        return '0'


def _resolver_atividades(atividades, cur=None, embedder=None):
    """
    Expressoes de atividade -> filtro CNAE ({faixas, cnaes, categorias, rotulo}).

    Mesma ordem dos dois caminhos: KB -> codigo CNAE -> ILIKE -> vetor
    (core.cnae_atividade). O fallback so' roda quando `cur` e' passado; a
    proximidade resolve a KB antes de abrir o banco e completa depois.
    """
    termos = [str(t).strip() for t in (atividades or []) if str(t).strip()]
    texto = ' e '.join(termos)
    if not texto:
        return {
            'faixas': [], 'cnaes': [], 'categorias': [], 'rotulo': '',
            'reconhecidas': [], 'nao_reconhecidas': [], 'fonte': 'nenhuma',
        }
    filtro = resolver_atividade(texto, cur=cur, embedder=embedder)
    if filtro['categorias'] or filtro['faixas'] or filtro['cnaes']:
        return {
            'faixas': sorted({tuple(f) for f in filtro['faixas']}),
            'cnaes': sorted({int(c) for c in filtro['cnaes']}),
            'categorias': list(filtro['categorias']),
            'rotulo': filtro['rotulo'],
            'reconhecidas': termos,
            'nao_reconhecidas': [],
            'fonte': filtro.get('fonte') or 'kb',
        }
    return {
        'faixas': [],
        'cnaes': [],
        'categorias': [],
        'rotulo': '',
        'reconhecidas': [],
        'nao_reconhecidas': filtro.get('nao_reconhecidas') or termos,
        'fonte': 'nenhuma',
    }


def _texto_filtros(uf=None, idade=None, capital=None, situacao=None):
    partes = []
    if uf:
        partes.append(f'UF {uf}')
    if situacao == 2:
        partes.append('somente ativas')
    if idade:
        partes.append(f'abertas há mais de {idade} ano(s)')
    if capital:
        partes.append(f'capital social acima de R$ {_fmt_numero(capital)}')
    return ', '.join(partes)


def _resumo(total, exibidas, tem_mais, rotulo, origem_rotulo, raio_km,
            uf=None, idade=None, capital=None, situacao=None,
            total_indisponivel=False, usar_endereco=False):
    """Texto deterministico da resposta (1 a 3 frases)."""
    atividade = rotulo or 'estabelecimentos'
    if total <= 0:
        return (
            f'Nenhum resultado de {atividade} num raio de {raio_km} km de '
            f'{origem_rotulo} com esses filtros.'
        )
    # Sem sufixo de genero ("ativos"/"ativas"): a atividade pode ser feminina
    # ("industrias") ou masculina ("supermercados"); o filtro de situacao ja
    # aparece no trecho "Filtros: ...".
    texto = (
        f'Encontrei {_fmt_numero(total)} {atividade} num raio de '
        f'{raio_km} km de {origem_rotulo}.'
    )
    extras = _texto_filtros(uf, idade, capital, situacao)
    if extras:
        texto += f' Filtros: {extras}.'
    if total_indisponivel:
        texto += ' O total exato não está disponível nesta base.'
    elif tem_mais:
        texto += f' Mostrando os {_fmt_numero(exibidas)} mais próximos.'
    else:
        texto += ' A lista está completa.'
    if usar_endereco:
        texto += (
            ' Distância em linha reta calculada pela coordenada do endereço '
            '(CNEFE) quando disponível; senão, pelo centroide do CEP '
            '(não é distância de rota).'
        )
    else:
        texto += (
            ' Distância em linha reta calculada pela coordenada do CEP '
            '(não é distância de rota).'
        )
    return texto


def _link_proximidade(origem_texto, origem, raio_km, situacao,
                      atividade=None, uf=None, idade=None, capital=None,
                      pergunta=None, resposta=None):
    """
    URL da pagina /proximidade/ com a MESMA consulta do chat.

    Leva origem/raio/situacao e tambem os filtros (atividade/UF/idade/capital)
    para a pagina abrir o recorte filtrado, e a pergunta/resposta do chat para
    a pagina mostrar o contexto (painel "consulta do chat"). Tudo passa por
    urlencode; textos sao truncados para nao estourar a URL.
    """
    if origem_texto:
        origem_param = origem_texto
    else:
        origem_param = f'{origem["latitude"]:.6f},{origem["longitude"]:.6f}'
    params = {'origem': origem_param, 'raio': str(raio_km)}
    if situacao == 2:
        params['situacao'] = 'ATIVA'
    if atividade:
        params['atividade'] = atividade
    if uf:
        params['uf'] = uf
    if idade:
        params['idade'] = str(idade)
    if capital:
        params['capital'] = str(int(capital))
    if pergunta:
        params['pergunta'] = str(pergunta)[:500]
    if resposta:
        params['resposta'] = str(resposta)[:600]
    return reverse('core:proximidade') + '?' + urlencode(params)


def _esclarecimento(mensagem):
    return {'ok': False, 'etapa': 'esclarecimento', 'erro': mensagem}


def _mensagem_atividade_nao_reconhecida(termos):
    exemplo = ', '.join(sorted(CATEGORIAS))
    return (
        f'Não reconheci a atividade: {", ".join(termos)}. '
        'Hoje a busca por proximidade entende: indústrias, metalúrgicas, '
        'clínicas médicas e odontológicas, transportadoras, supermercados, '
        'padarias e indústrias extrativas. '
        f'(categorias: {exemplo})'
    )


# ============================================================
# FUNCAO PRINCIPAL
# ============================================================

def consultar_proximidade(plano, contexto_geo=None, conectar=None,
                          limite_max=LIMITE_MAX_CHAT, pergunta=None):
    """
    Executa um plano de proximidade.

    plano: dict normalizado pelo planner.
    contexto_geo: {'latitude','longitude','rotulo'} da ultima consulta geo
                  da conversa (para "deste endereço").
    conectar: injecao para testes (devolve conexao psycopg2-like).
    limite_max: teto de linhas (a tela usa 1.000; o export do chat usa 5.000).
    pergunta: texto original (vai no link da pagina, para mostrar o contexto).

    Devolve dict:
      ok=True:  rows, colunas, total, exibidas, tem_mais, resumo, link,
                origem, raio_km, filtros, tempo_ms, plano
      ok=False: etapa ('esclarecimento'|'origem'|'atividade'|'execucao'), erro
    """
    if not isinstance(plano, dict):
        return _esclarecimento('Plano de consulta inválido.')

    origem_texto = (plano.get('origem') or '').strip() or None
    usar_contexto = bool(plano.get('usar_contexto'))
    raio_km = int(plano.get('raio_km') or RAIO_PADRAO_KM)
    raio_km = max(1, min(100, raio_km))
    limite = int(plano.get('limite') or LIMITE_PADRAO)
    limite = max(1, min(int(limite_max or LIMITE_MAX_CHAT), limite))
    situacao = 2 if plano.get('situacao') == 'ativa' else None
    uf = plano.get('uf') or None
    idade = plano.get('idade_min_anos')
    capital = plano.get('capital_min')
    dt_ini_antes_de = data_ha_anos(idade) if idade else None

    atividades = plano.get('atividades') or []
    # Primeira passada sem banco: KB/codigo CNAE. Se nao resolver, o fallback
    # ILIKE/vetor roda depois de abrir o cursor (mesma regra do SQL).
    filtro_cnae = _resolver_atividades(atividades)

    # --- origem sem precisar de banco
    origem = None
    if usar_contexto and not origem_texto:
        if not contexto_geo:
            return _esclarecimento(
                'Você disse "deste endereço", mas eu ainda não tenho uma '
                'origem nesta conversa. Informe um CNPJ, CEP, cidade/UF ou '
                'endereço (ex.: "Avenida Paulista, 1000, São Paulo/SP").'
            )
        origem = {
            'latitude': float(contexto_geo['latitude']),
            'longitude': float(contexto_geo['longitude']),
            'rotulo': contexto_geo.get('rotulo') or 'origem anterior',
        }
        origem_texto = contexto_geo.get('texto') or origem['rotulo']
    elif not origem_texto:
        return _esclarecimento(
            'Para buscar por proximidade eu preciso da origem: CNPJ completo, '
            'CEP, cidade/UF, endereço ou coordenada "latitude,longitude". '
            'Ex.: "Joinville/SC" ou "Avenida Paulista, 1000, São Paulo/SP".'
        )

    # --- consulta no banco
    conectar = conectar or _abrir_conexao
    try:
        conn = conectar()
    except Exception:
        logger.exception('Falha ao abrir conexao de proximidade')
        return {
            'ok': False,
            'etapa': 'execucao',
            'erro': 'Não foi possível consultar a base agora. Tente novamente.',
        }
    inicio = time.monotonic()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if atividades and not (
                filtro_cnae['categorias'] or filtro_cnae['faixas']
                or filtro_cnae['cnaes']):
            filtro_cnae = _resolver_atividades(atividades, cur=cur)
            if not (filtro_cnae['categorias'] or filtro_cnae['faixas']
                    or filtro_cnae['cnaes']):
                return {
                    'ok': False,
                    'etapa': 'atividade',
                    'erro': _mensagem_atividade_nao_reconhecida(
                        filtro_cnae['nao_reconhecidas'] or atividades),
                }

        if origem is None:
            origem, erro_origem = resolver_origem(cur, origem_texto)
            if not origem:
                erro = erro_origem or 'Não encontrei essa origem.'
                if 'não encontrad' in erro.lower():
                    erro = (
                        f'Não encontrei a origem "{origem_texto}". '
                        'Se for endereço, inclua cidade/UF '
                        '(ex.: "Avenida Paulista, 1000, São Paulo/SP") '
                        'ou use um CEP.'
                    )
                elif 'ambígu' in erro.lower() or 'ambigu' in erro.lower():
                    erro += ' Informe a UF (ex.: Sobral/CE).'
                else:
                    erro += (' Inclua cidade/UF ou use um CEP '
                             '(ex.: "Avenida Paulista, 1000, São Paulo/SP").')
                return _esclarecimento(erro)

        usar_endereco = _tem_endereco(cur)
        usa_filtro = bool(
            filtro_cnae['faixas'] or filtro_cnae['cnaes']
            or capital or dt_ini_antes_de
        )
        if usa_filtro:
            linhas, tem_mais, total = buscar_proximos_filtrado(
                cur,
                origem['latitude'],
                origem['longitude'],
                raio_km,
                limite=limite,
                situacao=situacao,
                matrizes=False,
                faixas_cnae=filtro_cnae['faixas'],
                cnaes=filtro_cnae['cnaes'],
                uf=uf,
                dt_ini_antes_de=dt_ini_antes_de,
                capital_min=capital,
                usar_endereco=usar_endereco,
            )
        else:
            # Sem filtro seletivo: motor da pagina (total vem da tabela
            # agregada por CEP; so nao existe com filtros de CNAE/data).
            busca = (buscar_proximos_endereco if usar_endereco
                     else buscar_proximos)
            linhas, tem_mais, total = busca(
                cur,
                origem['latitude'],
                origem['longitude'],
                raio_km,
                somente_matrizes=False,
                limite=limite,
                situacao=situacao,
            )
    except ValueError as exc:
        return {'ok': False, 'etapa': 'execucao', 'erro': str(exc)}
    except psycopg2.errors.QueryCanceled:
        return {
            'ok': False,
            'etapa': 'execucao',
            'erro': ('A consulta ficou pesada demais e foi cancelada. '
                     'Tente um raio menor ou informe a UF.'),
        }
    except psycopg2.Error:
        logger.exception('Erro do Postgres na consulta de proximidade')
        return {
            'ok': False,
            'etapa': 'execucao',
            'erro': 'Não foi possível consultar a base agora. Tente novamente.',
        }
    except Exception:
        logger.exception('Erro inesperado na consulta de proximidade')
        return {
            'ok': False,
            'etapa': 'execucao',
            'erro': 'Não foi possível concluir a consulta de proximidade.',
        }
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    tempo_ms = int((time.monotonic() - inicio) * 1000)
    total_indisponivel = total is None
    total_num = int(total or 0)
    rows = [_formatar_linha(linha) for linha in (linhas or [])]
    filtros_resumo = _texto_filtros(uf, idade, capital, situacao)
    resumo = _resumo(
        total_num, len(rows), bool(tem_mais), filtro_cnae['rotulo'],
        origem.get('rotulo') or origem_texto, raio_km,
        uf=uf, idade=idade, capital=capital, situacao=situacao,
        total_indisponivel=total_indisponivel, usar_endereco=usar_endereco,
    )
    link = _link_proximidade(
        origem_texto, origem, raio_km, situacao,
        atividade=filtro_cnae['rotulo'] or None,
        uf=uf, idade=idade, capital=capital,
        pergunta=pergunta, resposta=resumo,
    )

    # Plano publico guardado em Mensagem.plano: alem do que a tela mostra
    # (origem/raio/link), guarda os campos necessarios para REEXECUTAR a
    # consulta depois (export do chat e follow-up "deste endereco").
    plano_publico = {
        'origem': {
            'texto': origem_texto,
            'rotulo': origem.get('rotulo'),
            'latitude': float(origem['latitude']),
            'longitude': float(origem['longitude']),
        },
        'raio_km': raio_km,
        'atividades': atividades,
        'situacao': plano.get('situacao') or 'todas',
        'uf': plano.get('uf'),
        'idade_min_anos': idade,
        'capital_min': capital,
        'limite': limite,
        'usar_endereco': usar_endereco,
        'filtros_resumo': filtros_resumo,
        'categorias': filtro_cnae['categorias'],
        'link_proximidade': link,
    }

    return {
        'ok': True,
        'rows': rows,
        'colunas': list(COLUNAS),
        'total': total_num,
        'exibidas': len(rows),
        'tem_mais': bool(tem_mais),
        'resumo': resumo,
        'link': link,
        'origem': plano_publico['origem'],
        'raio_km': raio_km,
        'filtros': {
            'uf': uf,
            'idade_min_anos': idade,
            'capital_min': capital,
            'situacao': situacao,
            'categorias': filtro_cnae['categorias'],
        },
        'filtros_resumo': filtros_resumo,
        'tempo_ms': tempo_ms,
        'plano': plano_publico,
    }
