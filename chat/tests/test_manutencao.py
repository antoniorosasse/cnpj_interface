# cnpj_interface/chat/tests/test_manutencao.py
"""
Testes de chat/manutencao.py, do comando `limpar_acessos` e do bloco de
expurgo do painel (/admin/chat/logacesso/).

Roda com:
    python manage.py test chat.tests.test_manutencao
"""

from datetime import timedelta
from io import StringIO

from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chat.manutencao import MIN_DIAS, contar_acessos, limpar_acessos
from chat.models import LogAcesso

User = get_user_model()


def criar_log(tipo, dias_atras, ip='198.51.100.10'):
    """Cria um log e empurra o criado_em (auto_now_add) para N dias atras."""
    log = LogAcesso.objects.create(tipo=tipo, ip=ip)
    LogAcesso.objects.filter(pk=log.pk).update(
        criado_em=timezone.now() - timedelta(days=dias_atras)
    )
    return log


def antigos(dias=90):
    """Queryset dos logs que um expurgo de `dias` dias alcancaria."""
    return LogAcesso.objects.filter(
        criado_em__lt=timezone.now() - timedelta(days=dias)
    )


class ContarAcessosTestCase(TestCase):
    def setUp(self):
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=100)
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=95)
        criar_log(LogAcesso.TIPO_CHAT_PERGUNTA, dias_atras=100)
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=3)   # dentro da janela

    def test_conta_apenas_os_mais_antigos(self):
        self.assertEqual(contar_acessos(dias=90)['total'], 3)

    def test_conta_por_tipo(self):
        resumo = contar_acessos(dias=90)
        por_tipo = {linha['tipo']: linha['total'] for linha in resumo['por_tipo']}
        self.assertEqual(por_tipo[LogAcesso.TIPO_LOGIN], 2)
        self.assertEqual(por_tipo[LogAcesso.TIPO_CHAT_PERGUNTA], 1)

    def test_filtro_de_tipo(self):
        resumo = contar_acessos(dias=90, tipo=LogAcesso.TIPO_CHAT_PERGUNTA)
        self.assertEqual(resumo['total'], 1)

    def test_min_dias_vale_sete(self):
        self.assertEqual(MIN_DIAS, 7)

    def test_recusa_janela_menor_que_o_minimo(self):
        with self.assertRaises(ValueError):
            contar_acessos(dias=MIN_DIAS - 1)

    def test_recusa_tipo_invalido(self):
        with self.assertRaises(ValueError):
            contar_acessos(dias=90, tipo='inexistente')


class LimparAcessosTestCase(TestCase):
    def setUp(self):
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=200)
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=150)
        criar_log(LogAcesso.TIPO_CHAT_PERGUNTA, dias_atras=120)
        self.recente = criar_log(LogAcesso.TIPO_LOGIN, dias_atras=2)

    def test_dry_run_nao_apaga(self):
        resumo = limpar_acessos(dias=90, dry_run=True)
        self.assertEqual(resumo['total'], 3)
        self.assertEqual(resumo['removidos'], 0)
        self.assertTrue(resumo['dry_run'])
        self.assertEqual(LogAcesso.objects.count(), 4)

    def test_apaga_os_antigos_e_preserva_os_recentes(self):
        resumo = limpar_acessos(dias=90)
        self.assertEqual(resumo['removidos'], 3)
        self.assertFalse(resumo['dry_run'])
        self.assertEqual(LogAcesso.objects.count(), 1)
        self.assertTrue(LogAcesso.objects.filter(pk=self.recente.pk).exists())

    def test_apaga_em_lotes(self):
        resumo = limpar_acessos(dias=90, lote=2)
        self.assertEqual(resumo['removidos'], 3)
        self.assertEqual(LogAcesso.objects.count(), 1)

    def test_respeita_o_tipo(self):
        resumo = limpar_acessos(dias=90, tipo=LogAcesso.TIPO_CHAT_PERGUNTA)
        self.assertEqual(resumo['removidos'], 1)
        self.assertEqual(LogAcesso.objects.count(), 3)

    def test_recusa_janela_menor_que_o_minimo(self):
        with self.assertRaises(ValueError):
            limpar_acessos(dias=1)
        self.assertEqual(LogAcesso.objects.count(), 4)


class ComandoLimparAcessosTestCase(TestCase):
    def setUp(self):
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=200)
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=2)

    def _rodar(self, *args):
        saida = StringIO()
        call_command('limpar_acessos', *args, stdout=saida)
        return saida.getvalue()

    def test_sem_sim_nao_apaga(self):
        saida = self._rodar('--dias', '90')
        self.assertIn('[NADA FOI APAGADO]', saida)
        self.assertEqual(LogAcesso.objects.count(), 2)

    def test_dry_run_nao_apaga(self):
        saida = self._rodar('--dias', '90', '--dry-run')
        self.assertIn('--dry-run', saida)
        self.assertEqual(LogAcesso.objects.count(), 2)

    def test_com_sim_apaga(self):
        saida = self._rodar('--dias', '90', '--sim')
        self.assertIn('Apagados 1', saida)
        self.assertEqual(LogAcesso.objects.count(), 1)

    def test_dias_abaixo_do_minimo_e_erro(self):
        with self.assertRaises(CommandError):
            self._rodar('--dias', '1', '--sim')


class ExpurgoNoAdminTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            'admin_expurgo', 'admin@example.com', 'senha-teste',
        )
        self.client.force_login(self.staff)
        self.lista = reverse('admin:chat_logacesso_changelist')
        self.expurgar = reverse('admin:chat_logacesso_expurgar')
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=200)
        criar_log(LogAcesso.TIPO_LOGIN, dias_atras=150)
        self.recente = criar_log(LogAcesso.TIPO_LOGIN, dias_atras=2)

    def test_bloco_aparece_com_a_contagem(self):
        resposta = self.client.get(self.lista, {'expurgo_dias': 90})
        html = resposta.content.decode()
        self.assertEqual(resposta.status_code, 200)
        self.assertIn('Expurgo de logs antigos', html)
        self.assertEqual(resposta.context['expurgo']['total'], 2)
        self.assertIn('chat/logacesso/expurgar/', html)

    def test_janela_curta_demais_cai_no_padrao(self):
        resposta = self.client.get(self.lista, {'expurgo_dias': 1})
        self.assertEqual(resposta.context['expurgo']['dias'], 90)

    def test_get_no_expurgar_nao_apaga(self):
        resposta = self.client.get(self.expurgar)
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(antigos().count(), 2)

    def test_post_apaga_e_registra_no_log_entry(self):
        resposta = self.client.post(self.expurgar, {'dias': 90, 'tipo': ''})
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(antigos().count(), 0)
        self.assertTrue(LogAcesso.objects.filter(pk=self.recente.pk).exists())
        entrada = LogEntry.objects.filter(action_flag=DELETION).first()
        self.assertIsNotNone(entrada)
        self.assertIn('2 registro(s)', entrada.change_message)

    def test_post_com_tipo_apaga_so_aquele_tipo(self):
        criar_log(LogAcesso.TIPO_CHAT_PERGUNTA, dias_atras=200)
        resposta = self.client.post(
            self.expurgar, {'dias': 90, 'tipo': LogAcesso.TIPO_CHAT_PERGUNTA},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            antigos().filter(tipo=LogAcesso.TIPO_CHAT_PERGUNTA).count(), 0,
        )
        self.assertEqual(antigos().filter(tipo=LogAcesso.TIPO_LOGIN).count(), 2)

    def test_usuario_comum_nao_pode_expurgar(self):
        comum = User.objects.create_user(
            'comum_expurgo', 'comum@example.com', 'senha-teste', is_staff=True,
        )
        self.client.force_login(comum)
        resposta = self.client.post(self.expurgar, {'dias': 90})
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(antigos().count(), 2)
