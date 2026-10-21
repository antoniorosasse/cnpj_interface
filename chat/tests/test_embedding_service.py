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


class IntegracaoGerarSqlTests(TestCase):
    """Testa se o gerar_sql injeta o vetor corretamente."""

    @patch('chat.services.sql_generator.gerar_resposta')
    @patch('chat.services.embedding_service.gerar_vetor')
    def test_gerar_sql_com_busca_vetorial(self, mock_vetor, mock_llm):
        # LLM gera SQL com placeholder
        mock_llm.return_value = (
            'SELECT e.cnpj_basico, emp.razao_social, c.nm_cnae '
            'FROM c_estabelecimentos e '
            'JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico '
            'JOIN c_cnaes c ON e.cnae_principal = c.cnae '
            'WHERE e.cd_matriz_filial = 1 '
            'AND e.cnae_principal IN ( '
            '  SELECT cnae FROM c_cnaes ORDER BY embedding <=> %s::halfvec(384) LIMIT 5 '
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
