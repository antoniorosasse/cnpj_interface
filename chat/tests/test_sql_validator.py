# cnpj_interface/chat/tests/test_sql_validator.py
"""
Testes do validador de SQL.

Roda com:
    python manage.py test chat.tests.test_sql_validator
"""

from django.test import TestCase
from chat.services.sql_validator import (
    validar_sql,
    SQLValidationError,
    DEFAULT_MAX_ROWS,
)


class SQLValidatorTestCase(TestCase):

    # ============================================================
    # CASOS VÁLIDOS
    # ============================================================

    def test_select_simples(self):
        """SELECT básico deve passar."""
        r = validar_sql('SELECT * FROM c_empresas')
        self.assertTrue('SELECT' in r.sql.upper())
        self.assertTrue('LIMIT' in r.sql.upper())
        self.assertTrue(r.teve_limit)

    def test_select_com_limit(self):
        """SELECT com LIMIT válido deve passar."""
        r = validar_sql('SELECT * FROM c_empresas LIMIT 100')
        self.assertEqual(r.limit_valor, 100)
        self.assertFalse(r.teve_limit)

    def test_select_com_limit_maior(self):
        """LIMIT maior que o permitido deve ser reduzido."""
        r = validar_sql('SELECT * FROM c_empresas LIMIT 999999')
        self.assertEqual(r.limit_valor, DEFAULT_MAX_ROWS)
        self.assertTrue(r.teve_limit)

    def test_select_com_where(self):
        """SELECT com WHERE deve passar."""
        r = validar_sql(
            "SELECT * FROM c_estabelecimentos WHERE uf = 'SP' AND cd_sit_cadastro = 2"
        )
        self.assertTrue('LIMIT' in r.sql.upper())

    def test_select_com_join(self):
        """SELECT com JOIN deve passar."""
        r = validar_sql("""
            SELECT e.razao_social, c.nm_cnae
            FROM c_empresas e
            LEFT JOIN c_cnaes c ON e.nat_juridica = c.cnae
        """)
        self.assertTrue('JOIN' in r.sql.upper())

    def test_select_com_agregacao(self):
        """SELECT com GROUP BY deve passar."""
        r = validar_sql("""
            SELECT uf, COUNT(*) as total
            FROM c_estabelecimentos
            GROUP BY uf
            ORDER BY total DESC
        """)
        self.assertTrue('GROUP BY' in r.sql.upper())

    def test_remover_comentario_linha(self):
        """Comentários de linha devem ser removidos."""
        r = validar_sql("SELECT * FROM c_empresas -- comentário")
        self.assertNotIn('comentário', r.sql)

    def test_remover_comentario_bloco(self):
        """Comentários de bloco devem ser removidos."""
        r = validar_sql("SELECT * /* bloco */ FROM c_empresas")
        self.assertNotIn('bloco', r.sql)

    # ============================================================
    # CASOS INVÁLIDOS — SEGURANÇA
    # ============================================================

    def test_bloqueia_drop(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('DROP TABLE c_empresas')

    def test_bloqueia_delete(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('DELETE FROM c_empresas')

    def test_bloqueia_update(self):
        with self.assertRaises(SQLValidationError):
            validar_sql("UPDATE c_empresas SET razao_social = 'x'")

    def test_bloqueia_insert(self):
        with self.assertRaises(SQLValidationError):
            validar_sql("INSERT INTO c_empresas VALUES ('1', 'x')")

    def test_bloqueia_truncate(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('TRUNCATE c_empresas')

    def test_bloqueia_alter(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('ALTER TABLE c_empresas ADD COLUMN x int')

    def test_bloqueia_create(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('CREATE TABLE hack (id int)')

    def test_bloqueia_grant(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('GRANT ALL ON c_empresas TO public')

    def test_bloqueia_copy(self):
        with self.assertRaises(SQLValidationError):
            validar_sql("COPY c_empresas TO '/tmp/x.csv'")

    def test_bloqueia_pg_read_file(self):
        with self.assertRaises(SQLValidationError):
            validar_sql("SELECT pg_read_file('/etc/passwd')")

    def test_bloqueia_pg_sleep(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('SELECT pg_sleep(60)')

    def test_bloqueia_multiplas_queries(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('SELECT 1; DROP TABLE c_empresas')

    def test_bloqueia_select_com_drop_embutido(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('SELECT * FROM c_empresas; DROP TABLE x')

    def test_bloqueia_query_vazia(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('')

    def test_bloqueia_query_so_comentario(self):
        with self.assertRaises(SQLValidationError):
            validar_sql('-- só comentário')

    def test_permite_palavra_drop_como_parte_de_outra(self):
        """'DROPSHIPPING' não deve ser bloqueado (não é palavra isolada)."""
        r = validar_sql("SELECT 'DROPSHIPPING' FROM c_empresas")
        self.assertTrue('LIMIT' in r.sql.upper())