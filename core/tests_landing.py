# /home/neto/cnpj_interface/core/tests_landing.py
"""
Testes da landing comercial (core:landing em /site/).

A landing saiu do menu_interface e agora e' servida pelo proprio
cnpj_interface. Estes testes cobrem:
  - rota, secoes e ancoras;
  - as quatro solucoes (nome, descricao, icone e URL absoluta);
  - URLs por ambiente (portas no dev, subdominios em producao, host do request);
  - SEO (title/description/canonical/OG) e login;
  - o selo "Sistemas no ar" (health check mockado - teste nao faz rede);
  - template standalone no design system do portal (sem menu_interface);
  - site_home_url de core/context_processors.py usado pelo base.html.

Sem banco: o health check e' mockado e a view nao consulta o Postgres.
"""

from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from django.conf import settings
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from core.context_processors import url_inicio_do_produto
from core.landing import SOLUCOES, _check_health, urls_dos_apps


TEMPLATE = Path(settings.BASE_DIR) / 'core' / 'templates' / 'core' / 'landing.html'

STATUS_OK = {
    'sistemas': [
        {'id': 'cnpj', 'nome': 'Consulta de CNPJ', 'url': 'https://cnpj.busquecnpj.com.br/',
         'status': 'online', 'rotulo': 'No ar', 'status_cor': 'ok'},
        {'id': 'chat', 'nome': 'Chat com IA', 'url': 'https://chat.busquecnpj.com.br/chat/',
         'status': 'online', 'rotulo': 'No ar', 'status_cor': 'ok'},
        {'id': 'as400', 'nome': 'IBM i · DB2', 'url': 'https://as400.busquecnpj.com.br/',
         'status': 'online', 'rotulo': 'No ar', 'status_cor': 'ok'},
    ],
    'todos_online': True,
    'no_ar': 3,
    'total': 3,
    'texto': 'Sistemas no ar',
}

STATUS_OFFLINE = {
    **STATUS_OK,
    'sistemas': [
        {**s, 'status': 'offline', 'rotulo': 'Fora', 'status_cor': 'danger'}
        for s in STATUS_OK['sistemas']
    ],
    'todos_online': False,
    'no_ar': 0,
    'texto': 'Sistemas em operação',
}


class _RespostaFalsa:
    """Objeto minimo no lugar do retorno de urllib.request.urlopen."""

    def __init__(self, corpo, status=200):
        self._corpo = corpo
        self.status = status

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _LandingBase(SimpleTestCase):
    """Base: health check mockado (nenhuma requisicao de rede)."""

    def setUp(self):
        p = patch('core.landing.status_dos_sistemas', return_value=STATUS_OK)
        self.status_mock = p.start()
        self.addCleanup(p.stop)

    def get(self, url=None, **kwargs):
        return self.client.get(url or reverse('core:landing'), **kwargs)


class RotaESecoesTests(_LandingBase):
    def test_site_antigo_redireciona_301_para_a_raiz(self):
        """/site/ era o endereco antigo da landing: 301 para a raiz."""
        resp = self.client.get('/site/')
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp['Location'], '/')

    def test_raiz_serve_a_landing(self):
        self.assertEqual(reverse('core:landing'), '/')
        resp = self.get()
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'core/landing.html')

    def test_secoes_e_ancoras_da_landing(self):
        html = self.get().content.decode()
        for ancora in ('solucoes', 'servicos', 'contato'):
            with self.subTest(ancora=ancora):
                self.assertIn(f'id="{ancora}"', html)
                self.assertIn(f'href="#{ancora}"', html)

    def test_quatro_solucoes_com_nome_descricao_icone_e_url(self):
        resp = self.get()
        self.assertEqual([s['id'] for s in resp.context['solucoes']],
                         [s['id'] for s in SOLUCOES])
        html = resp.content.decode()
        for solucao in resp.context['solucoes']:
            with self.subTest(solucao=solucao['id']):
                self.assertTrue(solucao['url'].startswith('http'))
                self.assertIn(solucao['nome'], html)
                self.assertIn(solucao['descricao'], html)
                self.assertIn(solucao['icone'], html)
                self.assertIn(f'href="{solucao["url"]}"', html)

    def test_tres_servicos_no_template(self):
        resp = self.get()
        self.assertEqual(len(resp.context['servicos']), 3)
        html = resp.content.decode()
        for servico in resp.context['servicos']:
            with self.subTest(servico=servico['titulo']):
                self.assertIn(servico['titulo'], html)
                self.assertIn(servico['texto'], html)


class UrlsDosAppsTests(_LandingBase):
    @override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver', '127.0.0.1'])
    def test_dev_usa_as_portas_locais(self):
        html = self.get().content.decode()
        self.assertIn('http://testserver:8002/', html)              # empresas (cnpj)
        self.assertIn('http://testserver:8002/chat/', html)         # chat (mesmo app)
        self.assertIn('http://testserver:8002/proximidade/', html)
        self.assertIn('http://testserver:8002/estatisticas/', html)
        self.assertIn('http://testserver:8001/', html)              # as400
        self.assertIn('http://testserver:8000/menu/', html)         # portfolio

    @override_settings(DEBUG=False)
    def test_producao_usa_os_subdominios(self):
        html = self.get().content.decode()
        self.assertIn('https://busquecnpj.com.br/', html)
        self.assertIn('https://busquecnpj.com.br/proximidade/', html)
        self.assertIn('https://busquecnpj.com.br/estatisticas/', html)
        self.assertIn('https://chat.busquecnpj.com.br/chat/', html)
        self.assertIn('https://as400.busquecnpj.com.br/', html)
        self.assertIn('https://arosasse.tec.br/', html)
        self.assertNotIn(':8002', html)
        self.assertNotIn(':8001', html)

    @override_settings(DEBUG=True, ALLOWED_HOSTS=['192.168.3.132'])
    def test_urls_seguem_o_host_do_request(self):
        """Nada de host fixo: a landing responde no IP/host em que foi aberta."""
        html = self.get(HTTP_HOST='192.168.3.132').content.decode()
        self.assertIn('http://192.168.3.132:8002/', html)
        self.assertIn('http://192.168.3.132:8000/menu/', html)

    def test_urls_dos_apps_monta_caminhos_e_producao(self):
        request = RequestFactory().get('/', HTTP_HOST='testserver')
        with override_settings(DEBUG=False):
            urls = urls_dos_apps(request)
        self.assertEqual(urls['empresas'], 'https://busquecnpj.com.br/empresas/')
        self.assertEqual(urls['chat_ia'], 'https://chat.busquecnpj.com.br/chat/')
        self.assertEqual(urls['as400_app'], 'https://as400.busquecnpj.com.br/')
        self.assertEqual(urls['portfolio'], 'https://arosasse.tec.br/')


class SeoELoginTests(_LandingBase):
    def test_meta_title_description_canonical_e_og(self):
        html = self.get().content.decode()
        self.assertIn('<title>BusqueCNPJ — consulta de empresas, busca por proximidade e chat com IA</title>', html)
        self.assertIn('name="description"', html)
        self.assertIn('nome, ramo de atividade, cidade', html)  # descricao comercial preservada
        self.assertIn('<link rel="canonical" href="https://busquecnpj.com.br/">', html)
        for og in ('og:type', 'og:site_name', 'og:title', 'og:description', 'og:url'):
            with self.subTest(og=og):
                self.assertIn(f'property="{og}"', html)

    def test_entrar_aponta_para_o_login_do_proprio_app(self):
        html = self.get().content.decode()
        self.assertIn('href="/accounts/login/"', html)
        self.assertIn('Entrar', html)


class StatusDoHeroTests(_LandingBase):
    def test_selo_no_ar_quando_todos_respondem(self):
        html = self.get().content.decode()
        self.assertIn('Sistemas no ar', html)
        self.assertIn('74,4 milhões de estabelecimentos', html)
        self.assertIn('class="lp-selo"', html)
        self.assertNotIn('class="lp-selo lp-selo--warn"', html)

    def test_selo_de_operacao_quando_algo_esta_fora(self):
        self.status_mock.return_value = STATUS_OFFLINE
        html = self.get().content.decode()
        self.assertIn('Sistemas em operação', html)
        self.assertIn('class="lp-selo lp-selo--warn"', html)

    def test_health_check_le_o_json_do_endpoint(self):
        """O endpoint responde 200 sempre; so 'status: ok' conta como no ar."""
        ok = _RespostaFalsa(b'{"status": "ok", "db": "postgres", "ms": 5}')
        with patch('core.landing.urllib.request.urlopen', return_value=ok):
            self.assertEqual(_check_health('http://app/health/deep/')['status'], 'online')

        erro = _RespostaFalsa(b'{"status": "error", "db": "postgres"}')
        with patch('core.landing.urllib.request.urlopen', return_value=erro):
            self.assertEqual(_check_health('http://app/health/deep/')['status'], 'offline')

        with patch('core.landing.urllib.request.urlopen', side_effect=URLError('fora do ar')):
            self.assertEqual(_check_health('http://app/health/deep/')['status'], 'offline')


class TemplateStandaloneTests(_LandingBase):
    def test_template_e_standalone_e_nao_vem_do_menu_interface(self):
        fonte = TEMPLATE.read_text(encoding='utf-8')
        self.assertNotIn('{% extends', fonte)
        self.assertNotIn('menu_interface', fonte)
        self.assertNotIn('core/_topbar.html', fonte)
        html = self.get().content.decode()
        self.assertNotIn('menu_interface', html)
        self.assertNotIn('Portfólio de projetos', html)

    def test_usa_o_design_system_do_portal(self):
        fonte = TEMPLATE.read_text(encoding='utf-8')
        html = self.get().content.decode()
        for hook in ('css/portal.css', 'bootstrap@5.3.8', 'bootstrap-icons',
                     'interface-corporativa.js', 'ui-skip', 'id="conteudo"'):
            with self.subTest(hook=hook):
                self.assertIn(hook, html)
        self.assertIn('data-bs-theme', html)
        self.assertIn("localStorage.getItem('sistema-tema')", html)
        self.assertIn('id="btn-tema"', html)
        self.assertIn('id="icone-tema"', html)
        self.assertIn('id="navbarNav"', html)

    def test_sem_dados_ficticios(self):
        html = self.get().content.decode()
        for proibido in ('54,8', '54.8M', 'R$ 480', 'score 980', 'MOCK',
                         'Petrobras', 'Banco do Brasil'):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, html)


class SiteHomeUrlTests(SimpleTestCase):
    """O Inicio do shell (core/base.html) aponta para a landing do cnpj."""

    def _render_base(self):
        request = RequestFactory().get('/', HTTP_HOST='testserver')
        return render_to_string('core/base.html', {}, request=request)

    @override_settings(DEBUG=True)
    def test_em_debug_aponta_para_o_site_do_proprio_app(self):
        self.assertEqual(url_inicio_do_produto(None), 'http://localhost:8002/')
        html = self._render_base()
        # marca + item "Inicio" usam o mesmo site_home_url
        self.assertEqual(html.count('href="http://testserver/"'), 2)
        self.assertNotIn('site/', html)

    def test_em_producao_aponta_para_o_dominio_raiz(self):
        self.assertEqual(url_inicio_do_produto(None), 'https://busquecnpj.com.br/')
        html = self._render_base()
        self.assertEqual(html.count('href="https://busquecnpj.com.br/"'), 2)

    @override_settings(SITE_HOME_URL='https://exemplo.test/inicio/')
    def test_env_vence_o_padrao(self):
        self.assertEqual(url_inicio_do_produto(None), 'https://exemplo.test/inicio/')
