# cnpj_interface/chat/tests/test_mongo_models.py
"""
Testes das funcoes de acesso ao MongoDB (chat/mongo_models.py).

Usa o Mongo real (collection chat_conversas) e limpa antes/depois
de cada teste.

ATENCAO: estes testes APAGAM todos os documentos da collection
'chat_conversas' no banco configurado. Nao rodar com dados reais
sem um banco de teste separado.

Roda com:
    python manage.py test chat.tests.test_mongo_models

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from chat import mongo_models as mm


def _limpar_collection():
    """Apaga todos os documentos da collection chat_conversas."""
    mm._get_collection().delete_many({})


class MongoModelsBaseTests(TestCase):
    """Base: limpa a collection antes e depois de cada teste."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Confirma que o Mongo esta acessivel antes de rodar os testes
        try:
            mm._get_collection().database.client.admin.command('ping')
        except Exception as e:
            raise RuntimeError(
                f'Nao foi possivel conectar ao MongoDB: {e}. '
                'Verifique se o Mongo esta rodando e o .env esta correto.'
            )

    def setUp(self):
        _limpar_collection()

    def tearDown(self):
        _limpar_collection()


# ============================================================
# criar_conversa
# ============================================================

class CriarConversaTests(MongoModelsBaseTests):

    def test_cria_conversa_vazia(self):
        cid = mm.criar_conversa(usuario_id=1)
        self.assertIsInstance(cid, str)
        self.assertEqual(len(cid), 24)  # ObjectId tem 24 chars hex

    def test_cria_conversa_com_titulo(self):
        cid = mm.criar_conversa(usuario_id=1, titulo='Minha conversa')
        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertEqual(doc['titulo'], 'Minha conversa')

    def test_cria_conversa_sem_titulo_vira_string_vazia(self):
        cid = mm.criar_conversa(usuario_id=1, titulo='')
        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertEqual(doc['titulo'], '')

    def test_conversa_nova_tem_lista_mensagens_vazia(self):
        cid = mm.criar_conversa(usuario_id=1)
        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertEqual(doc['mensagens'], [])

    def test_conversa_nova_tem_timestamps(self):
        cid = mm.criar_conversa(usuario_id=1)
        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertIsInstance(doc['criado_em'], datetime)
        self.assertIsInstance(doc['atualizado_em'], datetime)


# ============================================================
# buscar_conversa
# ============================================================

class BuscarConversaTests(MongoModelsBaseTests):

    def test_busca_conversa_existente(self):
        cid = mm.criar_conversa(usuario_id=42)
        doc = mm.buscar_conversa(cid, usuario_id=42)
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertEqual(doc['usuario_id'], 42)

    def test_busca_conversa_de_outro_usuario_retorna_none(self):
        cid = mm.criar_conversa(usuario_id=1)
        doc = mm.buscar_conversa(cid, usuario_id=2)
        self.assertIsNone(doc)

    def test_busca_conversa_inexistente_retorna_none(self):
        # ObjectId valido mas que nao existe
        doc = mm.buscar_conversa('000000000000000000000000', usuario_id=1)
        self.assertIsNone(doc)

    def test_busca_conversa_id_invalido_retorna_none(self):
        doc = mm.buscar_conversa('nao-e-um-objectid', usuario_id=1)
        self.assertIsNone(doc)

    def test_busca_conversa_id_none_retorna_none(self):
        doc = mm.buscar_conversa(None, usuario_id=1)
        self.assertIsNone(doc)


# ============================================================
# listar_conversas
# ============================================================

class ListarConversasTests(MongoModelsBaseTests):

    def test_lista_vazia(self):
        conversas = mm.listar_conversas(usuario_id=1)
        self.assertEqual(conversas, [])

    def test_lista_todas_do_usuario(self):
        mm.criar_conversa(usuario_id=1, titulo='primeira')
        mm.criar_conversa(usuario_id=1, titulo='segunda')
        mm.criar_conversa(usuario_id=2, titulo='de outro usuario')

        conversas = mm.listar_conversas(usuario_id=1)
        self.assertEqual(len(conversas), 2)
        titulos = [c['titulo'] for c in conversas]
        self.assertIn('primeira', titulos)
        self.assertIn('segunda', titulos)
        self.assertNotIn('de outro usuario', titulos)

    def test_ordena_por_atualizado_em_desc(self):
        import time
        c1 = mm.criar_conversa(usuario_id=1, titulo='antiga')
        time.sleep(0.01)
        c2 = mm.criar_conversa(usuario_id=1, titulo='recente')

        conversas = mm.listar_conversas(usuario_id=1)
        self.assertEqual(conversas[0]['titulo'], 'recente')
        self.assertEqual(conversas[1]['titulo'], 'antiga')

    def test_limite_respeitado(self):
        for i in range(5):
            mm.criar_conversa(usuario_id=1, titulo=f'c{i}')

        conversas = mm.listar_conversas(usuario_id=1, limite=3)
        self.assertEqual(len(conversas), 3)

    def test_traz_total_mensagens(self):
        cid = mm.criar_conversa(usuario_id=1)
        mm.adicionar_mensagem(cid, 'user', 'oi')
        mm.adicionar_mensagem(cid, 'assistant', 'ola')

        conversas = mm.listar_conversas(usuario_id=1)
        self.assertEqual(conversas[0]['total_mensagens'], 2)

    def test_nao_traz_array_mensagens_completo(self):
        """A listagem deve ser leve: sem o array 'mensagens' completo."""
        cid = mm.criar_conversa(usuario_id=1)
        mm.adicionar_mensagem(cid, 'user', 'oi')

        conversas = mm.listar_conversas(usuario_id=1)
        self.assertNotIn('mensagens', conversas[0])


# ============================================================
# adicionar_mensagem
# ============================================================

class AdicionarMensagemTests(MongoModelsBaseTests):

    def test_adiciona_mensagem_user(self):
        cid = mm.criar_conversa(usuario_id=1)
        ok = mm.adicionar_mensagem(cid, 'user', 'Quantas empresas?')
        self.assertTrue(ok)

        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertEqual(len(doc['mensagens']), 1)
        self.assertEqual(doc['mensagens'][0]['papel'], 'user')
        self.assertEqual(doc['mensagens'][0]['conteudo'], 'Quantas empresas?')

    def test_adiciona_mensagem_assistant_com_extras(self):
        cid = mm.criar_conversa(usuario_id=1)
        mm.adicionar_mensagem(
            cid,
            'assistant',
            'SELECT COUNT(*) FROM c_empresas',
            sql='SELECT COUNT(*) FROM c_empresas',
            colunas=['count'],
            rows=[{'count': 36905702}],
            total_linhas=1,
            truncado=False,
            tempo_ms=1234,
        )

        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        msg = doc['mensagens'][0]
        self.assertEqual(msg['papel'], 'assistant')
        self.assertEqual(msg['sql'], 'SELECT COUNT(*) FROM c_empresas')
        self.assertEqual(msg['colunas'], ['count'])
        self.assertEqual(msg['rows'], [{'count': 36905702}])
        self.assertEqual(msg['total_linhas'], 1)
        self.assertFalse(msg['truncado'])
        self.assertEqual(msg['tempo_ms'], 1234)

    def test_extras_none_nao_sao_gravados(self):
        cid = mm.criar_conversa(usuario_id=1)
        mm.adicionar_mensagem(cid, 'assistant', 'ok', erro=None)

        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        msg = doc['mensagens'][0]
        self.assertNotIn('erro', msg)

    def test_papel_invalido_levanta_erro(self):
        cid = mm.criar_conversa(usuario_id=1)
        with self.assertRaises(ValueError):
            mm.adicionar_mensagem(cid, 'system', 'oi')

    def test_conversa_inexistente_retorna_false(self):
        ok = mm.adicionar_mensagem('000000000000000000000000', 'user', 'oi')
        self.assertFalse(ok)

    def test_atualiza_atualizado_em(self):
        cid = mm.criar_conversa(usuario_id=1)
        doc_antes = mm.buscar_conversa(cid, usuario_id=1)

        import time
        time.sleep(0.01)
        mm.adicionar_mensagem(cid, 'user', 'oi')

        doc_depois = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc_antes)
        self.assertIsNotNone(doc_depois)
        assert doc_antes is not None
        assert doc_depois is not None
        self.assertGreater(doc_depois['atualizado_em'], doc_antes['atualizado_em'])

    def test_serializa_decimal(self):
        """Decimal do Postgres vira float no Mongo."""
        cid = mm.criar_conversa(usuario_id=1)
        mm.adicionar_mensagem(
            cid,
            'assistant',
            'ok',
            rows=[{'capital': Decimal('8013289.50')}],
        )
        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertEqual(doc['mensagens'][0]['rows'][0]['capital'], 8013289.50)

    def test_serializa_date(self):
        """date (sem hora) vira datetime meia-noite."""
        from datetime import date
        cid = mm.criar_conversa(usuario_id=1)
        mm.adicionar_mensagem(
            cid,
            'assistant',
            'ok',
            rows=[{'data': date(2020, 1, 15)}],
        )
        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        valor = doc['mensagens'][0]['rows'][0]['data']
        self.assertIsInstance(valor, datetime)
        self.assertEqual(valor.year, 2020)
        self.assertEqual(valor.month, 1)
        self.assertEqual(valor.day, 15)


# ============================================================
# atualizar_titulo
# ============================================================

class AtualizarTituloTests(MongoModelsBaseTests):

    def test_atualiza_titulo(self):
        cid = mm.criar_conversa(usuario_id=1, titulo='antigo')
        ok = mm.atualizar_titulo(cid, 'novo')
        self.assertTrue(ok)

        doc = mm.buscar_conversa(cid, usuario_id=1)
        self.assertIsNotNone(doc)
        assert doc is not None
        self.assertEqual(doc['titulo'], 'novo')

    def test_conversa_inexistente_retorna_false(self):
        ok = mm.atualizar_titulo('000000000000000000000000', 'x')
        self.assertFalse(ok)


# ============================================================
# deletar_conversa
# ============================================================

class DeletarConversaTests(MongoModelsBaseTests):

    def test_deleta_conversa_do_usuario(self):
        cid = mm.criar_conversa(usuario_id=1)
        ok = mm.deletar_conversa(cid, usuario_id=1)
        self.assertTrue(ok)
        self.assertIsNone(mm.buscar_conversa(cid, usuario_id=1))

    def test_nao_deleta_conversa_de_outro_usuario(self):
        cid = mm.criar_conversa(usuario_id=1)
        ok = mm.deletar_conversa(cid, usuario_id=2)
        self.assertFalse(ok)
        # A conversa continua existindo
        self.assertIsNotNone(mm.buscar_conversa(cid, usuario_id=1))

    def test_conversa_inexistente_retorna_false(self):
        ok = mm.deletar_conversa('000000000000000000000000', usuario_id=1)
        self.assertFalse(ok)


# ============================================================
# tem_mensagens
# ============================================================

class TemMensagensTests(MongoModelsBaseTests):

    def test_conversa_vazia_retorna_false(self):
        cid = mm.criar_conversa(usuario_id=1)
        self.assertFalse(mm.tem_mensagens(cid))

    def test_conversa_com_mensagem_retorna_true(self):
        cid = mm.criar_conversa(usuario_id=1)
        mm.adicionar_mensagem(cid, 'user', 'oi')
        self.assertTrue(mm.tem_mensagens(cid))

    def test_conversa_inexistente_retorna_false(self):
        self.assertFalse(mm.tem_mensagens('000000000000000000000000'))


# ============================================================
# ultimas_mensagens
# ============================================================

class UltimasMensagensTests(MongoModelsBaseTests):

    def test_conversa_vazia_retorna_lista_vazia(self):
        cid = mm.criar_conversa(usuario_id=1)
        self.assertEqual(mm.ultimas_mensagens(cid), [])

    def test_retorna_em_ordem_cronologica(self):
        cid = mm.criar_conversa(usuario_id=1)
        mm.adicionar_mensagem(cid, 'user', 'primeira')
        mm.adicionar_mensagem(cid, 'assistant', 'segunda')
        mm.adicionar_mensagem(cid, 'user', 'terceira')

        msgs = mm.ultimas_mensagens(cid)
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0]['conteudo'], 'primeira')
        self.assertEqual(msgs[1]['conteudo'], 'segunda')
        self.assertEqual(msgs[2]['conteudo'], 'terceira')

    def test_respeita_limite(self):
        cid = mm.criar_conversa(usuario_id=1)
        for i in range(5):
            mm.adicionar_mensagem(cid, 'user', f'msg{i}')

        msgs = mm.ultimas_mensagens(cid, limite=2)
        self.assertEqual(len(msgs), 2)
        # Ultimas 2: msg3, msg4
        self.assertEqual(msgs[0]['conteudo'], 'msg3')
        self.assertEqual(msgs[1]['conteudo'], 'msg4')


# ============================================================
# listar_conversas_vazias
# ============================================================

class ListarConversasVaziasTests(MongoModelsBaseTests):

    def test_sem_conversas(self):
        self.assertEqual(mm.listar_conversas_vazias(usuario_id=1), [])

    def test_retorna_so_as_vazias(self):
        c1 = mm.criar_conversa(usuario_id=1, titulo='vazia1')
        c2 = mm.criar_conversa(usuario_id=1, titulo='com msg')
        mm.criar_conversa(usuario_id=1, titulo='vazia2')
        mm.adicionar_mensagem(c2, 'user', 'oi')

        vazias = mm.listar_conversas_vazias(usuario_id=1)
        self.assertEqual(len(vazias), 2)
        titulos = [v['titulo'] for v in vazias]
        self.assertIn('vazia1', titulos)
        self.assertIn('vazia2', titulos)
        self.assertNotIn('com msg', titulos)

    def test_nao_retorna_de_outro_usuario(self):
        mm.criar_conversa(usuario_id=2, titulo='de outro')
        vazias = mm.listar_conversas_vazias(usuario_id=1)
        self.assertEqual(vazias, [])


# ============================================================
# limpar_conversas_antigas
# ============================================================

class LimparConversasAntigasTests(MongoModelsBaseTests):

    def test_remove_so_antigas(self):
        cid_antiga = mm.criar_conversa(usuario_id=1, titulo='antiga')
        cid_nova = mm.criar_conversa(usuario_id=1, titulo='nova')

        # Forca a 'antiga' a ter atualizado_em no passado
        from bson import ObjectId
        mm._get_collection().update_one(
            {'_id': ObjectId(cid_antiga)},
            {'$set': {'atualizado_em': datetime.utcnow() - timedelta(days=60)}},
        )

        removidas = mm.limpar_conversas_antigas(usuario_id=1, dias=30)
        self.assertEqual(removidas, 1)
        self.assertIsNone(mm.buscar_conversa(cid_antiga, usuario_id=1))
        self.assertIsNotNone(mm.buscar_conversa(cid_nova, usuario_id=1))

    def test_nao_remove_de_outro_usuario(self):
        cid = mm.criar_conversa(usuario_id=2, titulo='antiga')
        from bson import ObjectId
        mm._get_collection().update_one(
            {'_id': ObjectId(cid)},
            {'$set': {'atualizado_em': datetime.utcnow() - timedelta(days=60)}},
        )

        removidas = mm.limpar_conversas_antigas(usuario_id=1, dias=30)
        self.assertEqual(removidas, 0)
        self.assertIsNotNone(mm.buscar_conversa(cid, usuario_id=2))
