# /home/neto/cnpj_interface/core/maps.py
"""Monta o link do Google Maps de um estabelecimento.

O Google nao posiciona o pino pelo numero do CEP: a coordenada de c_ceps e a do
CEP, isto e, a media do trecho que ele cobre (numa rua longa pode ficar
quilometros fora do numero) e, quando o CEP cobre o municipio inteiro
(c_ceps.generico), o centroide da area do municipio. Por isso a ordem e:

1. rua + numero -> endereco em texto (logradouro, numero, bairro, municipio,
   UF). O CEP fica de fora porque atrapalha a busca: sem hifen o Maps le
   "89204251" como numero de porta e nao acha a rua. No CEP generico o bairro
   tambem sai (bairro e CEP sao do municipio inteiro - na Receita vem "CENTRO"
   para tudo);
2. sem numero (ou sem rua) -> coordenada do CEP, lat,lon com 6 casas;
3. sem coordenada -> endereco em texto; sem rua, entra o CEP formatado.

Nao faz chamada de API: a URL 'maps/search/?api=1&query=' e publica e nao
exige chave. Quando o texto e 'lat,lon' o Google posiciona o pino na
coordenada; quando e texto livre, ele faz a busca pelo endereco.

O endereco da Receita e o fiscal e as vezes diverge do cadastro do Google
(CEP de trecho de rodovia, municipio do cadastro fiscal diferente do municipio
do mapa). Nesses casos nenhum texto de endereco posiciona certo e so o nome
acha o ponto: por isso existe `montar_maps_nome_url`, oferecido como segundo
link. Exemplo (Condor Auto Posto Atuba, CNPJ 01.184.650/0001-05): a Receita
diz 'RODOVIA BR-116, 850, ATUBA, COLOMBO/PR, CEP 83413-000', mas o CEP
83413-000 e do bairro Maua em Colombo e o Maps cadastra o posto como
'BR-116, 87 - Atuba, Curitiba/PR' (1,8 km do pino do endereco fiscal).
"""
from urllib.parse import quote_plus

BASE_MAPS = 'https://www.google.com/maps/search/?api=1&query='

# Limites geograficos do Brasil: coordenada fora daqui e erro de cadastro
LAT_MIN, LAT_MAX = -34.0, 6.5
LON_MIN, LON_MAX = -74.5, -32.0

# Numeros que nao ajudam a busca no Maps
NUMEROS_VAZIOS = {'0', 'SN', 'S/N', 'S/N.', 'S/Nº', 'SEM NUMERO'}


def montar_maps_url(estabelecimento):
    """Devolve a URL do Google Maps ou '' quando nao ha coordenada nem endereco."""
    if not estabelecimento:
        return ''

    logradouro = _texto(_campo(estabelecimento, 'logradouro'))
    numero = _numero_util(_campo(estabelecimento, 'numero'))
    generico = _generico(estabelecimento)

    # O numero do imovel so existe no texto: o pino do CEP e a media do trecho
    # que ele cobre (em rua longa fica quilometros fora) e, no CEP generico, o
    # centroide do municipio.
    if logradouro and (numero or generico):
        endereco = _endereco(estabelecimento, completo=not generico)
        if endereco:
            return BASE_MAPS + quote_plus(endereco)

    # Sem rua ou sem numero: o pino do CEP e o que sobra de mais preciso.
    coordenada = _coordenada(estabelecimento)
    if coordenada:
        return BASE_MAPS + coordenada

    endereco = _endereco(estabelecimento)
    if not endereco:
        return ''
    return BASE_MAPS + quote_plus(endereco)


def montar_maps_nome_url(estabelecimento):
    """Link que busca o estabelecimento pelo nome (fantasia, senao razao social).

    Segundo link, ao lado do de endereco: serve quando o endereco fiscal da
    Receita diverge do cadastro do Google e o texto cai em outro lugar. Sem
    nome ou sem municipio (o nome sozinho e ambiguo) devolve ''.
    """
    if not estabelecimento:
        return ''

    nome = (_texto(_campo(estabelecimento, 'nm_fantasia'))
            or _texto(_campo(estabelecimento, 'razao_social')))
    municipio = _municipio(estabelecimento)
    if len(nome) < 3 or not municipio:
        return ''

    uf = _texto(_campo(estabelecimento, 'uf'))
    partes = [nome, municipio, uf, 'Brasil' if uf else '']
    return BASE_MAPS + quote_plus(', '.join(parte for parte in partes if parte))


def _campo(estabelecimento, nome):
    if hasattr(estabelecimento, 'get'):
        return estabelecimento.get(nome)
    return getattr(estabelecimento, nome, None)


def _texto(valor):
    if valor is None:
        return ''
    return ' '.join(str(valor).split())


def _numero_util(valor):
    """Numero do imovel que ajuda a busca no Maps, ou ''.

    '0', 'SN', 'S/N' e afins nao posicionam nada e so atrapalham o texto.
    """
    numero = _texto(valor)
    if not numero or numero.upper() in NUMEROS_VAZIOS:
        return ''
    return numero


def _coordenada(estabelecimento):
    """'lat,lon' com 6 casas (o formato que o Maps entende) ou ''."""
    latitude = _numero(_campo(estabelecimento, 'latitude'))
    longitude = _numero(_campo(estabelecimento, 'longitude'))
    if latitude is None or longitude is None:
        return ''
    if not (LAT_MIN <= latitude <= LAT_MAX) or not (LON_MIN <= longitude <= LON_MAX):
        return ''
    return f'{latitude:.6f},{longitude:.6f}'


def _numero(valor):
    if valor is None or valor == '':
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _generico(estabelecimento):
    """CEP que cobre o municipio inteiro (coluna c_ceps.generico)."""
    return bool(_campo(estabelecimento, 'generico'))


def _cep_formatado(cep):
    """'89669000' -> '89669-000'.

    Sem o hifen o Maps le o CEP como um numero de porta e a busca falha
    ('... RUA X, 123, CENTRO, IPIRA, SC, 89669000, Brasil' nao resolve;
    '... RUA X, 123, IPIRA, SC' resolve).
    """
    texto = _texto(cep)
    digitos = ''.join(caractere for caractere in texto if caractere.isdigit())
    if len(digitos) != 8:
        return texto
    return f'{digitos[:5]}-{digitos[5:]}'


def _municipio(estabelecimento):
    """Municipio do estabelecimento (nome; endereco no exterior usa a cidade)."""
    return (_texto(_campo(estabelecimento, 'municipio_descricao'))
            or _texto(_campo(estabelecimento, 'nm_cidade_ext')))


def _endereco(estabelecimento, completo=True):
    """Endereco em texto. `completo=False` omite o bairro (CEP generico).

    O CEP so entra quando nao ha rua: com rua a busca fica melhor sem ele.
    """
    logradouro = _texto(_campo(estabelecimento, 'logradouro'))
    tipo = _texto(_campo(estabelecimento, 'tip_logradouro'))
    if tipo and logradouro and not logradouro.lower().startswith(tipo.lower()):
        logradouro = f'{tipo} {logradouro}'

    municipio = _municipio(estabelecimento)
    uf = _texto(_campo(estabelecimento, 'uf'))

    partes = [logradouro, _numero_util(_campo(estabelecimento, 'numero'))]
    if completo:
        partes.append(_texto(_campo(estabelecimento, 'bairro')))
    partes.extend([municipio, uf])
    if not logradouro:
        partes.append(_cep_formatado(_campo(estabelecimento, 'cep')))
    # 'Brasil' so entra quando o endereco e brasileiro (endereco no exterior
    # vem com nm_cidade_ext/cd_pais e sem UF)
    if uf:
        partes.append('Brasil')

    return ', '.join(parte for parte in partes if parte)
