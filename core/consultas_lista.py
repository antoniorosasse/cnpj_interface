# core/consultas_lista.py
"""SQL e filtros da lista de empresas (lista_empresas, em core/views.py).

Estavam inline dentro da view. Aqui ficam:

  - SQL_LISTA_BASE: o SELECT da paginacao, com o JOIN em c_empresas como
    unico ponto variavel;
  - SQL_LISTA_RAZAO_SOCIAL / SQL_LISTA_CNPJ: as duas versoes (razao social
    usa INNER JOIN, CNPJ usa LEFT JOIN);
  - montar_filtros: monta o WHERE extra e os parametros.

Nada aqui abre conexao nem importa Django: da para testar os filtros sem
banco. Os textos do SQL sao identicos aos que estavam na view -- a extracao
nao mudou query nenhuma.
"""

SQL_LISTA_BASE = """
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
                    {juncao_empresas}
                    LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                    LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
                    LEFT JOIN c_municipios_geo mg ON mg.cd_municipio = e.municipio
                    WHERE e.cd_matriz_filial = 1
                """

# Toda matriz tem cadastro em c_empresas: o INNER JOIN deixa o planner usar
# idx_empresas_razao_cnpj na ordenacao por razao social.
SQL_LISTA_RAZAO_SOCIAL = SQL_LISTA_BASE.format(
    juncao_empresas='INNER JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico')

# Ordenacao padrao (CNPJ): LEFT JOIN, para a lista nao esconder matriz que
# nao tenha cadastro em c_empresas.
SQL_LISTA_CNPJ = SQL_LISTA_BASE.format(
    juncao_empresas='LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico')


def montar_filtros(razao_social='', nome_fantasia='', cnpj='', uf='',
                   municipio_id=None, situacao_codigo=None, simples='', mei=''):
    """Monta o WHERE extra da lista e os parametros da query.

    Devolve (filtros_sql, filtros_params). O texto comeca com " AND " porque
    entra depois de "WHERE e.cd_matriz_filial = 1". situacao_codigo e o
    codigo de c_estabelecimentos.cd_sit_cadastro (quem chama resolve o rotulo
    pelo dict SITUACOES de core/views.py); None = sem filtro de situacao.
    """
    filtros_sql = ''
    filtros_params = []

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

    if situacao_codigo is not None:
        filtros_sql += " AND e.cd_sit_cadastro = %s"
        filtros_params.append(situacao_codigo)

    if simples == 'S':
        filtros_sql += " AND EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_simples = 'S')"
    elif simples == 'N':
        filtros_sql += " AND NOT EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_simples = 'S')"

    if mei == 'S':
        filtros_sql += " AND EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_mei = 'S')"
    elif mei == 'N':
        filtros_sql += " AND NOT EXISTS (SELECT 1 FROM c_simples s WHERE s.cnpj_basico = e.cnpj_basico AND s.op_mei = 'S')"

    return filtros_sql, filtros_params
