# cnpj_interface/chat/tests/test_admin_log.py
"""
Testes do painel de resumo do log de acesso (/admin/chat/logacesso/).

Cobre os tres pontos que precisam falar a mesma lingua:
  - chat/relatorio.resumo_acesso (as consultas)
  - o comando `manage.py relatorio_acesso` (terminal)
  - a tela do admin (painel + comandos para copiar)

Roda com:
    python manage.py test chat.tests.test_admin_log
"""

from datetime import timedelta
from io import StringIO

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from chat.admin import _parse_desde, _url_filtro
from chat.models import LogAcesso
from chat.relatorio import resumo_acesso

User = get_user_model()

UA_LONGO = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120'


def criar_log(tipo, quando=None, usuario=None, ip='198.51.100.10', ua='Mozilla/5.0 teste'):
    """Cria um log e, se preciso, reposiciona criado_em (auto_now_add)."""
    log = LogAcesso.objects.create(
        tipo=tipo, usuario=usuario, ip=ip, user_agent=ua,
    )
    if quando:
        LogAcesso.objects.filter(pk=log.pk).update(criado_em=quando)
        log.refresh_from_db()
    return log


class ResumoAcessoTestCase(TestCase):
    """Testa as consultas de resumo usadas pelo comando e pela tela."""

    def setUp(self):
        self.user = User.objects.create_user('demo', password='senha-demo')
        agora = timezone.now()
        criar_log(
            LogAcesso.TIPO_LOGIN, quando=agora - timedelta(days=1),
            usuario=self.user,
        )
        criar_log(
            LogAcesso.TIPO_LOGIN, quando=agora - timedelta(days=2),
            usuario=self.user, ip='198.51.100.11',
        )
        criar_log(
            LogAcesso.TIPO_CHAT_PERGUNTA, quando=agora - timedelta(days=1),
            usuario=self.user,
        )
        # Acesso anonimo (pagina publica de empresas)
        criar_log(LogAcesso.TIPO_EMPRESAS_LISTA, quando=agora - timedelta(days=1))
        criar_log(
            LogAcesso.TIPO_EMPRESAS_DETALHE, quando=agora - timedelta(days=1),
            usuario=self.user,
        )
        # Fora da janela de 7 dias
        criar_log(
            LogAcesso.TIPO_LOGIN, quando=agora - timedelta(days=40),
            usuario=self.user,
        )

    def test_total_e_totais_por_tipo(self):
        resumo = resumo_acesso(dias=7)
        totais = {linha['tipo']: linha['total'] for linha in resumo['total_por_tipo']}
        self.assertEqual(resumo['total'], 5)
        self.assertEqual(totais[LogAcesso.TIPO_LOGIN], 2)
        self.assertEqual(totais[LogAcesso.TIPO_CHAT_PERGUNTA], 1)
        self.assertEqual(totais[LogAcesso.TIPO_EMPRESAS_LISTA], 1)
        self.assertEqual(totais[LogAcesso.TIPO_EMPRESAS_DETALHE], 1)

    def test_ignora_registro_fora_da_janela(self):
        self.assertEqual(resumo_acesso(dias=30)['total'], 5)
        self.assertEqual(resumo_acesso(dias=1)['total'], 0)

    def test_filtro_por_tipo(self):
        resumo = resumo_acesso(dias=7, tipo=LogAcesso.TIPO_CHAT_PERGUNTA)
        self.assertEqual(resumo['total'], 1)
        self.assertEqual(resumo['tipo'], LogAcesso.TIPO_CHAT_PERGUNTA)

    def test_usuarios_distintos(self):
        resumo = resumo_acesso(dias=7)
        self.assertEqual(resumo['usuarios']['logaram'], 1)
        self.assertEqual(resumo['usuarios']['perguntaram'], 1)
        # Anonimo nao conta: so o usuario logado viu empresas
        self.assertEqual(resumo['usuarios']['viram_empresas'], 1)

    def test_por_dia_tem_as_duas_datas_ordenadas(self):
        resumo = resumo_acesso(dias=7)
        self.assertEqual(len(resumo['dias_ordenados']), 2)
        self.assertEqual(resumo['dias_ordenados'], sorted(resumo['dias_ordenados']))
        self.assertIn(LogAcesso.TIPO_LOGIN, resumo['tipos_presentes'])

    def test_top_ips(self):
        resumo = resumo_acesso(dias=7)
        self.assertEqual(resumo['top_ips_chat'][0]['ip'], '198.51.100.10')
        self.assertEqual(resumo['top_ips_chat'][0]['total'], 1)
        self.assertEqual(resumo['top_ips_empresas'][0]['total'], 2)

    def test_ultimos_logins_mais_recente_primeiro(self):
        resumo = resumo_acesso(dias=7)
        self.assertEqual(len(resumo['ultimos_logins']), 2)
        primeiro = resumo['ultimos_logins'][0]
        self.assertLess(primeiro.criado_em, timezone.now())
        self.assertEqual(primeiro.usuario, self.user)
        self.assertEqual(resumo['ultimos_logins'][1].ip, '198.51.100.11')


class RelatorioAcessoCommandTestCase(TestCase):
    """O comando de terminal continua imprimindo as mesmas secoes."""

    def setUp(self):
        self.user = User.objects.create_user('demo', password='senha-demo')
        criar_log(LogAcesso.TIPO_LOGIN, usuario=self.user)
        criar_log(
            LogAcesso.TIPO_CHAT_PERGUNTA, usuario=self.user, ip='198.51.100.20',
        )

    def test_saida_do_relatorio(self):
        saida = StringIO()
        call_command('relatorio_acesso', '--dias', '7', stdout=saida)
        texto = saida.getvalue()
        self.assertIn('Relatorio de acesso - ultimos 7 dia(s)', texto)
        self.assertIn('--- Totais por tipo ---', texto)
        self.assertIn(LogAcesso.TIPO_LOGIN, texto)
        self.assertIn('--- Por dia ---', texto)
        self.assertIn('Usuarios que logaram:       1', texto)
        self.assertIn('Usuarios que perguntaram:   1', texto)
        self.assertIn('--- Top 10 IPs (perguntas no chat) ---', texto)
        self.assertIn('198.51.100.20', texto)
        self.assertIn('--- Top 10 IPs (acessos a empresas) ---', texto)

    def test_filtro_por_tipo_no_comando(self):
        saida = StringIO()
        call_command(
            'relatorio_acesso', '--dias', '7', '--tipo', 'login', stdout=saida,
        )
        texto = saida.getvalue()
        self.assertIn('Filtro de tipo: login', texto)
        self.assertNotIn('chat_pergunta', texto)


class ParseDesdeTestCase(SimpleTestCase):
    """Leitura do parametro de periodo usado na URL do painel."""

    def test_data_pura(self):
        self.assertEqual(
            _parse_desde('2020-01-02').strftime('%Y-%m-%d'), '2020-01-02',
        )

    def test_data_e_hora(self):
        self.assertEqual(
            _parse_desde('2020-01-02 10:30:00').strftime('%H:%M'), '10:30',
        )

    def test_com_offset(self):
        self.assertTrue(timezone.is_aware(_parse_desde('2020-01-02 10:30:00-03:00')))

    def test_sem_valor(self):
        self.assertIsNone(_parse_desde(None))
        self.assertIsNone(_parse_desde(''))

    def test_valor_invalido(self):
        self.assertIsNone(_parse_desde('ontem'))


class PainelLogAcessoAdminTestCase(TestCase):
    """A tela do admin mostra painel, ultimos logins e comandos copiaveis."""

    def setUp(self):
        self.staff = User.objects.create_superuser(
            'admin_teste', 'admin@example.com', 'senha-teste',
        )
        self.client.force_login(self.staff)
        self.url = reverse('admin:chat_logacesso_changelist')
        criar_log(
            LogAcesso.TIPO_LOGIN, usuario=self.staff,
            ip='198.51.100.50', ua=UA_LONGO,
        )

    def test_exige_login(self):
        self.client.logout()
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 302)
        self.assertIn('/admin/login/', resposta['Location'])

    def test_painel_aparece(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        self.assertIn('Resumo do log de acesso', html)
        self.assertIn('Totais por tipo', html)
        self.assertIn('Top IPs', html)
        self.assertIn('Por dia', html)
        self.assertIn('Últimos logins no período', html)
        self.assertIn('198.51.100.50', html)
        self.assertIn('admin_teste', html)

    def test_comandos_para_copiar_estao_na_pagina(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('Comandos (copiar e colar)', html)
        self.assertIn('data-copiar="cmd-relatorio-prod"', html)
        self.assertIn('relatorio_acesso --dias 7', html)
        self.assertIn('manage.py shell', html)
        self.assertIn('select_related', html)

    def test_periodo_padrao_e_links(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.context['dias_atual'], 7)
        dias = [link['dias'] for link in resposta.context['links_periodo']]
        self.assertEqual(dias, [1, 7, 30, 90])
        # O link de periodo tem que ser um lookup valido do admin
        url_link = resposta.context['links_periodo'][2]['url']
        self.assertIn('criado_em__gte=', url_link)

    def test_link_de_periodo_nao_redireciona(self):
        # Antes de usar criado_em__gte, o Django respondia 302 ?e=1
        # (lookup invalido) e o filtro era descartado.
        resposta = self.client.get(
            self.url, {'criado_em__gte': '2020-01-01 00:00:00-03:00'},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.context['dias_atual'] > 2000)

    def test_janela_customizada_filtra_painel(self):
        resposta = self.client.get(
            self.url, {'criado_em__gte': '2999-01-01 00:00:00-03:00'},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['resumo']['total'], 0)

    def test_filtro_de_tipo_reflete_no_painel(self):
        resposta = self.client.get(
            self.url, {'tipo__exact': LogAcesso.TIPO_CHAT_PERGUNTA},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            resposta.context['resumo']['tipo'], LogAcesso.TIPO_CHAT_PERGUNTA,
        )
        self.assertEqual(resposta.context['resumo']['total'], 0)

    def test_links_do_painel_apontam_para_os_filtros(self):
        criar_log(LogAcesso.TIPO_CHAT_PERGUNTA, usuario=self.staff, ip='203.0.113.77')
        resposta = self.client.get(self.url)
        totais = {t['tipo']: t for t in resposta.context['totais']}
        self.assertIn(
            'tipo__exact=%s' % LogAcesso.TIPO_LOGIN,
            totais[LogAcesso.TIPO_LOGIN]['url'],
        )
        ips = resposta.context['resumo']['top_ips_chat']
        self.assertEqual(ips[0]['ip'], '203.0.113.77')
        self.assertIn('q=203.0.113.77', ips[0]['url'])
        dia = resposta.context['linhas_dia'][0]
        self.assertIn('criado_em__year=%d' % dia['dia'].year, dia['url'])

    def test_link_de_tipo_filtra_a_lista(self):
        esperado = LogAcesso.objects.filter(tipo=LogAcesso.TIPO_LOGIN).count()
        resposta = self.client.get(self.url, {'tipo__exact': LogAcesso.TIPO_LOGIN})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['cl'].result_count, esperado)
        self.assertGreaterEqual(esperado, 1)

    def test_link_de_dia_filtra_a_lista(self):
        hoje = timezone.localdate()
        esperado = LogAcesso.objects.filter(criado_em__date=hoje).count()
        resposta = self.client.get(self.url, {
            'criado_em__year': hoje.year,
            'criado_em__month': hoje.month,
            'criado_em__day': hoje.day,
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['cl'].result_count, esperado)
        self.assertGreaterEqual(esperado, 1)

    def test_link_de_ip_busca_a_lista(self):
        resposta = self.client.get(self.url, {'q': '198.51.100.50'})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['cl'].result_count, 1)

    def test_coluna_navegador_resumida(self):
        model_admin = admin.site._registry[LogAcesso]
        log = LogAcesso.objects.filter(tipo=LogAcesso.TIPO_LOGIN).first()
        resumido = model_admin.navegador(log)
        self.assertTrue(resumido.endswith('...'))
        self.assertEqual(len(resumido), 43)

    def test_somente_leitura(self):
        model_admin = admin.site._registry[LogAcesso]
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))


class UrlFiltroTestCase(SimpleTestCase):
    """Links do painel: mantem periodo/busca, descartam pagina e filtram."""

    def test_preserva_periodo_e_busca_e_descarta_pagina(self):
        request = RequestFactory().get('/', {
            'criado_em__gte': '2026-01-01 00:00:00',
            'q': '198.51',
            'p': '2',
        })
        url = _url_filtro(request, tipo__exact=LogAcesso.TIPO_LOGIN)
        self.assertIn('criado_em__gte=', url)
        self.assertIn('q=198.51', url)
        self.assertIn('tipo__exact=%s' % LogAcesso.TIPO_LOGIN, url)
        self.assertNotIn('p=2', url)

    def test_valor_none_remove_o_lookup(self):
        request = RequestFactory().get('/', {'tipo__exact': LogAcesso.TIPO_LOGIN})
        self.assertNotIn('tipo__exact', _url_filtro(request, tipo__exact=None))

