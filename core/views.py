# /home/neto/cnpj_interface/core/views.py
from typing import Any
import psycopg2
from psycopg2.extras import RealDictCursor
from django.conf import settings
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_POST

from core.log_acesso import (
    TIPO_DETALHE_EMPRESA,
    TIPO_LISTA_EMPRESAS,
    registrar_acesso,
)
from core.maps import montar_maps_nome_url, montar_maps_url
from core.proximidade import (
    LIMITE_PAGINA,
    PAGINA_ALEM_DO_FIM,
    PAGINA_SEM_TABELA,
    RAIO_MAX_KM,
    RAIO_MIN_KM,
    RAIO_PADRAO_KM,
    buscar_proximos,
    cursor_do_token,
    cursor_para_pagina,
    hist_de_texto,
    hist_para_texto,
    km_para_texto,
    resolver_origem,
    token_cursor,
)
from core.telefone import montar_whatsapp

import math
import time
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def consultar_com_salto(cur, conn, sql, params, offset, log=None):
    """
    Executa a consulta da pagina aplicando o OFFSET do salto "Ir para".

    Keyset nao pula N paginas, entao o salto absoluto usa OFFSET - e OFFSET
    so e barato quando a ordenacao sai do indice. Com filtros que exigem
    bitmap scan + Sort o salto estoura o `statement_timeout` de 8 s (ver a
    view). Nesse caso a consulta e repetida SEM o OFFSET (a transacao e
    desfeita, o que tambem devolve o plano padrao) e a tela mostra a primeira
    pagina com aviso, em vez de ficar vazia com um numero de pagina que nao
    abriu.

    Devolve (linhas, recusou_o_salto).
    """
    if not offset:
        cur.execute(sql, params)
        return cur.fetchall(), False

    try:
        cur.execute(sql + " OFFSET %s", list(params) + [offset])
        return cur.fetchall(), False
    except Exception as e:
        if log:
            log.info("Salto por OFFSET recusado (offset=%s): %s", offset, e)
        conn.rollback()
        cur.execute(sql, params)
        return cur.fetchall(), True


def get_postgres_connection():
    """Retorna uma conexão direta com PostgreSQL"""
    try:
        return psycopg2.connect(
            dbname=settings.PG_DB,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD,
            host=settings.PG_HOST,
            port=settings.PG_PORT,
            connect_timeout=5,   # 5s maximo pra conectar
        )
    except Exception as e:
        logger.error("Erro ao conectar ao PostgreSQL: %s", e)
        raise

LISTA_UFS = [
    ('AC', 'AC - Acre'),
    ('AL', 'AL - Alagoas'),
    ('AP', 'AP - Amapá'),
    ('AM', 'AM - Amazonas'),
    ('BA', 'BA - Bahia'),
    ('CE', 'CE - Ceará'),
    ('DF', 'DF - Distrito Federal'),
    ('ES', 'ES - Espírito Santo'),
    ('GO', 'GO - Goiás'),
    ('MA', 'MA - Maranhão'),
    ('MT', 'MT - Mato Grosso'),
    ('MS', 'MS - Mato Grosso do Sul'),
    ('MG', 'MG - Minas Gerais'),
    ('PA', 'PA - Pará'),
    ('PB', 'PB - Paraíba'),
    ('PR', 'PR - Paraná'),
    ('PE', 'PE - Pernambuco'),
    ('PI', 'PI - Piauí'),
    ('RJ', 'RJ - Rio de Janeiro'),
    ('RN', 'RN - Rio Grande do Norte'),
    ('RS', 'RS - Rio Grande do Sul'),
    ('RO', 'RO - Rondônia'),
    ('RR', 'RR - Roraima'),
    ('SC', 'SC - Santa Catarina'),
    ('SP', 'SP - São Paulo'),
    ('SE', 'SE - Sergipe'),
    ('TO', 'TO - Tocantins'),
    ('EX', 'EX - Exterior'),
]

# ============================================================
# MAPEAMENTO CORRETO DAS SITUAÇÕES
# ============================================================
SITUACOES = {
    'NULA': 1,
    'ATIVA': 2,
    'SUSPENSA': 3,
    'INAPTA': 4,
    'BAIXADA': 8,
}

# Rotulos do select de situacao (lista de empresas e /proximidade/), na ordem
# em que aparecem na tela.
SITUACOES_ROTULOS = [
    ('ATIVA', 'Ativa'),
    ('NULA', 'Nula'),
    ('SUSPENSA', 'Suspensa'),
    ('INAPTA', 'Inapta'),
    ('BAIXADA', 'Baixada'),
]


def lista_empresas(request):
    """
    Lista empresas - ordenacao por CNPJ ou RAZAO SOCIAL (keyset)

    Filtros: razao social, nome fantasia (texto livre), CNPJ, UF, cidade
    (codigo TOM em c_municipios_geo), situacao, Simples e MEI.
    Otimizado com CTE MATERIALIZED para ordenação por razão social
    """
    aviso = None
    
    # ============================================================
    # 1. PARÂMETROS
    # ============================================================
    razao_social = request.GET.get('razao_social', '').strip().upper()
    nome_fantasia = request.GET.get('nome_fantasia', '').strip().upper()
    cnpj = request.GET.get('cnpj', '').strip().replace('.', '').replace('/', '').replace('-', '')
    uf = request.GET.get('uf', '').strip().upper()
    situacao = request.GET.get('situacao', '').strip().upper()
    simples = request.GET.get('simples', '').strip()
    mei = request.GET.get('mei', '').strip()
    municipio = request.GET.get('municipio', '').strip()
    ordem = request.GET.get('ordem', 'cnpj')
    # A ordenacao por nome fantasia foi removida: o keyset por nm_fantasia
    # nunca foi confiavel (NULLS FIRST/DESC trocavam a ordem entre paginas).
    if ordem not in ('cnpj', 'razao_social'):
        ordem = 'cnpj'
   
    try:
        page = int(request.GET.get('page', 1))
    except (ValueError, TypeError):
        page = 1
    
    limit = 20
    last_cnpj = request.GET.get('last_cnpj', '')
    last_razao = request.GET.get('last_razao', '')
    direction = request.GET.get('dir', 'next')
    is_last_page = request.GET.get('last_page', '') == 'true'
    # Se está na página 1, força início limpo (elimina ponteiros reversos residuais)
    if page <= 1:
        direction = 'next'
        last_cnpj = ''
        last_razao = ''

    # ============================================================
    # 1.1 LOG DE ACESSO (lista de empresas)
    # Nunca quebra a consulta; deduplicado por 10 min no core/log_acesso.py
    # ============================================================
    filtros_usados = [
        f'{campo}={valor}'
        for campo, valor in (
            ('razao_social', razao_social),
            ('nome_fantasia', nome_fantasia),
            ('cnpj', cnpj),
            ('uf', uf),
            ('situacao', situacao),
            ('simples', simples),
            ('mei', mei),
            ('municipio', municipio),
        )
        if valor
    ]
    registrar_acesso(
        request,
        TIPO_LISTA_EMPRESAS,
        '; '.join(filtros_usados) if filtros_usados else 'sem filtros',
    )
    
    conn = None
    empresas = []
    total = 0
    total_paginas = 0
    has_next = False
    last_cnpj_next = ''
    first_cnpj_prev = ''
    last_razao_next = ''
    first_razao_prev = ''
    lista_municipios: list[Any] = []
    
    sql: str = ""
    params: list[Any] = []
    count_sql: str = ""
    count_params: list[Any] = []
    offset_salto = 0
    salto_recusado = 0
    
    try:
        conn = get_postgres_connection()
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            
            # Quando nome_fantasia for preenchido, a filtros_sql tem um UNION
            # (matriz + filial). Sem isso, o planner escolhe Merge Join
            # (le 36.5M de linhas, 22.9s). Desabilitando mergejoin/hashjoin,
            # forca Nested Loop (178ms).
            # SET LOCAL vale so ate o fim da transacao (nao afeta outras conexoes).
            if nome_fantasia:
                cur.execute("SET LOCAL enable_mergejoin = off")
                cur.execute("SET LOCAL enable_hashjoin = off")
            
            # ============================================================
            # 2. FILTROS
            # ============================================================
            filtros_sql = ""
            filtros_params: list[Any] = []

            # ------------------------------------------------------------
            # 2.0 CIDADE
            # c_municipios_geo liga o codigo TOM (e.municipio) ao nome/UF.
            # O select so carrega as cidades da UF escolhida (max. 645 em MG).
            # ------------------------------------------------------------
            municipio_id = int(municipio) if municipio.isdigit() else None
            if municipio_id and not uf:
                cur.execute('SELECT uf FROM c_municipios_geo WHERE cd_municipio = %s',
                            [municipio_id])
                linha_uf = cur.fetchone()
                uf = (linha_uf['uf'] or '') if linha_uf else ''
            if uf:
                cur.execute('SELECT cd_municipio, nome FROM c_municipios_geo '
                            'WHERE uf = %s ORDER BY nome', [uf])
                lista_municipios = cur.fetchall()

            if razao_social:
                filtros_sql += " AND emp.razao_social ILIKE %s"
                filtros_params.append(f'%{razao_social}%')
            
            if nome_fantasia:
                # Busca o fantasia na matriz OU em qualquer filial (via cnpj_basico).
                # Usa UNION (nao OR) porque o OR faz o planner rodar a subquery
                # por linha (7776ms); o UNION materializa uma vez (68ms).
                # Parte A: matrizes com fantasia na matriz (GIN parcial).
                # Parte B: matrizes cujo cnpj_basico tem fantasia em qualquer
                #          estabelecimento (GIN novo, sem filtro de matriz).
                filtros_sql += """ AND e.cnpj_basico IN (
                    SELECT cnpj_basico FROM c_estabelecimentos
                    WHERE cd_matriz_filial = 1 AND nm_fantasia ILIKE %s
                    UNION
                    SELECT cnpj_basico FROM c_estabelecimentos
                    WHERE nm_fantasia ILIKE %s
                )"""
                filtros_params.append(f'%{nome_fantasia}%')
                filtros_params.append(f'%{nome_fantasia}%')
            
            if cnpj:
                if len(cnpj) == 14:
                    # Usa o índice composto em vez de concatenar em runtime
                    filtros_sql += " AND e.cnpj_basico = %s AND e.cnpj_ordem = %s AND e.cnpj_dv = %s"
                    filtros_params.extend([cnpj[:8], cnpj[8:12], cnpj[12:14]])
                elif len(cnpj) == 8:
                    filtros_sql += " AND e.cnpj_basico = %s"
                    filtros_params.append(cnpj)
                else:
                    filtros_sql += " AND e.cnpj_basico LIKE %s"
                    filtros_params.append(f'{cnpj}%')  # Busca prefixada aproveita B-tree
            
            if uf:
                # O select de UF sempre vem preenchido; e.municipio e o codigo TOM
                filtros_sql += " AND e.uf = %s"
                filtros_params.append(uf.upper())

            if municipio_id:
                filtros_sql += " AND e.municipio = %s"
                filtros_params.append(municipio_id)
            
            if situacao and situacao in SITUACOES:
                filtros_sql += " AND e.cd_sit_cadastro = %s"
                filtros_params.append(SITUACOES[situacao])
            
            if simples == 'S':
                filtros_sql += " AND EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_simples = 'S')"
            elif simples == 'N':
                filtros_sql += " AND NOT EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_simples = 'S')"
            
            if mei == 'S':
                filtros_sql += " AND EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_mei = 'S')"
            elif mei == 'N':
                filtros_sql += " AND NOT EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_mei = 'S')"
            
            # ============================================================
            # 4. CONTAGEM OTIMIZADA (VIA METADADOS OU QUERY LIMPA)
            # ============================================================
            total = None

            # Caso 1: Sem filtros -> Total geral direto da tabela de totais (0.2 ms)
            filtros_ativos = [razao_social, nome_fantasia, cnpj, uf, situacao, simples, mei, municipio_id]
            if not any(filtros_ativos):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'geral' AND chave = 'total_matrizes'
                """)
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Caso 2: Filtro apenas por UF
            elif uf and not any([razao_social, nome_fantasia, cnpj, situacao, simples, mei, municipio_id]):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'uf' AND chave = %s
                """, [uf])
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Caso 3: Filtro apenas por Situação
            elif situacao and not any([razao_social, nome_fantasia, cnpj, uf, simples, mei, municipio_id]):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'situacao' AND chave = %s
                """, [situacao])
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Caso 4: Filtro apenas por Simples
            elif simples == 'S' and not any([razao_social, nome_fantasia, cnpj, uf, situacao, mei, municipio_id]):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'simples' AND chave = 'S'
                """)
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Caso 5: Filtro apenas por MEI
            elif mei == 'S' and not any([razao_social, nome_fantasia, cnpj, uf, situacao, simples, municipio_id]):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'mei' AND chave = 'S'
                """)
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Fallback para buscas combinadas: com cache de 15 min e proteção de timeout de 800ms
            total_estimado = False
            if total is None:
                from django.core.cache import cache
                cache_key = f"cnt_{razao_social}_{nome_fantasia}_{cnpj}_{uf}_{situacao}_{simples}_{mei}_{municipio_id}"
                total_em_cache = cache.get(cache_key)

                if total_em_cache is not None:
                    total = total_em_cache
                else:
                    join_emp = "LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico" if razao_social else ""
                    count_sql = f"""
                        SELECT COUNT(*) as total
                        FROM c_estabelecimentos e
                        {join_emp}
                        WHERE e.cd_matriz_filial = 1
                    """ + filtros_sql
                    count_params = list(filtros_params)

                    try:
                        cur.execute("SET LOCAL statement_timeout = 800")
                        cur.execute(count_sql, count_params)
                        resultado = cur.fetchone()
                        total = resultado['total'] if resultado else 0
                        cache.set(cache_key, total, 900)  # Salva por 15 min
                    except Exception:
                        # Se passou de 800ms (busca massiva), faz rollback limpo e não trava o usuário
                        conn.rollback()
                        total = 100000  # Define piso estimado para não ficar zero
                        total_estimado = True

            if total_estimado:
                total_paginas = 5000
            else:
                total_paginas = (total + limit - 1) // limit if total and total > 0 else 0

            # Campo "Ir para": pagina pedida sem cursor. Keyset nao pula N
            # paginas, entao este modo usa OFFSET (ver comentario abaixo).
            if not total_estimado and total_paginas and page > total_paginas:
                aviso = (f'A página {page} passa do fim ({total_paginas} '
                         'páginas); mostrando a última.')
                page = total_paginas
            if not total_paginas:
                page = 1
            pular = (page > 1 and not last_cnpj and not last_razao
                     and direction != 'prev' and not is_last_page)
            offset_salto = (page - 1) * limit if pular else 0

            # Salto por OFFSET custa caro sem a ordenacao saindo do indice: com
            # bitmap scan + Sort, uf=PR na pagina 5.000 passou de 25 s (medido
            # em 06/11/2026); forcando index scan fica ~1 s. Timeout de 8 s
            # protege o worker quando os filtros nao usam o indice de ordenacao
            # (ex.: razao social + UF), caso em que o salto e recusado.
            if pular:
                cur.execute("SET LOCAL statement_timeout = 8000")
                for opcao in ('enable_bitmapscan', 'enable_seqscan',
                              'enable_hashjoin', 'enable_mergejoin'):
                    cur.execute(f"SET LOCAL {opcao} = off")
            
            # ============================================================
            # 5. PAGINAÇÃO (KEYSET POR ORDENAÇÃO)
            # ============================================================
            resultados: list[Any] = []
            
# ============================================================
            # ----- ORDENAÇÃO POR RAZÃO SOCIAL (INNER JOIN + KEYSET) -----
            # ============================================================
            if ordem == 'razao_social':
                # Toda matriz possui cadastro em c_empresas: INNER JOIN permite usar idx_empresas_razao_cnpj
                sql = """
                    SELECT 
                        e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
                        emp.razao_social,
                        e.nm_fantasia,
                        e.logradouro,
                        e.numero,
                        e.bairro,
                        e.cep,
                        e.uf,
                        e.email,
                        e.ddd_1 || e.tel_1 AS telefone_1,
                        e.cnae_principal,
                        c.nm_cnae AS atividade_principal,
                        e.cd_sit_cadastro,
                        e.dt_ini AS data_inicio_atividade,
                        s.op_simples,
                        s.op_mei,
                        mg.nome AS municipio_nome,
                        CASE 
                            WHEN e.cd_sit_cadastro = 1 THEN 'NULA'
                            WHEN e.cd_sit_cadastro = 2 THEN 'ATIVA'
                            WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
                            WHEN e.cd_sit_cadastro = 4 THEN 'INAPTA'
                            WHEN e.cd_sit_cadastro = 8 THEN 'BAIXADA'
                            ELSE 'OUTRA'    
                        END AS situacao_descricao
                    FROM c_estabelecimentos e
                    INNER JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                    LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                    LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
                    LEFT JOIN c_municipios_geo mg ON mg.cd_municipio = e.municipio
                    WHERE e.cd_matriz_filial = 1
                """ + filtros_sql
                params = list(filtros_params)

                if is_last_page:
                    sql += " ORDER BY emp.razao_social DESC, e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC LIMIT %s"
                    params.append(limit)
                    cur.execute(sql, params)
                    resultados = cur.fetchall()
                    resultados = list(reversed(resultados))
                    has_next = False

                elif direction == 'prev':
                    if last_razao:
                        sql += " AND emp.razao_social < %s"
                        params.append(last_razao)

                    sql += " ORDER BY emp.razao_social DESC, e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC LIMIT %s"
                    params.append(limit + 1)
                    cur.execute(sql, params)
                    resultados = cur.fetchall()

                    if len(resultados) > limit:
                        has_next = True
                        resultados = resultados[:limit]
                    resultados = list(reversed(resultados))

                else:
                    if last_razao:
                        sql += " AND emp.razao_social > %s"
                        params.append(last_razao)

                    sql += " ORDER BY emp.razao_social, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv LIMIT %s"
                    params.append(limit + 1)
                    resultados, recusou = consultar_com_salto(
                        cur, conn, sql, params, offset_salto, logger)
                    if recusou:
                        salto_recusado = page

                    if len(resultados) > limit:
                        has_next = True
                        resultados = resultados[:-1]

                if resultados:
                    last_razao_next = resultados[-1]['razao_social'] or ''
                    first_razao_prev = resultados[0]['razao_social'] or ''
                    last_cnpj_next = resultados[-1]['cnpj_completo']
                    first_cnpj_prev = resultados[0]['cnpj_completo']
            
            # ============================================================
            # ----- ORDENAÇÃO POR CNPJ (PADRÃO) -----
            # ============================================================
            else:
                sql = """
                    SELECT 
                        e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
                        emp.razao_social,
                        e.nm_fantasia,
                        e.logradouro,
                        e.numero,
                        e.bairro,
                        e.cep,
                        e.uf,
                        e.email,
                        e.ddd_1 || e.tel_1 AS telefone_1,
                        e.cnae_principal,
                        c.nm_cnae AS atividade_principal,
                        e.cd_sit_cadastro,
                        e.dt_ini AS data_inicio_atividade,
                        s.op_simples,
                        s.op_mei,
                        mg.nome AS municipio_nome,
                        CASE 
                            WHEN e.cd_sit_cadastro = 1 THEN 'NULA'
                            WHEN e.cd_sit_cadastro = 2 THEN 'ATIVA'
                            WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
                            WHEN e.cd_sit_cadastro = 4 THEN 'INAPTA'
                            WHEN e.cd_sit_cadastro = 8 THEN 'BAIXADA'
                            ELSE 'OUTRA'    
                        END AS situacao_descricao
                    FROM c_estabelecimentos e
                    LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                    LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                    LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
                    LEFT JOIN c_municipios_geo mg ON mg.cd_municipio = e.municipio
                    WHERE e.cd_matriz_filial = 1
                """ + filtros_sql
                params = list(filtros_params)
                
                if is_last_page:
                    sql += " ORDER BY e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC"
                    sql += " LIMIT %s"
                    params.append(limit)
                    cur.execute(sql, params)
                    resultados = cur.fetchall()
                    resultados = list(reversed(resultados))
                    has_next = False
                    
                elif direction == 'prev':
                    if last_cnpj and len(last_cnpj) == 14:
                        sql += " AND (e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv) < (%s, %s, %s)"
                        params.extend([last_cnpj[:8], last_cnpj[8:12], last_cnpj[12:14]])
                    elif last_cnpj:
                        sql += " AND (e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) < %s"
                        params.append(last_cnpj)

                    sql += " ORDER BY e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC"
                    sql += " LIMIT %s"
                    params.append(limit + 1)
                    cur.execute(sql, params)
                    resultados = cur.fetchall()
                    
                    if len(resultados) > limit:
                        has_next = True
                        resultados = resultados[:limit]
                    resultados = list(reversed(resultados))
                    
                else:
                    if last_cnpj and len(last_cnpj) == 14:
                        sql += " AND (e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv) > (%s, %s, %s)"
                        params.extend([last_cnpj[:8], last_cnpj[8:12], last_cnpj[12:14]])
                    elif last_cnpj:
                        sql += " AND (e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) > %s"
                        params.append(last_cnpj)

                    sql += " ORDER BY e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv"
                    sql += " LIMIT %s"
                    params.append(limit + 1)
                    resultados, recusou = consultar_com_salto(
                        cur, conn, sql, params, offset_salto, logger)
                    if recusou:
                        salto_recusado = page
                    
                    if len(resultados) > limit:
                        has_next = True
                        resultados = resultados[:-1]
                
                if resultados:
                    last_cnpj_next = resultados[-1]['cnpj_completo']
                    first_cnpj_prev = resultados[0]['cnpj_completo']
            
            if pular:
                # Volta ao plano normal para as consultas seguintes (filiais e
                # cidades), que sao pequenas e se beneficiam do plano padrao.
                for opcao in ('enable_bitmapscan', 'enable_seqscan',
                              'enable_hashjoin', 'enable_mergejoin'):
                    cur.execute(f"RESET {opcao}")
                cur.execute("RESET statement_timeout")

            # ============================================================
            # 6. QUANTAS FILIAIS CADA EMPRESA DA PAGINA TEM
            # Uma query para os 20 cnpj_basico da pagina (indice
            # idx_estabelecimentos_cnpj_basico): ~2 ms. Evita um LATERAL
            # por linha dentro das tres queries de listagem.
            # ============================================================
            filiais_por_basico: dict[str, int] = {}
            if resultados:
                basicos = sorted({linha['cnpj_completo'][:8] for linha in resultados})
                cur.execute("""
                    SELECT cnpj_basico,
                           count(*) FILTER (WHERE cd_matriz_filial <> 1) AS filiais
                      FROM c_estabelecimentos
                     WHERE cnpj_basico = ANY(%s)
                     GROUP BY cnpj_basico
                """, [basicos])
                filiais_por_basico = {
                    linha['cnpj_basico']: linha['filiais'] for linha in cur.fetchall()
                }

            empresas = list(resultados)
            for linha in empresas:
                # RealDictRow e dict: da para acrescentar a coluna calculada
                linha['total_filiais'] = filiais_por_basico.get(linha['cnpj_completo'][:8], 0)

            if salto_recusado:
                # O OFFSET estourou o timeout: a consulta ja foi repetida sem
                # ele (consultar_com_salto), entao a tela mostra a primeira
                # pagina - com o total certo - e explica o que houve.
                aviso = (f'A página {salto_recusado} fica longe demais para '
                         'esses filtros (a consulta passou de 8 s). Mostrando '
                         'a primeira página: use as setas ou refine a busca.')
                page = 1

    except Exception as e:
        logger.exception("Erro ao listar empresas: %s", e)
        if not offset_salto:
            total = 0
        else:
            # Nem o salto nem a repeticao sem OFFSET funcionaram: mantem o
            # total ja contado e avisa, em vez de zerar a tela.
            aviso = (f'Não foi possível abrir a página {page} agora. Use as '
                     'setas a partir da primeira página ou refine a busca.')
            page = 1
        empresas = []
        has_next = False
        last_cnpj_next = ''
        first_cnpj_prev = ''
        last_razao_next = ''
        first_razao_prev = ''
    finally:
        if conn:
            conn.close()
    
    context = {
        'empresas': empresas,
        'razao_social': razao_social,
        'nome_fantasia': nome_fantasia,
        'cnpj': cnpj,
        'uf': uf,
        'situacao': situacao,
        'simples': simples,
        'mei': mei,
        'municipio': municipio,
        'ordem': ordem,
        'total': total,
        'total_paginas': total_paginas,
        'page': page,
        'has_next': has_next,
        'has_previous': page > 1,
        'last_cnpj': last_cnpj_next,
        'first_cnpj': first_cnpj_prev,
        'last_razao': last_razao_next,
        'first_razao': first_razao_prev,
        'lista_ufs': LISTA_UFS,
        'lista_municipios': lista_municipios,
        'total_estimado': total_estimado,
        'aviso': aviso or ('💡 Use os filtros para buscar empresas específicas' if not any([razao_social, nome_fantasia, cnpj, uf, situacao, simples, mei, municipio_id]) else None),
    }
    
    return render(request, 'core/lista.html', context)


def proximidade(request):
    """
    Estabelecimentos num raio de X km de um ponto.

    A origem pode ser CNPJ, CEP, cidade ("Joinville/SC"), endereco
    ("Rua do Principe, 500, Joinville/SC") ou coordenada ("-26.3,-48.8");
    ver core/proximidade.py. A paginacao e keyset por (km, cnpj): o avanco usa
    o cursor da ultima linha e o retorno usa a pilha de cursores em `hist`,
    entao nao existe OFFSET.
    """
    origem_txt = request.GET.get('origem', '').strip()
    try:
        raio = int(float(request.GET.get('raio', RAIO_PADRAO_KM)))
    except (TypeError, ValueError):
        raio = RAIO_PADRAO_KM
    raio = max(RAIO_MIN_KM, min(RAIO_MAX_KM, raio))

    # Situacao cadastral: mesma nomenclatura e mesmos codigos da lista de
    # empresas (SITUACOES). Valor desconhecido e ignorado.
    situacao = request.GET.get('situacao', '').strip().upper()
    if situacao not in SITUACOES:
        situacao = ''

    # Primeira visita (sem querystring) ja vem com "somente matrizes" marcado
    somente_matrizes = ('matrizes' in request.GET) if request.GET else True

    depois_km = request.GET.get('depois_km', '').strip()
    depois_cnpj = request.GET.get('depois_cnpj', '').strip()
    hist = hist_de_texto(request.GET.get('hist', ''))

    # Pagina pedida no campo "Ir para" (salto absoluto): o cursor nao vem da
    # querystring, e calculado em cursor_para_pagina.
    try:
        pagina_pedida = int(request.GET.get('page', '').strip())
    except (TypeError, ValueError):
        pagina_pedida = 0
    # Paginas ja navegadas antes desta (so existe depois de um salto): mantem
    # "Pagina X" correto enquanto o usuario usa as setas.
    try:
        pbase = int(request.GET.get('pbase', '').strip())
    except (TypeError, ValueError):
        pbase = 0
    pbase = max(0, pbase)

    # Cursor do keyset que abriu esta pagina (None = primeira pagina).
    depois = None
    if depois_km and len(depois_cnpj) == 14:
        try:
            depois = (float(depois_km), depois_cnpj)
        except ValueError:
            depois = None

    origem = None
    erro = None
    aviso = None
    resultados: list[Any] = []
    tem_mais = False
    total = None

    if origem_txt:
        conn = None
        try:
            conn = get_postgres_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                origem, erro = resolver_origem(cur, origem_txt)
                if origem:
                    if pagina_pedida > 1:
                        salto = cursor_para_pagina(
                            cur, origem['latitude'], origem['longitude'],
                            raio, somente_matrizes, pagina_pedida,
                            situacao=SITUACOES[situacao] if situacao else None,
                        )
                        if isinstance(salto, tuple):
                            depois = salto
                            hist = []
                            pbase = pagina_pedida - 1
                        else:
                            # Sem cursor, o salto nao vale: volta ao comeco.
                            hist = []
                            pbase = 0
                            if salto == PAGINA_ALEM_DO_FIM:
                                aviso = (f'A página {pagina_pedida} passa do '
                                         'fim desta busca; mostrando a '
                                         'primeira página.')
                            elif salto == PAGINA_SEM_TABELA:
                                aviso = ('Sem a tabela de contagem por CEP não '
                                         'dá para ir direto a uma página.')
                    resultados, tem_mais, total = buscar_proximos(
                        cur, origem['latitude'], origem['longitude'],
                        raio, somente_matrizes, depois,
                        situacao=SITUACOES[situacao] if situacao else None,
                    )
        except Exception as e:
            logger.exception("Erro na busca por proximidade: %s", e)
            erro = 'Não foi possível consultar a base agora. Tente novamente.'
        finally:
            if conn:
                conn.close()

        registrar_acesso(
            request,
            TIPO_LISTA_EMPRESAS,
            f'proximidade: {origem_txt} raio={raio}km '
            f'{"matrizes" if somente_matrizes else "todos"}'
            f'{(" situacao=" + situacao) if situacao else ""}',
        )

    # Paginacao keyset: pagina atual = len(hist) + 1; cada token do hist e o
    # cursor que abriu uma pagina anterior ('-' = comeco). `pbase` e o numero
    # de paginas antes do primeiro item do hist (so existe apos um salto).
    def montar_link(novo_hist, cursor, base=None):
        params = {'origem': origem_txt, 'raio': raio}
        if somente_matrizes:
            params['matrizes'] = '1'
        if situacao:
            params['situacao'] = situacao
        if novo_hist:
            params['hist'] = hist_para_texto(novo_hist)
        if cursor:
            params['depois_km'] = km_para_texto(cursor[0])
            params['depois_cnpj'] = cursor[1]
        base = pbase if base is None else base
        if base:
            params['pbase'] = base
        return '?' + urlencode(params)

    def montar_link_pagina(numero):
        """Salto absoluto: a view recalcula o cursor para a pagina pedida."""
        params = {'origem': origem_txt, 'raio': raio}
        if somente_matrizes:
            params['matrizes'] = '1'
        if situacao:
            params['situacao'] = situacao
        if numero > 1:
            params['page'] = numero
        return '?' + urlencode(params)

    pagina = pbase + len(hist) + 1
    link_primeira = montar_link_pagina(1)
    if hist:
        link_anterior = montar_link(hist[:-1], cursor_do_token(hist[-1]))
    elif pagina > 1:
        # Veio de um salto (hist vazio): voltar tambem e um salto.
        link_anterior = montar_link_pagina(pagina - 1)
    else:
        link_anterior = None

    link_proxima = None
    if tem_mais and resultados:
        ultima = resultados[-1]
        cursor_atual = (float(ultima['km']), ultima['cnpj_completo'])
        # Guarda contra loop: se o cursor nao andou (mesma ultima linha da
        # pagina que a abriu), nao ofereca "proxima" - o usuario cairia na
        # mesma pagina para sempre, com o contador subindo.
        if cursor_atual != depois:
            link_proxima = montar_link(hist + [token_cursor(depois)], cursor_atual)

    total_paginas = None
    if total is not None:
        total_paginas = max(1, math.ceil(int(total) / LIMITE_PAGINA))

    context = {
        'origem_txt': origem_txt,
        'origem': origem,
        'erro': erro,
        'aviso': aviso,
        'raio': raio,
        'raio_opcoes': [1, 2, 5, 10, 20, 50, 100],
        'somente_matrizes': somente_matrizes,
        'situacao': situacao,
        'situacoes': SITUACOES_ROTULOS,
        'resultados': resultados,
        'tem_mais': tem_mais,
        'total': total,
        'pagina': pagina,
        'total_paginas': total_paginas,
        'link_primeira': link_primeira,
        'link_anterior': link_anterior,
        'link_proxima': link_proxima,
    }
    return render(request, 'core/proximidade.html', context)


def buscar_empresas_rapido(request):
    """
    Busca rápida via AJAX (autocomplete)
    Retorna JSON com empresas que correspondem ao termo
    """
    termo = request.GET.get('q', '').strip().upper()
    
    if len(termo) < 3:
        return JsonResponse({
            'success': False,
            'message': 'Digite pelo menos 3 caracteres',
            'results': []
        })
    
    conn = None
    resultados = []
    
    try:
        conn = get_postgres_connection()
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Usa CTE MATERIALIZED para otimizar a busca
            sql = """
                WITH empresas_encontradas AS MATERIALIZED (
                    SELECT cnpj_basico, razao_social
                    FROM c_empresas
                    WHERE razao_social ILIKE %s
                    ORDER BY razao_social
                    LIMIT 20
                )
                SELECT 
                    e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
                    emp.razao_social,
                    e.nm_fantasia,
                    e.uf,
                    CASE 
                        WHEN e.cd_sit_cadastro = 1 THEN 'NULA'
                        WHEN e.cd_sit_cadastro = 2 THEN 'ATIVA'
                        WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
                        WHEN e.cd_sit_cadastro = 4 THEN 'INAPTA'
                        WHEN e.cd_sit_cadastro = 8 THEN 'BAIXADA'
                        ELSE 'OUTRA'
                    END AS situacao_descricao
                FROM empresas_encontradas emp
                INNER JOIN c_estabelecimentos e ON e.cnpj_basico = emp.cnpj_basico
                WHERE e.cd_matriz_filial = 1
                ORDER BY emp.razao_social, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv
                LIMIT 20
            """
            
            cur.execute(sql, [f'%{termo}%'])
            resultados = cur.fetchall()
            
    except Exception as e:
        logger.exception("Erro na busca rápida: %s", e)
        resultados = []
    finally:
        if conn:
            conn.close()
    
    # Converte para JSON
    results_list = []
    for row in resultados:
        results_list.append({
            'cnpj_completo': row['cnpj_completo'],
            'razao_social': row['razao_social'],
            'nm_fantasia': row['nm_fantasia'],
            'uf': row['uf'],
            'situacao_descricao': row['situacao_descricao'],
        })
    
    return JsonResponse({
        'success': True,
        'results': results_list,
        'total': len(results_list)
    })


def detalhe_empresa(request, cnpj):
    """Detalhe de uma empresa - COM TODAS AS INFORMAÇÕES"""
    # Log de acesso (detalhe da empresa) - nunca quebra a consulta
    registrar_acesso(request, TIPO_DETALHE_EMPRESA, f'cnpj={cnpj[:20]}')

    next_url = request.GET.get('next', '')
    
    conn = None
    empresa = None
    estabelecimentos = []
    socios = []
    total_socios = 0
    total_filiais = 0
    simples = None
    porte_empresa = None
    
    try:
        conn = get_postgres_connection()
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            
            cnpj_basico = cnpj[:8]
            
            # ============================================================
            # 1. DADOS DA MATRIZ (COM TODAS AS INFORMAÇÕES)
            # ============================================================
            sql_empresa = """
                SELECT 
                    -- CNPJ
                    e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
                    e.cnpj_basico,
                    e.cnpj_ordem,
                    e.cnpj_dv,
                    
                    -- Empresa
                    emp.razao_social,
                    emp.nat_juridica,
                    n.nat_juridica AS natureza_juridica_descricao,
                    emp.qual_resp,
                    emp.capital_social,
                    emp.porte_empresa,
                    CASE 
                        WHEN emp.porte_empresa = 0 THEN 'NÃO INFORMADO'
                        WHEN emp.porte_empresa = 1 THEN 'MICRO EMPRESA'
                        WHEN emp.porte_empresa = 3 THEN 'EMPRESA DE PEQUENO PORTE'
                        WHEN emp.porte_empresa = 5 THEN 'DEMAIS'
                        ELSE 'OUTRO'
                    END AS porte_empresa_descricao,
                    emp.ente_fed_resp,
                    
                    -- Estabelecimento
                    e.cd_matriz_filial,
                    e.nm_fantasia,
                    e.cd_sit_cadastro,
                    e.dt_sit_cadastro,
                    e.motivo_sit_cadastro,
                    m.motivo_sit_cadastro AS motivo_sit_cadastro_descricao,
                    e.nm_cidade_ext,
                    e.cd_pais,
                    p.pais AS pais_descricao,
                    e.dt_ini,
                    e.cnae_principal,
                    c.nm_cnae AS cnae_principal_descricao,
                    e.cnae_secundario,
                    
                    -- Endereço
                    e.tip_logradouro,
                    e.logradouro,
                    e.numero,
                    e.complemento,
                    e.bairro,
                    e.cep,
                    e.uf,
                    e.municipio,
                    mun.municipio AS municipio_descricao,
                    cc.latitude,
                    cc.longitude,
                    cc.generico,
                    
                    -- Contato
                    e.ddd_1,
                    e.tel_1,
                    e.ddd_2,
                    e.tel_2,
                    e.ddd_fax,
                    e.fax,
                    e.email,
                    e.sit_esp,
                    e.dt_sit_esp,
                    
                    -- Simples / MEI
                    s.op_simples,
                    s.dt_op_simples,
                    s.dt_exc_simples,
                    s.op_mei,
                    s.dt_op_mei,
                    s.dt_exc_mei,
                    
                    -- Situação
                    CASE 
                        WHEN e.cd_sit_cadastro = 1 THEN 'NULA'
                        WHEN e.cd_sit_cadastro = 2 THEN 'ATIVA'
                        WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
                        WHEN e.cd_sit_cadastro = 4 THEN 'INAPTA'
                        WHEN e.cd_sit_cadastro = 8 THEN 'BAIXADA'
                        ELSE 'OUTRA'
                    END AS situacao_descricao
                FROM c_estabelecimentos e
                LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                LEFT JOIN c_naturezas n ON emp.nat_juridica = n.cd_nat_juridica
                LEFT JOIN c_motivos m ON e.motivo_sit_cadastro = m.cd_motivo_sit_cadastro
                LEFT JOIN c_municipios mun ON e.municipio = mun.cd_municipio
                LEFT JOIN c_paises p ON e.cd_pais = p.cd_pais
                LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
                LEFT JOIN c_ceps cc ON cc.cep = e.cep
                WHERE e.cnpj_basico = %s
                AND e.cd_matriz_filial = 1
            """
            cur.execute(sql_empresa, [cnpj_basico])
            empresa = cur.fetchone()
            
            if not empresa:
                from django.http import Http404
                raise Http404("Empresa não encontrada")
            
            # ============================================================
            # 2. TODOS OS ESTABELECIMENTOS (MATRIZ + FILIAIS)
            # ============================================================
            sql_filiais = """
                SELECT 
                    e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
                    e.cnpj_ordem,
                    e.cnpj_dv,
                    e.cd_matriz_filial,
                    CASE 
                        WHEN e.cd_matriz_filial = 1 THEN 'MATRIZ'
                        WHEN e.cd_matriz_filial = 2 THEN 'FILIAL'
                        ELSE 'OUTRO'
                    END AS tipo,
                    e.nm_fantasia,
                    e.cd_sit_cadastro,
                    e.dt_sit_cadastro,
                    e.motivo_sit_cadastro,
                    m.motivo_sit_cadastro AS motivo_sit_cadastro_descricao,
                    e.dt_ini AS data_abertura,
                    e.cnae_principal,
                    c.nm_cnae AS atividade_principal,
                    e.cnae_secundario,
                    
                    -- Endereço
                    e.tip_logradouro,
                    e.logradouro,
                    e.numero,
                    e.complemento,
                    e.bairro,
                    e.cep,
                    e.uf,
                    e.municipio,
                    mun.municipio AS municipio_descricao,
                    cc.latitude,
                    cc.longitude,
                    cc.generico,
                    e.nm_cidade_ext,
                    e.cd_pais,
                    p.pais AS pais_descricao,
                    
                    -- Contato
                    e.ddd_1,
                    e.tel_1,
                    e.ddd_2,
                    e.tel_2,
                    e.ddd_fax,
                    e.fax,
                    e.email,
                    e.sit_esp,
                    e.dt_sit_esp,
                    
                    CASE 
                        WHEN e.cd_sit_cadastro = 1 THEN 'NULA'
                        WHEN e.cd_sit_cadastro = 2 THEN 'ATIVA'
                        WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
                        WHEN e.cd_sit_cadastro = 4 THEN 'INAPTA'
                        WHEN e.cd_sit_cadastro = 8 THEN 'BAIXADA'
                        ELSE 'OUTRA'
                    END AS situacao_descricao
                FROM c_estabelecimentos e
                LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                LEFT JOIN c_motivos m ON e.motivo_sit_cadastro = m.cd_motivo_sit_cadastro
                LEFT JOIN c_municipios mun ON e.municipio = mun.cd_municipio
                LEFT JOIN c_paises p ON e.cd_pais = p.cd_pais
                LEFT JOIN c_ceps cc ON cc.cep = e.cep
                WHERE e.cnpj_basico = %s
                ORDER BY e.cnpj_ordem, e.cnpj_dv
            """
            cur.execute(sql_filiais, [cnpj_basico])
            estabelecimentos = cur.fetchall()
            total_filiais = len([e for e in estabelecimentos if e['cd_matriz_filial'] == 2])

            # Link do Google Maps: coordenada do CEP (c_ceps) ou endereco em texto
            # (CEP generico, que cobre o municipio inteiro, usa o endereco).
            # O segundo link busca pelo nome: o endereco fiscal da Receita as
            # vezes diverge do Maps e so o nome acha o ponto (ver core/maps.py).
            empresa['maps_url'] = montar_maps_url(empresa)
            empresa['maps_nome_url'] = montar_maps_nome_url(empresa)
            # WhatsApp so quando um dos telefones e celular (core/telefone.py)
            empresa['whatsapp_url'] = montar_whatsapp(
                empresa.get('ddd_1'), empresa.get('tel_1'),
                empresa.get('ddd_2'), empresa.get('tel_2'),
            )
            for est in estabelecimentos:
                # A razao social vem de c_empresas (uma por cnpj_basico): as
                # filiais so trazem nm_fantasia na consulta delas.
                if not est.get('razao_social'):
                    est['razao_social'] = empresa.get('razao_social')
                est['maps_url'] = montar_maps_url(est)
                est['maps_nome_url'] = montar_maps_nome_url(est)
                est['whatsapp_url'] = montar_whatsapp(
                    est.get('ddd_1'), est.get('tel_1'),
                    est.get('ddd_2'), est.get('tel_2'),
                )
            
            # ============================================================
            # 3. SÓCIOS (COM TODAS AS INFORMAÇÕES)
            # ============================================================
            sql_socios = """
                SELECT 
                    s.id_socio,
                    s.id_tipo_socio,
                    CASE 
                        WHEN s.id_tipo_socio = 1 THEN 'PESSOA JURÍDICA'
                        WHEN s.id_tipo_socio = 2 THEN 'PESSOA FÍSICA'
                        WHEN s.id_tipo_socio = 3 THEN 'ESTRANGEIRO'
                        ELSE 'OUTRO'
                    END AS tipo_socio_descricao,
                    s.nome_ou_razao_social AS nome,
                    s.cnpj_cpf,
                    s.qualif_socio,
                    q.nm_quals AS qualificacao_descricao,
                    s.dt_entrada AS data_entrada,
                    s.cd_pais,
                    p.pais AS pais_socio,
                    s.repr_legal,
                    s.nm_repr AS nome_representante,
                    s.cd_qualif_repr,
                    qr.nm_quals AS qualificacao_representante,
                    s.faixa_etaria,
                    CASE 
                        WHEN s.faixa_etaria = 1 THEN '0-12 anos'
                        WHEN s.faixa_etaria = 2 THEN '13-20 anos'
                        WHEN s.faixa_etaria = 3 THEN '21-30 anos'
                        WHEN s.faixa_etaria = 4 THEN '31-40 anos'
                        WHEN s.faixa_etaria = 5 THEN '41-50 anos'
                        WHEN s.faixa_etaria = 6 THEN '51-60 anos'
                        WHEN s.faixa_etaria = 7 THEN '61-70 anos'
                        WHEN s.faixa_etaria = 8 THEN '71-80 anos'
                        WHEN s.faixa_etaria = 9 THEN '81+ anos'
                        ELSE 'NÃO INFORMADA'
                    END AS faixa_etaria_descricao
                FROM c_socios s
                LEFT JOIN c_qualificacoes q ON s.qualif_socio = q.cd_quals
                LEFT JOIN c_qualificacoes qr ON s.cd_qualif_repr = qr.cd_quals
                LEFT JOIN c_paises p ON s.cd_pais = p.cd_pais
                WHERE s.cnpj_basico = %s
                ORDER BY s.id_socio
            """
            cur.execute(sql_socios, [cnpj_basico])
            socios = cur.fetchall()
            total_socios = len(socios)
            
    except Exception as e:
        logger.exception("Erro ao buscar detalhe da empresa: %s", e)
        from django.http import Http404
        raise Http404("Erro ao buscar empresa")
    finally:
        if conn:
            conn.close()
    
    context = {
        'empresa': empresa,
        'estabelecimentos': estabelecimentos,
        'socios': socios,
        'total_socios': total_socios,
        'total_filiais': total_filiais,
        'total_estabelecimentos': len(estabelecimentos),
        'next_url': next_url,
    }
    
    return render(request, 'core/detalhe.html', context)


def estatisticas(request):
    """
    Exibe estatísticas lendo diretamente da tabela c_estatisticas_totais (em < 5ms).
    """
    conn = None
    context = {}
    try:
        conn = get_postgres_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT categoria, chave, descricao, valor, atualizado_em
                FROM c_estatisticas_totais
                ORDER BY categoria, valor DESC
            """)
            linhas = cur.fetchall()

            if not linhas:
                recalcular_totais_banco()
                cur.execute("""
                    SELECT categoria, chave, descricao, valor, atualizado_em
                    FROM c_estatisticas_totais
                    ORDER BY categoria, valor DESC
                """)
                linhas = cur.fetchall()

            por_cat = {}
            ultima_data = None
            for r in linhas:
                por_cat.setdefault(r['categoria'], []).append(r)
                if not ultima_data or (r['atualizado_em'] and r['atualizado_em'] > ultima_data):
                    ultima_data = r['atualizado_em']

            geral = {r['chave']: r['valor'] for r in por_cat.get('geral', [])}
            situacoes_dict = {r['chave']: r['valor'] for r in por_cat.get('situacao', [])}
            ufs = por_cat.get('uf', [])

            context['totais'] = {
                'total_matrizes': geral.get('total_matrizes', 0),
                'total_ativas': situacoes_dict.get('ATIVA', 0),
                'total_inaptas': situacoes_dict.get('INAPTA', 0),
                'total_baixadas': situacoes_dict.get('BAIXADA', 0),
                'total_suspensas': situacoes_dict.get('SUSPENSA', 0),
                'total_nulas': situacoes_dict.get('NULA', 0),
                'total_ufs': len(ufs),
            }

            simples_dict = {r['chave']: r['valor'] for r in por_cat.get('simples', [])}
            mei_dict = {r['chave']: r['valor'] for r in por_cat.get('mei', [])}
            context['simples_mei'] = {
                'total_simples': simples_dict.get('S', 0),
                'total_mei': mei_dict.get('S', 0),
            }

            context['top_ufs'] = [
                {'uf': r['chave'], 'total': r['valor']}
                for r in ufs[:10]
            ]

            context['situacoes'] = [
                {'situacao': r['chave'], 'total': r['valor']}
                for r in por_cat.get('situacao', [])
            ]

            context['portes'] = [
                {'porte': r['descricao'], 'total': r['valor']}
                for r in por_cat.get('porte', [])
            ]

            context['top_cnaes'] = [
                {'cnae_principal': r['chave'], 'nm_cnae': r['descricao'], 'total': r['valor']}
                for r in por_cat.get('cnae', [])[:10]
            ]

            context['top_naturezas'] = [
                {'codigo': r['chave'], 'descricao': r['descricao'], 'total': r['valor']}
                for r in por_cat.get('natureza', [])[:10]
            ]

            anos = por_cat.get('ano', [])
            anos.sort(key=lambda x: int(x['chave']) if x['chave'].isdigit() else 0, reverse=True)
            context['por_ano'] = [
                {'ano': int(r['chave']), 'total': r['valor']}
                for r in anos[:15]
            ]

            top_mun = []
            for r in por_cat.get('municipio', [])[:10]:
                partes = (r['descricao'] or '').split('|')
                nome = partes[0] if len(partes) > 0 else 'NÃO INFORMADO'
                uf_mun = partes[1] if len(partes) > 1 else ''
                top_mun.append({
                    'codigo': r['chave'],
                    'nome_municipio': nome,
                    'uf': uf_mun,
                    'total': r['valor']
                })
            context['top_municipios'] = top_mun

            socios_dict = {r['chave']: r['valor'] for r in por_cat.get('socios', [])}
            context['socios'] = {
                'total_socios': socios_dict.get('total_socios', 0),
                'total_empresas_com_socios': socios_dict.get('empresas_com_socios', 0),
            }

            if ultima_data:
                context['ultima_atualizacao'] = ultima_data.strftime('%d/%m/%Y %H:%M:%S')

    except Exception as e:
        logger.exception("Erro ao carregar estatísticas: %s", e)
    finally:
        if conn:
            conn.close()

    return render(request, 'core/estatisticas.html', context)

def health_deep(request):
    """
    Health check profundo: testa conexao real com o Postgres bancocnpj.

    Devolve JSON:
      {"status": "ok", "db": "postgres", "ms": 12}
      {"status": "error", "db": "postgres", "erro": "...", "ms": 3000}

    Sempre HTTP 200 (o menu le o JSON, nao o HTTP code).
    """
    from django.http import JsonResponse

    inicio = time.monotonic()
    conn = None
    try:
        conn = get_postgres_connection()
        # Timeout curto: statement_timeout so vale dentro da transacao
        cur = conn.cursor()
        cur.execute("SET LOCAL statement_timeout = 3000")
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()

        ms = int((time.monotonic() - inicio) * 1000)
        return JsonResponse({'status': 'ok', 'db': 'postgres', 'ms': ms})

    except Exception as exc:
        ms = int((time.monotonic() - inicio) * 1000)
        logger.warning('Health deep cnpj (postgres) falhou: %s', exc)
        return JsonResponse({
            'status': 'error',
            'db': 'postgres',
            'erro': str(exc)[:200],
            'ms': ms,
        })
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

def recalcular_totais_banco():
    """Chama a Stored Procedure do PostgreSQL para recalcular totais."""
    inicio = time.time()
    conn = get_postgres_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CALL sp_recalcular_estatisticas_totais();")
        conn.commit()
    finally:
        conn.close()
    return round(time.time() - inicio, 2)


@login_required
@require_POST
def recalcular_totais(request):
    """View administrativa para disparar o recálculo via botão."""
    if not request.user.is_superuser:
        raise PermissionDenied("Apenas administradores podem recalcular os totais.")

    try:
        from django.core.cache import cache
        from django.contrib import messages
        from django.shortcuts import redirect

        segundos = recalcular_totais_banco()

        # Limpa o cache da página de estatísticas para refletir na hora
        cache.delete('estatisticas_completas')

        messages.success(request, f"Totais do banco recalculados com sucesso em {segundos}s!")
    except Exception as e:
        logger.exception("Erro ao recalcular totais do banco: %s", e)
        messages.error(request, f"Erro ao recalcular totais: {e}")

    return redirect('core:estatisticas')