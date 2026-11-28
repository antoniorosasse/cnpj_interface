# cnpj_interface/chat/tests/test_sql_generator.py
"""
Testes do gerador de SQL (sql_generator.py).

Usa mock para nao chamar a API DeepSeek de verdade.

Roda com:
    python manage.py test chat.tests.test_sql_generator
"""

import re
from unittest.mock import patch

from django.test import TestCase

from chat.services.sql_generator import (
    gerar_sql,
    montar_bloco_categorias,
    _limpar_sql,
    _montar_user_prompt,
    _usa_fallback_vetor,
    SYSTEM_PROMPT,
)
from chat.services.schema_context import SCHEMA_CONTEXT
from chat.services.llm_client import LLMError


# Caminho do alvo a ser mockado
ALVO_LLM = 'chat.services.sql_generator.gerar_resposta'


class LimparSqlTests(TestCase):
    """Testes da funcao _limpar_sql (nao depende do LLM)."""

    def test_remove_cerca_markdown_sql(self):
        bruto = '```sql\nSELECT 1\n```'
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')

    def test_remove_cerca_markdown_simples(self):
        bruto = '```\nSELECT 1\n```'
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')

    def test_remove_prefixo_sql(self):
        bruto = 'SQL: SELECT 1'
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')

    def test_remove_ponto_virgula_final(self):
        bruto = 'SELECT 1;'
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')

    def test_string_vazia(self):
        self.assertEqual(_limpar_sql(''), '')

    def test_none(self):
        self.assertEqual(_limpar_sql(None), '')

    def test_espacos_extras(self):
        bruto = '   SELECT 1   '
        self.assertEqual(_limpar_sql(bruto), 'SELECT 1')


class MontarUserPromptTests(TestCase):
    """Testes da montagem do prompt do usuario."""

    def test_sem_historico(self):
        out = _montar_user_prompt('Quantas empresas?')
        self.assertIn('Pergunta atual: Quantas empresas?', out)
        self.assertNotIn('Historico', out)

    def test_com_historico(self):
        historico = [
            {'role': 'user', 'content': 'ola'},
            {'role': 'assistant', 'content': 'SELECT 1'},
        ]
        out = _montar_user_prompt('e agora?', historico)
        self.assertIn('Historico da conversa', out)
        self.assertIn('Usuario: ola', out)
        self.assertIn('Assistente: SELECT 1', out)
        self.assertIn('Pergunta atual: e agora?', out)

    def test_historico_ignora_papeis_invalidos(self):
        historico = [
            {'role': 'system', 'content': 'ignorar'},
            {'role': 'user', 'content': 'manter'},
        ]
        out = _montar_user_prompt('pergunta', historico)
        self.assertNotIn('ignorar', out)
        self.assertIn('manter', out)


class GerarSqlTests(TestCase):
    """Testes da funcao principal gerar_sql (com mock do LLM)."""

    def test_pergunta_vazia(self):
        r = gerar_sql('')
        self.assertFalse(r['ok'])
        self.assertEqual(r['tentativas'], 0)
        self.assertIn('vazia', r['erro'].lower())

    def test_pergunta_so_espacos(self):
        r = gerar_sql('   ')
        self.assertFalse(r['ok'])
        self.assertEqual(r['tentativas'], 0)

    @patch(ALVO_LLM)
    def test_sucesso_primeira_tentativa(self, mock_llm):
        mock_llm.return_value = 'SELECT 1'
        r = gerar_sql('pergunta qualquer')
        self.assertTrue(r['ok'])
        # O validador adiciona LIMIT 1000 quando o SQL nao tem LIMIT
        self.assertEqual(r['sql'], 'SELECT 1 LIMIT 1000')
        self.assertEqual(r['tentativas'], 1)
        self.assertIsNotNone(r['validation'])
        self.assertEqual(mock_llm.call_count, 1)

    @patch(ALVO_LLM)
    def test_llm_devolve_com_markdown(self, mock_llm):
        mock_llm.return_value = '```sql\nSELECT 1\n```'
        r = gerar_sql('pergunta')
        self.assertTrue(r['ok'])
        # O markdown foi removido E o validador adicionou LIMIT
        self.assertEqual(r['sql'], 'SELECT 1 LIMIT 1000')

    @patch(ALVO_LLM)
    def test_llm_devolve_vazio(self, mock_llm):
        # Duas respostas vazias -> falha
        mock_llm.return_value = ''
        r = gerar_sql('pergunta', max_tentativas=2)
        self.assertFalse(r['ok'])
        self.assertEqual(r['tentativas'], 2)
        self.assertIn('vazia', r['erro'].lower())
        self.assertEqual(mock_llm.call_count, 2)

    @patch(ALVO_LLM)
    def test_sql_invalido_retorna_falha(self, mock_llm):
        # LLM sempre devolve DROP -> validador rejeita em todas as tentativas
        mock_llm.return_value = 'DROP TABLE c_empresas'
        r = gerar_sql('pergunta', max_tentativas=2)
        self.assertFalse(r['ok'])
        self.assertEqual(r['tentativas'], 2)
        self.assertIn('proibida', r['erro'].lower())
        self.assertEqual(mock_llm.call_count, 2)

    @patch(ALVO_LLM)
    def test_retry_apos_sql_invalido(self, mock_llm):
        # Primeira resposta invalida, segunda valida
        mock_llm.side_effect = [
            'DROP TABLE c_empresas',
            'SELECT 1',
        ]
        r = gerar_sql('pergunta', max_tentativas=2)
        self.assertTrue(r['ok'])
        # SQL passa pelo validador, que adiciona LIMIT
        self.assertEqual(r['sql'], 'SELECT 1 LIMIT 1000')
        self.assertEqual(r['tentativas'], 2)
        self.assertEqual(mock_llm.call_count, 2)

    @patch(ALVO_LLM)
    def test_erro_llm_retorna_falha(self, mock_llm):
        mock_llm.side_effect = LLMError('timeout')
        r = gerar_sql('pergunta')
        self.assertFalse(r['ok'])
        self.assertIn('LLM', r['erro'])
        self.assertEqual(r['tentativas'], 1)

    @patch(ALVO_LLM)
    def test_sql_com_limit_existente(self, mock_llm):
        mock_llm.return_value = 'SELECT * FROM c_empresas LIMIT 10'
        r = gerar_sql('pergunta')
        self.assertTrue(r['ok'])
        # O validador mantem o LIMIT 10 (menor que o maximo)
        self.assertIn('LIMIT 10', r['sql'])
        self.assertFalse(r['validation'].teve_limit)

    @patch(ALVO_LLM)
    def test_sql_sem_limit_ganha_limit(self, mock_llm):
        mock_llm.return_value = 'SELECT * FROM c_empresas'
        r = gerar_sql('pergunta')
        self.assertTrue(r['ok'])
        self.assertIn('LIMIT', r['sql'].upper())
        self.assertTrue(r['validation'].teve_limit)
        
class FallbackVetorTests(TestCase):
    """
    Opcao 1 da busca semantica: o vetor SO pode entrar como fallback
    do ILIKE, protegido por NOT EXISTS (ver schema_context.py).
    """

    SQL_COM_FALLBACK = (
        'SELECT e.cnpj_basico FROM c_estabelecimentos e '
        'WHERE e.cnae_principal IN ( '
        "  SELECT cnae FROM c_cnaes WHERE nm_cnae ILIKE '%supermercado%' "
        '  UNION '
        '  SELECT cnae FROM ( '
        '    SELECT cnae FROM c_cnaes '
        '    WHERE NOT EXISTS ( '
        '      SELECT 1 FROM c_cnaes '
        "      WHERE nm_cnae ILIKE '%supermercado%' "
        '    ) '
        '    ORDER BY embedding <=> %s::halfvec(768) LIMIT 2 '
        '  ) sub '
        ') LIMIT 100'
    )

    SQL_SEM_FALLBACK = (
        'SELECT e.cnpj_basico FROM c_estabelecimentos e '
        'WHERE e.cnae_principal IN ( '
        "  SELECT cnae FROM c_cnaes WHERE nm_cnae ILIKE '%supermercado%' "
        '  UNION '
        '  SELECT cnae FROM ( '
        '    SELECT cnae FROM c_cnaes '
        '    ORDER BY embedding <=> %s::halfvec(768) LIMIT 2 '
        '  ) sub '
        ') LIMIT 100'
    )

    SQL_VETOR_PURO = (
        'SELECT e.cnpj_basico FROM c_estabelecimentos e '
        'WHERE e.cnae_principal IN ( '
        '  SELECT cnae FROM c_cnaes '
        '  ORDER BY embedding <=> %s::halfvec(768) LIMIT 2 '
        ') LIMIT 100'
    )

    def test_helper_aceita_not_exists(self):
        self.assertTrue(_usa_fallback_vetor(self.SQL_COM_FALLBACK))

    def test_helper_rejeita_union_sem_not_exists(self):
        self.assertFalse(_usa_fallback_vetor(self.SQL_SEM_FALLBACK))

    def test_helper_rejeita_vetor_puro(self):
        self.assertFalse(_usa_fallback_vetor(self.SQL_VETOR_PURO))

    def test_helper_ignora_sql_sem_vetor(self):
        self.assertFalse(_usa_fallback_vetor('SELECT 1 LIMIT 10'))

    @patch(ALVO_LLM)
    @patch('chat.services.embedding_service.gerar_vetor')
    def test_sql_com_fallback_injeta_vetor(self, mock_vetor, mock_llm):
        mock_llm.return_value = self.SQL_COM_FALLBACK
        mock_vetor.return_value = '[0.1,0.2]'

        r = gerar_sql('energia solar em picarras')

        self.assertTrue(r['ok'])
        self.assertEqual(r['tentativas'], 1)
        self.assertIn("'[0.1,0.2]'::halfvec(768)", r['sql'])
        self.assertNotIn('<=> %s', r['sql'])
        mock_vetor.assert_called_once_with('energia solar em picarras')

    @patch(ALVO_LLM)
    @patch('chat.services.embedding_service.gerar_vetor')
    def test_sql_sem_fallback_retenta_com_feedback(self, mock_vetor, mock_llm):
        mock_llm.side_effect = [self.SQL_SEM_FALLBACK, self.SQL_COM_FALLBACK]
        mock_vetor.return_value = '[0.1,0.2]'

        r = gerar_sql('energia solar em picarras', max_tentativas=2)

        self.assertTrue(r['ok'])
        self.assertEqual(r['tentativas'], 2)
        self.assertEqual(mock_llm.call_count, 2)
        # A segunda chamada recebe o feedback explicando o NOT EXISTS
        segundo_prompt = mock_llm.call_args.kwargs.get('user_prompt')
        self.assertIn('NOT EXISTS', segundo_prompt)
        self.assertIn('rejeitado pelo gerador', segundo_prompt)

    @patch(ALVO_LLM)
    @patch('chat.services.embedding_service.gerar_vetor')
    def test_sql_sem_fallback_falha_apos_tentativas(self, mock_vetor, mock_llm):
        mock_llm.return_value = self.SQL_SEM_FALLBACK

        r = gerar_sql('energia solar em picarras', max_tentativas=2)

        self.assertFalse(r['ok'])
        self.assertEqual(r['tipo_erro'], 'validacao')
        self.assertIn('fallback', r['erro'].lower())
        self.assertEqual(mock_llm.call_count, 2)
        # Nao chega a gerar vetor: o SQL e' barrado antes da injecao
        mock_vetor.assert_not_called()

    @patch(ALVO_LLM)
    @patch('chat.services.embedding_service.gerar_vetor')
    def test_vetor_puro_falha_apos_tentativas(self, mock_vetor, mock_llm):
        mock_llm.return_value = self.SQL_VETOR_PURO

        r = gerar_sql('energia solar', max_tentativas=2)

        self.assertFalse(r['ok'])
        self.assertEqual(r['tipo_erro'], 'validacao')
        self.assertEqual(mock_llm.call_count, 2)
        mock_vetor.assert_not_called()


class AtividadeENomeTests(TestCase):
    """
    Pergunta com ATIVIDADE + MARCA (ex: "mercados KOCH") precisa dos
    dois filtros: CNAE (regra 10) E nome fantasia (regra 9).

    Regressao: sem o filtro de CNAE a query virava "qualquer empresa
    com KOCH no nome", o LIMIT 100 cortava a lista e o KOCH
    HIPERMERCADO S/A (CNAE 4691500) nem aparecia.
    """

    SQL_ATIVIDADE_E_NOME = (
        'SELECT e.cnpj_basico, emp.razao_social, e.nm_fantasia, c.nm_cnae '
        'FROM c_estabelecimentos e '
        'JOIN c_empresas emp ON emp.cnpj_basico = e.cnpj_basico '
        'JOIN c_cnaes c ON c.cnae = e.cnae_principal '
        'WHERE e.cd_matriz_filial = 1 '
        'AND e.cnae_principal IN ( '
        'SELECT cnae FROM c_cnaes '
        "WHERE nm_cnae ILIKE '%mercado%' "
        'UNION '
        'SELECT cnae FROM ( '
        'SELECT cnae FROM c_cnaes '
        'WHERE NOT EXISTS ( '
        'SELECT 1 FROM c_cnaes '
        "WHERE nm_cnae ILIKE '%mercado%' "
        ') '
        'ORDER BY embedding <=> %s::halfvec(768) LIMIT 2 '
        ') sub '
        ') '
        'AND EXISTS ( '
        'SELECT 1 FROM c_estabelecimentos sub '
        'WHERE sub.cnpj_basico = e.cnpj_basico '
        "AND sub.nm_fantasia ILIKE '%KOCH%' "
        ') '
        'ORDER BY emp.razao_social '
        'LIMIT 100'
    )

    @staticmethod
    def _sem_quebras(texto):
        """Junta as quebras de linha do prompt para comparar frases."""
        return re.sub(r'\s+', ' ', texto)

    def test_prompt_do_sistema_exige_os_dois_filtros(self):
        texto = self._sem_quebras(SYSTEM_PROMPT)
        self.assertIn('mercados KOCH', texto)
        self.assertIn('NUNCA descarte o filtro de atividade', texto)
        self.assertIn('ORDER BY emp.razao_social', texto)
        # Regras novas: capital social e data de abertura (dt_ini).
        self.assertIn('capital_social', texto)
        self.assertIn('dt_ini', texto)

    def test_schema_context_tem_secao_atividade_e_nome(self):
        texto = self._sem_quebras(SCHEMA_CONTEXT)
        self.assertIn('ATIVIDADE + NOME (MARCA)', texto)
        self.assertIn("ILIKE '%mercado%'", texto)
        self.assertIn("nm_fantasia ILIKE '%KOCH%'", texto)

    @patch(ALVO_LLM)
    @patch('chat.services.embedding_service.gerar_vetor')
    def test_sql_com_atividade_e_nome_injeta_vetor(self, mock_vetor, mock_llm):
        mock_llm.return_value = self.SQL_ATIVIDADE_E_NOME
        mock_vetor.return_value = '[0.1,0.2]'

        r = gerar_sql('liste os mercados KOCH')

        self.assertTrue(r['ok'])
        # O vetor entra na subquery do fallback...
        self.assertIn("'[0.1,0.2]'::halfvec(768)", r['sql'])
        self.assertNotIn('<=> %s', r['sql'])
        # ...e os dois filtros continuam na query final
        self.assertIn("ILIKE '%mercado%'", r['sql'])
        self.assertIn("ILIKE '%KOCH%'", r['sql'])
        mock_vetor.assert_called_once_with('liste os mercados KOCH')


class ContagemDeEstabelecimentosTests(TestCase):
    """
    Listagens de empresas devem trazer qtd_estabelecimentos
    (matriz + filiais), que o texto natural cita e a tabela exibe.
    """

    def test_prompt_pede_contagem_de_estabelecimentos(self):
        texto = re.sub(r'\s+', ' ', SYSTEM_PROMPT)
        self.assertIn('qtd_estabelecimentos', texto)
        self.assertIn('COUNT(*) FROM c_estabelecimentos x', texto)

    def test_schema_context_explica_a_contagem(self):
        texto = re.sub(r'\s+', ' ', SCHEMA_CONTEXT)
        self.assertIn('qtd_estabelecimentos', texto)


class FiltroGeograficoPorEstabelecimentoTests(TestCase):
    """
    Regressao: "atacadistas de picarras" precisa trazer empresas que tem
    ESTABELECIMENTO na cidade, mesmo com a matriz em outra cidade.

    Caso real: KOCH HIPERMERCADO S/A (cnpj_basico 02831172) tem a matriz
    em Tijucas (CNAE 4691500, atacadista) e 4 filiais em Balneario de
    Picarras. Com o filtro e.municipio (na matriz) a rede nao aparecia.
    """

    SQL_SEMI_JOIN = (
        'SELECT e.cnpj_basico, '
        'e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo, '
        'emp.razao_social, e.nm_fantasia, m.municipio, e.uf, c.nm_cnae '
        'FROM c_estabelecimentos e '
        'JOIN c_empresas emp ON emp.cnpj_basico = e.cnpj_basico '
        'JOIN c_cnaes c ON c.cnae = e.cnae_principal '
        'LEFT JOIN c_municipios m ON m.cd_municipio = e.municipio '
        'WHERE e.cd_matriz_filial = 1 '
        "AND e.cnae_principal IN (SELECT cnae FROM c_cnaes "
        "WHERE nm_cnae ILIKE '%atacadista%') "
        'AND e.cnpj_basico IN ( '
        'SELECT f.cnpj_basico FROM c_estabelecimentos f '
        'WHERE f.municipio IN ( '
        'SELECT cd_municipio FROM c_municipios '
        "WHERE municipio ILIKE '%PICARRAS%' "
        ') '
        ') '
        'ORDER BY emp.razao_social '
        'LIMIT 100'
    )

    @staticmethod
    def _sem_quebras(texto):
        """Junta as quebras de linha do prompt para comparar frases."""
        return re.sub(r'\s+', ' ', texto)

    def test_prompt_usa_semi_join_e_nao_exists_na_cidade(self):
        texto = self._sem_quebras(SYSTEM_PROMPT)
        self.assertIn('FILTRO POR CIDADE', texto)
        self.assertIn(
            'e.cnpj_basico IN ( SELECT f.cnpj_basico '
            'FROM c_estabelecimentos f',
            texto,
        )
        self.assertIn('NUNCA use EXISTS correlacionado', texto)

    def test_prompt_cita_o_caso_koch(self):
        texto = self._sem_quebras(SYSTEM_PROMPT)
        self.assertIn('02831172', texto)
        self.assertIn('liste os atacadistas de picarras', texto)

    def test_prompt_tem_excecoes_de_custo(self):
        texto = self._sem_quebras(SYSTEM_PROMPT)
        self.assertIn('CAPITAL OU METROPOLE', texto)
        self.assertIn('ATIVIDADE MUITO COMUM', texto)
        self.assertIn('CONTAGEM PURA', texto)

    def test_schema_context_documenta_o_filtro_por_estabelecimento(self):
        texto = self._sem_quebras(SCHEMA_CONTEXT)
        self.assertIn('ESTABELECIMENTO NA CIDADE', texto)
        self.assertIn('02831172', texto)
        self.assertIn('FORMA ERRADA (EXISTS correlacionado', texto)

    def test_documentacao_cita_o_indice_de_cidade_e_o_aviso_do_any_array(self):
        """O LLM so escolhe o semi-join se souber do indice que o cobre.

        Regressao de 31/10/2026: idx_estab_municipio_cnpj (municipio,
        cnpj_basico) foi criado em producao (Index Only Scan, ~165 ms para
        os 632 mil cnpj de Curitiba). O `= ANY(ARRAY(SELECT ...))` parece
        mais rapido em cidade pequena, mas desanda em Lages/Curitiba.
        """
        for texto in (self._sem_quebras(SYSTEM_PROMPT),
                      self._sem_quebras(SCHEMA_CONTEXT)):
            self.assertIn('idx_estab_municipio_cnpj', texto)
            self.assertIn('= ANY(ARRAY(SELECT ...))', texto)

    @patch(ALVO_LLM)
    def test_sql_com_semi_join_geografico_e_aceito(self, mock_llm):
        mock_llm.return_value = self.SQL_SEMI_JOIN

        r = gerar_sql('liste os atacadistas de picarras')

        self.assertTrue(r['ok'], msg=r.get('erro'))
        self.assertEqual(r['tentativas'], 1)
        # O semi-join geografico e o filtro de CNAE continuam na query final
        self.assertIn('e.cnpj_basico IN (', r['sql'])
        self.assertIn("municipio ILIKE '%PICARRAS%'", r['sql'])
        self.assertIn("nm_cnae ILIKE '%atacadista%'", r['sql'])
        self.assertTrue(r['sql'].endswith('LIMIT 100'))


class HistoricoNoPromptTests(TestCase):
    """Verifica que o historico e passado ao LLM via prompt."""

    @patch(ALVO_LLM)
    def test_historico_aparece_no_prompt(self, mock_llm):
        mock_llm.return_value = 'SELECT 1'
        historico = [
            {'role': 'user', 'content': 'pergunta antiga'},
            {'role': 'assistant', 'content': 'SELECT id FROM x'},
        ]
        gerar_sql('pergunta nova', historico=historico)

        # Pega o user_prompt que foi passado ao mock
        args, kwargs = mock_llm.call_args
        user_prompt = kwargs.get('user_prompt') or args[1]

        self.assertIn('pergunta antiga', user_prompt)
        self.assertIn('SELECT id FROM x', user_prompt)
        self.assertIn('pergunta nova', user_prompt)
        self.assertIn('Historico da conversa', user_prompt)

    @patch(ALVO_LLM)
    def test_sem_historico_nao_tem_secao(self, mock_llm):
        mock_llm.return_value = 'SELECT 1'
        gerar_sql('pergunta nova', historico=None)

        args, kwargs = mock_llm.call_args
        user_prompt = kwargs.get('user_prompt') or args[1]

        self.assertNotIn('Historico da conversa', user_prompt)


class CategoriaCnaeKBTests(TestCase):
    """
    KB fixa de categorias de negocio (core/cnae_categorias.py).

    As perguntas reais ("industrias metalurgicas", "clinicas medicas e
    odontologicas") nao sao termos do nm_cnae: o ILIKE acha coisa errada
    ("clinica" casa "clinicas de estetica") e o vetor, sozinho, traz no
    maximo 2 vizinhos. O bloco entra no prompt do usuario com o filtro
    CNAE exato e proibe o embedding para a atividade.
    """

    def test_bloco_de_metalurgicas_usa_as_divisoes_24_e_25(self):
        bloco = montar_bloco_categorias('industrias metalurgicas em SC')
        self.assertIn('2400000', bloco)
        self.assertIn('2599999', bloco)
        self.assertIn('e.cnae_principal BETWEEN 2400000 AND 2599999', bloco)
        # Instrucao explicita: nada de embedding/vetor para a atividade.
        self.assertIn('NAO use embedding', bloco)
        self.assertIn('%s::halfvec(768)', bloco)

    def test_bloco_de_clinicas_usa_os_codigos_da_kb(self):
        bloco = montar_bloco_categorias('clinicas medicas e odontologicas')
        self.assertIn('8630501', bloco)
        self.assertIn('8640202', bloco)
        self.assertIn('e.cnae_principal IN (', bloco)
        self.assertNotIn('BETWEEN', bloco)

    def test_termo_fora_da_kb_nao_gera_bloco(self):
        self.assertEqual(
            montar_bloco_categorias('empresas de energia solar'), '')

    def test_prompt_e_schema_citam_a_kb_e_a_quantidade(self):
        texto = re.sub(r'\s+', ' ', SYSTEM_PROMPT)
        self.assertIn('CATEGORIA CNAE DETECTADA PELA KB', texto)
        self.assertIn('LIMIT N', texto)
        schema = re.sub(r'\s+', ' ', SCHEMA_CONTEXT)
        self.assertIn('CATEGORIAS DE NEGOCIO (KB FIXA)', schema)
        self.assertIn('BETWEEN 1000000 AND 3399999', schema)
        self.assertIn('8630501', schema)

    @patch(ALVO_LLM)
    def test_gerar_sql_inclui_o_bloco_no_user_prompt(self, mock_llm):
        # SQL fake valido: o fluxo (limpeza + validador + LIMIT) nao muda.
        mock_llm.return_value = 'SELECT 1 AS x'
        r = gerar_sql('industrias metalurgicas em SC')

        self.assertTrue(r['ok'], msg=r.get('erro'))
        self.assertEqual(r['sql'], 'SELECT 1 AS x LIMIT 1000')

        user_prompt = mock_llm.call_args.kwargs.get('user_prompt')
        self.assertIn('CATEGORIA CNAE DETECTADA PELA KB', user_prompt)
        self.assertIn('2400000', user_prompt)
        self.assertIn(
            'Pergunta atual: industrias metalurgicas em SC', user_prompt)
        # O bloco vem DEPOIS da pergunta atual (nao a substitui).
        self.assertGreater(
            user_prompt.index('CATEGORIA CNAE DETECTADA PELA KB'),
            user_prompt.index('Pergunta atual'),
        )


class CorrecoesPosGeracaoTests(TestCase):
    def test_remove_count_por_linha(self):
        from chat.services.sql_generator import _remover_count_por_linha
        sql = ("SELECT e.cnpj_basico, "
               "(SELECT COUNT(*) FROM c_estabelecimentos x "
               "WHERE x.cnpj_basico = e.cnpj_basico) AS qtd_estabelecimentos, "
               "e.uf FROM c_estabelecimentos e")
        limpo = _remover_count_por_linha(sql)
        self.assertNotIn('qtd_estabelecimentos', limpo)
        self.assertIn('e.uf', limpo)

    def test_valida_cnaes_combinados_da_kb(self):
        from chat.services.sql_generator import _validar_cnaes_da_kb
        pergunta = 'supermercados e padarias em Joinville/SC'
        erro = _validar_cnaes_da_kb(
            'SELECT 1 WHERE e.cnae_principal = 1091102', pergunta)
        self.assertIsNotNone(erro)
        ok = ('SELECT 1 WHERE e.cnae_principal IN '
              '(1091102, 4711302, 4721101, 4721102)')
        self.assertIsNone(_validar_cnaes_da_kb(ok, pergunta))

    def test_remove_order_by_inventado(self):
        from chat.services.sql_generator import _remover_order_by_nao_pedido
        sql = ('SELECT e.cnpj_basico FROM c_estabelecimentos e '
               'ORDER BY emp.razao_social LIMIT 100')
        limpo = _remover_order_by_nao_pedido(
            sql, 'Postos de gasolina em Joinville/SC')
        self.assertNotIn('ORDER BY', limpo)
        self.assertIn('LIMIT 100', limpo)

    def test_mantem_order_by_pedido(self):
        from chat.services.sql_generator import _remover_order_by_nao_pedido
        sql = ('SELECT e.cnpj_basico FROM c_estabelecimentos e '
               'ORDER BY emp.capital_social DESC LIMIT 100')
        limpo = _remover_order_by_nao_pedido(
            sql, 'Ordene as metalurgicas de SC por capital social')
        self.assertIn('ORDER BY emp.capital_social DESC', limpo)

    def test_mantem_order_by_em_agregacao(self):
        from chat.services.sql_generator import _remover_order_by_nao_pedido
        sql = ('SELECT e.uf, COUNT(*) AS total FROM c_estabelecimentos e '
               'GROUP BY e.uf ORDER BY total DESC')
        limpo = _remover_order_by_nao_pedido(
            sql, 'Quantas empresas ativas existem por UF?')
        self.assertIn('ORDER BY total DESC', limpo)

    def test_amostra_multi_categoria_usa_md5(self):
        from chat.services.sql_generator import _amostra_multi_categoria
        sql = ('SELECT e.cnpj_basico FROM c_estabelecimentos e '
               'WHERE e.cd_matriz_filial = 1 LIMIT 100')
        novo = _amostra_multi_categoria(
            sql, 'supermercados e padarias em Joinville/SC')
        self.assertIn('ORDER BY md5(e.cnpj_basico) LIMIT 100', novo)

    def test_amostra_uma_categoria_nao_muda(self):
        from chat.services.sql_generator import _amostra_multi_categoria
        sql = ('SELECT e.cnpj_basico FROM c_estabelecimentos e '
               'WHERE e.cd_matriz_filial = 1 LIMIT 100')
        self.assertEqual(_amostra_multi_categoria(
            sql, 'supermercados em Blumenau/SC'), sql)

    def test_garante_ordem_por_capital_pedida(self):
        from chat.services.sql_generator import _garantir_ordem_pedida
        sql = ('SELECT e.cnpj_basico, emp.capital_social FROM c_estabelecimentos e '
               'JOIN c_empresas emp ON emp.cnpj_basico = e.cnpj_basico '
               "WHERE e.uf = 'SC' LIMIT 100")
        novo = _garantir_ordem_pedida(
            sql, 'Ordene as metalurgicas de SC por capital social')
        self.assertIn('ORDER BY emp.capital_social DESC', novo)

    def test_garante_ordem_por_data_de_abertura(self):
        from chat.services.sql_generator import _garantir_ordem_pedida
        sql = ('SELECT e.cnpj_basico, e.dt_ini FROM c_estabelecimentos e '
               'LIMIT 100')
        novo = _garantir_ordem_pedida(
            sql, 'MEIs ativos em Joinville ordenados por tempo de abertura')
        self.assertIn('ORDER BY e.dt_ini ASC', novo)

    def test_mantem_order_by_de_agregado_pronto(self):
        from chat.services.sql_generator import _remover_order_by_nao_pedido
        sql = ('SELECT chave, valor FROM c_estatisticas_totais '
               "WHERE categoria = 'cnae' ORDER BY valor DESC LIMIT 10")
        limpo = _remover_order_by_nao_pedido(
            sql, 'Quais os 10 CNAEs com mais empresas ativas?')
        self.assertIn('ORDER BY valor DESC', limpo)
