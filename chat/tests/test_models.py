# cnpj_interface/chat/tests/test_models.py
"""
Testes dos modelos Conversa e Mensagem.

Roda com:
    python manage.py test chat.tests.test_models
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from chat import repo_postgres
from chat.models import Conversa, Mensagem


User = get_user_model()


class ConversaTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='teste', password='test-only'
        )

    def test_criar_conversa(self):
        c = Conversa.objects.create(usuario=self.user)
        self.assertIsNotNone(c.pk)
        self.assertEqual(c.usuario, self.user)
        self.assertEqual(c.titulo, '')

    def test_str_com_titulo(self):
        c = Conversa.objects.create(usuario=self.user, titulo='Minha conversa')
        self.assertEqual(str(c), 'Minha conversa')

    def test_str_sem_titulo(self):
        c = Conversa.objects.create(usuario=self.user)
        self.assertIn('Conversa #', str(c))

    def test_total_mensagens(self):
        c = Conversa.objects.create(usuario=self.user)
        self.assertEqual(c.total_mensagens, 0)
        Mensagem.objects.create(conversa=c, papel='user', conteudo='oi')
        self.assertEqual(c.total_mensagens, 1)

    def test_ordering_por_atualizado_em_desc(self):
        c1 = Conversa.objects.create(usuario=self.user, titulo='primeira')
        c2 = Conversa.objects.create(usuario=self.user, titulo='segunda')
        # Atualiza c1 pra ficar mais recente
        c1.save()
        lista = list(Conversa.objects.all())
        self.assertEqual(lista[0].pk, c1.pk)


class MensagemTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='teste2', password='test-only'
        )
        self.conversa = Conversa.objects.create(usuario=self.user)

    def test_criar_mensagem_user(self):
        m = Mensagem.objects.create(
            conversa=self.conversa,
            papel=Mensagem.PAPEL_USER,
            conteudo='Quantas empresas?',
        )
        self.assertIsNotNone(m.pk)
        self.assertEqual(m.papel, 'user')
        self.assertEqual(m.conversa, self.conversa)

    def test_criar_mensagem_assistant(self):
        m = Mensagem.objects.create(
            conversa=self.conversa,
            papel=Mensagem.PAPEL_ASSISTANT,
            conteudo='SELECT COUNT(*) FROM c_empresas',
            sql='SELECT COUNT(*) FROM c_empresas',
            total_linhas=1,
            tempo_ms=1234,
        )
        self.assertEqual(m.sql, 'SELECT COUNT(*) FROM c_empresas')
        self.assertEqual(m.total_linhas, 1)
        self.assertEqual(m.tempo_ms, 1234)

    def test_criar_mensagem_com_plano(self):
        """plano (JSON) da consulta de proximidade e' gravado e lido de volta."""
        plano = {
            'origem': {
                'logradouro': 'Rua Blumenau',
                'numero': '3011',
                'municipio': 'Joinville',
                'uf': 'SC',
            },
            'raio_km': 5.0,
            'filtros': {'uf': 'SC', 'situacao': 2},
            'ordem': 'endereco',
        }
        m = Mensagem.objects.create(
            conversa=self.conversa,
            papel=Mensagem.PAPEL_ASSISTANT,
            conteudo='Encontrei 12 empresas num raio de 5 km.',
            plano=plano,
        )
        lida = Mensagem.objects.get(pk=m.pk)
        self.assertEqual(lida.plano, plano)
        # JSON ida e volta: os tipos continuam os mesmos (float, int, str)
        self.assertEqual(lida.plano['raio_km'], 5.0)
        self.assertEqual(lida.plano['filtros']['situacao'], 2)
        self.assertEqual(lida.plano['origem']['uf'], 'SC')

    def test_mensagem_sem_plano_continua_funcionando(self):
        """Mensagem sem plano (consultas que nao sao de proximidade) e' nula."""
        m = Mensagem.objects.create(
            conversa=self.conversa,
            papel=Mensagem.PAPEL_USER,
            conteudo='oi',
        )
        lida = Mensagem.objects.get(pk=m.pk)
        self.assertIsNone(lida.plano)
        self.assertEqual(lida.conteudo, 'oi')

    def test_cascade_delete(self):
        Mensagem.objects.create(
            conversa=self.conversa,
            papel='user',
            conteudo='oi',
        )
        self.assertEqual(Mensagem.objects.count(), 1)
        self.conversa.delete()
        self.assertEqual(Mensagem.objects.count(), 0)

    def test_ordering_por_criado_em(self):
        m1 = Mensagem.objects.create(
            conversa=self.conversa, papel='user', conteudo='primeira'
        )
        m2 = Mensagem.objects.create(
            conversa=self.conversa, papel='assistant', conteudo='segunda'
        )
        lista = list(self.conversa.mensagens.all())   # type: ignore[attr-defined]
        self.assertEqual(lista[0].pk, m1.pk)
        self.assertEqual(lista[1].pk, m2.pk)


class RepoPostgresPlanoTests(TestCase):
    """repo_postgres.adicionar_mensagem repassa o plano para o JSONField."""

    PLANO = {
        'origem': {'municipio': 'Joinville', 'uf': 'SC'},
        'raio_km': 10.0,
        'filtros': {'situacao': 2, 'cnae': ['4744-0/99']},
    }

    def setUp(self):
        self.user = User.objects.create_user(
            username='teste3', password='test-only'
        )
        self.conversa = Conversa.objects.create(usuario=self.user)

    def test_adicionar_mensagem_com_plano(self):
        ok = repo_postgres.adicionar_mensagem(
            self.conversa.pk,
            'assistant',
            'Achei 42 empresas.',
            sql='SELECT cnpj FROM ...',
            total_linhas=42,
            tempo_ms=120,
            plano=self.PLANO,
        )
        self.assertTrue(ok)
        msg = Mensagem.objects.get(conversa=self.conversa)
        self.assertEqual(msg.papel, 'assistant')
        self.assertEqual(msg.sql, 'SELECT cnpj FROM ...')
        self.assertEqual(msg.plano, self.PLANO)

    def test_adicionar_mensagem_sem_plano_fica_nulo(self):
        ok = repo_postgres.adicionar_mensagem(
            self.conversa.pk, 'user', 'Quantas empresas?',
        )
        self.assertTrue(ok)
        msg = Mensagem.objects.get(conversa=self.conversa)
        self.assertIsNone(msg.plano)
        self.assertEqual(msg.conteudo, 'Quantas empresas?')

    def test_plano_none_nao_quebra(self):
        ok = repo_postgres.adicionar_mensagem(
            self.conversa.pk, 'assistant', 'ok', plano=None,
        )
        self.assertTrue(ok)
        self.assertIsNone(Mensagem.objects.get(conversa=self.conversa).plano)

