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

Total de paginas: o COUNT exato no raio custa 5-15 s (bitmap scan em 74,4 M de
estabelecimentos), entao a contagem vem da tabela agregada `c_ceps_estab`
(cep, n_matrizes, n_total), criada por
`ProjetoCargaDados: manage.py contar_estab_por_cep`. Sem essa tabela a busca
continua funcionando e a paginacao mostra "Pagina X" sem o total.

Filtro de situacao (`situacao=` na querystring, codigo cd_sit_cadastro): o total
sai de `c_ceps_estab_sit` (cep, cd_sit_cadastro, n_matrizes, n_total), criada por
`ProjetoCargaDados: manage.py contar_estab_por_cep_situacao`. A tabela sem
situacao (`c_ceps_estab`) nao serve para o filtro. Se `c_ceps_estab_sit` nao
existir, a tela cai para "Pagina X" (sem total) e continua funcionando.

Busca filtrada (`buscar_proximos_filtrado`, desde 25/11/2026): quando a
pergunta traz CNAE, data de abertura ou capital social, os CEPs do bbox sao
lidos em Python e passados como array (`= ANY(%(ceps)s)`); o filtro roda num
CTE MATERIALIZED antes da distancia. A ordem importa: com a varredura do raio
(ceps primeiro) + filtro de CNAE a consulta estourou 30 s, e a CTE de CEPs
sem array tem estimativa ~20x menor que o real (147 x 3.275 em Curitiba/5 km),
o que leva o planner a um hash join de 74 M de linhas. O total exato vem de
`count(*) OVER ()` na mesma consulta. Nessa funcao as filiais entram por
padrao (o raio de supermercado/padaria e' lead); a pagina continua com o
checkbox "somente matrizes". Medido em dev (25/11/2026): 4 a 22 s no
primeiro acesso (banco frio e I/O-bound) e 1,8 a 3,2 s na segunda execucao nos
3 casos sem capital; o caso com capital social e' o pior (17 a 22 s). Um indice
nao-parcial em cnae_principal foi testado e nao mudou o tempo: o gargalo e'
I/O aleatorio no heap ao varrer o raio (ver README).

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

# Ordenacao pelo km do ENDERECO (ordem=endereco na tela). A janela de CEPs
# continua sendo o que segura o custo: ela cresce por km_cep menos a folga do
# CEP (c_ceps_folga), de modo que um CEP fora da janela nao pode ter endereco
# mais perto que o corte. Folga acima de FOLGA_MAX_ORDEM_ENDERECO (1 km) nao
# usa a coordenada do endereco: cai no km do CEP (CEP generico/rural, onde o
# endereco nao ajuda). ALVO_JANELA: quantas linhas a janela precisa cobrir
# antes de cortar (margem para o keyset). Medido em Joinville/5 km: 0,50 s
# com a janela materializada, contra 1,0 s sem materializar.
FOLGA_MAX_ORDEM_ENDERECO = 1.0
ALVO_JANELA_ENDERECO = 60

# Mapa da /proximidade/ (densidade do raio): a coordenada da empresa e a do
# CEP, e um CEP concentra centenas/milhares de empresas no mesmo ponto. Um
# marcador por empresa empilharia tudo em uma bolinha (medido: as 20 linhas da
# pagina 1 caem em 1-2 coordenadas). Entao o mapa agrega em grade: cada celula
# tem a soma das empresas. Graus testados em (0.005 = ~550 m, 0.01 = ~1,1 km,
# 0.02 = ~2,2 km); o primeiro que couber em MAX_CELULAS_DENSIDADE vence.
GRADES_DENSIDADE = (0.005, 0.01, 0.02)
MAX_CELULAS_DENSIDADE = 3000

# Caixa usada para achar o logradouro a partir do centro do municipio: os CEPs
# nao tem indice geografico, entao primeiro recortamos a caixa (varredura de
# c_ceps, ~250 ms) e so depois aplicamos translate() nas linhas que sobraram.
RAIO_BUSCA_LOGRADOURO_KM = 60

# Tempo (s) que o resultado de to_regclass fica em memoria no processo.
TTL_TABELA_TOTAL = 300
_tabela_total_cache = None  # (valor, expira_em)

# Margem (em linhas) da janela de CEPs por km (ver SQL_PROXIMOS). A janela
# mantem so os CEPs ate cobrir as linhas da pagina; a margem cobre diferenca
# entre a contagem agregada (c_ceps_estab/_sit) e a realidade quando a tabela
# esta um pouco defasada, sem varrer o raio inteiro. Contagem subestimada so
# traz CEPs a mais (seguro); superestimada e absorvida pela margem.
MARGEM_JANELA_KM = 200

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

# Media das coordenadas do ENDERECO (c_estab_geo/CNEFE) das empresas do
# logradouro. A media dos CEPs pode cair fora da rua quando o logradouro e'
# longo/curvo ou tem CEP com coordenada ruim (caso Rua do Principe,
# Joinville/SC: media dos CEPs -26.3051/-48.8364 x enderecos -26.30296/-48.84527,
# ~1 km fora). Usada quando ha pelo menos N_GEO_MIN enderecos casados.
SQL_LOGRADOURO_GEO = f"""
    WITH ceps AS MATERIALIZED (
        SELECT cep
          FROM c_ceps
         WHERE latitude IS NOT NULL
           AND longitude IS NOT NULL
           AND latitude BETWEEN %(lat_min)s AND %(lat_max)s
           AND longitude BETWEEN %(lon_min)s AND %(lon_max)s
           AND (%(uf)s = '' OR uf = %(uf)s)
           AND (%(ibge)s = 0 OR id_municipio = %(ibge)s)
           AND {sql_sem_acento('logradouro')} LIKE %(termo)s
    )
    SELECT count(*) AS n_geo,
           avg(g.latitude) AS latitude,
           avg(g.longitude) AS longitude
      FROM c_estabelecimentos e
      JOIN c_estab_geo g
        ON g.cnpj_completo = e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv
     WHERE e.cep IN (SELECT cep FROM ceps)
       AND {sql_sem_acento('e.logradouro')} LIKE %(termo)s
"""

# Minimo de enderecos casados para trocar a media dos CEPs pela dos enderecos.
N_GEO_MIN = 5
# Acima deste numero de CEPs o join com c_estab_geo fica caro demais; fica
# a media dos CEPs (avenida longa/reta).
N_CEPS_GEO_MAX = 20

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

    # Tenta a media dos ENDERECOS (CNEFE). A media dos CEPs fica no meio
    # geometrico dos centroides e, em rua longa/curva, cai fora da rua.
    latitude = float(linha['latitude'])
    longitude = float(linha['longitude'])
    rotulo_geo = f'média de {linha["n_ceps"]} CEPs do logradouro'
    try:
        # Logradouro com muitos CEPs (avenida longa: Avenida Paulista tem 86)
        # faz o join com c_estab_geo varrer dezenas de milhares de enderecos
        # (medido: 29 s). Nesses casos a media dos CEPs ja e' boa e barata.
        if (int(linha['n_ceps']) <= N_CEPS_GEO_MAX
                and tem_tabela_total(cur, 'c_estab_geo')):
            # A RFB grava o logradouro SEM o tipo ("DO PRINCIPE", nao "RUA DO
            # PRINCIPE"); por isso tenta os dois termos e usa o que tiver
            # enderecos suficientes (o primeiro pode casar so 5 ruas/CEP).
            # O termo sem o tipo ("DO PRINCIPE") e' o que casa com a RFB;
            # tenta ele primeiro para nao pagar duas consultas.
            for termo_geo in reversed(termos):
                cur.execute(SQL_LOGRADOURO_GEO,
                            {**params, 'termo': f'%{termo_geo}%'})
                geo = cur.fetchone()
                if (geo and geo.get('n_geo')
                        and _float(geo.get('latitude')) is not None
                        and int(geo['n_geo']) >= N_GEO_MIN):
                    latitude = float(geo['latitude'])
                    longitude = float(geo['longitude'])
                    rotulo_geo = 'média dos endereços do logradouro'
                    break
    except Exception:
        # Sem c_estab_geo (ou consulta falhou): mantem a media dos CEPs.
        pass

    municipio = linha['nome_municipio'] or cidade['rotulo'].split('/')[0]
    rotulo_uf = f'/{linha["uf"]}' if linha['uf'] else ''
    return {
        'latitude': latitude,
        'longitude': longitude,
        'rotulo': f'{logradouro} - {municipio}{rotulo_uf} ({rotulo_geo})',
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


# CTE `ceps` (fallback): todos os CEPs do raio, ordenados por km. E o
# comportamento historico - funciona sempre, mas o planner do PostgreSQL 16
# faz um Hash Join em c_municipios_geo que quebra a ordem `c.km`, forca um
# Sort completo do raio inteiro e estoura o statement_timeout em regiao densa
# (Curitiba/10 km: 521 mil estabelecimentos). Por isso, com a tabela agregada
# disponivel, usar CTE_CEPS_JANELA (ver comentario la).
CTE_CEPS_TODOS = """
    , ceps AS MATERIALIZED (
        SELECT * FROM ceps_todos
         {filtro_km_min}
         ORDER BY km
    )
"""

# CTE `ceps` (janela por km): mantem so os CEPs dos grupos de km que podem
# conter as linhas da pagina. A ordenacao final e (km, cnpj_basico, ...); como
# km e do CEP, as linhas de um mesmo km saem contiguas. Com a contagem por CEP
# (c_ceps_estab / c_ceps_estab_sit, as mesmas tabelas do total), soma-se quantas
# empresas cabem em cada km e corta-se assim que a pagina ja esta coberta.
#   - pagina 1: acumulado antes do grupo < limite + margem.
#   - pagina N (cursor): idem, somando a contagem do km do cursor (as linhas
#     antes dele naquele grupo serao descartadas pelo keyset).
# E um superset do resultado real: o ORDER BY/LIMIT/keyset da query principal
# continua decidindo as linhas. Sem isso, o PG16 ordena o raio inteiro.
#
# Parametros de format:
#   {filtro_km_min}    poda opcional (WHERE km >= depois_km) - quando ha cursor
#   {tabela_contagem}  c_ceps_estab ou c_ceps_estab_sit
#   {coluna_contagem}  n_matrizes ou n_total
#   {filtro_contagem}  restricao extra da tabela de contagem (situacao)
#   {limite_janela}    expressao SQL: quantas linhas a janela precisa cobrir
CTE_CEPS_JANELA = """
    , ceps_contagem AS (
        SELECT t.cep, t.km, coalesce(ce.{coluna_contagem}, 0) AS n_aqui
          FROM (SELECT * FROM ceps_todos {filtro_km_min}) t
          LEFT JOIN {tabela_contagem} ce ON ce.cep = t.cep {filtro_contagem}
    )
    , ceps_km AS (
        SELECT km, sum(n_km) OVER (ORDER BY km) - n_km AS acum_antes
          FROM (SELECT km, sum(n_aqui) AS n_km FROM ceps_contagem GROUP BY km) g
    )
    , ceps AS MATERIALIZED (
        SELECT c.cep, c.latitude, c.longitude, c.nome_municipio, c.km
          FROM ceps_todos c
          JOIN ceps_km k USING (km)
         WHERE k.acum_antes < {limite_janela}
         ORDER BY c.km
    )
"""


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
    -- O total do raio precisa de TODOS os CEPs (ceps_todos); a busca usa uma
    -- CTE `ceps` propria: janela por km quando ha tabela agregada (ver
    -- CTE_CEPS_JANELA), senao todos os CEPs ordenados por km (CTE_CEPS_TODOS).
    {cte_ceps}
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
           e.email,
           e.cnae_principal,
           cn.nm_cnae AS atividade_principal,
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
      LEFT JOIN c_cnaes cn ON cn.cnae = e.cnae_principal
     WHERE c.km <= %(raio)s
       {filtro_matriz}
       {filtro_situacao}
       {filtro_keyset}
     ORDER BY c.km, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv
     LIMIT %(limite)s
"""

# Busca filtrada (chat e filtros que a pagina ainda nao tem): os CEPs do
# bbox sao lidos ANTES, em Python (uma consulta barata em c_ceps), e passados
# como array no `= ANY(%(ceps)s)`. Assim o planner nao tem a estimativa errada
# da CTE (ele achava 147 CEPs onde havia 3.275 em Curitiba/5 km e trocava o
# nested loop por um hash join que varria 74 M de linhas).
#
# A ordem foi medida em 25/11/2026 (base de 74,4 M, banco local):
#   - filtro no join do raio com CNAE: estourou 30 s;
#   - candidato por CNAE antes do raio: varre o estado inteiro (e filial nao
#     tem indice de cnae) -> timeout;
#   - CEPs do raio + array + filtro: 1,5 a 3 s quentes nos casos do chat.
#
# `cand_estab` MATERIALIZED garante que o filtro de estabelecimento roda
# primeiro. O capital social vira um EXISTS correlacionado (lookup por
# cnpj_basico so nos candidatos): como JOIN o planner comeca por c_empresas
# (71 M) e a consulta chega a 22 s.
#
# `count(*) OVER ()` traz o total exato com os mesmos filtros na mesma
# passada. Cuidado: o total conta estabelecimentos, nao empresas - com
# filiais incluidas (padrao do chat) a mesma empresa pode aparecer mais de
# uma vez.
#
# Modo ENDERECO (usar_endereco=True): junta c_estab_geo (CNEFE) e usa a
# coordenada do endereco quando existe E a folga do CEP (c_ceps_folga) e' de
# ate 1 km; senao continua o centroide do CEP. O bbox dos CEPs e' aberto em
# FOLGA_MAX_ORDEM_ENDERECO para nao perder enderecos que caem dentro do raio
# mas cujo CEP esta fora da caixa. O km da linha passa a ser o do endereco
# (e o keyset/total acompanham).
SQL_CEPS_CAIXA = """
    SELECT cep
      FROM c_ceps
     WHERE latitude BETWEEN %(lat_min)s AND %(lat_max)s
       AND longitude BETWEEN %(lon_min)s AND %(lon_max)s
"""

SQL_PROXIMOS_FILTRADO = """
    WITH cand_estab AS MATERIALIZED (
        SELECT e.cnpj_basico,
               e.cnpj_ordem,
               e.cnpj_dv,
               e.cep,
               e.cd_matriz_filial,
               e.nm_fantasia,
               e.logradouro,
               e.numero,
               e.bairro,
               e.uf,
               e.municipio,
               e.ddd_1,
               e.tel_1,
               e.email,
               e.cnae_principal,
               e.cd_sit_cadastro,
               e.dt_ini
          FROM c_estabelecimentos e
         WHERE e.cep = ANY(%(ceps)s)
           {filtros}
    )
    , cand_km AS (
        SELECT e.*,
               {campos_coordenada}
               c.nome_municipio AS cep_municipio,
               {campos_km}
               {campo_precisao_cand}
          FROM cand_estab e
          JOIN c_ceps c ON c.cep = e.cep
          {join_endereco}
    )
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
           COALESCE(mg.nome, e.cep_municipio) AS municipio_nome,
           e.ddd_1 || e.tel_1 AS telefone_1,
           e.email,
           e.cnae_principal,
           cn.nm_cnae AS atividade_principal,
           e.cd_sit_cadastro,
           CASE e.cd_sit_cadastro
               WHEN 1 THEN 'NULA'
               WHEN 2 THEN 'ATIVA'
               WHEN 3 THEN 'SUSPENSA'
               WHEN 4 THEN 'INAPTA'
               WHEN 8 THEN 'BAIXADA'
           END AS situacao_descricao,
           e.dt_ini,
           e.latitude,
           e.longitude,
           e.km
           {campo_precisao}
           {col_total}
      FROM cand_km e
      LEFT JOIN c_municipios_geo mg ON mg.cd_municipio = e.municipio
      LEFT JOIN c_cnaes cn ON cn.cnae = e.cnae_principal
     WHERE e.km <= %(raio)s
       {filtro_depois}
     ORDER BY e.km, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv
     LIMIT %(limite_mais)s
"""

# Teto e timeout da busca filtrada. O teto de 5.000 e' o mesmo do export do
# chat; a tela usa 1.000 (CHAT_SQL_MAX_ROWS). O timeout e' maior que o da
# pagina porque o candidato filtrado pode ser grande (atividade comum em
# capital) - mesmo assim, acima disso o certo e' refinar o filtro.
LIMITE_MAX_FILTRADO = 5000
TIMEOUT_FILTRADO_MS = 25000

# CEPs do bbox passados como array no `= ANY`. Em capital densa 5 km ja da
# ~3.300 (Curitiba) a ~9.500 (Av. Paulista); 50 mil cobre ate ~10 km nesses
# casos. Acima disso o array fica grande demais e o certo e' reduzir o raio
# (medido em 25/11/2026).
MAX_CEPS_FILTRADO = 50000

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
# ---------------------------------------------------------------------------
# Ordem por distancia do ENDERECO (ordem=endereco)
# ---------------------------------------------------------------------------
# O km do CEP e o que sustenta a paginacao rapida: todas as empresas de um CEP
# dividem o mesmo km, entao a janela cumulativa por km e exata. Com o km do
# endereco (c_estab_geo) isso deixa de valer, e a janela passa a usar
# `km_cep - folga_cep` como limite inferior: se `km_cep - folga` ja passou do
# corte, nenhum endereco daquele CEP pode estar mais perto. A folga vem de
# c_ceps_folga (ProjetoCargaDados: carregar_folga_ceps) e e limitada a
# FOLGA_MAX_ORDEM_ENDERECO; CEP com folga maior continua ordenando pelo km do
# CEP (o endereco nao ajudaria mesmo).
#
# A janela e MATERIALIZED de proposito: sem isso o planner faz o join com
# c_estabelecimentos para todos os CEPs do raio e so depois corta (~1,0 s em
# Joinville/5 km, contra ~0,50 s com a materializacao).
SQL_PROXIMOS_ENDERECO = """
    WITH ceps_todos AS MATERIALIZED (
        SELECT c.cep,
               c.latitude,
               c.longitude,
               c.nome_municipio,
               coalesce(f.folga_km, 0)::float AS folga,
               6371 * acos(least(1.0,
                   cos(radians(%(lat)s)) * cos(radians(c.latitude)) *
                   cos(radians(c.longitude) - radians(%(lon)s)) +
                   sin(radians(%(lat)s)) * sin(radians(c.latitude))
               )) AS km
          FROM c_ceps c
          LEFT JOIN c_ceps_folga f ON f.cep = c.cep
         WHERE c.latitude BETWEEN %(lat_min)s AND %(lat_max)s
           AND c.longitude BETWEEN %(lon_min)s AND %(lon_max)s
    )
    , contagem AS MATERIALIZED (
        SELECT t.*, coalesce(ce.{coluna_contagem}, 0) AS n
          FROM ceps_todos t
          LEFT JOIN {tabela_contagem} ce ON ce.cep = t.cep {filtro_contagem}
         WHERE t.km >= %(km_min)s
    )
    , acumulado AS (
        SELECT km, sum(n) OVER (ORDER BY km) - n AS antes
          FROM (SELECT km, sum(n) AS n FROM contagem GROUP BY km) g
    )
    , corte AS (
        SELECT coalesce(min(km), 1e9) AS km_corte
          FROM acumulado WHERE antes >= %(alvo)s
    )
    , extensao AS (
        SELECT coalesce(max(least(folga, %(folga_max)s)), 0) AS ext
          FROM contagem WHERE km <= (SELECT km_corte FROM corte)
    )
    , janela AS MATERIALIZED (
        SELECT * FROM contagem
         WHERE km <= (SELECT km_corte + ext FROM corte, extensao)
           AND km <= %(raio)s
    )
    {cte_total}
    , base AS (
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
               e.email,
               e.cnae_principal,
               cn.nm_cnae AS atividade_principal,
               e.cd_sit_cadastro,
               CASE e.cd_sit_cadastro
                   WHEN 1 THEN 'NULA'
                   WHEN 2 THEN 'ATIVA'
                   WHEN 3 THEN 'SUSPENSA'
                   WHEN 4 THEN 'INAPTA'
                   WHEN 8 THEN 'BAIXADA'
               END AS situacao_descricao,
               CASE WHEN g.latitude IS NOT NULL AND coalesce(c.folga, 0) <= %(folga_max)s
                    THEN g.latitude ELSE c.latitude END AS latitude,
               CASE WHEN g.latitude IS NOT NULL AND coalesce(c.folga, 0) <= %(folga_max)s
                    THEN g.longitude ELSE c.longitude END AS longitude,
               c.latitude  AS latitude_cep,
               c.longitude AS longitude_cep,
               c.km AS km_cep,
               CASE WHEN g.latitude IS NOT NULL AND coalesce(c.folga, 0) <= %(folga_max)s
                    THEN 6371 * acos(least(1.0,
                         cos(radians(%(lat)s)) * cos(radians(g.latitude)) *
                         cos(radians(g.longitude) - radians(%(lon)s)) +
                         sin(radians(%(lat)s)) * sin(radians(g.latitude))
                    ))
                    ELSE c.km
               END AS km,
               g.precisao AS geo_precisao
               {col_total}
          FROM janela c
          JOIN c_estabelecimentos e ON e.cep = c.cep
          LEFT JOIN c_estab_geo g
                 ON g.cnpj_completo = e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv
          LEFT JOIN c_municipios_geo mg ON mg.cd_municipio = e.municipio
          LEFT JOIN c_cnaes cn ON cn.cnae = e.cnae_principal
         WHERE TRUE
           {filtro_matriz}
           {filtro_situacao}
    )
    SELECT * FROM base
     WHERE (%(depois_km)s IS NULL
            OR (km, cnpj_basico, cnpj_ordem, cnpj_dv) >
               (%(depois_km)s, %(depois_basico)s, %(depois_ordem)s, %(depois_dv)s))
     ORDER BY km, cnpj_basico, cnpj_ordem, cnpj_dv
     LIMIT %(limite)s
"""


def buscar_proximos_endereco(cur, latitude, longitude, raio_km, somente_matrizes,
                             depois=None, limite=LIMITE_PAGINA, situacao=None):
    """
    Como buscar_proximos, mas ordenando pelo km do ENDERECO (c_estab_geo).

    O km devolvido em cada linha e o do endereco quando existe (precisao
    'exato'/'rua' e folga do CEP <= 1 km); senao e o do CEP, como antes. O
    keyset continua sendo (km, cnpj) - so muda a origem do km.
    """
    params = {
        'lat': latitude,
        'lon': longitude,
        'raio': raio_km,
        'limite': limite + 1,
        'alvo': ALVO_JANELA_ENDERECO,
        'folga_max': FOLGA_MAX_ORDEM_ENDERECO,
        'km_min': 0.0,
    }
    params.update(caixa_geografica(latitude, longitude, raio_km))

    if depois:
        params.update({
            'depois_km': depois[0],
            'depois_basico': depois[1][:8],
            'depois_ordem': depois[1][8:12],
            'depois_dv': depois[1][12:14],
        })
        # Poda: um CEP cujo km - folga ja passou do cursor nao contribui.
        params['km_min'] = depois[0] - FOLGA_MAX_ORDEM_ENDERECO
    else:
        params['depois_km'] = None
        params['depois_basico'] = None
        params['depois_ordem'] = None
        params['depois_dv'] = None

    filtro_matriz = 'AND e.cd_matriz_filial = 1' if somente_matrizes else ''
    filtro_situacao = ''
    if situacao:
        filtro_situacao = 'AND e.cd_sit_cadastro = %(situacao)s'
        params['situacao'] = situacao
        tabela_contagem = 'c_ceps_estab_sit'
        filtro_contagem = 'AND ce.cd_sit_cadastro = %(situacao)s'
        coluna_contagem = 'n_matrizes' if somente_matrizes else 'n_total'
        cte_total = SQL_TOTAL_RAIO_SIT.format(coluna=coluna_contagem,
                                              cte_ceps='ceps_todos')
        col_total = ', (SELECT n FROM total) AS total_raio'
    else:
        tabela_contagem = 'c_ceps_estab'
        filtro_contagem = ''
        coluna_contagem = 'n_matrizes' if somente_matrizes else 'n_total'
        cte_total = SQL_TOTAL_RAIO.format(coluna=coluna_contagem,
                                          cte_ceps='ceps_todos')
        col_total = ', (SELECT n FROM total) AS total_raio'

    sql = SQL_PROXIMOS_ENDERECO.format(
        tabela_contagem=tabela_contagem,
        coluna_contagem=coluna_contagem,
        filtro_contagem=filtro_contagem,
        cte_total=cte_total,
        col_total=col_total,
        filtro_matriz=filtro_matriz,
        filtro_situacao=filtro_situacao,
    )
    cur.execute("SET LOCAL statement_timeout = 15000")
    cur.execute(sql, params)
    linhas = cur.fetchall()
    tem_mais = len(linhas) > limite
    linhas = list(linhas[:limite])

    if linhas:
        basicos = sorted({linha['cnpj_basico'] for linha in linhas})
        cur.execute('SELECT cnpj_basico, razao_social FROM c_empresas '
                    'WHERE cnpj_basico = ANY(%s)', [basicos])
        razoes = {r['cnpj_basico']: r['razao_social'] for r in cur.fetchall()}
        for linha in linhas:
            linha['razao_social'] = razoes.get(linha['cnpj_basico'])
            linha['maps_url'] = montar_maps_url(linha)
            linha['geo_precisao'] = linha.get('geo_precisao')

    if linhas and linhas[0].get('total_raio') is not None:
        total = linhas[0]['total_raio']
    else:
        total = 0 if depois is None else None

    return linhas, tem_mais, total


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
    Havendo a tabela agregada, a CTE de CEPs ainda usa a janela por km
    (CTE_CEPS_JANELA): so entram os grupos de km que cobrem a pagina, e o
    PostgreSQL 16 nao precisa ordenar o raio inteiro (ver o comentario da
    constante). Sem a tabela agregada, cai em CTE_CEPS_TODOS (comportamento
    antigo, correto porem sem essa poda).
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
    coluna_contagem = 'n_matrizes' if somente_matrizes else 'n_total'
    if situacao:
        com_total = tem_tabela_total(cur, 'c_ceps_estab_sit')
        cte_total = SQL_TOTAL_RAIO_SIT
        tabela_contagem = 'c_ceps_estab_sit'
    else:
        com_total = tem_tabela_total(cur, 'c_ceps_estab')
        cte_total = SQL_TOTAL_RAIO
        tabela_contagem = 'c_ceps_estab'

    # CTE `ceps`: com a tabela agregada, usa a janela por km (CTE_CEPS_JANELA)
    # para nao ordenar o raio inteiro no PG16 (ver comentario da constante).
    # A contagem usada na janela e a mesma do total (mesma tabela e coluna),
    # entao a janela e exatamente "as linhas que o total conta ate aqui".
    if com_total:
        filtro_contagem = ('AND ce.cd_sit_cadastro = %(situacao)s'
                           if situacao else '')
        if depois:
            # Janela = km do cursor inteiro + o que falta pra pagina. O grupo
            # do km do cursor entra por inteiro porque o keyset ainda vai
            # descartar as linhas antes do cifrao dele.
            filtro_contagem_cb = ('AND cb.cd_sit_cadastro = %(situacao)s'
                                  if situacao else '')
            limite_janela = (
                'coalesce((SELECT sum(coalesce(cb.{col}, 0)) '
                'FROM ceps_todos tb '
                'LEFT JOIN {tab} cb ON cb.cep = tb.cep {filtro_cb} '
                'WHERE tb.km = %(depois_km)s), 0) + %(limite)s + {margem}'
            ).format(col=coluna_contagem, tab=tabela_contagem,
                     filtro_cb=filtro_contagem_cb, margem=MARGEM_JANELA_KM)
        else:
            limite_janela = '%(limite)s + {}'.format(MARGEM_JANELA_KM)
        cte_ceps = CTE_CEPS_JANELA.format(
            filtro_km_min=filtro_km_min,
            tabela_contagem=tabela_contagem,
            coluna_contagem=coluna_contagem,
            filtro_contagem=filtro_contagem,
            limite_janela=limite_janela,
        )
    else:
        # Sem tabela agregada nao ha como medir a janela: mantem o
        # comportamento antigo (todos os CEPs do raio, ordenados por km).
        cte_ceps = CTE_CEPS_TODOS.format(filtro_km_min=filtro_km_min)

    sql = SQL_PROXIMOS.format(
        cte_ceps=cte_ceps,
        filtro_matriz=filtro_matriz,
        filtro_situacao=filtro_situacao,
        filtro_keyset=filtro_keyset,
        cte_total=(cte_total.format(
            coluna=coluna_contagem,
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


def buscar_proximos_filtrado(cur, latitude, longitude, raio_km, limite=248,
                             situacao=None, matrizes=False,
                             faixas_cnae=None, cnaes=None, uf=None,
                             dt_ini_antes_de=None, capital_min=None,
                             depois=None, contar=True, usar_endereco=False):
    """
    Estabelecimentos ate raio_km do ponto, com filtros seletivos.

    Diferente de `buscar_proximos` (que varre todos os CEPs do raio e usa a
    tabela agregada para o total), aqui o filtro entra antes da distancia:

      1. os CEPs do bbox sao lidos em Python (uma consulta barata em c_ceps);
      2. `cand_estab` MATERIALIZED filtra c_estabelecimentos por
         `cep = ANY(ceps)` + filtros (CNAE, UF, situacao, dt_ini);
      3. se houver capital social, o filtro e' um EXISTS correlacionado por
         cnpj_basico (se virar JOIN, o planner comeca por c_empresas (71 M)
         e a consulta chega a 22 s);
      4. a distancia haversine e o total (`count(*) OVER ()`) saem na mesma
         consulta.

    Parametros:
      raio_km         1..100 (clampado); na pratica o filtro limita pelo
                      numero de CEPs do bbox (MAX_CEPS_FILTRADO, ~10 km em
                      capital). Acima disso levanta ValueError.
      limite          1..LIMITE_MAX_FILTRADO (default 248 = "as N mais proximas")
      situacao        codigo cd_sit_cadastro (2 = ativa) ou None
      matrizes        True = so matriz; False (default) = matriz + filial.
                      O chat usa False porque filial de supermercado/padaria
                      tambem e' lead; a pagina usa True no checkbox.
      faixas_cnae     lista de (min, max) inteiros, ex.: [(2400000, 2599999)]
      cnaes           lista de subclasses explicitas, ex.: [8630501, ...]
      uf              sigla (filtra e.uf)
      dt_ini_antes_de date: "abertas ha mais de X anos" (dt_ini < data)
      capital_min     numero: "capital social acima de R$ X" (> capital_min)
      depois          cursor keyset (km, cnpj_completo) da ultima linha da
                      pagina anterior (None = primeira pagina); usado pela
                      pagina e pelo export para paginar sem OFFSET
      contar          True (default) devolve o total exato via count(*) OVER ();
                      False nao calcula o total (export em blocos, onde o custo
                      de contar cada bloco nao compensa) e devolve total=None
      usar_endereco   True = km/coordenada do ENDERECO (c_estab_geo/CNEFE)
                      quando existe e a folga do CEP e' <= 1 km; senao do CEP.
                      Quem chama deve conferir se c_estab_geo e c_ceps_folga
                      existem (a pagina usa _resolver_ordem).

    Sem pelo menos um de faixas_cnae/cnaes/capital_min/dt_ini_antes_de a
    funcao levanta ValueError: `uf`/`situacao` sozinhos nao sao seletivos o
    bastante e o caminho correto e' `buscar_proximos`.

    Devolve (linhas, tem_mais, total):
      linhas   list[dict] com cnpj_completo, razao_social, municipio_nome,
               atividade_principal, situacao_descricao, dt_ini, latitude,
               longitude, km, maps_url etc. (campos de `buscar_proximos` +
               capital_social)
      tem_mais True quando total > limite
      total    total exato da consulta com os mesmos filtros (count(*) OVER ())
    """
    faixas = [(int(a), int(b)) for a, b in (faixas_cnae or [])
              if a is not None and b is not None]
    lista_cnaes = sorted({int(c) for c in (cnaes or []) if c is not None})
    if not faixas and not lista_cnaes and capital_min is None and dt_ini_antes_de is None:
        raise ValueError(
            'buscar_proximos_filtrado exige filtro de CNAE, data de abertura '
            'ou capital social; sem filtro seletivo use buscar_proximos.'
        )

    raio_km = max(RAIO_MIN_KM, min(RAIO_MAX_KM, int(raio_km)))
    limite = max(1, min(LIMITE_MAX_FILTRADO, int(limite)))

    # No modo endereco o bbox dos CEPs abre FOLGA_MAX_ORDEM_ENDERECO: um
    # endereco pode estar dentro do raio mesmo com o centroide do CEP um pouco
    # fora da caixa (a folga e' o desvio maximo endereco x centroide no CEP).
    raio_caixa = raio_km
    if usar_endereco:
        raio_caixa = min(RAIO_MAX_KM, raio_km + FOLGA_MAX_ORDEM_ENDERECO)
    caixa = caixa_geografica(latitude, longitude, raio_caixa)
    params = {
        'lat': latitude,
        'lon': longitude,
        'raio': raio_km,
        'limite_mais': limite + 1,
    }
    params.update(caixa)
    if usar_endereco:
        params['folga_max'] = FOLGA_MAX_ORDEM_ENDERECO

    # CEPs do bbox lidos antes e passados como array. O `= ANY(array)` usa o
    # indice de cep sem depender da estimativa do planner (que errava feio na
    # CTE: 147 x 3.275 CEPs em Curitiba/5 km).
    cur.execute(SQL_CEPS_CAIXA, caixa)
    ceps = [linha['cep'] for linha in cur.fetchall()]
    if not ceps:
        return [], False, 0
    if len(ceps) > MAX_CEPS_FILTRADO:
        raise ValueError(
            'A área de busca é grande demais para esse filtro: '
            f'{len(ceps):,} CEPs no raio. '
            'Reduza o raio para cerca de 10 km ou informe a UF/cidade.'
        )
    params['ceps'] = ceps

    filtros = []
    if matrizes:
        filtros.append('AND e.cd_matriz_filial = 1')
    if situacao not in (None, ''):
        params['situacao'] = int(situacao)
        filtros.append('AND e.cd_sit_cadastro = %(situacao)s')
    if uf:
        params['uf'] = str(uf).strip().upper()[:2]
        filtros.append('AND e.uf = %(uf)s')
    if dt_ini_antes_de is not None:
        params['dt_ini_antes_de'] = dt_ini_antes_de
        filtros.append('AND e.dt_ini < %(dt_ini_antes_de)s')

    # CNAE: faixas (divisao/secao) e/ou subclasses explicitas, ligadas por OR
    # dentro do mesmo filtro. Um OR de BETWEEN por divisao nao escala (medido:
    # 11,6 s com BETWEENs separados x 0,7 s com a faixa unica), por isso a
    # taxonomia ja entrega divisoes consecutivas num intervalo so.
    partes_cnae = []
    for indice, (inicio, fim) in enumerate(faixas):
        params[f'cnae_min_{indice}'] = inicio
        params[f'cnae_max_{indice}'] = fim
        partes_cnae.append(
            f'e.cnae_principal BETWEEN %(cnae_min_{indice})s '
            f'AND %(cnae_max_{indice})s'
        )
    if lista_cnaes:
        params['cnaes'] = lista_cnaes
        partes_cnae.append('e.cnae_principal = ANY(%(cnaes)s)')
    if partes_cnae:
        filtros.append('AND (' + ' OR '.join(partes_cnae) + ')')

    # Capital social entra como EXISTS correlacionado: o lookup por
    # cnpj_basico (PK) roda so nos candidatos. Medido em 25/11/2026
    # (Curitiba/10 km, transportadoras): 22 s com JOIN/hash contra c_empresas
    # (71 M) x 8,4 s com EXISTS.
    if capital_min is not None:
        params['capital_min'] = float(capital_min)
        filtros.append(
            'AND EXISTS (SELECT 1 FROM c_empresas emp '
            'WHERE emp.cnpj_basico = e.cnpj_basico '
            'AND emp.capital_social > %(capital_min)s)'
        )

    filtro_depois = ''
    if depois:
        try:
            depois_km, depois_cnpj = depois
            depois_km = float(depois_km)
            if len(str(depois_cnpj)) == 14:
                filtro_depois = (
                    'AND (e.km, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv) > '
                    '(%(depois_km)s, %(depois_basico)s, %(depois_ordem)s, '
                    '%(depois_dv)s)'
                )
                params.update({
                    'depois_km': depois_km,
                    'depois_basico': str(depois_cnpj)[:8],
                    'depois_ordem': str(depois_cnpj)[8:12],
                    'depois_dv': str(depois_cnpj)[12:14],
                })
        except (TypeError, ValueError):
            filtro_depois = ''

    if usar_endereco:
        campos_coordenada = (
            'CASE WHEN g.latitude IS NOT NULL '
            'AND coalesce(f.folga_km, 0) <= %(folga_max)s\n'
            '                    THEN g.latitude ELSE c.latitude END AS latitude,\n'
            '               CASE WHEN g.latitude IS NOT NULL '
            'AND coalesce(f.folga_km, 0) <= %(folga_max)s\n'
            '                    THEN g.longitude ELSE c.longitude END AS longitude,'
        )
        haversine_g = (
            '6371 * acos(least(1.0,\n'
            '                       cos(radians(%(lat)s)) * cos(radians(g.latitude)) *\n'
            '                       cos(radians(g.longitude) - radians(%(lon)s)) +\n'
            '                       sin(radians(%(lat)s)) * sin(radians(g.latitude))\n'
            '                   ))'
        )
        haversine_c = (
            '6371 * acos(least(1.0,\n'
            '                       cos(radians(%(lat)s)) * cos(radians(c.latitude)) *\n'
            '                       cos(radians(c.longitude) - radians(%(lon)s)) +\n'
            '                       sin(radians(%(lat)s)) * sin(radians(c.latitude))\n'
            '                   ))'
        )
        campos_km = (
            'CASE WHEN g.latitude IS NOT NULL '
            'AND coalesce(f.folga_km, 0) <= %(folga_max)s\n'
            f'                    THEN {haversine_g}\n'
            f'                    ELSE {haversine_c} END AS km'
        )
        campo_precisao_cand = (
            ",\n               CASE WHEN g.latitude IS NOT NULL "
            "AND coalesce(f.folga_km, 0) <= %(folga_max)s "
            "THEN g.precisao ELSE 'cep' END AS geo_precisao"
        )
        join_endereco = (
            'LEFT JOIN c_estab_geo g\n'
            '                 ON g.cnpj_completo = e.cnpj_basico || e.cnpj_ordem '
            '|| e.cnpj_dv\n'
            '          LEFT JOIN c_ceps_folga f ON f.cep = e.cep'
        )
        campo_precisao = ',\n           e.geo_precisao'
    else:
        campos_coordenada = 'c.latitude,\n               c.longitude,'
        campos_km = (
            '6371 * acos(least(1.0,\n'
            '                   cos(radians(%(lat)s)) * cos(radians(c.latitude)) *\n'
            '                   cos(radians(c.longitude) - radians(%(lon)s)) +\n'
            '                   sin(radians(%(lat)s)) * sin(radians(c.latitude))\n'
            '               )) AS km'
        )
        campo_precisao_cand = ''
        join_endereco = ''
        campo_precisao = ''

    sql = SQL_PROXIMOS_FILTRADO.format(
        filtros='\n           '.join(filtros),
        filtro_depois=filtro_depois,
        col_total=(', count(*) OVER () AS total_filtrado' if contar else ''),
        campos_coordenada=campos_coordenada,
        campos_km=campos_km,
        campo_precisao_cand=campo_precisao_cand,
        join_endereco=join_endereco,
        campo_precisao=campo_precisao,
    )

    cur.execute(f'SET LOCAL statement_timeout = {TIMEOUT_FILTRADO_MS}')
    cur.execute(sql, params)
    linhas = cur.fetchall()

    if contar:
        total = int(linhas[0]['total_filtrado']) if linhas else 0
        tem_mais = total > limite
    else:
        total = None
        tem_mais = len(linhas) > limite
    linhas = list(linhas[:limite])

    if linhas:
        # Razao social e capital vem de c_empresas so para as linhas da pagina
        # (o join no candidato inteiro materializaria texto de milhares de
        # empresas sem necessidade).
        basicos = sorted({linha['cnpj_basico'] for linha in linhas})
        cur.execute('SELECT cnpj_basico, razao_social, capital_social '
                    'FROM c_empresas WHERE cnpj_basico = ANY(%s)', [basicos])
        dados = {r['cnpj_basico']: r for r in cur.fetchall()}
        for linha in linhas:
            empresa = dados.get(linha['cnpj_basico']) or {}
            linha['razao_social'] = empresa.get('razao_social')
            linha['capital_social'] = empresa.get('capital_social')
            linha['maps_url'] = montar_maps_url(linha)

    return linhas, tem_mais, total


# ---------------------------------------------------------------------------
# Densidade do raio (mapa da /proximidade/)
# ---------------------------------------------------------------------------
# Mesma varredura da busca (bbox em c_ceps + haversine), mas agrupada em grade
# e somando a contagem por CEP (c_ceps_estab / c_ceps_estab_sit). Nao usa
# c_estabelecimentos: o mapa mostra concentracao, nao linha a linha, e o
# agregado por CEP e o que mantem o custo igual ao da propria busca.
# floor() em vez de round(): com parametro float o Postgres usa divisao em
# double precision, e round(double, int) nao existe (so round(numeric, int)).
SQL_DENSIDADE = """
    SELECT floor(c.latitude / %(grao)s) * %(grao)s AS lat,
           floor(c.longitude / %(grao)s) * %(grao)s AS lon,
           sum(ce.{coluna})::bigint AS n
      FROM c_ceps c
      JOIN {tabela} ce ON ce.cep = c.cep
     WHERE c.latitude BETWEEN %(lat_min)s AND %(lat_max)s
       AND c.longitude BETWEEN %(lon_min)s AND %(lon_max)s
       AND 6371 * acos(least(1.0,
              cos(radians(%(lat)s)) * cos(radians(c.latitude)) *
              cos(radians(c.longitude) - radians(%(lon)s)) +
              sin(radians(%(lat)s)) * sin(radians(c.latitude))
           )) <= %(raio)s
       {filtro_situacao}
     GROUP BY 1, 2
     ORDER BY n DESC
"""


def buscar_densidade(cur, latitude, longitude, raio_km, somente_matrizes,
                     situacao=None):
    """
    Celulas da grade com a contagem de empresas no raio, para o mapa.

    Devolve {'grao', 'celulas', 'truncado'} - celulas = [[lat, lon, n], ...],
    lat/lon arredondados em 5 casas (o suficiente para o marcador). None quando
    falta a tabela agregada correspondente (mesma degradacao do total: sem
    c_ceps_estab nao ha contagem pronta e o COUNT no raio custa 5-15 s).

    A grade comeca em 0.005 grau (~550 m) e engrossa se passar de
    MAX_CELULAS_DENSIDADE: o payload do mapa fica pequeno e as areas mais
    densas continuam visiveis. `truncado` avisa a tela quando nem o grao mais
    grosso coube (ficam so as celulas mais densas).
    """
    if situacao:
        if not tem_tabela_total(cur, 'c_ceps_estab_sit'):
            return None
        tabela = 'c_ceps_estab_sit'
        filtro_situacao = 'AND ce.cd_sit_cadastro = %(situacao)s'
    else:
        if not tem_tabela_total(cur, 'c_ceps_estab'):
            return None
        tabela = 'c_ceps_estab'
        filtro_situacao = ''

    coluna = 'n_matrizes' if somente_matrizes else 'n_total'
    params = {'lat': latitude, 'lon': longitude, 'raio': raio_km}
    params.update(caixa_geografica(latitude, longitude, raio_km))
    if situacao:
        params['situacao'] = situacao
    sql = SQL_DENSIDADE.format(tabela=tabela, coluna=coluna,
                               filtro_situacao=filtro_situacao)

    cur.execute("SET LOCAL statement_timeout = 15000")
    celulas = []
    grao = GRADES_DENSIDADE[0]
    for grao in GRADES_DENSIDADE:
        cur.execute(sql, {**params, 'grao': grao})
        celulas = [
            [round(float(linha['lat']), 5), round(float(linha['lon']), 5),
             int(linha['n'])]
            for linha in cur.fetchall() if linha['n']
        ]
        if len(celulas) <= MAX_CELULAS_DENSIDADE:
            break

    truncado = len(celulas) > MAX_CELULAS_DENSIDADE
    if truncado:
        celulas = celulas[:MAX_CELULAS_DENSIDADE]
    return {'grao': grao, 'celulas': celulas, 'truncado': truncado}


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
