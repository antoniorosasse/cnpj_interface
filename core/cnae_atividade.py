# cnpj_interface/core/cnae_atividade.py
"""
Resolucao de atividade para os DOIS caminhos (SQL e proximidade).

Ordem (a mesma do prompt do SQL):
1. KB de categorias (core.cnae_categorias) -> CNAEs/faixas exatas;
2. codigo CNAE digitado;
3. ILIKE em c_cnaes.nm_cnae (frase normalizada/singularizada);
4. vetor (embedding <=> %s) com LIMIT 2, so quando o ILIKE nao achar nada.

A proximidade precisava ser deterministica; agora ela usa o mesmo fallback
textual/vetorial do SQL, mas sempre devolve uma lista FECHADA de CNAEs para o
motor filtrado. Sem resultado, o chamador continua pedindo esclarecimento.
"""
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

STOPWORDS = {
    'a', 'as', 'o', 'os', 'de', 'da', 'das', 'do', 'dos', 'e', 'em', 'no',
    'na', 'nos', 'nas', 'para', 'com', 'por', 'tipo', 'tipos', 'ramo',
    'setor', 'segmento', 'empresa', 'empresas', 'estabelecimento',
    'estabelecimentos', 'atividade', 'atividades', 'que', 'onde', 'perto',
    'proximo', 'proximas', 'proximos', 'num', 'numa', 'raio', 'km',
}

GENERICOS = {
    'comercio', 'servico', 'servicos', 'industria', 'industrias', 'loja',
    'lojas', 'negocio', 'negocios',
}

LIMITE_ILIKE = 30
LIMITE_VETOR = 2
SCORE_MIN_VETOR = 0.20


def _normalizar(texto):
    decomposto = unicodedata.normalize('NFKD', str(texto or ''))
    limpo = ''.join(c for c in decomposto if not unicodedata.combining(c))
    limpo = re.sub(r'[^a-z0-9]+', ' ', limpo.lower())
    return re.sub(r'\s+', ' ', limpo).strip()


def _singular(token):
    """Singular simples de PT-BR para a frase de ILIKE (nao e' stemmer)."""
    if len(token) <= 4 or token.endswith(('s', 'es')):
        if token.endswith('oes') and len(token) > 5:
            return token[:-3] + 'ao'
        if token.endswith('aes') and len(token) > 5:
            return token[:-3] + 'ao'
        if token.endswith('is') and len(token) > 5:
            return token[:-2] + 'l'
        if token.endswith('ns') and len(token) > 5:
            return token[:-2] + 'm'
        if token.endswith('s') and not token.endswith('ss') and len(token) > 4:
            return token[:-1]
    return token


def _frases_busca(texto):
    """Frases candidatas para o ILIKE, da mais especifica para a mais curta."""
    normalizado = _normalizar(texto)
    tokens = [t for t in normalizado.split() if t not in STOPWORDS]
    if not tokens:
        return []
    tokens = [_singular(t) for t in tokens]
    frases = []
    completa = ' '.join(tokens)
    if completa:
        frases.append(completa)
    # Se a frase tem palavras genericas, tenta sem elas ("loja de energia solar"
    # -> "energia solar"), mas nunca deixa uma palavra generica sozinha.
    uteis = [t for t in tokens if t not in GENERICOS]
    if uteis and ' '.join(uteis) != completa:
        frases.append(' '.join(uteis))
    # NAO cai para token individual: "energia solar" nao pode casar
    # "energia eletrica" no ILIKE; o SQL manda o vetor nesse caso.
    return list(dict.fromkeys(f for f in frases if f))


def _par_cnae(linha):
    if isinstance(linha, dict):
        return int(linha['cnae']), linha['nm_cnae'], linha.get('distancia')
    return int(linha[0]), linha[1], (linha[2] if len(linha) > 2 else None)


def _consultar_ilike(cur, frases):
    for frase in frases:
        try:
            cur.execute(
                'SELECT cnae, nm_cnae FROM c_cnaes '
                "WHERE nm_cnae ILIKE %s ORDER BY cnae LIMIT %s",
                (f'%{frase}%', LIMITE_ILIKE),
            )
            linhas = cur.fetchall()
        except Exception:
            logger.exception('Falha no fallback ILIKE de CNAE')
            return None
        if linhas:
            # ILIKE lexical: score fixo alto; nao e' distancia vetorial.
            return [(c, n, 0.10) for c, n, _ in (_par_cnae(l) for l in linhas)]
    return []


def _embedder_padrao(texto):
    from chat.services.embedding_service import gerar_vetor
    return gerar_vetor(texto)


def _consultar_vetor(cur, texto, embedder):
    try:
        vetor = embedder(texto)
    except Exception:
        logger.exception('Falha ao gerar embedding da atividade')
        return []
    if not vetor:
        return []
    try:
        cur.execute(
            'SELECT cnae, nm_cnae, '
            '       (embedding <=> %s::halfvec(384)) AS distancia '
            'FROM c_cnaes WHERE embedding IS NOT NULL '
            'ORDER BY distancia LIMIT %s',
            (vetor, LIMITE_VETOR),
        )
        linhas = cur.fetchall()
    except Exception:
        logger.exception('Falha no fallback vetorial de CNAE')
        return []
    return [_par_cnae(l) for l in linhas]


def _alias_publicado(texto):
    """Alias aprovado no admin (KB dinamica), se existir.

    Tenta a expressao inteira e, se ela tiver " e ", cada termo individual
    (o chat pode juntar varias atividades).
    """
    try:
        from chat.models import CnaeAlias
    except Exception:
        return None
    candidatos = [_normalizar(texto)]
    if ' e ' in (texto or ''):
        candidatos += [_normalizar(p) for p in str(texto).split(' e ')]
    for termo in dict.fromkeys(c for c in candidatos if c):
        try:
            alias = CnaeAlias.objects.filter(
                termo_normalizado=termo, ativo=True).first()
        except Exception:
            logger.exception('Falha ao consultar alias CNAE publicado')
            return None
        if alias:
            return alias
    return None


def resolver_atividade(texto, cur=None, embedder=None):
    """
    Resolve uma expressao de atividade para {categorias, faixas, cnaes,
    rotulo, fonte, nao_reconhecidas}.

    `cur` e' obrigatorio para os fallbacks ILIKE/vetor; sem ele so' a KB e o
    codigo CNAE respondem. `embedder` e' injetavel nos testes.
    """
    from core.cnae_categorias import resolver_filtro_cnae

    original = (texto or '').strip()
    filtro = resolver_filtro_cnae(original)
    if filtro['categorias']:
        return {
            'categorias': list(filtro['categorias']),
            'faixas': sorted({tuple(f) for f in filtro['faixas']}),
            'cnaes': sorted({int(c) for c in filtro['cnaes']}),
            'rotulo': filtro['rotulo'],
            'fonte': 'kb',
            'score': 1.0,
            'nao_reconhecidas': [],
        }

    # Codigo CNAE digitado no lugar da categoria (mesma tolerancia da pagina).
    digitos = ''.join(c for c in original if c.isdigit())
    if len(digitos) in (6, 7):
        return {
            'categorias': [],
            'faixas': [],
            'cnaes': [int(digitos)],
            'rotulo': f'CNAE {digitos}',
            'fonte': 'codigo',
            'score': 1.0,
            'nao_reconhecidas': [],
        }

    alias = _alias_publicado(original)
    if alias and alias.cnaes:
        return {
            'categorias': list(alias.categorias or []),
            'faixas': [],
            'cnaes': sorted({int(c) for c in alias.cnaes}),
            'rotulo': alias.termo,
            'fonte': 'alias',
            'score': 1.0,
            'nao_reconhecidas': [],
        }

    vazio = {
        'categorias': [],
        'faixas': [],
        'cnaes': [],
        'rotulo': '',
        'fonte': 'nenhuma',
        'score': None,
        'nao_reconhecidas': [original] if original else [],
    }
    if not original or cur is None:
        return vazio

    # Fallback 1: mesmo ILIKE do prompt do SQL.
    linhas = _consultar_ilike(cur, _frases_busca(original))
    if linhas is None:
        # Cursor invalido/erro de banco: nao adianta tentar o vetor.
        return vazio
    fonte = 'iliKe'
    if not linhas:
        # Fallback 2: mesmo vetor do prompt do SQL (LIMIT 2).
        linhas = _consultar_vetor(cur, original, embedder or _embedder_padrao)
        fonte = 'vetor'
    if not linhas:
        return vazio

    # Deduplica e devolve uma lista FECHADA para o motor filtrado.
    vistos = {}
    scores = []
    for cnae, nome, distancia in linhas:
        vistos.setdefault(cnae, nome)
        if distancia is not None:
            scores.append(max(0.0, 1.0 - float(distancia)))
    score = max(scores) if scores else (0.9 if fonte == 'iliKe' else None)
    if fonte == 'vetor' and (score is None or score < SCORE_MIN_VETOR):
        return vazio
    return {
        'categorias': [],
        'faixas': [],
        'cnaes': sorted(vistos),
        'rotulo': original,
        'fonte': fonte,
        'score': score,
        'nao_reconhecidas': [],
        'cnaes_nomes': vistos,
    }
