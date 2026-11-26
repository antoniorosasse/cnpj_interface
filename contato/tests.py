"""
Testes do formulario de contato da pagina do produto.

O health check da landing e' mockado: teste nao faz requisicao de rede.
"""

from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from contato.models import LeadContato

STATUS_OK = {'todos_online': True, 'texto': 'Sistemas no ar', 'sistemas': []}


class ContatoBase(TestCase):
    def setUp(self):
        cache.clear()
        p = patch('core.landing.status_dos_sistemas', return_value=STATUS_OK)
        self.status_mock = p.start()
        self.addCleanup(p.stop)

    def dados(self, **extra):
        base = {
            'nome': 'Ana Souza',
            'email': 'ana@empresa.com.br',
            'empresa': 'Empresa Exemplo',
            'telefone': '(47) 99999-0000',
            'mensagem': 'Preciso enriquecer a base de clientes com dados da Receita.',
            'consentimento': 'on',
        }
        base.update(extra)
        return base


class FormularioTests(ContatoBase):
    def test_landing_mostra_o_formulario(self):
        resp = self.client.get('/')
        self.assertContains(resp, 'action="/contato/"')
        self.assertContains(resp, 'name="mensagem"')
        self.assertContains(resp, 'Quer ver com os dados da sua região?')

    def test_get_no_endpoint_e_recusado(self):
        self.assertEqual(self.client.get('/contato/').status_code, 405)

    def test_post_valido_grava_e_redireciona(self):
        resp = self.client.post('/contato/', self.dados())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/?enviado=1#contato')
        lead = LeadContato.objects.get()
        self.assertEqual(lead.nome, 'Ana Souza')
        self.assertEqual(lead.email, 'ana@empresa.com.br')

    def test_landing_confirma_o_envio(self):
        resp = self.client.get('/?enviado=1')
        self.assertContains(resp, 'Mensagem recebida')

    def test_sem_consentimento_nao_grava(self):
        dados = self.dados()
        dados.pop('consentimento')
        resp = self.client.post('/contato/', dados)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(LeadContato.objects.count(), 0)
        self.assertContains(resp, 'autorizacao', status_code=400)

    def test_erro_devolve_os_dados_digitados(self):
        resp = self.client.post('/contato/', self.dados(email='nao-e-email'))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(LeadContato.objects.count(), 0)
        self.assertContains(resp, 'Ana Souza', status_code=400)

    def test_honeypot_finge_sucesso_e_nao_grava(self):
        resp = self.client.post('/contato/', self.dados(website='http://spam.example'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(LeadContato.objects.count(), 0)

    def test_limite_por_ip(self):
        for _ in range(5):
            self.client.post('/contato/', self.dados())
        resp = self.client.post('/contato/', self.dados())
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(LeadContato.objects.count(), 5)


class EmailTests(ContatoBase):
    def test_sem_smtp_nao_tenta_enviar(self):
        self.client.post('/contato/', self.dados())
        self.assertEqual(LeadContato.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        EMAIL_HOST='smtp.exemplo.com',
        CONTATO_EMAIL_DESTINO='contato@busquecnpj.com.br',
        DEFAULT_FROM_EMAIL='site@busquecnpj.com.br',
    )
    def test_com_smtp_avisa_por_email(self):
        self.client.post('/contato/', self.dados())
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['contato@busquecnpj.com.br'])
        self.assertIn('Ana Souza', msg.body)
        self.assertIn('ana@empresa.com.br', msg.body)
