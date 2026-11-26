# cnpj_interface/exportacao/tests_proximidade.py
"""
Testes do export da consulta de proximidade do chat (Fase 3 - 25/11/2026).

Nao chamam o banco: `consultar_proximidade` e' mockado; o objetivo e' garantir
que a ultima resposta com plano (sem SQL) gera CSV/JSON e que o cabecalho sai
com rotulos legiveis.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from chat import repo
from chat.models import Conversa


User = get_user_model()

ALVO_CONSULTAR = 'chat.services.proximidade_service.consultar_proximidade'


def _resultado_ok():
    plano = {
        'origem': {'texto': 'Joinville/SC', 'rotulo': 'Joinville/SC',
                   'latitude': -26.3, 'longitude': -48.8},
        'raio_km': 10,
        'atividades': ['indústrias metalúrgicas'],
        'situacao': 'ativa',
        'uf': 'SC',
        'idade_min_anos': 3,
        'capital_min': None,
        'link_proximidade': '/proximidade/?origem=Joinville/SC&raio=10',
    }
    return {
        'ok': True,
        'rows': [{
            'cnpj_completo': '84709955000102',
            'razao_social': 'CIA INDUSTRIAL H. CARLOS SCHNEIDER',
            'nm_fantasia': 'SCHNEIDER',
            'municipio_nome': 'Joinville',
            'uf': 'SC',
            'atividade_principal': 'Metalurgia de outros metais',
            'situacao_descricao': 'ATIVA',
            'km': 0.45,
            'telefone_1': '4733334444',
            'email': 'contato@exemplo.com',
            'dt_ini': '1969-01-02',
            'latitude': -26.29,
            'longitude': -48.82,
        }],
        'colunas': ['cnpj_completo', 'razao_social', 'nm_fantasia',
                    'municipio_nome', 'uf', 'atividade_principal',
                    'situacao_descricao', 'km', 'telefone_1', 'email',
                    'dt_ini'],
        'total': 1,
        'exibidas': 1,
        'tem_mais': False,
        'resumo': 'Encontrei 1 indústria metalúrgica.',
        'link': '/proximidade/?origem=Joinville/SC&raio=10',
        'origem': plano['origem'],
        'raio_km': 10,
        'filtros': {'uf': 'SC'},
        'filtros_resumo': 'UF SC',
        'tempo_ms': 10,
        'plano': plano,
    }


class ExportProximidadeChatTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='teste_export_prox', password='test-only')
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=['is_staff', 'is_superuser'])
        self.client = Client()
        self.client.force_login(self.user)
        self.conversa_id = repo.criar_conversa(user=self.user)
        repo.adicionar_mensagem(self.conversa_id, 'user',
                                'Metalúrgicas num raio de 10 km de Joinville/SC')
        repo.adicionar_mensagem(
            self.conversa_id, 'assistant', 'Encontrei 1 ...',
            resposta_texto='Encontrei 1 ...',
            plano=self._plano(),
            total_linhas=1,
        )
        sessao = self.client.session
        sessao['chat_conversa_id'] = self.conversa_id
        sessao.save()

    def tearDown(self):
        Conversa.objects.filter(usuario=self.user).delete()
        self.user.delete()

    @staticmethod
    def _plano():
        return {
            'origem': {'texto': 'Joinville/SC', 'rotulo': 'Joinville/SC',
                       'latitude': -26.3, 'longitude': -48.8},
            'raio_km': 10,
            'atividades': ['indústrias metalúrgicas'],
            'situacao': 'ativa',
            'uf': 'SC',
            'idade_min_anos': 3,
            'capital_min': None,
            'link_proximidade': '/proximidade/?origem=Joinville/SC&raio=10',
        }

    @patch(ALVO_CONSULTAR, return_value=_resultado_ok())
    def test_export_csv_usa_rotulos_e_reexecuta_o_plano(self, mock_consultar):
        resp = self.client.get(reverse('exportacao:exportar_chat'),
                               {'formato': 'csv'})
        self.assertEqual(resp.status_code, 200)
        conteudo = resp.content.decode('utf-8-sig')
        self.assertIn('CNPJ', conteudo.splitlines()[0])
        self.assertIn('84709955000102', conteudo)
        plano_enviado = mock_consultar.call_args.args[0]
        self.assertEqual(plano_enviado['origem'], 'Joinville/SC')
        self.assertEqual(plano_enviado['raio_km'], 10)
        self.assertEqual(plano_enviado['situacao'], 'ativa')

    @patch(ALVO_CONSULTAR, return_value=_resultado_ok())
    def test_export_json(self, mock_consultar):
        resp = self.client.get(reverse('exportacao:exportar_chat'),
                               {'formato': 'json'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'84709955000102', resp.content)
