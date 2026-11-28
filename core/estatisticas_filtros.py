# /home/neto/ProjetosPython/cnpj_interface/core/estatisticas_filtros.py
"""
Filtros da pagina /estatisticas/.

Le da tabela pre-agregada `c_estatisticas_totais` (categorias
`filtro_uf_*`) em vez de varrer os 74,4 M de estabelecimentos. Somente a
combinacao cidade + CNAE (que nao esta pre-agregada) usa consulta estreita
nas tabelas gigantes - ela e' rapida porque cidade e CNAE recortam muito.

Sem acentos no codigo; textos de tela com acento.
"""
import hashlib

from django.core.cache import cache
from psycopg2.extras import RealDictCursor

# Situacao cadastral (mesmos codigos de core.views.SITUACOES)
SITUACOES = [
    (2, 'ATIVA', 'Ativa'),
    (1, 'NULA', 'Nula'),
    (3, 'SUSPENSA', 'Suspensa'),
    (4, 'INAPTA', 'Inapta'),
    (8, 'BAIXADA', 'Baixada'),
]
SIT_LABEL = {cod: rotulo for cod, rotulo, _ in SITUACOES}
SIT_ROTULO = {cod: nome for cod, _, nome in SITUACOES}

PORTES = [('1', 'ME', 'Micro empresa'), ('3', 'EPP', 'Empresa de pequeno porte'),
          ('5', 'DEMAIS', 'Demais'), ('0', 'NAO_INFORMADO', 'Não informado')]
PORTE_LABEL = {cod: rotulo for cod, rotulo, _ in PORTES}
PORTE_NOME = {cod: nome for cod, _, nome in PORTES}

CAT_UF = 'filtro_uf_sit_porte'
CAT_MUN = 'filtro_uf_mun_sit_porte'
CAT_CNAE = 'filtro_uf_cnae_sit_porte'
CAT_ANO = 'filtro_uf_ano_sit_porte'

# Posicao de cada dimensao na chave 'uf|dim|sit|porte'.
POS_UF = 1
POS_DIM = 2
POS_SIT = 3
POS_PORTE = 4
POS_SIT_UF = 2
POS_PORTE_UF = 3


def _filtro_partes(filtros, pos_sit=POS_SIT, pos_porte=POS_PORTE):
    """(where, params) para as categorias de filtro.

    CAT_UF usa 'uf|sit|porte' (sit na posicao 2); as demais usam
    'uf|dimensao|sit|porte' (sit na posicao 3).
    """
    where = []
    params = []
    if filtros.get('uf'):
        where.append('chave LIKE %s')
        params.append(filtros['uf'] + '|%')
    if filtros.get('situacao'):
        where.append(f"split_part(chave, '|', {pos_sit}) = %s")
        params.append(str(filtros['situacao']))
    if filtros.get('porte'):
        where.append(f"split_part(chave, '|', {pos_porte}) = %s")
        params.append(str(filtros['porte']))
    return where, params


def _q(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchall()


def carregar_opcoes(conn, uf=None):
    """UFs, CNAEs e (quando ha UF) cidades para os selects."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT chave AS uf, valor
              FROM c_estatisticas_totais
             WHERE categoria = 'uf'
             ORDER BY chave
        """)
        ufs = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT cnae, nm_cnae FROM c_cnaes ORDER BY nm_cnae")
        cnaes = [dict(r) for r in cur.fetchall()]

        cidades = []
        if uf:
            cur.execute("""
                SELECT split_part(chave, '|', 2) AS codigo,
                       MAX(descricao) AS descricao
                  FROM c_estatisticas_totais
                 WHERE categoria = %s AND chave LIKE %s
                 GROUP BY 1
                 ORDER BY 2
            """, [CAT_MUN, uf + '|%'])
            for r in cur.fetchall():
                partes = (r['descricao'] or '').split('|')
                cidades.append({
                    'codigo': r['codigo'],
                    'nome': partes[0] if partes else '',
                    'uf': partes[1] if len(partes) > 1 else uf,
                })
    return {'ufs': ufs, 'cnaes': cnaes, 'cidades': cidades}


def _stats_agregadas(conn, filtros):
    """Estatisticas para filtros apenas de UF/situacao/porte."""
    where_uf, params_uf = _filtro_partes(filtros, POS_SIT_UF, POS_PORTE_UF)
    where_dim, params_dim = _filtro_partes(filtros)
    f_uf = (' AND ' + ' AND '.join(where_uf)) if where_uf else ''
    f_dim = (' AND ' + ' AND '.join(where_dim)) if where_dim else ''
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        def soma_uf():
            return _q(cur, f"""
                SELECT COALESCE(SUM(valor), 0) AS total
                  FROM c_estatisticas_totais
                 WHERE categoria = %s{f_uf}
            """, [CAT_UF] + params_uf)[0]['total']

        total = int(soma_uf())

        sit_rows = _q(cur, f"""
            SELECT split_part(chave, '|', {POS_SIT_UF}) AS cod,
                   SUM(valor) AS total
              FROM c_estatisticas_totais
             WHERE categoria = %s{f_uf}
             GROUP BY 1
        """, [CAT_UF] + params_uf)
        por_sit = {int(r['cod']): int(r['total']) for r in sit_rows}

        porte_rows = _q(cur, f"""
            SELECT split_part(chave, '|', {POS_PORTE_UF}) AS cod,
                   SUM(valor) AS total
              FROM c_estatisticas_totais
             WHERE categoria = %s{f_uf}
             GROUP BY 1
        """, [CAT_UF] + params_uf)
        por_porte = {r['cod']: int(r['total']) for r in porte_rows}

        # Top UFs respeitando situacao/porte (sem o filtro de UF, senao
        # so' apareceria a UF escolhida).
        where_top, params_top = [], []
        if filtros.get('situacao'):
            where_top.append(f"split_part(chave, '|', {POS_SIT_UF}) = %s")
            params_top.append(str(filtros['situacao']))
        if filtros.get('porte'):
            where_top.append(f"split_part(chave, '|', {POS_PORTE_UF}) = %s")
            params_top.append(str(filtros['porte']))
        f_top = (' AND ' + ' AND '.join(where_top)) if where_top else ''
        uf_rows = _q(cur, f"""
            SELECT split_part(chave, '|', {POS_UF}) AS uf, SUM(valor) AS total
              FROM c_estatisticas_totais
             WHERE categoria = %s{f_top}
             GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """, [CAT_UF] + params_top)

        cnae_rows = _q(cur, f"""
            SELECT split_part(chave, '|', {POS_DIM}) AS cnae,
                   MAX(descricao) AS nm_cnae,
                   SUM(valor) AS total
              FROM c_estatisticas_totais
             WHERE categoria = %s{f_dim}
             GROUP BY 1 ORDER BY 3 DESC LIMIT 10
        """, [CAT_CNAE] + params_dim)

        mun_rows = _q(cur, f"""
            SELECT split_part(chave, '|', {POS_UF}) AS uf,
                   split_part(chave, '|', {POS_DIM}) AS codigo,
                   MAX(descricao) AS descricao,
                   SUM(valor) AS total
              FROM c_estatisticas_totais
             WHERE categoria = %s{f_dim}
             GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 10
        """, [CAT_MUN] + params_dim)

        ano_rows = _q(cur, f"""
            SELECT split_part(chave, '|', {POS_DIM}) AS ano, SUM(valor) AS total
              FROM c_estatisticas_totais
             WHERE categoria = %s{f_dim}
             GROUP BY 1 ORDER BY 1 DESC LIMIT 15
        """, [CAT_ANO] + params_dim)

    top_ufs = [{'uf': r['uf'], 'total': int(r['total'])} for r in uf_rows]
    top_cnaes = [{'cnae_principal': r['cnae'], 'nm_cnae': r['nm_cnae'] or 'N/A',
                  'total': int(r['total'])} for r in cnae_rows]
    top_municipios = []
    for r in mun_rows:
        partes = (r['descricao'] or '').split('|')
        top_municipios.append({
            'codigo': r['codigo'],
            'nome_municipio': partes[0] if partes else 'NAO INFORMADO',
            'uf': r['uf'],
            'total': int(r['total']),
        })
    por_ano = [{'ano': int(r['ano']), 'total': int(r['total'])}
               for r in ano_rows]
    return {
        'total': total,
        'por_sit': por_sit,
        'por_porte': por_porte,
        'top_ufs': top_ufs,
        'top_cnaes': top_cnaes,
        'top_municipios': top_municipios,
        'por_ano': por_ano,
    }


def _stats_cidade(conn, filtros):
    """Cidade selecionada sem CNAE: KPIs/porte/situacao vem do agregado.

    Top CNAE e serie anual nao existem no nivel UF+municipio; sao calculados
    na consulta estreita quando nao ha filtro de porte (com porte a consulta
    voltaria a juntar 71 M e estourar o tempo).
    """
    uf = filtros.get('uf')
    mun = filtros['cidade']
    where = ["categoria = %s", "chave LIKE %s"]
    params = [CAT_MUN, f'%|{mun}|%']
    if uf:
        where.append("split_part(chave, '|', 1) = %s")
        params.append(uf)
    if filtros.get('situacao'):
        where.append(f"split_part(chave, '|', {POS_SIT}) = %s")
        params.append(str(filtros['situacao']))
    if filtros.get('porte'):
        where.append(f"split_part(chave, '|', {POS_PORTE}) = %s")
        params.append(str(filtros['porte']))
    filtro_sql = ' AND '.join(where)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT COALESCE(SUM(valor), 0) AS total FROM c_estatisticas_totais WHERE {filtro_sql}", params)
        total = int(cur.fetchone()['total'])
        cur.execute(f"""
            SELECT split_part(chave, '|', {POS_SIT}) AS cod,
                   SUM(valor) AS total
              FROM c_estatisticas_totais WHERE {filtro_sql} GROUP BY 1
        """, params)
        por_sit = {int(r['cod']): int(r['total']) for r in cur.fetchall()}
        cur.execute(f"""
            SELECT split_part(chave, '|', {POS_PORTE}) AS cod,
                   SUM(valor) AS total
              FROM c_estatisticas_totais WHERE {filtro_sql} GROUP BY 1
        """, params)
        por_porte = {r['cod']: int(r['total']) for r in cur.fetchall()}
        cur.execute(f"""
            SELECT split_part(chave, '|', {POS_UF}) AS uf, SUM(valor) AS total
              FROM c_estatisticas_totais WHERE {filtro_sql}
             GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """, params)
        top_ufs = [{'uf': r['uf'], 'total': int(r['total'])} for r in cur.fetchall()]
        cur.execute(f"""
            SELECT split_part(chave, '|', {POS_DIM}) AS codigo,
                   MAX(descricao) AS descricao, SUM(valor) AS total
              FROM c_estatisticas_totais WHERE {filtro_sql}
             GROUP BY 1 ORDER BY 3 DESC LIMIT 10
        """, params)
        top_municipios = []
        for r in cur.fetchall():
            partes = (r['descricao'] or '').split('|')
            top_municipios.append({
                'codigo': r['codigo'],
                'nome_municipio': partes[0] if partes else 'NAO INFORMADO',
                'uf': partes[1] if len(partes) > 1 else (uf or ''),
                'total': int(r['total']),
            })

    top_cnaes, por_ano = [], []
    rankings = not bool(filtros.get('porte'))
    if rankings:
        # Consulta estreita na cidade (sem join c_empresas).
        base = ['e.cd_matriz_filial = 1', 'e.municipio = %s']
        bparams = [mun]
        if filtros.get('situacao'):
            base.append('e.cd_sit_cadastro = %s')
            bparams.append(filtros['situacao'])
        bwhere = ' AND '.join(base)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SET statement_timeout = 15000')
            try:
                cur.execute(f"""
                    SELECT e.cnae_principal AS cnae, c.nm_cnae, COUNT(*) AS total
                      FROM c_estabelecimentos e
                      LEFT JOIN c_cnaes c ON c.cnae = e.cnae_principal
                     WHERE {bwhere}
                     GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10
                """, bparams)
                top_cnaes = [{'cnae_principal': str(r['cnae']),
                              'nm_cnae': r['nm_cnae'] or 'N/A',
                              'total': int(r['total'])} for r in cur.fetchall()]
                cur.execute(f"""
                    SELECT EXTRACT(YEAR FROM e.dt_ini)::integer AS ano,
                           COUNT(*) AS total
                      FROM c_estabelecimentos e
                     WHERE {bwhere} AND e.dt_ini IS NOT NULL
                     GROUP BY 1 ORDER BY 1 DESC LIMIT 15
                """, bparams)
                por_ano = [{'ano': int(r['ano']), 'total': int(r['total'])}
                           for r in cur.fetchall()]
            except Exception:
                rankings = False
            finally:
                try:
                    cur.execute('RESET statement_timeout')
                except Exception:
                    pass

    return {
        'total': total, 'por_sit': por_sit, 'por_porte': por_porte,
        'top_ufs': top_ufs, 'top_cnaes': top_cnaes,
        'top_municipios': top_municipios, 'por_ano': por_ano,
        'rankings_indisponiveis': not rankings,
    }


LIMITE_RANKING_CNAE = 300000


def _stats_cnae(conn, filtros):
    """Um ou mais CNAEs selecionados, sem cidade.

    O nivel UF+CNAE+situacao+porte responde KPIs, situacao, porte, top CNAE
    e top UF. Ranking de cidade/ano nao existe nesse nivel; so' vale calcular
    quando a selecao e' seletiva (senao a consulta passa de 20 s).
    """
    uf = filtros.get('uf')
    cnaes = [int(c) for c in (filtros.get('cnaes') or [])]
    if not cnaes:
        return _stats_agregadas(conn, filtros)
    marcadores = ', '.join(['%s'] * len(cnaes))
    where = ["categoria = %s", f"split_part(chave, '|', {POS_DIM}) IN ({marcadores})"]
    params = [CAT_CNAE] + [str(c) for c in cnaes]
    if uf:
        where.append("split_part(chave, '|', 1) = %s")
        params.append(uf)
    if filtros.get('situacao'):
        where.append(f"split_part(chave, '|', {POS_SIT}) = %s")
        params.append(str(filtros['situacao']))
    if filtros.get('porte'):
        where.append(f"split_part(chave, '|', {POS_PORTE}) = %s")
        params.append(str(filtros['porte']))
    filtro_sql = ' AND '.join(where)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT COALESCE(SUM(valor), 0) AS total FROM c_estatisticas_totais WHERE {filtro_sql}", params)
        total = int(cur.fetchone()['total'])
        cur.execute(f"""
            SELECT split_part(chave, '|', {POS_SIT}) AS cod, SUM(valor) AS total
              FROM c_estatisticas_totais WHERE {filtro_sql} GROUP BY 1
        """, params)
        por_sit = {int(r['cod']): int(r['total']) for r in cur.fetchall()}
        cur.execute(f"""
            SELECT split_part(chave, '|', {POS_PORTE}) AS cod, SUM(valor) AS total
              FROM c_estatisticas_totais WHERE {filtro_sql} GROUP BY 1
        """, params)
        por_porte = {r['cod']: int(r['total']) for r in cur.fetchall()}
        cur.execute(f"""
            SELECT split_part(chave, '|', {POS_UF}) AS uf, SUM(valor) AS total
              FROM c_estatisticas_totais WHERE {filtro_sql}
             GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """, params)
        top_ufs = [{'uf': r['uf'], 'total': int(r['total'])} for r in cur.fetchall()]
        cur.execute(f"""
            SELECT split_part(chave, '|', {POS_DIM}) AS cnae,
                   MAX(descricao) AS nm_cnae, SUM(valor) AS total
              FROM c_estatisticas_totais WHERE {filtro_sql}
             GROUP BY 1 ORDER BY 3 DESC LIMIT 50
        """, params)
        top_cnaes = [{'cnae_principal': str(r['cnae']),
                      'nm_cnae': r['nm_cnae'] or 'N/A',
                      'total': int(r['total'])} for r in cur.fetchall()]

    top_municipios, por_ano = [], []
    rankings = not (filtros.get('porte') or total > LIMITE_RANKING_CNAE)
    if rankings:
        base = ['e.cd_matriz_filial = 1',
                f'e.cnae_principal IN ({marcadores})']
        bparams = list(cnaes)
        if uf:
            base.append('e.uf = %s')
            bparams.append(uf)
        if filtros.get('situacao'):
            base.append('e.cd_sit_cadastro = %s')
            bparams.append(filtros['situacao'])
        bwhere = ' AND '.join(base)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SET statement_timeout = 15000')
            try:
                cur.execute(f"""
                    SELECT e.municipio AS codigo, m.municipio AS nome,
                           e.uf, COUNT(*) AS total
                      FROM c_estabelecimentos e
                      LEFT JOIN c_municipios m ON m.cd_municipio = e.municipio
                     WHERE {bwhere}
                     GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 10
                """, bparams)
                top_municipios = [{
                    'codigo': str(r['codigo']),
                    'nome_municipio': r['nome'] or 'NAO INFORMADO',
                    'uf': r['uf'],
                    'total': int(r['total']),
                } for r in cur.fetchall()]
                cur.execute(f"""
                    SELECT EXTRACT(YEAR FROM e.dt_ini)::integer AS ano,
                           COUNT(*) AS total
                      FROM c_estabelecimentos e
                     WHERE {bwhere} AND e.dt_ini IS NOT NULL
                     GROUP BY 1 ORDER BY 1 DESC LIMIT 15
                """, bparams)
                por_ano = [{'ano': int(r['ano']), 'total': int(r['total'])}
                           for r in cur.fetchall()]
            except Exception:
                rankings = False
            finally:
                try:
                    cur.execute('RESET statement_timeout')
                except Exception:
                    pass

    return {
        'total': total, 'por_sit': por_sit, 'por_porte': por_porte,
        'top_ufs': top_ufs, 'top_cnaes': top_cnaes,
        'top_municipios': top_municipios, 'por_ano': por_ano,
        'rankings_indisponiveis': not rankings,
    }


def _where_dinamico(filtros):
    """WHERE para a consulta estreita (cidade e/ou CNAE selecionados)."""
    where = ['e.cd_matriz_filial = 1']
    params = []
    # Codigo de municipio ja e' unico: nao repete a UF (com UF o planner
    # troca o indice bom por um scan de 20 s - medido em Joinville/SC).
    if filtros.get('cidade'):
        where.append('e.municipio = %s')
        params.append(filtros['cidade'])
    elif filtros.get('uf'):
        where.append('e.uf = %s')
        params.append(filtros['uf'])
    cnaes = filtros.get('cnaes') or []
    if cnaes:
        where.append('e.cnae_principal IN (' + ', '.join(['%s'] * len(cnaes)) + ')')
        params.extend(int(c) for c in cnaes)
    if filtros.get('situacao'):
        where.append('e.cd_sit_cadastro = %s')
        params.append(filtros['situacao'])
    if filtros.get('porte'):
        where.append('emp.porte_empresa = %s')
        params.append(int(filtros['porte']))
    return ' AND '.join(where), params


def _stats_dinamicas(conn, filtros):
    """Cidade e/ou CNAE selecionados: consulta estreita nas tabelas."""
    where, params = _where_dinamico(filtros)
    # So' junta c_empresas quando o porte entra no WHERE (ou na distribuicao
    # por porte). Sem isso a contagem de uma cidade virava join de 290 mil
    # linhas (~15 s).
    join_emp = ('JOIN c_empresas emp ON emp.cnpj_basico = e.cnpj_basico'
                if filtros.get('porte') else '')
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SET statement_timeout = 15000')
        try:
            cur.execute(f"SELECT COUNT(*) AS total FROM c_estabelecimentos e {join_emp} WHERE {where}", params)
            total = int(cur.fetchone()['total'])

            cur.execute(f"""
                SELECT e.cd_sit_cadastro AS cod, COUNT(*) AS total
                  FROM c_estabelecimentos e {join_emp}
                 WHERE {where} GROUP BY 1
            """, params)
            por_sit = {int(r['cod']): int(r['total']) for r in cur.fetchall()}

            cur.execute(f"""
                SELECT emp.porte_empresa AS cod, COUNT(*) AS total
                  FROM c_estabelecimentos e
                  JOIN c_empresas emp ON emp.cnpj_basico = e.cnpj_basico
                 WHERE {where} GROUP BY 1
            """, params)
            por_porte = {}
            for r in cur.fetchall():
                cod = r['cod']
                chave = ('1' if cod == 1 else '3' if cod == 3 else '5' if cod == 5 else '0')
                por_porte[chave] = int(r['total'])

            cur.execute(f"""
                SELECT e.cnae_principal AS cnae, c.nm_cnae, COUNT(*) AS total
                  FROM c_estabelecimentos e {join_emp}
                  LEFT JOIN c_cnaes c ON c.cnae = e.cnae_principal
                 WHERE {where}
                 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10
            """, params)
            top_cnaes = [{'cnae_principal': str(r['cnae']),
                          'nm_cnae': r['nm_cnae'] or 'N/A',
                          'total': int(r['total'])} for r in cur.fetchall()]

            cur.execute(f"""
                SELECT e.municipio AS codigo, m.municipio AS nome,
                       e.uf, COUNT(*) AS total
                  FROM c_estabelecimentos e {join_emp}
                  LEFT JOIN c_municipios m ON m.cd_municipio = e.municipio
                 WHERE {where}
                 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 10
            """, params)
            top_municipios = [{
                'codigo': str(r['codigo']),
                'nome_municipio': r['nome'] or 'NAO INFORMADO',
                'uf': r['uf'],
                'total': int(r['total']),
            } for r in cur.fetchall()]

            cur.execute(f"""
                SELECT e.uf, COUNT(*) AS total
                  FROM c_estabelecimentos e {join_emp}
                 WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT 10
            """, params)
            top_ufs = [{'uf': r['uf'], 'total': int(r['total'])}
                       for r in cur.fetchall()]

            cur.execute(f"""
                SELECT EXTRACT(YEAR FROM e.dt_ini)::integer AS ano,
                       COUNT(*) AS total
                  FROM c_estabelecimentos e {join_emp}
                 WHERE {where} AND e.dt_ini IS NOT NULL
                 GROUP BY 1 ORDER BY 1 DESC LIMIT 15
            """, params)
            por_ano = [{'ano': int(r['ano']), 'total': int(r['total'])}
                       for r in cur.fetchall()]
        finally:
            try:
                cur.execute('RESET statement_timeout')
            except Exception:
                pass

    return {
        'total': total,
        'por_sit': por_sit,
        'por_porte': por_porte,
        'top_ufs': top_ufs,
        'top_cnaes': top_cnaes,
        'top_municipios': top_municipios,
        'por_ano': por_ano,
    }


def consultar(conn, filtros):
    """Devolve as estatisticas do recorte no formato do template."""
    bruto = '|'.join(f'{k}={v}' for k, v in sorted(filtros.items())
                     if v not in (None, '', []))
    chave_cache = 'estat_filtros:' + hashlib.md5(bruto.encode('utf-8')).hexdigest()
    em_cache = cache.get(chave_cache)
    if em_cache is not None:
        return em_cache
    if filtros.get('cnaes') and filtros.get('cidade'):
        base = _stats_dinamicas(conn, filtros)
    elif filtros.get('cnaes'):
        base = _stats_cnae(conn, filtros)
    elif filtros.get('cidade'):
        base = _stats_cidade(conn, filtros)
    else:
        base = _stats_agregadas(conn, filtros)

    total = base['total']
    situacoes = []
    for cod, rotulo, nome in SITUACOES:
        qtd = base['por_sit'].get(cod, 0)
        situacoes.append({'situacao': rotulo, 'rotulo': nome, 'total': qtd,
                          'codigo': cod})
    portes = []
    for cod, rotulo, nome in PORTES:
        portes.append({'porte': nome.upper() if nome else rotulo,
                       'codigo': cod,
                       'total': base['por_porte'].get(cod, 0)})

    totais = {
        'total_matrizes': total,
        'total_ativas': base['por_sit'].get(2, 0),
        'total_inaptas': base['por_sit'].get(4, 0),
        'total_baixadas': base['por_sit'].get(8, 0),
        'total_suspensas': base['por_sit'].get(3, 0),
        'total_nulas': base['por_sit'].get(1, 0),
        'total_ufs': 1 if filtros.get('uf') else 27,
    }
    resultado = {
        'totais': totais,
        'situacoes': situacoes,
        'portes': portes,
        'top_ufs': ([{'uf': filtros['uf'], 'total': total}]
                    if filtros.get('uf') else base['top_ufs']),
        'top_cnaes': base['top_cnaes'],
        'top_municipios': base['top_municipios'],
        'por_ano': base['por_ano'],
        'simples_mei': None,
        'top_naturezas': [],
        'socios': None,
        'rankings_indisponiveis': base.get('rankings_indisponiveis', False),
    }
    cache.set(chave_cache, resultado, 300)
    return resultado


def filtros_do_request(request):
    """Le e valida os filtros da querystring."""
    def inteiro(nome):
        valor = (request.GET.get(nome) or '').strip()
        return int(valor) if valor.isdigit() else None

    uf = (request.GET.get('uf') or '').strip().upper()
    if len(uf) != 2 or not uf.isalpha():
        uf = None
    cidade = inteiro('cidade')
    cnaes = []
    for bruto in request.GET.getlist('cnae'):
        for parte in str(bruto).split(','):
            parte = parte.strip()
            if parte.isdigit():
                cnaes.append(int(parte))
    cnaes = sorted(set(cnaes))
    situacao = inteiro('situacao')
    if situacao not in SIT_LABEL:
        situacao = None
    porte = (request.GET.get('porte') or '').strip()
    if porte not in PORTE_LABEL:
        porte = None
    return {
        'uf': uf, 'cidade': cidade, 'cnaes': cnaes,
        'situacao': situacao, 'porte': porte,
    }
