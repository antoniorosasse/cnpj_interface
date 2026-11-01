# cnpj_interface/chat/tests/test_embedding_service.py
"""
Testes do servico de embedding (chat/services/embedding_service.py)
e da integracao com o sql_generator.

Usa mock para nao carregar o modelo real (que demora ~5s e ocupa ~200MB).
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase

from chat.services import embedding_service


class GerarVetorTests(TestCase):
    """Testes da funcao gerar_vetor."""

    @patch('chat.services.embedding_service._get_modelo')
    def test_formato_do_vetor(self, mock_modelo):
        # Simula o modelo retornando um vetor de 3 dimensoes.
        # Criamos um mock que se comporta como numpy array: tem .tolist()
        mock_emb = MagicMock()
        mock_emb.tolist.return_value = [0.1, 0.2, 0.3]

        mock_encode = MagicMock()
        # encode() retorna uma LISTA de embeddings. O primeiro item
        # e' o nosso mock_emb (que finge ser um numpy array).
        mock_encode.return_value = [mock_emb]

        mock_modelo.return_value.encode = mock_encode

        vetor = embedding_service.gerar_vetor('teste')

        # Deve ser string no formato [0.100000,0.200000,0.300000]
        self.assertEqual(vetor, '[0.100000,0.200000,0.300000]')

    @patch('chat.services.embedding_service._get_modelo')
    def test_modelo_falha_retorna_none(self, mock_modelo):
        mock_modelo.side_effect = Exception('Modelo indisponivel')

        vetor = embedding_service.gerar_vetor('teste')

        self.assertIsNone(vetor)


class TemPlaceholderTests(TestCase):
    """Testes da funcao tem_placeholder."""

    def test_sql_com_placeholder(self):
        self.assertTrue(embedding_service.tem_placeholder('SELECT %s'))

    def test_sql_sem_placeholder(self):
        self.assertFalse(embedding_service.tem_placeholder('SELECT 1'))

    def test_sql_vazio(self):
        self.assertFalse(embedding_service.tem_placeholder(''))

    def test_like_com_porcentagem_s_nao_e_placeholder(self):
        # "%supermercado%" comeca com "%s", mas nao e' um placeholder.
        self.assertFalse(
            embedding_service.tem_placeholder(
                "SELECT * FROM t WHERE nome ILIKE '%supermercado%'"
            )
        )

    def test_placeholder_em_comentario_nao_conta(self):
        self.assertFalse(
            embedding_service.tem_placeholder('SELECT 1 -- usar %s aqui')
        )


class InjetarVetorTests(TestCase):
    """Testes da funcao injetar_vetor."""

    def test_sem_placeholder_nao_muda_sql(self):
        sql = 'SELECT 1'
        sql_final, erro = embedding_service.injetar_vetor(sql, 'pergunta')
        self.assertEqual(sql_final, sql)
        self.assertIsNone(erro)

    @patch('chat.services.embedding_service.gerar_vetor')
    def test_injeta_vetor_com_cast(self, mock_gerar):
        mock_gerar.return_value = '[0.1,0.2]'
        sql = 'SELECT * FROM t ORDER BY embedding <=> %s LIMIT 5'

        sql_final, erro = embedding_service.injetar_vetor(sql, 'pergunta')

        self.assertEqual(sql_final, "SELECT * FROM t ORDER BY embedding <=> '[0.1,0.2]'::halfvec(384) LIMIT 5")
        self.assertIsNone(erro)
        mock_gerar.assert_called_once_with('pergunta')

    @patch('chat.services.embedding_service.gerar_vetor')
    def test_nao_duplica_cast(self, mock_gerar):
        mock_gerar.return_value = '[0.1,0.2]'
        # O SQL ja tem o cast (a IA copiou do schema_context)
        sql = 'SELECT * FROM t ORDER BY embedding <=> %s::halfvec(384) LIMIT 5'

        sql_final, erro = embedding_service.injetar_vetor(sql, 'pergunta')

        # Nao deve ter ::halfvec duplicado
        self.assertEqual(sql_final, "SELECT * FROM t ORDER BY embedding <=> '[0.1,0.2]'::halfvec(384) LIMIT 5")
        self.assertIsNone(erro)

    @patch('chat.services.embedding_service.gerar_vetor')
    def test_falha_no_embedding_retorna_erro(self, mock_gerar):
        mock_gerar.return_value = None  # simula falha no modelo
        sql = 'SELECT * FROM t WHERE x = %s'

        sql_final, erro = embedding_service.injetar_vetor(sql, 'pergunta')

        self.assertEqual(sql_final, sql)  # SQL original inalterado
        self.assertIsNotNone(erro)
        if erro is not None:  # <-- Adicionado para o Pylance
            self.assertIn('Falha ao gerar o vetor', erro)

    @patch('chat.services.embedding_service.gerar_vetor')
    def test_like_antes_do_placeholder_nao_corrompe_sql(self, mock_gerar):
        # Regressao do erro "sintaxe em ou proximo a '['" no chat:
        # o "%s" de '%supermercado%' era substituido no lugar do
        # placeholder real do vetor.
        mock_gerar.return_value = '[0.1,0.2]'
        sql = (
            'SELECT e.cnpj_basico FROM c_estabelecimentos e '
            'WHERE e.cnae_principal IN ( '
            "  SELECT cnae FROM c_cnaes WHERE nm_cnae ILIKE '%supermercado%' "
            '  UNION '
            '  SELECT cnae FROM ( '
            '    SELECT cnae FROM c_cnaes '
            '    ORDER BY embedding <=> %s::halfvec(384) LIMIT 2 '
            '  ) sub '
            ') LIMIT 100'
        )

        sql_final, erro = embedding_service.injetar_vetor(sql, 'supermercados')

        self.assertIsNone(erro)
        # O literal do ILIKE fica intacto...
        self.assertIn("ILIKE '%supermercado%'", sql_final)
        self.assertNotIn("'upermercado%'", sql_final)
        # ...e o vetor entra no placeholder de verdade.
        self.assertIn("'[0.1,0.2]'::halfvec(384)", sql_final)
        self.assertNotIn('<=> %s', sql_final)
        mock_gerar.assert_called_once_with('supermercados')

    @patch('chat.services.embedding_service.gerar_vetor')
    def test_placeholder_so_dentro_de_literal_nao_injeta(self, mock_gerar):
        sql = "SELECT * FROM t WHERE nome ILIKE '%supermercado%'"

        sql_final, erro = embedding_service.injetar_vetor(sql, 'pergunta')

        self.assertEqual(sql_final, sql)
        self.assertIsNone(erro)
        mock_gerar.assert_not_called()

    @patch('chat.services.embedding_service.gerar_vetor')
    def test_injeta_multiplos_placeholders(self, mock_gerar):
        mock_gerar.return_value = '[0.1,0.2]'
        sql = (
            'SELECT * FROM t WHERE a <=> %s::halfvec(384) '
            "AND b ILIKE '%s%' "
            'AND c <=> %s LIMIT 5'
        )

        sql_final, erro = embedding_service.injetar_vetor(sql, 'pergunta')

        self.assertIsNone(erro)
        self.assertEqual(
            sql_final,
            "SELECT * FROM t WHERE a <=> '[0.1,0.2]'::halfvec(384) "
            "AND b ILIKE '%s%' "
            "AND c <=> '[0.1,0.2]'::halfvec(384) LIMIT 5",
        )
        mock_gerar.assert_called_once_with('pergunta')


class IntegracaoGerarSqlTests(TestCase):
    """Testa se o gerar_sql injeta o vetor corretamente."""

    @patch('chat.services.sql_generator.gerar_resposta')
    @patch('chat.services.embedding_service.gerar_vetor')
    def test_gerar_sql_com_busca_vetorial(self, mock_vetor, mock_llm):
        # LLM gera SQL com placeholder no formato de fallback (opcao 1)
        mock_llm.return_value = (
            'SELECT e.cnpj_basico, emp.razao_social, c.nm_cnae '
            'FROM c_estabelecimentos e '
            'JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico '
            'JOIN c_cnaes c ON e.cnae_principal = c.cnae '
            'WHERE e.cd_matriz_filial = 1 '
            'AND e.cnae_principal IN ( '
            "  SELECT cnae FROM c_cnaes WHERE nm_cnae ILIKE '%energia solar%' "
            '  UNION '
            '  SELECT cnae FROM ( '
            '    SELECT cnae FROM c_cnaes '
            '    WHERE NOT EXISTS ( '
            '      SELECT 1 FROM c_cnaes '
            "      WHERE nm_cnae ILIKE '%energia solar%' "
            '    ) '
            '    ORDER BY embedding <=> %s::halfvec(384) LIMIT 2 '
            '  ) sub '
            ') LIMIT 100'
        )
        mock_vetor.return_value = '[0.1,0.2,0.3]'

        from chat.services.sql_generator import gerar_sql
        r = gerar_sql('empresas de energia solar')

        self.assertTrue(r['ok'])
        self.assertIn("'[0.1,0.2,0.3]'::halfvec(384)", r['sql'])
        self.assertNotIn('%s', r['sql'])
        self.assertNotIn('::halfvec(384)::halfvec(384)', r['sql'])

    @patch('chat.services.sql_generator.gerar_resposta')
    @patch('chat.services.embedding_service.gerar_vetor')
    def test_gerar_sql_sem_busca_vetorial(self, mock_vetor, mock_llm):
        # LLM gera SQL normal (sem placeholder)
        mock_llm.return_value = 'SELECT COUNT(*) FROM c_empresas'

        from chat.services.sql_generator import gerar_sql
        r = gerar_sql('quantas empresas existem')

        self.assertTrue(r['ok'])
        self.assertNotIn('%s', r['sql'])
        # O gerar_vetor nem deve ser chamado
        mock_vetor.assert_not_called()
