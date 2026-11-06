# /home/neto/cnpj_interface/core/proximidade.py
"""
Busca por proximidade: estabelecimentos num raio de X km de um ponto.

Nao ha PostGIS no servidor (conferido em 2026-08: PG 18 local e PG 16 na VPS
sem a extensao, e cube/earthdistance tambem nao instalados). A busca entao e
SQL puro:

    1. caixa (bbox) calculada em Python -> varredura de c_ceps usando
       latitude/longitude (1,26 milhao de linhas, ~150-250 ms)
    2. join por cep com c_estabelecimentos (indice idx_estabelecimentos_cep)
    3. distancia haversine exata nas linhas da caixa
    4. keyset por (km, cnpj_basico, cnpj_ordem, cnpj_dv), sem OFFSET

A origem pode ser: coordenada "-26.9,-48.7", CEP (8 digitos), CNPJ (14
digitos, usa o endereco da matriz), cidade ("Joinville" ou "Joinville/SC") ou
endereco ("Rua do Principe, Joinville/SC" / "Rua do Principe, 89201-000").

Acentos: o banco nao tem a extensao unaccent (pg_extension so traz pg_trgm e
vector, conferido em 2026-11), entao a comparacao de nomes usa
translate()+upper() em SQL (ver `sql_sem_acento`) contra o texto normalizado em
Python por `sem_acento`. Assim "Picarras" acha "Balneario Picarras".

Total de paginas: o COUNT exato no raio custa 5-15 s (bitmap scan em 38,5 M de
estabelecimentos), entao a contagem vem da tabela agregada `c_ceps_estab`
(cep, n_matrizes, n_total), criada por
`ProjetoCargaDados: manage.py contar_estab_por_cep`. Sem essa tabela a busca
continua funcionando e a paginacao mostra "Pagina X" sem o total.

Filtro de situacao (`situacao=` na querystring, codigo cd_sit_cadastro): o total
sai de `c_ceps_estab_sit` (cep, cd_sit_cadastro, n_matrizes, n_total), criada por
`ProjetoCargaDados: manage.py contar_estab_por_cep_situacao`. A tabela sem
situacao (`c_ceps_estab`) nao serve para o filtro. Se `c_ceps_estab_sit` nao
existir, a tela cai para "Pagina X" (sem total) e continua funcionando.

Cursor do keyset: o km vai e volta na querystring com `repr(float)`
(round-trip exato; ver `km_para_texto`). Arredondar o km (f'{km:.6f}', que era o
codigo ate 2026-11) quebra a paginacao quando varias linhas empatam no mesmo km
- que e o caso normal, porque o km e do CEP e um CEP concentra milhares de
empresas: para cima o cursor pula o resto do grupo (perde linhas), para baixo
ele fica antes do grupo e a mesma pagina se repete para sempre.
"""
import math
import re
import time
import unicodedata

from core.maps import montar_maps_url

RAIO_PADRAO_KM = 10
RAIO_MIN_KM = 1
RAIO_MAX_KM = 100
LIMITE_PAGINA = 20

# Caixa usada para achar o logradouro a partir do centro do municipio: os CEPs
# nao tem indice geografico, entao primeiro recortamos a caixa (varredura de
# c_ceps, ~250 ms) e so depois aplicamos translate() nas linhas que sobraram.
RAIO_BUSCA_LOGRADOURO_KM = 60

# Tempo (s) que o resultado de to_regclass fica em memoria no processo.
TTL_TABELA_TOTAL = 300
_tabela_total_cache = None  # (valor, expira_em)

# Quantos cursores o historico de paginacao guarda (cada um vira ~22 chars na URL).
HIST_MAXIMO = 60

# 1 grau de latitude ~ 111,32 km em qualquer lugar; em longitude encolhe
# conforme a latitude (cos).
KM_POR_GRAU_LAT = 111.32

RE_COORDENADA = re.compile(r'^\s*(-?\d{1,3}(?:[.,]\d+)?)\s*,\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$')
RE_CEP = re.compile(r'^\d{5}-?\d{3}$')
RE_CEP_NO_TEXTO = re.compile(r'\b\d{5}-\d{3}\b|\b\d{8}\b')
RE_SO_NUMERO = re.compile(r'^\d{1,6}$')

# Separadores do endereco: virgula, ponto-e-virgula, hifen solto ou " - ".
RE_PARTES_ENDERECO = re.compile(r'[,;]|\s+-\s+|\s+-\s*|\s*-\s+')

# Prefixos que atrapalham o LIKE quando a base grafa o logradouro sem eles.
TIPOS_LOGRADOURO = (
    'RUA', 'R', 'AVENIDA', 'AV', 'ALAMEDA', 'AL', 'TRAVESSA', 'TV', 'TRAV',
    'RODOVIA', 'ROD', 'ESTRADA', 'EST', 'PRACA', 'PCA', 'LARGO', 'LGO',
    'VIELA', 'SERVIDAO', 'PASSAGEM', 'VIA', 'BECO',
)

# 24 acentuadas minusculas + 24 maiusculas -> mesmas 24 letras sem acento e em
# caixa alta. Fazer a conversao em uma unica chamada evita depender do locale do
# cluster (aqui o upper() do Postgres esta em locale C, que nao sobe acentuada).
_ACENTOS = ('áàâãäéèêëíìîïóòôõöúùûüçñ'
            'ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ')
_SEM_ACENTO = ('AAAAAEEEEIIIIOOOOOUUUUCN'
               'AAAAAEEEEIIIIOOOOOUUUUCN')


def sql_sem_acento(coluna):
    """Expressao SQL: maiuscula, sem acento (para LIKE/igualdade em nomes)."""
    return f"upper(translate({coluna}, '{_ACENTOS}', '{_SEM_ACENTO}'))"


def sem_acento(texto):
    """Maiuscula, sem acento e com espacos colapsados (lado Python)."""
    if not texto:
        return ''
    decomposto = unicodedata.normalize('NFKD', str(texto))
    limpo = ''.join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', limpo).strip().upper()


SQL_CIDADE_EXATA = f"""
    SELECT cd_municipio, cd_ibge, nome, uf, latitude, longitude, n_ceps
      FROM c_municipios_geo
     WHERE {sql_sem_acento('nome')} = %(nome)s
       AND (%(uf)s = '' OR uf = %(uf)s)
       AND latitude IS NOT NULL
     ORDER BY n_ceps DESC
     LIMIT 5
"""

SQL_CIDADE_PARCIAL = f"""
    SELECT cd_municipio, cd_ibge, nome, uf, latitude, longitude, n_ceps
      FROM c_municipios_geo
     WHERE {sql_sem_acento('nome')} LIKE %(nome)s
       AND (%(uf)s = '' OR uf = %(uf)s)
       AND latitude IS NOT NULL
     ORDER BY n_ceps DESC
     LIMIT 5
"""

# Logradouro dentro da caixa do municipio. O BETWEEN vem antes do translate de
# proposito: o planner descarta as linhas fora da caixa sem chamar a funcao.
# O municipio e filtrado por id_municipio (IBGE), nao por nome: 19.120 CEPs tem
# nome_municipio com grafia diferente da de c_municipios_geo.
SQL_LOGRADOURO = f"""
    SELECT count(*) AS n_ceps,
           avg(latitude) AS latitude,
           avg(longitude) AS longitude,
           min(nome_municipio) AS nome_municipio,
           min(uf) AS uf
      FROM c_ceps
     WHERE latitude IS NOT NULL
       AND longitude IS NOT NULL
       AND latitude BETWEEN %(lat_min)s AND %(lat_max)s
       AND longitude BETWEEN %(lon_min)s AND %(lon_max)s
       AND (%(uf)s = '' OR uf = %(uf)s)
       AND (%(ibge)s = 0 OR id_municipio = %(ibge)s)
       AND {sql_sem_acento('logradouro')} LIKE %(termo)s
"""

SQL_CEP = """
    SELECT cep, latitude, longitude, nome_municipio, uf
      FROM c_ceps
     WHERE cep = %(cep)s
       AND latitude IS NOT NULL
       AND longitude IS NOT NULL
"""

# CNPJ -> coordenada do CEP da matriz; se o CEP nao tiver coordenada, cai no
# centroide do municipio (c_municipios_geo).
SQL_CNPJ = """
    SELECT e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
           e.uf,
           c.latitude,
           c.longitude,
           COALESCE(c.nome_municipio, mg.nome) AS municipio_nome,
           mg.latitude  AS mun_latitude,
           mg.longitude AS mun_longitude
      FROM c_estabelecimentos e
      LEFT JOIN c_ceps c ON c.cep = e.cep
      LEFT JOIN c_municipios_geo mg ON mg.cd_municipio = e.municipio
     WHERE e.cnpj_basico = %(basico)s
       AND e.cnpj_ordem = %(ordem)s
       AND e.cnpj_dv = %(dv)s
     LIMIT 1
"""


def _float(valor):
    """Converte '26,9' ou '26.9' em float; None quando nao der."""
    if valor is None:
        return None
    try:
        return float(str(valor).replace(',', '.'))
    except (TypeError, ValueError):
        return None


def _origem_cidade(cur, texto):
    """Cidade ('Joinville' ou 'Joinville/SC') -> centroide do municipio."""
    if '/' in texto:
        nome, _, uf = texto.partition('/')
    else:
        nome, uf = texto, ''
    nome = sem_acento(nome)
    uf = uf.strip().upper()[:2]

    cur.execute(SQL_CIDADE_EXATA, {'nome': nome, 'uf': uf})
    linhas = cur.fetchall()
    if not linhas:
        cur.execute(SQL_CIDADE_PARCIAL, {'nome': f'%{nome}%', 'uf': uf})
        linhas = cur.fetchall()

    if not linhas:
        return None, f'Cidade "{texto}" não encontrada. Tente "Joinville/SC".'

    municipios = {(linha['nome'], linha['uf']) for linha in linhas}
    if len(municipios) > 1:
        opcoes = ', '.join(f'{nome_mun}/{uf_mun}' for nome_mun, uf_mun in sorted(municipios)[:4])
        return None, (f'"{texto}" é ambíguo: {opcoes}. '
                      'Informe a UF (ex.: Sobral/CE).')

    linha = linhas[0]
    latitude = _float(linha['latitude'])
    longitude = _float(linha['longitude'])
    if latitude is None or longitude is None:
        return None, f'O município {linha["nome"]}/{linha["uf"]} não tem coordenada na base.'

    return {
        'latitude': latitude,
        'longitude': longitude,
        'rotulo': f'{linha["nome"]}/{linha["uf"]} (centro do município)',
        'nome': sem_acento(linha['nome']),
        'uf': linha['uf'],
        'cd_ibge': linha.get('cd_ibge') or 0,
    }, None


def _limpar_logradouro(parte):
    """Tira o numero do imovel do fim da parte ('Rua X, 500' -> 'Rua X')."""
    limpo = re.sub(r'[\s,]+\d{1,6}\s*$', '', parte or '').strip()
    return re.sub(r'\s+', ' ', limpo)


def _remover_tipo_logradouro(termo):
    """'RUA SETE DE SETEMBRO' -> 'SETE DE SETEMBRO' (a base as vezes omite o tipo).

    So vale quando sobra mais de uma palavra: com uma so ("AVENIDA BRASIL" ->
    "BRASIL") o LIKE passaria a casar Rua Brasil, Assis Brasil, Brasília e
    Vital Brasil juntos e a media cairia numa coordenada que nao e de nenhuma
    delas.
    """
    palavras = termo.split()
    sobra = ''
    if len(palavras) > 2 and palavras[0] in TIPOS_LOGRADOURO:
        sobra = palavras[1:]
    elif len(palavras) > 2 and palavras[1] in TIPOS_LOGRADOURO:
        sobra = palavras[2:]
    return ' '.join(sobra) if len(sobra) > 1 else ''


def _achar_cidade_no_texto(cur, partes):
    """Indice da parte que e um municipio (prefere as que tem '/UF')."""
    ordem = [i for i, parte in enumerate(partes) if '/' in parte]
    ordem += [i for i in range(len(partes) - 1, -1, -1) if i not in ordem]
    for indice in ordem:
        cidade, _erro = _origem_cidade(cur, partes[indice])
        if cidade:
            return indice, cidade
    return None, None


def _origem_endereco(cur, texto):
    """
    'Rua do Principe, 500, Joinville/SC' ou 'Rua do Principe, 89201-000'.

    A base nao guarda numero de imovel: a coordenada devolvida e a media dos
    CEPs do logradouro (o "meio" da rua), nao o numero exato.
    """
    partes = [p.strip() for p in RE_PARTES_ENDERECO.split(texto) if p.strip()]
    if len(partes) < 2:
        return None, None

    # 1) CEP no meio do texto: resposta exata, sem procurar logradouro.
    achado = RE_CEP_NO_TEXTO.search(texto)
    if achado:
        cep = achado.group(0).replace('-', '')
        cur.execute(SQL_CEP, {'cep': cep})
        linha = cur.fetchone()
        latitude = _float(linha['latitude']) if linha else None
        if latitude is not None and _float(linha['longitude']) is not None:
            logradouro = _limpar_logradouro(partes[0])
            rotulo = f'{logradouro} - ' if logradouro else ''
            rotulo += f'CEP {achado.group(0)}'
            if linha['nome_municipio']:
                rotulo += f' ({linha["nome_municipio"]}'
                rotulo += f'/{linha["uf"]})' if linha['uf'] else ')'
            return {
                'latitude': latitude,
                'longitude': _float(linha['longitude']),
                'rotulo': rotulo,
            }, None

    # 2) Logradouro + cidade/UF.
    indice_cidade, cidade = _achar_cidade_no_texto(cur, partes)
    if not cidade:
        return None, ('Não encontrei a cidade no endereço. Use '
                      '"Rua do Príncipe, 500, Joinville/SC" ou informe só a cidade.')

    logradouro = ''
    for indice, parte in enumerate(partes):
        if indice == indice_cidade or RE_SO_NUMERO.match(parte):
            continue
        logradouro = _limpar_logradouro(parte)
        if logradouro:
            break
    if not logradouro:
        return None, ('Informe o logradouro antes da cidade '
                      '(ex.: "Rua do Príncipe, Joinville/SC").')

    caixa = caixa_geografica(cidade['latitude'], cidade['longitude'],
                             RAIO_BUSCA_LOGRADOURO_KM)
    termos = [sem_acento(logradouro)]
    sem_tipo = _remover_tipo_logradouro(termos[0])
    if sem_tipo:
        termos.append(sem_tipo)

    # So procura dentro do municipio informado: cair para a UF inteira trazia
    # homonimo de outra cidade (ex.: "Rua das Palmeiras, Joinville/SC" virava
    # Blumenau/SC, a 100 km). Municipio e id_municipio IBGE, nao o nome.
    params = {
        **caixa,
        'uf': cidade.get('uf') or '',
        'ibge': cidade.get('cd_ibge') or 0,
    }
    linha = None
    for termo in termos:
        cur.execute(SQL_LOGRADOURO, {**params, 'termo': f'%{termo}%'})
        linha = cur.fetchone()
        if linha and linha['n_ceps']:
            break

    if not linha or not linha['n_ceps'] or _float(linha['latitude']) is None:
        return None, (f'Não encontrei o logradouro "{logradouro}" em '
                      f'{cidade["rotulo"].split(" (")[0]}. '
                      'Tente só a cidade ou um CEP.')

    municipio = linha['nome_municipio'] or cidade['rotulo'].split('/')[0]
    rotulo_uf = f'/{linha["uf"]}' if linha['uf'] else ''
    return {
        'latitude': float(linha['latitude']),
        'longitude': float(linha['longitude']),
        'rotulo': (f'{logradouro} - {municipio}{rotulo_uf} '
                   f'(média de {linha["n_ceps"]} CEPs do logradouro)'),
    }, None


def resolver_origem(cur, texto):
    """
    Traduz o texto digitado em uma coordenada.

    Devolve (origem, erro): origem = {'latitude', 'longitude', 'rotulo'}.
    """
    texto = (texto or '').strip()
    if not texto:
        return None, 'Informe um CNPJ, CEP, cidade ou coordenada.'

    coordenada = RE_COORDENADA.match(texto)
    if coordenada:
        lat = _float(coordenada.group(1))
        lon = _float(coordenada.group(2))
        if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return None, 'Coordenada inválida. Use "latitude,longitude" (ex.: -26.9,-48.7).'
        return {'latitude': lat, 'longitude': lon,
                'rotulo': f'coordenada {lat:.5f}, {lon:.5f}'}, None

    if RE_CEP.match(texto):
        cep = texto.replace('-', '').replace('.', '')
        cur.execute(SQL_CEP, {'cep': cep})
        linha = cur.fetchone()
        if not linha:
            return None, (f'CEP {texto} não tem coordenada na base. '
                          'Tente a cidade (ex.: Joinville/SC).')
        return {
            'latitude': float(linha['latitude']),
            'longitude': float(linha['longitude']),
            'rotulo': (f'CEP {texto} - {linha["nome_municipio"] or ""}'
                       f'{"/" + linha["uf"] if linha["uf"] else ""}'),
        }, None

    digitos = ''.join(c for c in texto if c.isdigit())
    if len(digitos) == 14:
        cur.execute(SQL_CNPJ, {'basico': digitos[:8], 'ordem': digitos[8:12],
                               'dv': digitos[12:]})
        linha = cur.fetchone()
        if not linha:
            return None, f'CNPJ {texto} não encontrado.'
        rotulo_cnpj = (f'CNPJ {digitos} - {linha["municipio_nome"] or ""}'
                       f'{"/" + linha["uf"] if linha["uf"] else ""}')
        latitude = _float(linha['latitude'])
        longitude = _float(linha['longitude'])
        if latitude is not None and longitude is not None:
            return {'latitude': latitude, 'longitude': longitude,
                    'rotulo': rotulo_cnpj}, None
        latitude = _float(linha['mun_latitude'])
        longitude = _float(linha['mun_longitude'])
        if latitude is not None and longitude is not None:
            return {'latitude': latitude, 'longitude': longitude,
                    'rotulo': f'{rotulo_cnpj} (centro do município)'}, None
        return None, (f'O CNPJ {texto} não tem endereço com coordenada. '
                      'Tente a cidade (ex.: Joinville/SC).')

    # Endereco ("Rua X, 500, Cidade/UF") antes de cidade: um nome de municipio
    # nao tem virgula, entao o formato com partes so aparece em endereco.
    if RE_PARTES_ENDERECO.search(texto):
        origem_endereco, erro_endereco = _origem_endereco(cur, texto)
        if origem_endereco:
            return origem_endereco, None
        origem_cidade, erro_cidade = _origem_cidade(cur, texto)
        if origem_cidade:
            return origem_cidade, None
        return None, erro_endereco or erro_cidade

    return _origem_cidade(cur, texto)


def caixa_geografica(latitude, longitude, raio_km):
    """bbox que contem o circulo do raio (folga suficiente para o haversine)."""
    delta_lat = raio_km / KM_POR_GRAU_LAT
    # cos(lat) pode chegar a zero perto dos polos: protege o divisor
    cos_lat = max(math.cos(math.radians(latitude)), 0.01)
    delta_lon = raio_km / (KM_POR_GRAU_LAT * cos_lat)
    return {
        'lat_min': latitude - delta_lat,
        'lat_max': latitude + delta_lat,
        'lon_min': longitude - delta_lon,
        'lon_max': longitude + delta_lon,
    }


SQL_PROXIMOS = """
    WITH ceps_todos AS MATERIALIZED (
        SELECT c.cep,
               c.latitude,
               c.longitude,
               c.nome_municipio,
               6371 * acos(least(1.0,
                   cos(radians(%(lat)s)) * cos(radians(c.latitude)) *
                   cos(radians(c.longitude) - radians(%(lon)s)) +
                   sin(radians(%(lat)s)) * sin(radians(c.latitude))
               )) AS km
          FROM c_ceps c
         WHERE c.latitude BETWEEN %(lat_min)s AND %(lat_max)s
           AND c.longitude BETWEEN %(lon_min)s AND %(lon_max)s
    )
    -- O total do raio precisa de TODOS os CEPs (ceps_todos); a busca pode
    -- comecar no km do cursor (ceps), o que evita varrer o raio inteiro.
    , ceps AS MATERIALIZED (
        SELECT * FROM ceps_todos
         {filtro_km_min}
         ORDER BY km
    )
    {cte_total}
    SELECT e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
           e.cnpj_basico,
           e.cnpj_ordem,
           e.cnpj_dv,
           e.cd_matriz_filial,
           e.nm_fantasia,
           e.logradouro,
           e.numero,
           e.bairro,
           e.uf,
           COALESCE(mg.nome, c.nome_municipio) AS municipio_nome,
           e.ddd_1 || e.tel_1 AS telefone_1,
           e.cd_sit_cadastro,
           CASE e.cd_sit_cadastro
               WHEN 1 THEN 'NULA'
               WHEN 2 THEN 'ATIVA'
               WHEN 3 THEN 'SUSPENSA'
               WHEN 4 THEN 'INAPTA'
               WHEN 8 THEN 'BAIXADA'
           END AS situacao_descricao,
           c.latitude,
           c.longitude,
           c.km
           {col_total}
      FROM ceps c
      JOIN c_estabelecimentos e ON e.cep = c.cep
      LEFT JOIN c_municipios_geo mg ON mg.cd_municipio = e.municipio
     WHERE c.km <= %(raio)s
       {filtro_matriz}
       {filtro_situacao}
       {filtro_keyset}
     ORDER BY c.km, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv
     LIMIT %(limite)s
"""

# Soma das contagens por CEP: c_ceps_estab (ProjetoCargaDados:
# contar_estab_por_cep) evita o COUNT no raio, que custa 5-15 s. O ANY(ARRAY)
# mantem 1 busca no indice por CEP; com JOIN o planner le a tabela inteira
# (~130 MB) e o raio de 100 km passa de 1,2 s para ~430 ms.
SQL_TOTAL_RAIO = """
    , total AS (
        SELECT coalesce(sum(ce.{coluna}), 0)::bigint AS n
          FROM c_ceps_estab ce
         WHERE ce.cep = ANY(ARRAY(SELECT c.cep FROM {cte_ceps} c
                                   WHERE c.km <= %(raio)s))
    )
"""

# Mesma ideia com o filtro de situacao: c_ceps_estab_sit tem uma linha por
# (cep, cd_sit_cadastro), entao a soma ja vem restrita a situacao pedida.
# Criada por ProjetoCargaDados: manage.py contar_estab_por_cep_situacao.
SQL_TOTAL_RAIO_SIT = """
    , total AS (
        SELECT coalesce(sum(ce.{coluna}), 0)::bigint AS n
          FROM c_ceps_estab_sit ce
         WHERE ce.cep = ANY(ARRAY(SELECT c.cep FROM {cte_ceps} c
                                   WHERE c.km <= %(raio)s))
           AND ce.cd_sit_cadastro = %(situacao)s
    )
"""

# ---------------------------------------------------------------------------
# Salto para uma pagina qualquer ("Ir para a pagina N")
# ---------------------------------------------------------------------------
# A listagem e keyset (km, cnpj), entao nao existe "pular N paginas" de graca:
# um OFFSET no raio custou de 4 s a 23 s (medido em 06/11/2026, Joinville/SC,
# 10 km, 150 mil matrizes), porque o km e calculado sobre todos os
# estabelecimentos do raio. O caminho barato usa o fato de o km ser do CEP:
#   1) a tabela agregada diz quantas empresas tem cada CEP; acumulando por km
#      descobrimos em qual CEP cai a linha pedida (~0,4 s);
#   2) dentro daquele CEP (indice idx_estabelecimentos_cep) um OFFSET pequeno
#      devolve o CNPJ da linha (~0,4 s).
# O resultado e o cursor keyset da linha ANTERIOR a primeira da pagina, que e
# exatamente o que buscar_proximos espera em `depois`.
SQL_CEP_DO_INDICE = """
    WITH ceps AS MATERIALIZED (
        SELECT c.cep,
               6371 * acos(least(1.0,
                   cos(radians(%(lat)s)) * cos(radians(c.latitude)) *
                   cos(radians(c.longitude) - radians(%(lon)s)) +
                   sin(radians(%(lat)s)) * sin(radians(c.latitude))
               )) AS km
          FROM c_ceps c
         WHERE c.latitude BETWEEN %(lat_min)s AND %(lat_max)s
           AND c.longitude BETWEEN %(lon_min)s AND %(lon_max)s
    )
    , contagem AS (
        SELECT c.cep, c.km, ce.{coluna} AS n
          FROM ceps c
          JOIN {tabela} ce ON ce.cep = c.cep
         WHERE c.km <= %(raio)s
           {filtro_situacao}
    )
    , acumulado AS (
        SELECT cep, km, sum(n) OVER (ORDER BY km, cep) - n AS antes
          FROM contagem
    )
    SELECT cep, km, antes
      FROM acumulado
     WHERE antes <= %(alvo)s
     ORDER BY antes DESC
     LIMIT 1
"""

# CNPJ que ocupa a posicao `resto` dentro do CEP achado acima (0 = primeiro).
SQL_CNPJ_NA_POSICAO = """
    SELECT cnpj_basico || cnpj_ordem || cnpj_dv AS cnpj_completo
      FROM c_estabelecimentos
     WHERE cep = %(cep)s
       {filtro_matriz}
       {filtro_situacao}
     ORDER BY cnpj_basico, cnpj_ordem, cnpj_dv
     OFFSET %(resto)s
     LIMIT 1
"""

# Retornos de cursor_para_pagina que nao sao um cursor.
PAGINA_ALEM_DO_FIM = 'fim'
PAGINA_SEM_TABELA = 'sem-tabela'


def tem_tabela_total(cur, nome='c_ceps_estab'):
    """
    A tabela agregada `nome` existe? (cache de TTL_TABELA_TOTAL s por nome)

    c_ceps_estab serve ao total sem filtro; c_ceps_estab_sit, ao total com
    filtro de situacao.
    """
    global _tabela_total_cache
    agora = time.time()
    if not isinstance(_tabela_total_cache, dict):
        _tabela_total_cache = {}
    item = _tabela_total_cache.get(nome)
    if item and item[1] > agora:
        return item[0]
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS existe", [nome])
    linha = cur.fetchone()
    existe = bool(linha['existe']) if linha else False
    _tabela_total_cache[nome] = (existe, agora + TTL_TABELA_TOTAL)
    return existe


def buscar_proximos(cur, latitude, longitude, raio_km, somente_matrizes,
                    depois=None, limite=LIMITE_PAGINA, situacao=None):
    """
    Estabelecimentos ate raio_km do ponto, ordenados do mais perto.

    depois = (km, cnpj_completo) do ultimo resultado da pagina anterior.
    situacao = codigo cd_sit_cadastro (1 nula, 2 ativa, 3 suspensa, 4 inapta,
    8 baixada) ou None para todas.
    Com `depois`, a CTE de CEPs e podada em km >= depois_km (o total do raio
    continua saindo de ceps_todos): mesma lista, sem varrer o raio inteiro.
    Devolve (linhas, tem_mais, total) - total e None quando a tabela agregada
    nao existe: c_ceps_estab sem filtro, c_ceps_estab_sit com filtro de
    situacao (ver o docstring do modulo).
    """
    params = {
        'lat': latitude,
        'lon': longitude,
        'raio': raio_km,
        'limite': limite + 1,
    }
    params.update(caixa_geografica(latitude, longitude, raio_km))

    filtro_matriz = 'AND e.cd_matriz_filial = 1' if somente_matrizes else ''
    filtro_situacao = ''
    if situacao:
        filtro_situacao = 'AND e.cd_sit_cadastro = %(situacao)s'
        params['situacao'] = situacao
    filtro_keyset = ''
    filtro_km_min = ''
    if depois:
        filtro_keyset = ('AND (c.km, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv) > '
                         '(%(depois_km)s, %(depois_basico)s, %(depois_ordem)s, %(depois_dv)s)')
        # Poda da CTE de CEPs: toda linha que passa no keyset tem
        # km >= depois_km, entao descartar os CEPs mais proximos que o cursor
        # nao muda o resultado. Sem isso o custo cresce com a pagina (o
        # planner percorre os CEPs em ordem e joga fora tudo antes do cursor):
        # Joinville/10 km ia de 0,4 s na pagina 2 para 9 s na ultima.
        filtro_km_min = 'WHERE km >= %(depois_km)s'
        cnpj = depois[1]
        params.update({
            'depois_km': depois[0],
            'depois_basico': cnpj[:8],
            'depois_ordem': cnpj[8:12],
            'depois_dv': cnpj[12:14],
        })

    # Total: c_ceps_estab (sem filtro) ou c_ceps_estab_sit (com filtro de
    # situacao). Sem a tabela correspondente, a tela fica em "Pagina X".
    if situacao:
        com_total = tem_tabela_total(cur, 'c_ceps_estab_sit')
        cte_total = SQL_TOTAL_RAIO_SIT
    else:
        com_total = tem_tabela_total(cur, 'c_ceps_estab')
        cte_total = SQL_TOTAL_RAIO
    sql = SQL_PROXIMOS.format(
        filtro_matriz=filtro_matriz,
        filtro_situacao=filtro_situacao,
        filtro_keyset=filtro_keyset,
        filtro_km_min=filtro_km_min,
        cte_total=(cte_total.format(
            coluna='n_matrizes' if somente_matrizes else 'n_total',
            cte_ceps='ceps_todos')
            if com_total else ''),
        col_total=', (SELECT n FROM total) AS total_raio' if com_total else '',
    )
    cur.execute("SET LOCAL statement_timeout = 15000")
    cur.execute(sql, params)
    linhas = cur.fetchall()
    tem_mais = len(linhas) > limite
    linhas = list(linhas[:limite])
    if com_total and linhas:
        total = linhas[0]['total_raio']
    else:
        # Sem linhas a CTE nao devolve o total; na primeira pagina (sem cursor)
        # o vazio significa zero estabelecimentos no raio, e isso ja e o total.
        total = 0 if (com_total and depois is None) else None

    if linhas:
        # Razao social vem de c_empresas: consulta a parte para nao fazer
        # o join dentro da CTE (que nao tem LIMIT e veria a cidade inteira).
        basicos = sorted({linha['cnpj_basico'] for linha in linhas})
        cur.execute('SELECT cnpj_basico, razao_social FROM c_empresas '
                    'WHERE cnpj_basico = ANY(%s)', [basicos])
        razoes = {r['cnpj_basico']: r['razao_social'] for r in cur.fetchall()}
        for linha in linhas:
            linha['razao_social'] = razoes.get(linha['cnpj_basico'])
            linha['maps_url'] = montar_maps_url(linha)

    return linhas, tem_mais, total


def cursor_para_pagina(cur, latitude, longitude, raio_km, somente_matrizes,
                       numero, situacao=None, limite=LIMITE_PAGINA):
    """
    Cursor keyset da linha anterior a primeira da pagina `numero`.

    Existe porque a paginacao e keyset (nao ha como pular N paginas com
    OFFSET barato dentro do raio). Devolve:
      None                  -> pagina 1 (comeco da lista, sem cursor);
      PAGINA_ALEM_DO_FIM    -> a posicao pedida passa do fim do raio;
      PAGINA_SEM_TABELA     -> falta a tabela agregada (sem ela nao ha posicao);
      (km, cnpj_completo)   -> cursor para buscar_proximos(depois=...).

    situacao = codigo cd_sit_cadastro (2 ativa, ...) ou None para todas, igual
    a buscar_proximos - e a mesma tabela agregada que da o total, entao o salto
    sempre cai dentro do "de X paginas" que a tela mostra.
    """
    if numero <= 1:
        return None

    coluna = 'n_matrizes' if somente_matrizes else 'n_total'
    tabela = 'c_ceps_estab_sit' if situacao else 'c_ceps_estab'
    if not tem_tabela_total(cur, tabela):
        return PAGINA_SEM_TABELA

    # Linha (0-based) imediatamente antes da primeira da pagina pedida.
    alvo = (numero - 1) * limite - 1
    if alvo < 0:
        return None

    params = {'lat': latitude, 'lon': longitude, 'raio': raio_km, 'alvo': alvo}
    params.update(caixa_geografica(latitude, longitude, raio_km))
    filtro_situacao = ''
    if situacao:
        filtro_situacao = 'AND ce.cd_sit_cadastro = %(situacao)s'
        params['situacao'] = situacao

    cur.execute("SET LOCAL statement_timeout = 15000")
    cur.execute(SQL_CEP_DO_INDICE.format(
        tabela=tabela, coluna=coluna, filtro_situacao=filtro_situacao), params)
    linha = cur.fetchone()
    if not linha:
        return PAGINA_ALEM_DO_FIM

    resto = alvo - int(linha['antes'])
    params_cnpj = {'cep': linha['cep'], 'resto': resto}
    filtro_situacao = ''
    if situacao:
        filtro_situacao = 'AND cd_sit_cadastro = %(situacao)s'
        params_cnpj['situacao'] = situacao

    cur.execute(SQL_CNPJ_NA_POSICAO.format(
        filtro_matriz='AND cd_matriz_filial = 1' if somente_matrizes else '',
        filtro_situacao=filtro_situacao), params_cnpj)
    posicao = cur.fetchone()
    if not posicao:
        return PAGINA_ALEM_DO_FIM

    return (float(linha['km']), posicao['cnpj_completo'])


def km_para_texto(km):
    """
    km -> texto de querystring/token que volta identico no float().

    `repr(float)` faz round-trip exato. Nao use f'{km:.6f}': arredondar o km
    quebra o keyset quando varias linhas empatam no mesmo km (o km e do CEP, e
    um CEP concentra milhares de empresas). Arredondando para cima o cursor
    salta o resto do grupo e perde linhas; para baixo ele fica antes do grupo e
    a mesma pagina se repete para sempre.
    """
    return repr(float(km))


def token_cursor(depois):
    """Cursor keyset -> token do historico ('-' quando a pagina e a primeira)."""
    if not depois:
        return '-'
    return f'{km_para_texto(depois[0])}|{depois[1]}'


def cursor_do_token(token):
    """Token do historico -> cursor keyset (None = comeco)."""
    if not token or token == '-':
        return None
    km, _, cnpj = token.partition('|')
    try:
        return (float(km), cnpj)
    except (TypeError, ValueError):
        return None


def hist_de_texto(texto):
    """Querystring 'hist' -> lista de tokens (limita o tamanho da URL)."""
    tokens = [t for t in (texto or '').split(';') if t]
    return tokens[-HIST_MAXIMO:]


def hist_para_texto(hist):
    return ';'.join(hist)
