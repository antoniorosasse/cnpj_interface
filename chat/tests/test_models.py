# cnpj_interface/chat/tests/test_models.py
"""
Testes dos modelos Conversa e Mensagem.

Roda com:
    python manage.py test chat.tests.test_models
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

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

