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


def lista_empresas(request):
    """
    Lista empresas - COM ORDENAÇÃO POR CNPJ, RAZÃO SOCIAL, NOME FANTASIA
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
    ordem = request.GET.get('ordem', 'cnpj')
   
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
    # Se está na página 1, força início limpo (elimina ponteiros reversos residuais)
    if page <= 1:
        direction = 'next'
        last_cnpj = ''
        last_razao = ''
        last_fantasia = ''
    
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
                filtros_sql += " AND emp.razao_social ILIKE %s"
                filtros_params.append(f'%{razao_social}%')
            
            if nome_fantasia:
                filtros_sql += " AND e.nm_fantasia ILIKE %s"
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
                filtros_sql += " AND e.uf = %s"
                filtros_params.append(uf.upper())
            
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
            filtros_ativos = [razao_social, nome_fantasia, cnpj, uf, situacao, simples, mei]
            if not any(filtros_ativos):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'geral' AND chave = 'total_matrizes'
                """)
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Caso 2: Filtro apenas por UF
            elif uf and not any([razao_social, nome_fantasia, cnpj, situacao, simples, mei]):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'uf' AND chave = %s
                """, [uf])
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Caso 3: Filtro apenas por Situação
            elif situacao and not any([razao_social, nome_fantasia, cnpj, uf, simples, mei]):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'situacao' AND chave = %s
                """, [situacao])
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Caso 4: Filtro apenas por Simples
            elif simples == 'S' and not any([razao_social, nome_fantasia, cnpj, uf, situacao, mei]):
                cur.execute("""
                    SELECT valor FROM c_estatisticas_totais 
                    WHERE categoria = 'simples' AND chave = 'S'
                """)
                res = cur.fetchone()
                if res:
                    total = res['valor']

            # Caso 5: Filtro apenas por MEI
            elif mei == 'S' and not any([razao_social, nome_fantasia, cnpj, uf, situacao, simples]):
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
                cache_key = f"cnt_{razao_social}_{nome_fantasia}_{cnpj}_{uf}_{situacao}_{simples}_{mei}"
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
            # ----- ORDENAÇÃO POR NOME FANTASIA -----
            # ============================================================
            elif ordem == 'nome_fantasia':
                join_emp = "INNER JOIN" if razao_social else "LEFT JOIN"

                sql = f"""
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
                    {join_emp} c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                    LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                    LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
                    WHERE e.cd_matriz_filial = 1
                """ + filtros_sql
                params = list(filtros_params)

                try:
                    cur.execute("SET LOCAL statement_timeout = 10000")  # 10 segundos

                    if is_last_page:
                        sql += " ORDER BY e.nm_fantasia DESC NULLS LAST, e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC LIMIT %s"
                        params.append(limit)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()
                        resultados = list(reversed(resultados))
                        has_next = False

                    elif direction == 'prev':
                        if last_fantasia:
                            sql += " AND e.nm_fantasia < %s"
                            params.append(last_fantasia)

                        sql += " ORDER BY e.nm_fantasia DESC NULLS LAST, e.cnpj_basico DESC, e.cnpj_ordem DESC, e.cnpj_dv DESC LIMIT %s"
                        params.append(limit + 1)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()

                        if len(resultados) > limit:
                            has_next = True
                            resultados = resultados[:limit]
                        resultados = list(reversed(resultados))

                    else:
                        if last_fantasia:
                            sql += " AND e.nm_fantasia > %s"
                            params.append(last_fantasia)

                        sql += " ORDER BY e.nm_fantasia NULLS FIRST, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv LIMIT %s"
                        params.append(limit + 1)
                        cur.execute(sql, params)
                        resultados = cur.fetchall()

                        if len(resultados) > limit:
                            has_next = True
                            resultados = resultados[:-1]

                except Exception:
                    # Cancelou aos 10s: avisa o usuário com transparência
                    conn.rollback()
                    resultados = []
                    aviso = "⚠️ A consulta excedeu o tempo limite de 10 segundos. Tente refinar a busca com mais filtros (como Razão Social ou CNPJ)."
                    fallback_sql = f"""
                        SELECT 
                            e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
                            emp.razao_social, e.nm_fantasia, e.logradouro, e.numero, e.bairro,
                            e.cep, e.uf, e.email, e.ddd_1 || e.tel_1 AS telefone_1,
                            e.cnae_principal, c.nm_cnae AS atividade_principal, e.cd_sit_cadastro,
                            e.dt_ini AS data_inicio_atividade, s.op_simples, s.op_mei,
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
                    """ + filtros_sql + " ORDER BY e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv LIMIT %s"
                    fallback_params = list(filtros_params) + [limit]
                    cur.execute(fallback_sql, fallback_params)
                    resultados = cur.fetchall()
                    has_next = len(resultados) >= limit

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
        'lista_ufs': LISTA_UFS,  # <--- CERTIFIQUE-SE DE QUE ESTA LINHA ESTÁ AQUI
        'aviso': '💡 Use os filtros para buscar empresas específicas' if not any([razao_social, nome_fantasia, cnpj, uf, situacao, simples, mei]) else None,
        'total_estimado': total_estimado,
        'aviso': aviso or ('💡 Use os filtros para buscar empresas específicas' if not any([razao_social, nome_fantasia, cnpj, uf, situacao, simples, mei]) else None),
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