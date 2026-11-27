# cnpj_interface/chat/tests/test_ia_credentials.py
"""Testes das credenciais de IA no admin (Fase API de IA)."""
from django.test import TestCase

from chat.ia_credentials import criptografar, descriptografar, mascarar
from chat.models import ConfiguracaoIA


class CredenciaisIATests(TestCase):
    def test_roundtrip_e_mascara(self):
        chave = 'sk-teste-1234567890abcdef'
        cifrada = criptografar(chave)
        self.assertNotIn(chave, cifrada)
        self.assertEqual(descriptografar(cifrada), chave)
        self.assertNotIn('1234567890', mascarar(chave))

    def test_configuracao_mascara_chave(self):
        conf = ConfiguracaoIA.objects.create(
            nome='Chat clientes',
            finalidade=ConfiguracaoIA.FINALIDADE_CHAT,
            api_key_encrypted=criptografar('sk-segredo-123456'),
        )
        self.assertNotIn('segredo-123456', conf.api_key_mascarada)
        self.assertTrue(conf.api_key_mascarada.startswith('sk-seg'))


class ResolucaoConfiguracaoIATests(TestCase):
    def test_admin_tem_precedencia_e_separa_finalidades(self):
        from chat.ia_credentials import criptografar
        from chat.services import llm_client
        chat = ConfiguracaoIA.objects.create(
            nome='Chat clientes', finalidade=ConfiguracaoIA.FINALIDADE_CHAT,
            base_url='https://chat.exemplo/v1', modelo='modelo-chat',
            api_key_encrypted=criptografar('sk-chat'), padrao=True)
        kb = ConfiguracaoIA.objects.create(
            nome='KB interna', finalidade=ConfiguracaoIA.FINALIDADE_KB,
            base_url='https://kb.exemplo/v1', modelo='modelo-kb',
            api_key_encrypted=criptografar('sk-kb'), padrao=True)
        dados_chat = llm_client._resolver(llm_client.FINALIDADE_CHAT)
        dados_kb = llm_client._resolver(llm_client.FINALIDADE_KB)
        self.assertEqual(dados_chat['config'].pk, chat.pk)
        self.assertEqual(dados_chat['modelo'], 'modelo-chat')
        self.assertEqual(dados_chat['api_key'], 'sk-chat')
        self.assertEqual(dados_kb['config'].pk, kb.pk)
        self.assertEqual(dados_kb['modelo'], 'modelo-kb')
        self.assertEqual(dados_kb['api_key'], 'sk-kb')

    def test_limpar_cache_nao_quebra(self):
        from chat.services.llm_client import limpar_cache
        limpar_cache()


class AdminConfiguracaoIATests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_superuser(
            username='admin_teste', email='admin@example.com', password='x')
        self.client.force_login(self.user)

    def test_pagina_admin_abre_e_salva_chave_criptografada(self):
        url = '/admin/chat/configuracaoia/add/'
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        resposta = self.client.post(url, {
            'nome': 'Chat clientes',
            'finalidade': ConfiguracaoIA.FINALIDADE_CHAT,
            'provedor': 'deepseek',
            'base_url': 'https://api.deepseek.com',
            'modelo': 'deepseek-chat',
            'ativo': 'on',
            'padrao': 'on',
            'timeout': '60',
            'api_key': 'sk-teste-admin-123456',
        })
        self.assertEqual(resposta.status_code, 302)
        conf = ConfiguracaoIA.objects.get(nome='Chat clientes')
        self.assertNotIn('sk-teste-admin-123456', conf.api_key_encrypted)
        from chat.ia_credentials import descriptografar
        self.assertEqual(descriptografar(conf.api_key_encrypted),
                         'sk-teste-admin-123456')
