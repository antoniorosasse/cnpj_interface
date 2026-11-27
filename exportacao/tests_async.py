# exportacao/tests_async.py
"""Testes da exportacao completa (fila, status e download)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from exportacao.async_export import (creditos_exportacao_completa, criar_job,
                                     limite_para)
from exportacao.models import Exportacao


class ExportacaoCompletaTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            'adm-exp', 'adm@example.com', 'x')

    def test_limite_staff_e_cliente(self):
        self.assertGreaterEqual(limite_para(self.user), 500000)
        cliente = type('U', (), {'is_authenticated': True, 'is_staff': False,
                                 'is_superuser': False})()
        self.assertEqual(limite_para(cliente), 50000)

    def test_preco_por_linha_com_minimo(self):
        self.assertEqual(creditos_exportacao_completa(0), 1)
        self.assertEqual(creditos_exportacao_completa(219), 219)
        with self.settings(EXPORTACAO_COMPLETA_CREDITOS_POR_LINHA=2):
            self.assertEqual(creditos_exportacao_completa(10), 20)

    def test_criar_job_gera_token_e_status_pendente(self):
        job = criar_job(self.user, None, 'SELECT 1 AS x', 'csv', '', None, 1)
        self.assertTrue(job.completo)
        self.assertEqual(job.status, 'pendente')
        self.assertTrue(job.token)
        self.assertIsNotNone(job.expira_em)

    def test_status_exige_dono_ou_staff(self):
        job = criar_job(self.user, None, 'SELECT 1 AS x', 'csv', '', None, 1)
        c = Client(SERVER_NAME='localhost')
        c.force_login(self.user)
        r = c.get(f'/exportar/completo/{job.token}/')
        self.assertEqual(r.status_code, 200)

        outro = get_user_model().objects.create_user('outro-exp', password='x')
        c.force_login(outro)
        r = c.get(f'/exportar/completo/{job.token}/')
        self.assertEqual(r.status_code, 403)

    def test_download_antes_de_pronto_da_409(self):
        job = criar_job(self.user, None, 'SELECT 1 AS x', 'csv', '', None, 1)
        c = Client(SERVER_NAME='localhost')
        c.force_login(self.user)
        r = c.get(f'/exportar/completo/{job.token}/baixar/')
        self.assertEqual(r.status_code, 409)

    @patch('exportacao.async_export.iniciar_job')
    @patch('chat.views.chat_api_view')
    def test_exportacao_completa_sem_lista_responde_404(self, mock_chat, mock_start):
        c = Client(SERVER_NAME='localhost')
        c.force_login(self.user)
        r = c.get('/exportar/chat/completo/?formato=csv')
        self.assertEqual(r.status_code, 404)
        mock_start.assert_not_called()
