# core/tests_interface.py
"""Contrato de interface do portal (F1/F2/F3).

Estes testes nao tocam banco: leem os templates e renderizam o base. Servem
para impedir que um reskin remova hooks usados pelo JS ou reintroduza dados
ficticios do mockup.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

BASE = Path(settings.BASE_DIR)
TEMPLATES = BASE / 'core' / 'templates' / 'core'
CHAT_TEMPLATES = BASE / 'chat' / 'templates' / 'chat'


def ler(caminho):
    return (BASE / caminho).read_text(encoding='utf-8')


class BaseShellTests(SimpleTestCase):
    def render_base(self):
        request = RequestFactory().get('/', HTTP_HOST='localhost')
        request.user = AnonymousUser()
        return render_to_string('core/base.html', {}, request=request)

    def test_hooks_globais_do_shell(self):
        html = self.render_base()
        for hook in (
            'id="btn-tema"', 'id="icone-tema"', 'id="navbarNav"',
            'ui-skip', 'id="conteudo"', 'data-ui-global-search',
            'aria-label="Navegação principal"', 'maxmind.com',
        ):
            self.assertIn(hook, html)

    def test_shell_carrega_portal_css_e_js(self):
        html = self.render_base()
        self.assertIn('css/portal.css', html)
        self.assertIn('js/interface-corporativa.js', html)
        self.assertNotIn('interface-corporativa.css', html)

    def test_shell_nao_tem_dado_ficticio(self):
        html = self.render_base()
        for proibido in ('54.8M', '54,8 M', 'R$ 480', 'score 980'):
            self.assertNotIn(proibido, html)


class ListaContractTests(SimpleTestCase):
    def test_campos_do_formulario(self):
        html = ler('core/templates/core/lista.html')
        for campo in (
            'razao_social', 'nome_fantasia', 'cnpj', 'uf', 'municipio',
            'situacao', 'simples', 'mei', 'ordem', 'page',
        ):
            self.assertRegex(html, r'name="%s"' % re.escape(campo))
        for parametro in ('last_cnpj=', 'last_razao=', 'dir=', 'a_partir_de='):
            self.assertIn(parametro, html)

    def test_hooks_do_painel_e_da_tabela(self):
        html = ler('core/templates/core/lista.html')
        for hook in (
            'id="ui-results-layout"', 'ui-results-list', 'ui-results-table',
            'id="ui-details-panel"', 'id="ui-details-content"',
            'id="ui-details-close"', 'id="ui-details-full"',
            'id="ui-panel-status"', 'data-company-detail', 'data-copy-cnpj',
            'ui-advanced-toggle', 'id="ui-advanced"', 'ui-extra-column',
        ):
            self.assertIn(hook, html)

    def test_nao_adiciona_porte_nem_score(self):
        html = ler('core/templates/core/lista.html').lower()
        self.assertNotIn('name="porte"', html)
        self.assertNotIn('score', html)


class DetalheContractTests(SimpleTestCase):
    def test_wrapper_e_cards_para_as_abas(self):
        html = ler('core/templates/core/detalhe.html')
        for hook in (
            'ui-detail-page', 'class="card', 'Sócios', 'Estabelecimentos',
            'core:dossie', 'next_url',
        ):
            self.assertIn(hook, html)

    def test_links_de_mapa_e_whatsapp_preservados(self):
        html = ler('core/templates/core/detalhe.html')
        self.assertTrue('maps' in html.lower() or 'whatsapp' in html.lower())

    def test_sem_dado_ficticio(self):
        html = ler('core/templates/core/detalhe.html')
        for proibido in ('Score', 'score', 'Certidões', 'certidoes'):
            self.assertNotIn(proibido, html)


class ProximidadeContractTests(SimpleTestCase):
    def test_campos_do_formulario(self):
        html = ler('core/templates/core/proximidade.html')
        for campo in ('origem', 'raio', 'situacao', 'matrizes', 'page'):
            self.assertRegex(html, r'name="%s"' % re.escape(campo))

    def test_hooks_de_resultado(self):
        html = ler('core/templates/core/proximidade.html')
        for hook in ('ui-results-layout', 'ui-results-list', 'Distância'):
            self.assertIn(hook, html)


class EstatisticasContractTests(SimpleTestCase):
    def test_form_de_recalculo_preservado(self):
        html = ler('core/templates/core/estatisticas.html')
        for hook in (
            'csrf_token', 'core:recalcular_totais', 'recalculo',
            'setTimeout', '15000',
        ):
            self.assertIn(hook, html)

    def test_graficos_usam_contexto_real(self):
        html = ler('core/templates/core/estatisticas.html')
        for chave in ('portes', 'top_cnaes', 'por_ano'):
            self.assertIn(chave, html)

    def test_sem_numeros_ficticios(self):
        html = ler('core/templates/core/estatisticas.html')
        for proibido in ('54.8M', '54,8 M', 'R$ 480', '2018 - 2026'):
            self.assertNotIn(proibido, html)

    def test_carrega_js_e_dados_dos_graficos(self):
        html = ler('core/templates/core/estatisticas.html')
        for hook in ('portal-inteligencia.js', 'intel-donut', 'data-porte-total',
                     'data-ano', 'data-total'):
            self.assertIn(hook, html)


class ChatContractTests(SimpleTestCase):
    def test_ids_e_fetch_do_chat(self):
        html = ler('chat/templates/chat/chat.html')
        for hook in (
            'id="thread"', 'id="pergunta"', 'id="enviar"', 'id="spinner"',
            'id="info"', "fetch('/chat/api/'", 'Chart.js',
            '__toggleGrafico',
        ):
            self.assertIn(hook, html)

    def test_classes_de_mensagem(self):
        html = ler('chat/templates/chat/chat.html')
        for hook in (
            'turno', 'papel', 'rodape-rag', 'resposta-natural',
            'sql-detalhe', 'sql-box', 'resultado-historico',
            'tabela-wrapper', 'sis-tabela', 'sis-thead',
        ):
            self.assertIn(hook, html)

    def test_forms_de_conversa(self):
        html = ler('chat/templates/chat/chat.html')
        for hook in ('chat:nova', 'chat:deletar', 'chat:historico', 'csrf_token'):
            self.assertIn(hook, html)


class SemDadosFicticiosTests(SimpleTestCase):
    def test_templates_nao_copiaram_o_mockup(self):
        for nome in (
            'core/templates/core/lista.html',
            'core/templates/core/detalhe.html',
            'core/templates/core/proximidade.html',
            'core/templates/core/estatisticas.html',
            'chat/templates/chat/chat.html',
        ):
            html = ler(nome)
            for proibido in ('MOCK_COMPANIES', 'Petrobras', 'Banco do Brasil S/A'):
                self.assertNotIn(proibido, html, msg=nome)
