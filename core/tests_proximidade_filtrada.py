# cnpj_interface/core/tests_proximidade_filtrada.py
"""
Testes de `core.proximidade.buscar_proximos_filtrado` (25/11/2026).

Sem banco: cursor falso no padrao de `core/tests.py` (CursorFakeSequencia).
O objetivo aqui e' o contrato: CEPs do bbox lidos antes, filtros certos no
SQL/params, total vindo do count(*) OVER (), limite/raio clampados, razao
social/capital juntados na pagina e erro quando nao ha filtro seletivo.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from core import proximidade


class CursorFakeFiltrado:
    """Cursor minimo: SET e' ignorado; cada execute consome um resultado."""

    def __init__(self, resultados):
        self.fila = list(resultados)
        self.executados = []
        self.atual = None

    def execute(self, sql, params=None):
        self.executados.append((sql, params))
        if sql.strip().upper().startswith('SET'):
            self.atual = None
            return
        self.atual = self.fila.pop(0) if self.fila else None

    def fetchall(self):
        if self.atual is None:
            return []
        return self.atual if isinstance(self.atual, list) else [self.atual]

    def fetchone(self):
        if isinstance(self.atual, list):
            return self.atual[0] if self.atual else None
        return self.atual


class BuscaProximosFiltradaTests(SimpleTestCase):
    """Contrato de buscar_proximos_filtrado (o SQL roda de verdade em dev)."""

    CEPS = [{'cep': '89201000'}, {'cep': '89202000'}]

    @staticmethod
    def _linha(km, cnpj, total=42, **extra):
        linha = {
            'km': Decimal(str(km)),
            'cnpj_completo': cnpj,
            'cnpj_basico': cnpj[:8],
            'cnpj_ordem': cnpj[8:12],
            'cnpj_dv': cnpj[12:],
            'cd_matriz_filial': 1,
            'nm_fantasia': 'METALURGICA X',
            'logradouro': 'Rua A',
            'numero': '10',
            'bairro': 'Centro',
            'uf': 'SC',
            'municipio_nome': 'Joinville',
            'telefone_1': '4733334444',
            'cd_sit_cadastro': 2,
            'dt_ini': date(2010, 1, 1),
            'latitude': Decimal('-26.3'),
            'longitude': Decimal('-48.8'),
            'total_filtrado': total,
        }
        linha.update(extra)
        return linha

    @classmethod
    def _cursor(cls, linhas_principais, empresas=None):
        return CursorFakeFiltrado([cls.CEPS, linhas_principais, empresas or []])

    @staticmethod
    def _sql_principal(cur):
        for sql, _params in cur.executados:
            if 'cand_estab' in sql:
                return sql
        raise AssertionError('SQL principal nao executado')

    @staticmethod
    def _params_principal(cur):
        for sql, params in cur.executados:
            if 'cand_estab' in sql:
                return params
        raise AssertionError('SQL principal nao executado')

    def test_exige_filtro_seletivo(self):
        cur = CursorFakeFiltrado([])
        with self.assertRaises(ValueError):
            proximidade.buscar_proximos_filtrado(cur, -26.3, -48.8, 10)

    def test_ceps_do_bbox_sao_lidos_antes_e_passados_como_array(self):
        cur = self._cursor([self._linha(0.1, '11111111000111')])
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=1, cnaes=[4711302])
        # A primeira consulta (fora SET) e' a dos CEPs.
        consultas = [s for s, _p in cur.executados
                     if not s.strip().upper().startswith('SET')]
        self.assertIn('FROM c_ceps', consultas[0])
        sql = self._sql_principal(cur)
        params = self._params_principal(cur)
        self.assertIn('cand_estab AS MATERIALIZED', sql)
        self.assertIn('e.cep = ANY(%(ceps)s)', sql)
        self.assertEqual(params['ceps'], ['89201000', '89202000'])

    def test_faixas_uf_e_situacao_entram_no_sql_e_nos_params(self):
        cur = self._cursor([
            self._linha(0.1, '11111111000111'),
            self._linha(0.2, '22222222000122'),
        ], [{'cnpj_basico': '11111111', 'razao_social': 'A',
            'capital_social': None}])
        linhas, tem_mais, total = proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=1,
            situacao=2, uf='sc', faixas_cnae=[(2400000, 2599999)])

        self.assertEqual(total, 42)
        self.assertTrue(tem_mais)
        self.assertEqual(len(linhas), 1)  # limite=1, mas o total e' 42

        sql = self._sql_principal(cur)
        params = self._params_principal(cur)
        self.assertIn('count(*) OVER () AS total_filtrado', sql)
        self.assertIn('cnae_principal BETWEEN %(cnae_min_0)s', sql)
        self.assertIn('e.uf = %(uf)s', sql)
        self.assertIn('e.cd_sit_cadastro = %(situacao)s', sql)
        self.assertIn('ORDER BY e.km, e.cnpj_basico', sql)
        self.assertEqual(params['cnae_min_0'], 2400000)
        self.assertEqual(params['cnae_max_0'], 2599999)
        self.assertEqual(params['uf'], 'SC')
        self.assertEqual(params['situacao'], 2)
        self.assertEqual(params['limite_mais'], 2)

    def test_cnaes_explicitos_usam_any(self):
        cur = self._cursor([self._linha(0.1, '11111111000111')])
        proximidade.buscar_proximos_filtrado(
            cur, -23.56, -46.64, 5, limite=1,
            cnaes=[8630504, 8630505])
        sql = self._sql_principal(cur)
        params = self._params_principal(cur)
        self.assertIn('e.cnae_principal = ANY(%(cnaes)s)', sql)
        self.assertEqual(params['cnaes'], [8630504, 8630505])

    def test_capital_social_usa_exists_correlacionado(self):
        cur = self._cursor(
            [self._linha(0.1, '11111111000111')],
            [{'cnpj_basico': '11111111', 'razao_social': 'TRANSP X',
              'capital_social': Decimal('600000')}])
        linhas, _tem_mais, _total = proximidade.buscar_proximos_filtrado(
            cur, -25.4, -49.2, 10, limite=1,
            cnaes=[4930202], uf='PR', capital_min=500000)
        sql = self._sql_principal(cur)
        params = self._params_principal(cur)
        self.assertIn('EXISTS (SELECT 1 FROM c_empresas emp', sql)
        self.assertIn('emp.capital_social > %(capital_min)s', sql)
        self.assertNotIn('JOIN c_empresas', sql)
        self.assertEqual(params['capital_min'], 500000.0)
        self.assertEqual(linhas[0]['razao_social'], 'TRANSP X')
        self.assertEqual(linhas[0]['capital_social'], Decimal('600000'))

    def test_data_de_abertura_usa_menor_que(self):
        cur = self._cursor([self._linha(0.1, '11111111000111')])
        limite = date(2023, 11, 25)
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=1,
            cnaes=[4711302], dt_ini_antes_de=limite)
        sql = self._sql_principal(cur)
        params = self._params_principal(cur)
        self.assertIn('e.dt_ini < %(dt_ini_antes_de)s', sql)
        self.assertEqual(params['dt_ini_antes_de'], limite)

    def test_sem_matrizes_inclui_filiais_por_padrao(self):
        cur = self._cursor([self._linha(0.1, '11111111000111')])
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=1, cnaes=[4711302])
        self.assertNotIn('cd_matriz_filial = 1', self._sql_principal(cur))

    def test_matrizes_liga_o_filtro(self):
        cur = self._cursor([self._linha(0.1, '11111111000111')])
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=1, matrizes=True,
            cnaes=[4711302])
        self.assertIn('e.cd_matriz_filial = 1', self._sql_principal(cur))

    def test_limite_e_raio_sao_clampados(self):
        cur = self._cursor([self._linha(0.1, '11111111000111')])
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 500, limite=999999, cnaes=[4711302])
        params = self._params_principal(cur)
        self.assertEqual(params['raio'], proximidade.RAIO_MAX_KM)
        self.assertEqual(params['limite_mais'],
                         proximidade.LIMITE_MAX_FILTRADO + 1)

        cur = self._cursor([self._linha(0.1, '11111111000111')])
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 0, limite=0, cnaes=[4711302])
        params = self._params_principal(cur)
        self.assertEqual(params['raio'], proximidade.RAIO_MIN_KM)
        self.assertEqual(params['limite_mais'], 2)

    def test_raio_com_ceps_demais_pede_reducao(self):
        cur = CursorFakeFiltrado([
            [{'cep': '89201000'}, {'cep': '89202000'}],
        ])
        with patch.object(proximidade, 'MAX_CEPS_FILTRADO', 1):
            with self.assertRaises(ValueError):
                proximidade.buscar_proximos_filtrado(
                    cur, -26.3, -48.8, 10, cnaes=[4711302])

    def test_sem_ceps_no_bbox_devolve_vazio(self):
        cur = CursorFakeFiltrado([[]])
        linhas, tem_mais, total = proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, cnaes=[4711302])
        self.assertEqual((linhas, tem_mais, total), ([], False, 0))

    def test_sem_linhas_total_zero_e_sem_busca_de_empresa(self):
        cur = self._cursor([])
        linhas, tem_mais, total = proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=10, cnaes=[4711302])
        self.assertEqual(linhas, [])
        self.assertFalse(tem_mais)
        self.assertEqual(total, 0)
        # CEPs + consulta principal; sem linhas, nao consulta c_empresas.
        consultas = [s for s, _p in cur.executados
                     if not s.strip().upper().startswith('SET')]
        self.assertEqual(len(consultas), 2)

    def test_modo_endereco_junta_geo_e_folga(self):
        cur = self._cursor([self._linha(0.5, '99999999000199')])
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, cnaes=[4711302], usar_endereco=True)
        sql = self._sql_principal(cur)
        params = self._params_principal(cur)
        self.assertIn('c_estab_geo', sql)
        self.assertIn('c_ceps_folga', sql)
        self.assertIn('AS geo_precisao', sql)
        self.assertEqual(params['folga_max'], proximidade.FOLGA_MAX_ORDEM_ENDERECO)
        # bbox aberto pela folga (10 km + 1 km)
        self.assertIn('c_estab_geo', sql)
        self.assertLess(params['lat_min'], -26.3 - 10 / 111.32)

    def test_modo_cep_nao_junta_geo(self):
        cur = self._cursor([self._linha(0.5, '99999999000199')])
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, cnaes=[4711302])
        sql = self._sql_principal(cur)
        self.assertNotIn('c_estab_geo', sql)
        self.assertNotIn('geo_precisao', sql)

    def test_keyset_depois_entra_no_sql_e_nos_params(self):
        cur = self._cursor([self._linha(0.5, '99999999000199')])
        proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=20,
            cnaes=[4711302], depois=(0.4, '12345678000190'))
        sql = self._sql_principal(cur)
        params = self._params_principal(cur)
        self.assertIn('(e.km, e.cnpj_basico', sql)
        self.assertEqual(params['depois_km'], 0.4)
        self.assertEqual(params['depois_basico'], '12345678')
        self.assertEqual(params['depois_ordem'], '0001')
        self.assertEqual(params['depois_dv'], '90')

    def test_contar_false_nao_traz_total(self):
        cur = self._cursor([
            self._linha(0.1, '11111111000111'),
            self._linha(0.2, '22222222000122'),
        ])
        linhas, tem_mais, total = proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=1, cnaes=[4711302], contar=False)
        sql = self._sql_principal(cur)
        self.assertNotIn('count(*) OVER ()', sql)
        self.assertEqual(total, None)
        self.assertTrue(tem_mais)
        self.assertEqual(len(linhas), 1)

    def test_maps_url_e_preenchida_na_pagina(self):
        cur = self._cursor([self._linha(0.1, '11111111000111')])
        linhas, _tem_mais, _total = proximidade.buscar_proximos_filtrado(
            cur, -26.3, -48.8, 10, limite=1, cnaes=[4711302])
        self.assertTrue(linhas[0]['maps_url'].startswith(
            'https://www.google.com/maps/'))
