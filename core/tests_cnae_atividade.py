# cnpj_interface/core/tests_cnae_atividade.py
"""Testes do resolvedor de atividade compartilhado entre SQL e proximidade."""
from django.test import SimpleTestCase

from core.cnae_atividade import resolver_atividade


class FakeCursor:
    """Cursor fake que separa ILIKE de consulta vetorial."""

    def __init__(self, ilike=(), vetor=()):
        self.ilike = list(ilike)
        self.vetor = list(vetor)
        self.chamadas = []

    def execute(self, sql, params=None):
        self.chamadas.append((sql, params))
        self._sql = sql

    def fetchall(self):
        if 'ORDER BY embedding' in self._sql:
            return self.vetor
        return self.ilike


class ResolverAtividadeTests(SimpleTestCase):
    def test_kb_nao_toca_no_banco(self):
        filtro = resolver_atividade('indústrias metalúrgicas')
        self.assertEqual(filtro['categorias'], ['metalurgica'])
        self.assertEqual(filtro['fonte'], 'kb')
        self.assertEqual(filtro['nao_reconhecidas'], [])

    def test_codigo_cnae(self):
        filtro = resolver_atividade('8630504')
        self.assertEqual(filtro['cnaes'], [8630504])
        self.assertEqual(filtro['fonte'], 'codigo')

    def test_ilike_antes_do_vetor(self):
        cur = FakeCursor(ilike=[{'cnae': 5620104, 'nm_cnae': 'Fornecimento de alimentos'}],
                        vetor=[{'cnae': 9999999, 'nm_cnae': 'Vetor nao deveria rodar'}])
        filtro = resolver_atividade('pizzarias', cur=cur)
        self.assertEqual(filtro['cnaes'], [5620104])
        self.assertEqual(filtro['fonte'], 'iliKe')
        self.assertTrue(any('ILIKE' in sql for sql, _ in cur.chamadas))
        self.assertFalse(any('ORDER BY embedding' in sql for sql, _ in cur.chamadas))

    def test_vetor_so_quando_ilike_nao_acha(self):
        cur = FakeCursor(
            ilike=[],
            vetor=[{'cnae': 3511500, 'nm_cnae': 'Geração de energia elétrica'}],
        )
        chamadas = []

        def embedder(texto):
            chamadas.append(texto)
            return '[0.1,0.2]'

        filtro = resolver_atividade('energia solar', cur=cur, embedder=embedder)
        self.assertEqual(filtro['cnaes'], [3511500])
        self.assertEqual(filtro['fonte'], 'vetor')
        self.assertEqual(chamadas, ['energia solar'])
        # A frase inteira foi tentada no ILIKE; nao pode ter virado "energia"
        # solta (que casaria "energia eletrica").
        ilike_params = [p for sql, p in cur.chamadas if 'ILIKE' in sql]
        self.assertTrue(ilike_params)
        self.assertFalse(any(p == '%energia%' for p in ilike_params))

    def test_sem_resultado(self):
        cur = FakeCursor(ilike=[], vetor=[])
        filtro = resolver_atividade('naves espaciais', cur=cur,
                                    embedder=lambda texto: '[0.1]')
        self.assertEqual(filtro['cnaes'], [])
        self.assertEqual(filtro['fonte'], 'nenhuma')
        self.assertEqual(filtro['nao_reconhecidas'], ['naves espaciais'])

    def test_sem_cursor_nao_dispara_fallback(self):
        filtro = resolver_atividade('energia solar')
        self.assertEqual(filtro['cnaes'], [])
        self.assertEqual(filtro['fonte'], 'nenhuma')
