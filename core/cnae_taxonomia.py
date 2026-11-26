# cnpj_interface/core/cnae_taxonomia.py
"""
Taxonomia CNAE 2.3 (secoes e divisoes) derivada do codigo da subclasse.

A tabela `c_cnaes` guarda so `cnae` (integer), `nm_cnae` e `embedding`: nao
existe coluna de secao/divisao/grupo/classe. A hierarquia e derivavel do
proprio codigo, mas com dois detalhes que ja causaram erro:

  - a subclasse CNAE 2.3 tem 7 digitos, porem as divisoes 01 a 09 comecam com
    zero e o campo e integer: "0111301" esta gravado como 111301 (6 digitos).
    Por isso a divisao e sempre calculada sobre o codigo normalizado com
    `normalizar_cnae()` (7 caracteres), nunca sobre `cnae // 100000`.
  - as faixas em inteiro continuam funcionando sem normalizar porque o zero a
    esquerda some: divisao 01 = 100000..199999, divisao 10 = 1000000..1099999.
    Use `faixa_divisao()` / `faixas_divisoes()` para montar filtro SQL.

Fonte: CNAE 2.3 (IBGE/CONCLA). Usado pela KB de categorias de negocio
(`core/cnae_categorias.py`) e pelo motor de proximidade filtrado
(`core/proximidade.py::buscar_proximos_filtrado`).

Sem acentos no codigo; os nomes oficiais (exibidos ao usuario) tem acento.
"""

# Secao: letra -> nome oficial e intervalo de divisoes (inclusive).
SECOES = {
    'A': {'nome': 'Agricultura, pecuária, produção florestal, pesca e aquicultura',
          'divisoes': (1, 3)},
    'B': {'nome': 'Indústrias extrativas', 'divisoes': (5, 9)},
    'C': {'nome': 'Indústrias de transformação', 'divisoes': (10, 33)},
    'D': {'nome': 'Eletricidade e gás', 'divisoes': (35, 35)},
    'E': {'nome': 'Água, esgoto, atividades de gestão de resíduos e descontaminação',
          'divisoes': (36, 39)},
    'F': {'nome': 'Construção', 'divisoes': (41, 43)},
    'G': {'nome': 'Comércio; reparação de veículos automotores e motocicletas',
          'divisoes': (45, 47)},
    'H': {'nome': 'Transporte, armazenagem e correio', 'divisoes': (49, 53)},
    'I': {'nome': 'Alojamento e alimentação', 'divisoes': (55, 56)},
    'J': {'nome': 'Informação e comunicação', 'divisoes': (58, 63)},
    'K': {'nome': 'Atividades financeiras, de seguros e serviços relacionados',
          'divisoes': (64, 66)},
    'L': {'nome': 'Atividades imobiliárias', 'divisoes': (68, 68)},
    'M': {'nome': 'Atividades profissionais, científicas e técnicas',
          'divisoes': (69, 75)},
    'N': {'nome': 'Atividades administrativas e serviços complementares',
          'divisoes': (77, 82)},
    'O': {'nome': 'Administração pública, defesa e seguridade social',
          'divisoes': (84, 84)},
    'P': {'nome': 'Educação', 'divisoes': (85, 85)},
    'Q': {'nome': 'Saúde humana e serviços sociais', 'divisoes': (86, 88)},
    'R': {'nome': 'Artes, cultura, esporte e recreação', 'divisoes': (90, 93)},
    'S': {'nome': 'Outras atividades de serviços', 'divisoes': (94, 96)},
    'T': {'nome': 'Serviços domésticos', 'divisoes': (97, 97)},
    'U': {'nome': 'Organismos internacionais e outras instituições extraterritoriais',
          'divisoes': (99, 99)},
}

# Divisao: numero -> nome oficial (CNAE 2.3).
DIVISOES = {
    1: 'Agricultura, pecuária e serviços relacionados',
    2: 'Produção florestal',
    3: 'Pesca e aquicultura',
    5: 'Extração de carvão mineral',
    6: 'Extração de petróleo e gás natural',
    7: 'Extração de minerais metálicos',
    8: 'Extração de minerais não-metálicos',
    9: 'Atividades de apoio à extração de minerais',
    10: 'Fabricação de produtos alimentícios',
    11: 'Fabricação de bebidas',
    12: 'Fabricação de produtos do fumo',
    13: 'Fabricação de produtos têxteis',
    14: 'Confecção de artigos do vestuário e acessórios',
    15: 'Preparação de couros e fabricação de artefatos de couro, artigos para viagem e calçados',
    16: 'Fabricação de produtos de madeira',
    17: 'Fabricação de celulose, papel e produtos de papel',
    18: 'Impressão e reprodução de gravações',
    19: 'Fabricação de coque, de produtos derivados do petróleo e de biocombustíveis',
    20: 'Fabricação de produtos químicos',
    21: 'Fabricação de produtos farmoquímicos e farmacêuticos',
    22: 'Fabricação de produtos de borracha e de material plástico',
    23: 'Fabricação de produtos de minerais não-metálicos',
    24: 'Metalurgia',
    25: 'Fabricação de produtos de metal, exceto máquinas e equipamentos',
    26: 'Fabricação de equipamentos de informática, produtos eletrônicos e ópticos',
    27: 'Fabricação de máquinas, aparelhos e materiais elétricos',
    28: 'Fabricação de máquinas e equipamentos',
    29: 'Fabricação de veículos automotores, reboques e carrocerias',
    30: 'Fabricação de outros equipamentos de transporte, exceto veículos automotores',
    31: 'Fabricação de móveis',
    32: 'Fabricação de produtos diversos',
    33: 'Manutenção, reparação e instalação de máquinas e equipamentos',
    35: 'Eletricidade e gás',
    36: 'Captação, tratamento e distribuição de água',
    37: 'Esgoto e atividades relacionadas',
    38: 'Coleta, tratamento e disposição de resíduos; recuperação de materiais',
    39: 'Descontaminação e outros serviços de gestão de resíduos',
    41: 'Construção de edifícios',
    42: 'Obras de infraestrutura',
    43: 'Serviços especializados para construção',
    45: 'Comércio e reparação de veículos automotores e motocicletas',
    46: 'Comércio por atacado, exceto veículos automotores e motocicletas',
    47: 'Comércio varejista',
    49: 'Transporte terrestre',
    50: 'Transporte aquaviário',
    51: 'Transporte aéreo',
    52: 'Armazenamento e atividades auxiliares dos transportes',
    53: 'Correio e outras atividades de entrega',
    55: 'Alojamento',
    56: 'Alimentação',
    58: 'Edição e edição integrada à impressão',
    59: 'Atividades cinematográficas, produção de vídeos e de programas de televisão, gravação de som e edição de música',
    60: 'Atividades de rádio e de televisão',
    61: 'Telecomunicações',
    62: 'Atividades dos serviços de tecnologia da informação',
    63: 'Atividades de prestação de serviços de informação',
    64: 'Atividades de serviços financeiros',
    65: 'Seguros, resseguros, previdência complementar e planos de saúde',
    66: 'Atividades auxiliares dos serviços financeiros, seguros, previdência complementar e planos de saúde',
    68: 'Atividades imobiliárias',
    69: 'Atividades jurídicas, de contabilidade e de auditoria',
    70: 'Atividades de sedes de empresas e de consultoria em gestão empresarial',
    71: 'Serviços de arquitetura e engenharia; testes e análises técnicas',
    72: 'Pesquisa e desenvolvimento científico',
    73: 'Publicidade e pesquisa de mercado',
    74: 'Outras atividades profissionais, científicas e técnicas',
    75: 'Atividades veterinárias',
    77: 'Aluguéis de máquinas, equipamentos e outros bens',
    78: 'Seleção, agenciamento e locação de mão-de-obra',
    79: 'Agências de viagens, operadores turísticos e serviços de reservas',
    80: 'Atividades de vigilância, segurança e investigação',
    81: 'Serviços para edifícios e atividades paisagísticas',
    82: 'Serviços de escritório, de apoio administrativo e outros serviços prestados às empresas',
    84: 'Administração pública, defesa e seguridade social',
    85: 'Educação',
    86: 'Atividades de atenção à saúde humana',
    87: 'Atividades de atenção à saúde humana integradas com assistência social, prestadas em residências coletivas e particulares',
    88: 'Serviços de assistência social sem alojamento',
    90: 'Atividades artísticas, criativas e de espetáculos',
    91: 'Atividades de bibliotecas, arquivos, museus e outras atividades culturais',
    92: 'Atividades de exploração de jogos de azar e apostas',
    93: 'Atividades esportivas e de recreação e lazer',
    94: 'Atividades de organizações associativas',
    95: 'Reparação e manutenção de equipamentos de informática e comunicação e de objetos pessoais e domésticos',
    96: 'Outras atividades de serviços pessoais',
    97: 'Serviços domésticos',
    99: 'Organismos internacionais e outras instituições extraterritoriais',
}

# Grao do intervalo de uma divisao em inteiro: divisao * 100000 + 99999.
GRAO_DIVISAO = 100000


def normalizar_cnae(cnae):
    """
    Codigo CNAE como string de 7 caracteres ("0111301", "2411300").

    Aceita int, string com ou sem pontuacao ("2411-3/00") e None.
    Devolve '' quando nao ha digitos.
    """
    if cnae is None:
        return ''
    digitos = ''.join(c for c in str(cnae) if c.isdigit())
    return digitos.zfill(7) if digitos else ''


def divisao(cnae):
    """Numero da divisao (1..99) ou None quando o codigo nao tem digitos."""
    normalizado = normalizar_cnae(cnae)
    if len(normalizado) < 2:
        return None
    return int(normalizado[:2])


def secao(cnae):
    """Letra da secao (A..U) ou '' quando a divisao nao esta na taxonomia."""
    numero = divisao(cnae)
    if numero is None:
        return ''
    for letra, dados in SECOES.items():
        inicio, fim = dados['divisoes']
        if inicio <= numero <= fim:
            return letra
    return ''


def faixa_divisao(numero):
    """
    Intervalo (min, max) em inteiro que cobre uma divisao.

    Ex.: 24 -> (2400000, 2499999); 5 -> (500000, 599999); 1 -> (100000, 199999).
    """
    numero = int(numero)
    inicio = numero * GRAO_DIVISAO
    return (inicio, inicio + GRAO_DIVISAO - 1)


def faixas_divisoes(divisoes):
    """
    Une divisoes consecutivas em intervalos (min, max) para o filtro SQL.

    Ex.: [24, 25] -> [(2400000, 2599999)];
         range(10, 34) -> [(1000000, 3399999)];
         [5, 9] -> [(500000, 599999)].
    """
    numeros = sorted({int(d) for d in (divisoes or [])})
    if not numeros:
        return []
    faixas = []
    inicio = atual = numeros[0]
    for numero in numeros[1:]:
        if numero == atual + 1:
            atual = numero
            continue
        faixas.append((inicio * GRAO_DIVISAO, atual * GRAO_DIVISAO + GRAO_DIVISAO - 1))
        inicio = atual = numero
    faixas.append((inicio * GRAO_DIVISAO, atual * GRAO_DIVISAO + GRAO_DIVISAO - 1))
    return faixas


def nome_divisao(numero):
    """Nome oficial da divisao (com acento) ou rotulo generico."""
    try:
        numero = int(numero)
    except (TypeError, ValueError):
        return ''
    return DIVISOES.get(numero, f'Divisão {numero:02d}')


def nome_secao(letra):
    """Nome oficial da secao (com acento) ou '' quando nao existe."""
    dados = SECOES.get((letra or '').strip().upper())
    return dados['nome'] if dados else ''


def divisoes_da_secao(letra):
    """Lista de divisoes de uma secao (ex.: 'C' -> [10, ..., 33])."""
    dados = SECOES.get((letra or '').strip().upper())
    if not dados:
        return []
    inicio, fim = dados['divisoes']
    return list(range(inicio, fim + 1))
