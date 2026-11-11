# core/consultas_empresa.py
"""
Carga de dados para o dossie em PDF (core/dossie_pdf.py).

Le da base CNPJ (settings.PG_*) a mesma informacao que a pagina de detalhe
mostra. As queries aqui sao enxutas de proposito: o dossie nao precisa de
coordenada, WhatsApp, link de mapa nem texto formatado de tela.

Nao importa core.views (evita ciclo de import): recebe a conexao psycopg2
pronta, quem abre/fecha e a view.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from psycopg2.extras import RealDictCursor

# Matriz do CNPJ consultado. Um CNPJ completo tem 14 digitos, mas a base e
# indexada por cnpj_basico (8) + cnpj_ordem (4) + cnpj_dv (2): o dossie abre
# sempre pela matriz do cnpj_basico, como a pagina de detalhe.
SQL_MATRIZ = """
    SELECT
        e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
        e.cnpj_basico,
        emp.razao_social,
        e.nm_fantasia,

        emp.nat_juridica,
        n.nat_juridica AS natureza_juridica_descricao,
        emp.qual_resp,
        q.nm_quals AS qual_resp_descricao,
        emp.capital_social,
        emp.porte_empresa,
        CASE
            WHEN emp.porte_empresa = 0 THEN 'NAO INFORMADO'
            WHEN emp.porte_empresa = 1 THEN 'MICRO EMPRESA'
            WHEN emp.porte_empresa = 3 THEN 'PEQUENO PORTE'
            WHEN emp.porte_empresa = 5 THEN 'DEMAIS'
            ELSE 'OUTRO'
        END AS porte_empresa_descricao,
        emp.ente_fed_resp,

        e.dt_ini,
        e.cd_sit_cadastro,
        e.dt_sit_cadastro,
        e.motivo_sit_cadastro,
        m.motivo_sit_cadastro AS motivo_sit_cadastro_descricao,
        e.sit_esp,
        e.dt_sit_esp,
        CASE
            WHEN e.cd_sit_cadastro = 1 THEN 'NULA'
            WHEN e.cd_sit_cadastro = 2 THEN 'ATIVA'
            WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
            WHEN e.cd_sit_cadastro = 4 THEN 'INAPTA'
            WHEN e.cd_sit_cadastro = 8 THEN 'BAIXADA'
            ELSE 'OUTRA'
        END AS situacao_descricao,

        e.cnae_principal,
        c.nm_cnae AS cnae_principal_descricao,
        e.cnae_secundario,

        e.tip_logradouro,
        e.logradouro,
        e.numero,
        e.complemento,
        e.bairro,
        e.cep,
        e.uf,
        e.municipio,
        mun.municipio AS municipio_descricao,
        e.nm_cidade_ext,
        e.cd_pais,
        p.pais AS pais_descricao,

        e.ddd_1,
        e.tel_1,
        e.ddd_2,
        e.tel_2,
        e.ddd_fax,
        e.fax,
        e.email,

        s.op_simples,
        s.dt_op_simples,
        s.dt_exc_simples,
        s.op_mei,
        s.dt_op_mei,
        s.dt_exc_mei
    FROM c_estabelecimentos e
    LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
    LEFT JOIN c_naturezas n ON emp.nat_juridica = n.cd_nat_juridica
    LEFT JOIN c_qualificacoes q ON emp.qual_resp = q.cd_quals
    LEFT JOIN c_motivos m ON e.motivo_sit_cadastro = m.cd_motivo_sit_cadastro
    LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
    LEFT JOIN c_municipios mun ON e.municipio = mun.cd_municipio
    LEFT JOIN c_paises p ON e.cd_pais = p.cd_pais
    LEFT JOIN c_simples s ON e.cnpj_basico = s.cnpj_basico
    WHERE e.cnpj_basico = %s
      AND e.cd_matriz_filial = 1
    LIMIT 1
"""


# Matriz + filiais (o dossie lista todas; quem decide quantas imprime e o PDF)
SQL_ESTABELECIMENTOS = """
    SELECT
        e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
        e.cnpj_ordem,
        e.cd_matriz_filial,
        CASE
            WHEN e.cd_matriz_filial = 1 THEN 'MATRIZ'
            WHEN e.cd_matriz_filial = 2 THEN 'FILIAL'
            ELSE 'OUTRO'
        END AS tipo,
        e.nm_fantasia,
        e.cd_sit_cadastro,
        e.dt_sit_cadastro,
        e.dt_ini AS data_abertura,
        e.cnae_principal,
        c.nm_cnae AS atividade_principal,
        e.tip_logradouro,
        e.logradouro,
        e.numero,
        e.complemento,
        e.bairro,
        e.cep,
        e.uf,
        mun.municipio AS municipio_descricao,
        e.nm_cidade_ext,
        e.cd_pais,
        p.pais AS pais_descricao,
        e.ddd_1,
        e.tel_1,
        e.email,
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
    LEFT JOIN c_municipios mun ON e.municipio = mun.cd_municipio
    LEFT JOIN c_paises p ON e.cd_pais = p.cd_pais
    WHERE e.cnpj_basico = %s
    ORDER BY e.cnpj_ordem, e.cnpj_dv
"""


SQL_SOCIOS = """
    SELECT
        s.id_socio,
        s.id_tipo_socio,
        CASE
            WHEN s.id_tipo_socio = 1 THEN 'PESSOA JURIDICA'
            WHEN s.id_tipo_socio = 2 THEN 'PESSOA FISICA'
            WHEN s.id_tipo_socio = 3 THEN 'ESTRANGEIRO'
            ELSE 'OUTRO'
        END AS tipo_socio_descricao,
        s.nome_ou_razao_social AS nome,
        s.cnpj_cpf,
        s.qualif_socio,
        q.nm_quals AS qualificacao_descricao,
        s.dt_entrada AS data_entrada,
        p.pais AS pais_socio,
        s.nm_repr AS nome_representante,
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
            ELSE 'NAO INFORMADA'
        END AS faixa_etaria_descricao
    FROM c_socios s
    LEFT JOIN c_qualificacoes q ON s.qualif_socio = q.cd_quals
    LEFT JOIN c_paises p ON s.cd_pais = p.cd_pais
    WHERE s.cnpj_basico = %s
    ORDER BY s.id_socio
"""


def carregar_dossie(conn, cnpj):
    """
    Devolve:
        {'empresa': dict, 'estabelecimentos': [dict], 'socios': [dict],
         'total_filiais': int}

    Levanta LookupError quando o cnpj_basico nao tem matriz na base (a view
    traduz para 404). Erro de SQL sobe como veio, para o log.
    """
    cnpj_basico = (cnpj or '')[:8]
    if len(cnpj_basico) != 8 or not cnpj_basico.isdigit():
        raise LookupError(f'cnpj invalido: {cnpj!r}')

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_MATRIZ, [cnpj_basico])
        empresa = cur.fetchone()
        if not empresa:
            raise LookupError(f'cnpj_basico sem matriz na base: {cnpj_basico}')

        cur.execute(SQL_ESTABELECIMENTOS, [cnpj_basico])
        estabelecimentos = cur.fetchall()

        cur.execute(SQL_SOCIOS, [cnpj_basico])
        socios = cur.fetchall()

    total_filiais = sum(1 for e in estabelecimentos if e['cd_matriz_filial'] == 2)

    return {
        'empresa': dict(empresa),
        'estabelecimentos': [dict(e) for e in estabelecimentos],
        'socios': [dict(s) for s in socios],
        'total_filiais': total_filiais,
    }
