# cnpj_interface/core/cnae_categorias.py
"""
KB fixa de categorias de negocio -> CNAE (decisao de 25/11/2026).

Por que existe: as perguntas reais ("industrias metalurgicas", "clinicas
medicas e odontologicas", "transportadoras", "supermercados e padarias") nao
sao termos do `nm_cnae`. O ILIKE acha coisa errada (`clinica` casa
"Clinicas de estetica") e o vetor, sozinho, devolve no maximo 2 vizinhos -
insuficiente para representar uma divisao inteira. Esta KB fixa o mapeamento
(com sinonimos e plural) e e a fonte deterministica que o chat usa; o vetor
continua apenas como fallback para termos fora da KB.

Decisoes registradas no README (secao "Chat + proximidade"):
  - industria = secao C (divisoes 10 a 33); a extrativa (secao B, 05 a 09)
    fica em categoria separada e so entra quando pedida.
  - metalurgica = divisoes 24 + 25 (uso de mercado).
  - clinicas = 8630-5/01..05 + 8640-2/02 (laboratorios clinicos inclusos).
  - transportadoras = 4930-2/01..04 (transporte rodoviario de carga).
  - supermercados e padarias = 4711302 + 1091102 + 4721101 + 4721102.

Sem acentos no codigo; os rotulos (exibidos ao usuario) tem acento.
"""
import re
import unicodedata

from core.cnae_taxonomia import faixas_divisoes

# Palavras que nao mudam a categoria (ligacoes e genericos).
_STOPWORDS = {
    'a', 'as', 'o', 'os', 'de', 'da', 'das', 'do', 'dos', 'e', 'em', 'no',
    'na', 'nos', 'nas', 'para', 'com', 'por', 'tipo', 'tipos', 'ramo',
    'setor', 'segmento', 'empresa', 'empresas',
}

# Categorias: slug -> dados.
#   rotulo: texto para a resposta natural (com acento)
#   divisoes: divisoes CNAE cobertas (vira intervalo SQL)
#   cnaes: codigos explicitos (subclasses) quando a divisao inteira nao serve
#   generica: categoria ampla; quando aparece junto de uma especifica a
#             especifica vence ("industrias metalurgicas" -> metalurgica)
#   aliases: termos normalizados (sem acento, minusculo) que disparam a categoria
CATEGORIAS = {
    'industria': {
        'rotulo': 'indústrias de transformação',
        'divisoes': tuple(range(10, 34)),
        'cnaes': (),
        'generica': True,
        'aliases': (
            'industria', 'industrias', 'industrial', 'industriais',
            'fabrica', 'fabricas', 'manufatura', 'manufaturas',
            'industria de transformacao', 'industrias de transformacao',
        ),
    },
    'industria_extrativa': {
        'rotulo': 'indústrias extrativas',
        'divisoes': (5, 6, 7, 8, 9),
        'cnaes': (),
        'generica': False,
        'aliases': (
            'industria extrativa', 'industrias extrativas', 'extrativa',
            'extrativismo', 'mineracao', 'mineradora', 'mineradoras',
        ),
    },
    'metalurgica': {
        'rotulo': 'indústrias metalúrgicas',
        'divisoes': (24, 25),
        'cnaes': (),
        'generica': False,
        'aliases': (
            'metalurgica', 'metalurgicas', 'metalurgico', 'metalurgicos',
            'metalurgia', 'siderurgica', 'siderurgicas', 'siderurgia',
        ),
    },
    'clinicas_medicas_odontologicas': {
        'rotulo': 'clínicas médicas e odontológicas',
        'divisoes': (),
        'cnaes': (8630501, 8630502, 8630503, 8630504, 8630505, 8640202),
        'generica': False,
        'aliases': (
            'clinica medica', 'clinicas medicas', 'clinica odontologica',
            'clinicas odontologicas', 'clinicas medicas e odontologicas',
            'medicas e odontologicas', 'medica e odontologica',
            'consultorio medico', 'consultorios medicos',
            'odontologia', 'dentista', 'dentistas',
        ),
    },
    'academias': {
        'rotulo': 'academias',
        'divisoes': (),
        'cnaes': (9313100,),
        'generica': False,
        'aliases': (
            'academia', 'academias', 'crossfit', 'pilates', 'ginastica',
            'musculacao', 'condicionamento fisico',
        ),
    },
    'pizzarias': {
        'rotulo': 'pizzarias',
        'divisoes': (),
        'cnaes': (5611201, 5620104),
        'generica': False,
        'aliases': ('pizzaria', 'pizzarias', 'pizza', 'pizzaiolo'),
    },
    'oficinas_mecanicas': {
        'rotulo': 'oficinas mecânicas',
        'divisoes': (),
        'cnaes': (4520001, 4520003, 4520004),
        'generica': False,
        'aliases': (
            'oficina mecanica', 'oficinas mecanicas', 'mecanica de veiculos',
            'auto eletrica', 'auto center', 'auto servico',
        ),
    },
    'postos': {
        'rotulo': 'postos de combustíveis',
        'divisoes': (),
        'cnaes': (4731800,),
        'generica': False,
        'aliases': (
            'posto', 'postos', 'posto de gasolina', 'postos de gasolina',
            'posto de combustivel', 'postos de combustiveis',
            'posto de combustiveis', 'gasolina', 'combustivel',
            'combustiveis',
        ),
    },
    'transportadoras': {
        'rotulo': 'transportadoras de carga',
        'divisoes': (),
        'cnaes': (4930201, 4930202, 4930203, 4930204),
        'generica': False,
        'aliases': (
            'transportadora', 'transportadoras', 'transportadora de carga',
            'transportadoras de carga', 'transporte de carga',
            'transportes de carga',
        ),
    },
    'supermercados': {
        'rotulo': 'supermercados',
        'divisoes': (),
        'cnaes': (4711302,),
        'generica': False,
        'aliases': ('supermercado', 'supermercados'),
    },
    'padarias': {
        'rotulo': 'padarias',
        'divisoes': (),
        'cnaes': (1091102, 4721101, 4721102),
        'generica': False,
        'aliases': (
            'padaria', 'padarias', 'panificadora', 'panificadoras',
            'panificacao',
        ),
    },
}

def _normalizar(texto):
    """Minusculo, sem acento, pontuacao virando espaco e espacos colapsados."""
    if not texto:
        return ''
    decomposto = unicodedata.normalize('NFKD', str(texto))
    limpo = ''.join(c for c in decomposto if not unicodedata.combining(c))
    limpo = limpo.lower()
    limpo = re.sub(r'[^a-z0-9]+', ' ', limpo)
    return re.sub(r'\s+', ' ', limpo).strip()


# Alias normalizado -> slug, ordenado do mais longo para o mais curto. O mais
# longo ganha quando dois casam no mesmo trecho ("industria extrativa" antes de
# "industria").
_ALIASES = tuple(sorted(
    (
        (_normalizar(alias), slug)
        for slug, dados in CATEGORIAS.items()
        for alias in dados['aliases']
    ),
    key=lambda item: (-len(item[0]), item[0]),
))


def _termo_presente(texto_normalizado, termo):
    """True quando o termo aparece como palavra/frase inteira no texto."""
    if not texto_normalizado or not termo:
        return False
    padrao = r'(^|\s)' + re.escape(termo) + r'(\s|$)'
    return re.search(padrao, texto_normalizado) is not None


def resolver_categorias(texto):
    """
    Lista de slugs de categoria encontrados no texto, na ordem em que aparecem.

    O mesmo trecho nao gera slug repetido. "supermercados e padarias" devolve
    as duas categorias; "industrias metalurgicas" devolve industria e
    metalurgica (quem escolhe o que vale e' `resolver_filtro_cnae`).
    """
    normalizado = _normalizar(texto)
    if not normalizado:
        return []
    achados = []
    for alias, slug in _ALIASES:
        if not _termo_presente(normalizado, alias):
            continue
        posicao = normalizado.find(alias)
        achados.append((posicao, -len(alias), slug))
    achados.sort()
    slugs = []
    for _posicao, _tamanho, slug in achados:
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def faixas_da_categoria(slug):
    """Intervalos (min, max) de CNAE inteiros da categoria (pode ser vazio)."""
    dados = CATEGORIAS.get(slug) or {}
    return faixas_divisoes(dados.get('divisoes') or [])


def cnaes_da_categoria(slug):
    """Codigos CNAE explicitos da categoria (pode ser vazio)."""
    dados = CATEGORIAS.get(slug) or {}
    return list(dados.get('cnaes') or [])


def rotulo_categoria(slug):
    """Rotulo humano da categoria (com acento) ou o proprio slug."""
    dados = CATEGORIAS.get(slug) or {}
    return dados.get('rotulo') or slug


def descrever_categorias(slugs):
    """'supermercados e padarias', 'indústrias de transformação, ...'."""
    rotulos = [rotulo_categoria(slug) for slug in (slugs or []) if slug in CATEGORIAS]
    if not rotulos:
        return ''
    if len(rotulos) == 1:
        return rotulos[0]
    return ', '.join(rotulos[:-1]) + ' e ' + rotulos[-1]


def resolver_filtro_cnae(texto):
    """
    Texto em PT-BR -> filtro CNAE pronto para o motor de proximidade.

    Devolve dict:
      {
        'categorias': [slug, ...],   # o que foi reconhecido
        'faixas': [(min, max), ...], # intervalo inteiro para BETWEEN
        'cnaes': [codigo, ...],      # subclasses explicitas para ANY
        'rotulo': 'texto para a resposta',
      }

    Quando a pergunta traz uma categoria ampla junto de uma especifica
    ("industrias metalurgicas"), a especifica vence: 24+25 ja esta dentro de
    10..33, mas o filtro fica menor e a resposta nao mistura industrias em
    geral.
    """
    slugs = resolver_categorias(texto)
    if not slugs:
        return {'categorias': [], 'faixas': [], 'cnaes': [], 'rotulo': ''}

    especificas = [slug for slug in slugs if not CATEGORIAS[slug].get('generica')]
    selecionadas = especificas or slugs

    faixas = []
    cnaes = set()
    for slug in selecionadas:
        faixas.extend(faixas_da_categoria(slug))
        cnaes.update(cnaes_da_categoria(slug))

    return {
        'categorias': selecionadas,
        'faixas': sorted(set(faixas)),
        'cnaes': sorted(cnaes),
        'rotulo': descrever_categorias(selecionadas),
    }
