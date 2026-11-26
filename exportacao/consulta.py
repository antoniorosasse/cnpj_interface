"""Leitura das linhas para exportacao (lista de empresas). Sem acentos."""

from psycopg2.extras import RealDictCursor

from core.consultas_lista import SQL_LISTA_CNPJ, montar_filtros
from core.views import SITUACOES, get_postgres_connection


def iterar_linhas(filtros: dict, limite: int, lote: int = 1000):
    """
    Executa a mesma consulta da lista, sem keyset, com LIMIT.

    'filtros' usa as mesmas chaves da view lista_empresas. Devolve um gerador de
    dicts (uma linha por estabelecimento matriz). SET statement_timeout evita
    exportacao travada; a conexao e' sempre fechada no fim.
    """
    situacao = (filtros.get('situacao') or '').upper()
    where, params = montar_filtros(
        razao_social=filtros.get('razao_social', ''),
        nome_fantasia=filtros.get('nome_fantasia', ''),
        cnpj=filtros.get('cnpj', ''),
        uf=filtros.get('uf', ''),
        municipio_id=filtros.get('municipio_id'),
        situacao_codigo=SITUACOES.get(situacao) if situacao else None,
        simples=filtros.get('simples', ''),
        mei=filtros.get('mei', ''),
        cnae=filtros.get('cnae', ''),
    )
    sql = (
        SQL_LISTA_CNPJ + where
        + ' ORDER BY e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv LIMIT %s'
    )

    conn = get_postgres_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET statement_timeout = '15s'")
            cur.execute(sql, params + [limite])
            while True:
                linhas = cur.fetchmany(lote)
                if not linhas:
                    break
                for linha in linhas:
                    yield dict(linha)
    finally:
        conn.close()


def iterar_proximidade(latitude, longitude, raio_km, somente_matrizes,
                       situacao_codigo, limite, lote=2000,
                       faixas_cnae=None, cnaes=None, uf=None,
                       idade_anos=None, capital_min=None, usar_endereco=True):
    """
    Exporta a busca por proximidade em BLOCOS usando o keyset da tela.

    Uma consulta unica com LIMIT grande estourava o statement_timeout no raio
    de 10 km (38 mil linhas) e o arquivo saia so' com o cabecalho. Aqui cada
    bloco custa o mesmo que uma pagina (keyset por km/cnpj), entao o custo por
    bloco fica estavel e o total cresce linear.

    A razao social entra numa segunda query por bloco (ANY(%s) em c_empresas),
    sem mexer no SQL da tela.
    """
    from core.proximidade import (
        buscar_proximos,
        buscar_proximos_endereco,
        buscar_proximos_filtrado,
        tem_tabela_total,
    )

    filtrado = bool(faixas_cnae or cnaes or uf or idade_anos or capital_min)
    if filtrado:
        from core.views import data_ha_anos
        dt_ini_antes_de = data_ha_anos(idade_anos) if idade_anos else None
    else:
        dt_ini_antes_de = None

    conn = get_postgres_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET statement_timeout = '25s'")
            try:
                usar_endereco = bool(usar_endereco
                                     and tem_tabela_total(cur, 'c_estab_geo')
                                     and tem_tabela_total(cur, 'c_ceps_folga'))
            except Exception:
                usar_endereco = False
            restante = limite
            depois = None
            while restante > 0:
                pedido = min(lote, restante)
                if filtrado:
                    linhas, tem_mais, _total = buscar_proximos_filtrado(
                        cur, latitude, longitude, raio_km, limite=pedido,
                        situacao=situacao_codigo,
                        matrizes=somente_matrizes,
                        faixas_cnae=faixas_cnae, cnaes=cnaes, uf=uf,
                        dt_ini_antes_de=dt_ini_antes_de,
                        capital_min=capital_min,
                        depois=depois, contar=False,
                        usar_endereco=usar_endereco,
                    )
                else:
                    busca = (buscar_proximos_endereco if usar_endereco
                             else buscar_proximos)
                    linhas, tem_mais, _total = busca(
                        cur, latitude, longitude, raio_km, somente_matrizes,
                        depois=depois, limite=pedido, situacao=situacao_codigo,
                    )
                if not linhas:
                    break
                linhas = [dict(linha) for linha in linhas[:pedido]]
                basicos = sorted({l['cnpj_basico'] for l in linhas if l.get('cnpj_basico')})
                razao = {}
                if basicos:
                    cur.execute(
                        "SELECT cnpj_basico, razao_social FROM c_empresas "
                        "WHERE cnpj_basico = ANY(%s)", (basicos,),
                    )
                    razao = {r['cnpj_basico']: r['razao_social'] for r in cur.fetchall()}
                for linha in linhas:
                    linha['razao_social'] = razao.get(linha.get('cnpj_basico'), '')
                    yield linha
                restante -= len(linhas)
                if not tem_mais or len(linhas) < pedido:
                    break
                ultimo = linhas[-1]
                depois = (ultimo['km'], ultimo['cnpj_completo'])
    finally:
        conn.close()
