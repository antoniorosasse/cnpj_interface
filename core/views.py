# /home/neto/cnpj_interface/core/views.py
from typing import Any
import psycopg2
from psycopg2.extras import RealDictCursor
from django.conf import settings
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
import time
import logging

logger = logging.getLogger(__name__)


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
        print(f"❌ Erro ao conectar ao PostgreSQL: {e}")
        raise


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


def lista_empresas(request):
    """
    Lista empresas - COM ORDENAÇÃO POR CNPJ, RAZÃO SOCIAL, NOME FANTASIA
    Otimizado com CTE MATERIALIZED para ordenação por razão social
    """
    
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
    ordem = request.GET.get('ordem', 'cnpj')
    a_partir_de = request.GET.get('a_partir_de', '').strip().upper()
    
    try:
        page = int(request.GET.get('page', 1))
    except (ValueError, TypeError):
        page = 1
    
    limit = 20
    last_cnpj = request.GET.get('last_cnpj', '')
    last_razao = request.GET.get('last_razao', '')
    last_fantasia = request.GET.get('last_fantasia', '')
    direction = request.GET.get('dir', 'next')
    is_last_page = request.GET.get('last_page', '') == 'true'
    
    conn = None
    empresas = []
    total = 0
    total_paginas = 0
    has_next = False
    last_cnpj_next = ''
    first_cnpj_prev = ''
    last_razao_next = ''
    first_razao_prev = ''
    last_fantasia_next = ''
    first_fantasia_prev = ''
    
    sql: str = ""
    params: list[Any] = []
    count_sql: str = ""
    count_params: list[Any] = []
    
    try:
        conn = get_postgres_connection()
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            
            # ============================================================
            # 2. FILTROS
            # ============================================================
            filtros_sql = ""
            filtros_params: list[Any] = []
            
            if razao_social:
                filtros_sql += " AND UPPER(emp.razao_social) LIKE %s"
                filtros_params.append(f'%{razao_social}%')
            
            if nome_fantasia:
                filtros_sql += " AND UPPER(e.nm_fantasia) LIKE %s"
                filtros_params.append(f'%{nome_fantasia}%')
            
            if cnpj:
                if len(cnpj) == 14:
                    filtros_sql += " AND e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv = %s"
                    filtros_params.append(cnpj)
                elif len(cnpj) == 8:
                    filtros_sql += " AND e.cnpj_basico = %s"
                    filtros_params.append(cnpj)
                else:
                    filtros_sql += " AND e.cnpj_basico LIKE %s"
                    filtros_params.append(f'%{cnpj}%')
            
            if uf:
                filtros_sql += " AND UPPER(e.uf) = %s"
                filtros_params.append(uf)
            
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
            # 3. FILTRO "A PARTIR DE"
            # ============================================================
            if a_partir_de:
                if ordem == 'razao_social':
                    filtros_sql += " AND emp.razao_social >= %s"
                    filtros_params.append(a_partir_de)
                elif ordem == 'nome_fantasia':
                    filtros_sql += " AND e.nm_fantasia >= %s"
                    filtros_params.append(a_partir_de)
                else:
                    filtros_sql += " AND (e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) >= %s"
                    filtros_params.append(a_partir_de)
            
            # ============================================================
            # 4. CONTAGEM
            # ============================================================
            count_sql = """
                SELECT COUNT(*) as total
                FROM c_estabelecimentos e
                LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                WHERE e.cd_matriz_filial = 1
            """ + filtros_sql
            count_params = list(filtros_params)
            
            cur.execute(count_sql, count_params)
            resultado = cur.fetchone()
            total = resultado['total'] if resultado else 0
            
            total_paginas = (total + limit - 1) // limit if total > 0 else 0
            
            # ============================================================
            # 5. PAGINAÇÃO (KEYSET POR ORDENAÇÃO)
            # ============================================================
            resultados: list[Any] = []
            
            # ============================================================
            # ----- ORDENAÇÃO POR RAZÃO SOCIAL (OTIMIZADO COM CTE) -----
            # ============================================================
            if ordem == 'razao_social':
                
                # ✅ DETECTA SE É A PRIMEIRA PÁGINA (sem last_razao)
                is_first_page = (not last_razao) and (not last_cnpj) and (direction == 'next') and (not is_last_page)
                
                if is_first_page:
                    # ============================================================
                    # ✅ OTIMIZADO: CTE MATERIALIZED
                    # Ordena c_empresas PRIMEIRO, pega 20, depois faz JOIN
                    # ============================================================
                    
                    # Filtros que se aplicam a c_empresas (na CTE)
                    filtros_emp = ""
                    filtros_emp_params: list[Any] = []
                    
                    if razao_social:
                        filtros_emp += " AND UPPER(razao_social) LIKE %s"
                        filtros_emp_params.append(f'%{razao_social}%')
                    
                    if a_partir_de:
                        filtros_emp += " AND razao_social >= %s"
                        filtros_emp_params.append(a_partir_de)
                    
                    # Filtros que se aplicam a c_estabelecimentos
                    filtros_est = " AND e.cd_matriz_filial = 1"
                    filtros_est_params: list[Any] = []
                    
                    if nome_fantasia:
                        filtros_est += " AND UPPER(e.nm_fantasia) LIKE %s"
                        filtros_est_params.append(f'%{nome_fantasia}%')
                    
                    if cnpj:
                        if len(cnpj) == 14:
                            filtros_est += " AND e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv = %s"
                            filtros_est_params.append(cnpj)
                        elif len(cnpj) == 8:
                            filtros_est += " AND e.cnpj_basico = %s"
                            filtros_est_params.append(cnpj)
                        else:
                            filtros_est += " AND e.cnpj_basico LIKE %s"
                            filtros_est_params.append(f'%{cnpj}%')
                    
                    if uf:
                        filtros_est += " AND UPPER(e.uf) = %s"
                        filtros_est_params.append(uf)
                    
                    if situacao and situacao in SITUACOES:
                        filtros_est += " AND e.cd_sit_cadastro = %s"
                        filtros_est_params.append(SITUACOES[situacao])
                    
                    if simples == 'S':
                        filtros_est += " AND EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_simples = 'S')"
                    elif simples == 'N':
                        filtros_est += " AND NOT EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_simples = 'S')"
                    
                    if mei == 'S':
                        filtros_est += " AND EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_mei = 'S')"
                    elif mei == 'N':
                        filtros_est += " AND NOT EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_mei = 'S')"
                    
                    sql = f"""
                        WITH empresas_ordenadas AS MATERIALIZED (
                            SELECT cnpj_basico, razao_social
                            FROM c_empresas
                            WHERE 1=1 {filtros_emp}
                            ORDER BY razao_social
                            LIMIT %s
                        )
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
                            CASE 
                                WHEN e.cd_sit_cadastro = 1 THEN 'NULA'
                                WHEN e.cd_sit_cadastro = 2 THEN 'ATIVA'
                                WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
                                WHEN e.cd_sit_cadastro = 4 THEN 'INAPTA'
                                WHEN e.cd_sit_cadastro = 8 THEN 'BAIXADA'
                                ELSE 'OUTRA'    
                            END AS situacao_descricao
                        FROM empresas_ordenadas emp
                        INNER JOIN c_estabelecimentos e ON e.cnpj_basico = emp.cnpj_basico
                        LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                        LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
                        WHERE 1=1 {filtros_est}
                        ORDER BY emp.razao_social, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv
                        LIMIT %s
                    """
                    
                    params = list(filtros_emp_params) + [limit + 1] + list(filtros_est_params) + [limit + 1]
                    
                    cur.execute(sql, params)
                    resultados = cur.fetchall()
                    
                    if len(resultados) > limit:
                        has_next = True
                        resultados = resultados[:-1]
                
                else:
                    # ============================================================
                    # KEYSET: já tem ponto de partida (last_razao + last_cnpj)
                    # ============================================================
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
                        WHERE e.cd_matriz_filial = 1
                    """ + filtros_sql
                    params = list(filtros_params)
                    
                    if is_last_page:
                        sql += " ORDER BY emp.razao_social DESC, e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC"
                        sql += " LIMIT %s"
                        params.append(limit)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()
                        resultados = list(reversed(resultados))
                        has_next = False
                    
                    elif direction == 'prev':
                        if last_razao and last_cnpj:
                            sql += " AND (emp.razao_social < %s OR (emp.razao_social = %s AND (e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) < %s))"
                            params.extend([last_razao, last_razao, last_cnpj])
                        sql += " ORDER BY emp.razao_social DESC, e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC"
                        sql += " LIMIT %s"
                        params.append(limit + 1)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()
                        
                        if len(resultados) > limit:
                            has_next = True
                            resultados = resultados[:limit]
                        resultados = list(reversed(resultados))
                        
                    else:
                        if last_razao and last_cnpj:
                            sql += " AND (emp.razao_social > %s OR (emp.razao_social = %s AND (e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) > %s))"
                            params.extend([last_razao, last_razao, last_cnpj])
                        sql += " ORDER BY emp.razao_social, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv"
                        sql += " LIMIT %s"
                        params.append(limit + 1)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()
                        
                        if len(resultados) > limit:
                            has_next = True
                            resultados = resultados[:-1]
                
                if resultados:
                    last_razao_next = resultados[-1]['razao_social'] or ''
                    first_razao_prev = resultados[0]['razao_social'] or ''
                    last_cnpj_next = resultados[-1]['cnpj_completo']
                    first_cnpj_prev = resultados[0]['cnpj_completo']
            
            # ============================================================
            # ----- ORDENAÇÃO POR NOME FANTASIA (OTIMIZADO COM CTE) -----
            # ============================================================
            elif ordem == 'nome_fantasia':
                is_first_page = (not last_fantasia) and (not last_cnpj) and (direction == 'next') and (not is_last_page)
                
                if is_first_page:
                    # Filtros que se aplicam a c_estabelecimentos (na CTE)
                    filtros_est_cte = " AND cd_matriz_filial = 1"
                    filtros_est_cte_params: list[Any] = []
                    
                    if nome_fantasia:
                        filtros_est_cte += " AND UPPER(nm_fantasia) LIKE %s"
                        filtros_est_cte_params.append(f'%{nome_fantasia}%')
                    
                    if a_partir_de:
                        filtros_est_cte += " AND nm_fantasia >= %s"
                        filtros_est_cte_params.append(a_partir_de)
                    
                    if uf:
                        filtros_est_cte += " AND UPPER(uf) = %s"
                        filtros_est_cte_params.append(uf)
                    
                    if situacao and situacao in SITUACOES:
                        filtros_est_cte += " AND cd_sit_cadastro = %s"
                        filtros_est_cte_params.append(SITUACOES[situacao])
                    
                    if simples == 'S':
                        filtros_est_cte += " AND EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = c_estabelecimentos.cnpj_basico AND s.op_simples = 'S')"
                    elif simples == 'N':
                        filtros_est_cte += " AND NOT EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = c_estabelecimentos.cnpj_basico AND s.op_simples = 'S')"
                    
                    if mei == 'S':
                        filtros_est_cte += " AND EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = c_estabelecimentos.cnpj_basico AND s.op_mei = 'S')"
                    elif mei == 'N':
                        filtros_est_cte += " AND NOT EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = c_estabelecimentos.cnpj_basico AND s.op_mei = 'S')"
                    
                    # Filtros que se aplicam a c_empresas (no SELECT principal)
                    filtros_emp_sel = ""
                    filtros_emp_sel_params: list[Any] = []
                    
                    if razao_social:
                        filtros_emp_sel += " AND UPPER(emp.razao_social) LIKE %s"
                        filtros_emp_sel_params.append(f'%{razao_social}%')
                    
                    if cnpj:
                        if len(cnpj) == 14:
                            filtros_emp_sel += " AND (e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) = %s"
                            filtros_emp_sel_params.append(cnpj)
                        elif len(cnpj) == 8:
                            filtros_emp_sel += " AND e.cnpj_basico = %s"
                            filtros_emp_sel_params.append(cnpj)
                        else:
                            filtros_emp_sel += " AND e.cnpj_basico LIKE %s"
                            filtros_emp_sel_params.append(f'%{cnpj}%')
                    
                    sql = f"""
                        WITH estabelecimentos_ordenados AS MATERIALIZED (
                            SELECT cnpj_basico, cnpj_ordem, cnpj_dv, nm_fantasia
                            FROM c_estabelecimentos
                            WHERE 1=1 {filtros_est_cte}
                            ORDER BY nm_fantasia, cnpj_basico, cnpj_ordem, cnpj_dv
                            LIMIT %s
                        )
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
                            CASE 
                                WHEN e.cd_sit_cadastro = 1 THEN 'NULA'
                                WHEN e.cd_sit_cadastro = 2 THEN 'ATIVA'
                                WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
                                WHEN e.cd_sit_cadastro = 4 THEN 'INAPTA'
                                WHEN e.cd_sit_cadastro = 8 THEN 'BAIXADA'
                                ELSE 'OUTRA'    
                            END AS situacao_descricao
                        FROM estabelecimentos_ordenados est_cte
                        INNER JOIN c_estabelecimentos e 
                            ON e.cnpj_basico = est_cte.cnpj_basico 
                            AND e.cnpj_ordem = est_cte.cnpj_ordem 
                            AND e.cnpj_dv = est_cte.cnpj_dv
                        LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                        LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                        LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
                        WHERE 1=1 {filtros_emp_sel}
                        ORDER BY e.nm_fantasia, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv
                        LIMIT %s
                    """
                    
                    params = list(filtros_est_cte_params) + [limit + 1] + list(filtros_emp_sel_params) + [limit + 1]
                    
                    cur.execute(sql, params)
                    resultados = cur.fetchall()
                    
                    if len(resultados) > limit:
                        has_next = True
                        resultados = resultados[:-1]
                
                else:
                    # KEYSET
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
                        WHERE e.cd_matriz_filial = 1
                    """ + filtros_sql
                    params = list(filtros_params)
                    
                    if is_last_page:
                        sql += " ORDER BY e.nm_fantasia DESC, e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC"
                        sql += " LIMIT %s"
                        params.append(limit)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()
                        resultados = list(reversed(resultados))
                        has_next = False
                    
                    elif direction == 'prev':
                        if last_fantasia and last_cnpj:
                            sql += " AND (e.nm_fantasia < %s OR (e.nm_fantasia = %s AND (e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) < %s))"
                            params.extend([last_fantasia, last_fantasia, last_cnpj])
                        sql += " ORDER BY e.nm_fantasia DESC, e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC"
                        sql += " LIMIT %s"
                        params.append(limit + 1)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()
                        
                        if len(resultados) > limit:
                            has_next = True
                            resultados = resultados[:limit]
                        resultados = list(reversed(resultados))
                        
                    else:
                        if last_fantasia and last_cnpj:
                            sql += " AND (e.nm_fantasia > %s OR (e.nm_fantasia = %s AND (e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) > %s))"
                            params.extend([last_fantasia, last_fantasia, last_cnpj])
                        sql += " ORDER BY e.nm_fantasia, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv"
                        sql += " LIMIT %s"
                        params.append(limit + 1)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()
                        
                        if len(resultados) > limit:
                            has_next = True
                            resultados = resultados[:-1]
                
                if resultados:
                    last_fantasia_next = resultados[-1]['nm_fantasia'] or ''
                    first_fantasia_prev = resultados[0]['nm_fantasia'] or ''
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
                    if last_cnpj:
                        sql += " AND e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv < %s"
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
                    if last_cnpj:
                        sql += " AND e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv > %s"
                        params.append(last_cnpj)
                    sql += " ORDER BY e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv"
                    sql += " LIMIT %s"
                    params.append(limit + 1)
                    cur.execute(sql, params)
                    resultados = cur.fetchall()
                    
                    if len(resultados) > limit:
                        has_next = True
                        resultados = resultados[:-1]
                
                if resultados:
                    last_cnpj_next = resultados[-1]['cnpj_completo']
                    first_cnpj_prev = resultados[0]['cnpj_completo']
            
            empresas = resultados
            
    except Exception as e:
        import traceback
        print(f"❌ Erro: {e}")
        traceback.print_exc()
        empresas = []
        total = 0
        has_next = False
        last_cnpj_next = ''
        first_cnpj_prev = ''
        last_razao_next = ''
        first_razao_prev = ''
        last_fantasia_next = ''
        first_fantasia_prev = ''
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
        'ordem': ordem,
        'a_partir_de': a_partir_de,
        'total': total,
        'total_paginas': total_paginas,
        'page': page,
        'has_next': has_next,
        'has_previous': page > 1,
        'last_cnpj': last_cnpj_next,
        'first_cnpj': first_cnpj_prev,
        'last_razao': last_razao_next,
        'first_razao': first_razao_prev,
        'last_fantasia': last_fantasia_next,
        'first_fantasia': first_fantasia_prev,
        'aviso': '💡 Use os filtros para buscar empresas específicas' if not any([razao_social, nome_fantasia, cnpj, uf, situacao, simples, mei, a_partir_de]) else None,
    }
    
    return render(request, 'core/lista.html', context)


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
                    WHERE UPPER(razao_social) LIKE %s
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
        import traceback
        print(f"❌ Erro na busca rápida: {e}")
        traceback.print_exc()
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
                WHERE e.cnpj_basico = %s
                ORDER BY e.cnpj_ordem, e.cnpj_dv
            """
            cur.execute(sql_filiais, [cnpj_basico])
            estabelecimentos = cur.fetchall()
            total_filiais = len([e for e in estabelecimentos if e['cd_matriz_filial'] == 2])
            
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
        import traceback
        print(f"❌ Erro no detalhe: {e}")
        traceback.print_exc()
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
    }
    
    return render(request, 'core/detalhe.html', context)


def estatisticas(request):
    """
    Estatísticas completas com CACHE (atualiza a cada 1 hora)
    """
    from django.core.cache import cache
    
    # ✅ SE O USUÁRIO CLICOU EM "ATUALIZAR", LIMPA O CACHE
    if request.GET.get('refresh') == '1':
        cache.delete('estatisticas_completas')
        print("🔄 Cache de estatísticas limpo pelo usuário")
    
    context = cache.get('estatisticas_completas')
    
    if context is None:
        conn = None
        context = {}
        
        try:
            conn = get_postgres_connection()
            
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                
                # ============================================================
                # 1. TOTAIS GERAIS
                # ============================================================
                cur.execute("""
                    SELECT 
                        COUNT(*) as total_matrizes,
                        COUNT(CASE WHEN cd_sit_cadastro = 2 THEN 1 END) as total_ativas,
                        COUNT(CASE WHEN cd_sit_cadastro = 4 THEN 1 END) as total_inaptas,
                        COUNT(CASE WHEN cd_sit_cadastro = 8 THEN 1 END) as total_baixadas,
                        COUNT(CASE WHEN cd_sit_cadastro = 3 THEN 1 END) as total_suspensas,
                        COUNT(CASE WHEN cd_sit_cadastro = 1 THEN 1 END) as total_nulas,
                        COUNT(DISTINCT uf) as total_ufs
                    FROM c_estabelecimentos
                    WHERE cd_matriz_filial = 1
                """)
                resultado = cur.fetchone()
                context['totais'] = dict(resultado) if resultado else {}
                
                # ============================================================
                # 2. TOP 10 UFs
                # ============================================================
                cur.execute("""
                    SELECT 
                        uf,
                        COUNT(*) as total
                    FROM c_estabelecimentos
                    WHERE cd_matriz_filial = 1
                    AND uf IS NOT NULL AND uf != ''
                    GROUP BY uf
                    ORDER BY total DESC
                    LIMIT 10
                """)
                context['top_ufs'] = [dict(r) for r in cur.fetchall()]
                
                # ============================================================
                # 3. DISTRIBUIÇÃO POR SITUAÇÃO
                # ============================================================
                cur.execute("""
                    SELECT 
                        CASE 
                            WHEN cd_sit_cadastro = 1 THEN 'NULA'
                            WHEN cd_sit_cadastro = 2 THEN 'ATIVA'
                            WHEN cd_sit_cadastro = 3 THEN 'SUSPENSA'
                            WHEN cd_sit_cadastro = 4 THEN 'INAPTA'
                            WHEN cd_sit_cadastro = 8 THEN 'BAIXADA'
                            ELSE 'OUTRA'
                        END AS situacao,
                        COUNT(*) as total
                    FROM c_estabelecimentos
                    WHERE cd_matriz_filial = 1
                    GROUP BY cd_sit_cadastro
                    ORDER BY total DESC
                """)
                context['situacoes'] = [dict(r) for r in cur.fetchall()]
                
                # ============================================================
                # 4. DISTRIBUIÇÃO POR PORTE DA EMPRESA
                # ============================================================
                cur.execute("""
                    SELECT 
                        CASE 
                            WHEN emp.porte_empresa = 0 THEN 'NÃO INFORMADO'
                            WHEN emp.porte_empresa = 1 THEN 'MICRO EMPRESA'
                            WHEN emp.porte_empresa = 3 THEN 'EMPRESA DE PEQUENO PORTE'
                            WHEN emp.porte_empresa = 5 THEN 'DEMAIS'
                            WHEN emp.porte_empresa IS NULL THEN 'NÃO INFORMADO'
                            ELSE 'NÃO INFORMADO'
                        END AS porte,
                        COUNT(*) as total
                    FROM c_estabelecimentos e
                    LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                    WHERE e.cd_matriz_filial = 1
                    GROUP BY emp.porte_empresa
                    ORDER BY total DESC
                """)
                context['portes'] = [dict(r) for r in cur.fetchall()]
                
                # ============================================================
                # 5. TOP 10 CNAES PRINCIPAIS
                # ============================================================
                cur.execute("""
                    SELECT 
                        e.cnae_principal,
                        c.nm_cnae,
                        COUNT(*) as total
                    FROM c_estabelecimentos e
                    LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                    WHERE e.cd_matriz_filial = 1
                    AND e.cnae_principal IS NOT NULL
                    GROUP BY e.cnae_principal, c.nm_cnae
                    ORDER BY total DESC
                    LIMIT 10
                """)
                context['top_cnaes'] = [dict(r) for r in cur.fetchall()]
                
                # ============================================================
                # 6. TOP 10 NATUREZAS JURÍDICAS
                # ============================================================
                cur.execute("""
                    SELECT 
                        emp.nat_juridica AS codigo,
                        COALESCE(n.nat_juridica, 'NÃO INFORMADA') AS descricao,
                        COUNT(*) as total
                    FROM c_estabelecimentos e
                    LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                    LEFT JOIN c_naturezas n ON emp.nat_juridica = n.cd_nat_juridica
                    WHERE e.cd_matriz_filial = 1
                    AND emp.nat_juridica IS NOT NULL
                    GROUP BY emp.nat_juridica, n.nat_juridica
                    ORDER BY total DESC
                    LIMIT 10
                """)
                context['top_naturezas'] = [dict(r) for r in cur.fetchall()]
                
                # ============================================================
                # 7. SIMPLES E MEI
                # ============================================================
                cur.execute("""
                    SELECT 
                        COUNT(CASE WHEN s.op_simples = 'S' THEN 1 END) as total_simples,
                        COUNT(CASE WHEN s.op_mei = 'S' THEN 1 END) as total_mei
                    FROM c_estabelecimentos e
                    LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
                    WHERE e.cd_matriz_filial = 1
                """)
                resultado = cur.fetchone()
                context['simples_mei'] = dict(resultado) if resultado else {}
                
                # ============================================================
                # 8. EMPRESAS POR ANO DE ABERTURA
                # ============================================================
                cur.execute("""
                    SELECT 
                        EXTRACT(YEAR FROM dt_ini)::integer as ano,
                        COUNT(*) as total
                    FROM c_estabelecimentos
                    WHERE cd_matriz_filial = 1
                    AND dt_ini IS NOT NULL
                    AND EXTRACT(YEAR FROM dt_ini) >= 2000
                    GROUP BY EXTRACT(YEAR FROM dt_ini)
                    ORDER BY ano DESC
                    LIMIT 15
                """)
                context['por_ano'] = [dict(r) for r in cur.fetchall()]
                
                # ============================================================
                # 9. TOP 10 MUNICÍPIOS
                # ============================================================
                cur.execute("""
                    SELECT 
                        e.municipio AS codigo,
                        COALESCE(m.municipio, 'NÃO INFORMADO') AS nome_municipio,
                        e.uf,
                        COUNT(*) as total
                    FROM c_estabelecimentos e
                    LEFT JOIN c_municipios m ON e.municipio = m.cd_municipio
                    WHERE e.cd_matriz_filial = 1
                    AND e.municipio IS NOT NULL
                    GROUP BY e.municipio, m.municipio, e.uf
                    ORDER BY total DESC
                    LIMIT 10
                """)
                context['top_municipios'] = [dict(r) for r in cur.fetchall()]
                
                # ============================================================
                # 10. TOTAL DE SÓCIOS
                # ============================================================
                cur.execute("""
                    SELECT 
                        COUNT(*) as total_socios,
                        COUNT(DISTINCT cnpj_basico) as total_empresas_com_socios
                    FROM c_socios
                """)
                resultado = cur.fetchone()
                context['socios'] = dict(resultado) if resultado else {}
                
                # ============================================================
                # 11. DATA DA ÚLTIMA ATUALIZAÇÃO
                # ============================================================
                from datetime import datetime
                context['ultima_atualizacao'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
                
            cache.set('estatisticas_completas', context, 3600)
            print("✅ Estatísticas calculadas e salvas no cache")
            
        except Exception as e:
            import traceback
            print(f"❌ Erro nas estatísticas: {e}")
            traceback.print_exc()
        finally:
            if conn:
                conn.close()
    else:
        print("✅ Estatísticas carregadas do cache")
    
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

