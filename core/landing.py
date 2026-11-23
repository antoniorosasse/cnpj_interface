# /home/neto/cnpj_interface/core/landing.py
"""
Conteudo e view da landing comercial (site do produto).

A landing apresenta as quatro solucoes do sistema e os servicos. Ela mora
no cnpj_interface (/site/) e nao depende do menu_interface: as URLs dos apps
sao montadas aqui pelo ambiente do request (dev: portas 8000/8001/8002;
producao: subdominios e arosasse.tec.br).

O selo "Sistemas no ar" consulta o /health/deep/ dos apps com timeout curto
e cache em memoria (60s online, 10s offline). Se a checagem nao for
conclusiva, o selo cai para "Sistemas em operacao" - nunca inventa status.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import json
import os
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.shortcuts import render

import django


# ============================================================
# URLS DOS APPS
# ============================================================
# Portas do runserver local (mesmas usadas pelo menu_interface): o cnpj e o
# chat dividem a 8002, o AS/400 fica na 8001 e o portfolio na 8000.
PORTA_CNPJ_DEV = 8002
PORTA_AS400_DEV = 8001
PORTA_MENU_DEV = 8000
CAMINHO_PORTFOLIO_DEV = '/menu/'

URL_CNPJ_PROD = 'https://busquecnpj.com.br'
URL_CHAT_PROD = 'https://chat.busquecnpj.com.br'
URL_AS400_PROD = 'https://as400.busquecnpj.com.br'
URL_PORTFOLIO_PROD = 'https://arosasse.tec.br/'

URL_GITHUB = 'https://github.com/antoniorosasse'


def _host(request):
    """Host do request sem porta (o dev troca de porta por servico)."""
    return request.get_host().split(':')[0]


def urls_dos_apps(request):
    """
    URLs absolutas dos apps no ambiente do request.

    Em DEBUG cada app roda num runserver local, entao a URL leva a porta do
    servico. Em producao cada app tem o seu subdominio (o chat e o AS/400
    aparecem assim no menu_interface).
    """
    host = _host(request)
    if settings.DEBUG:
        cnpj = f'http://{host}:{PORTA_CNPJ_DEV}'
        chat = cnpj
        as400 = f'http://{host}:{PORTA_AS400_DEV}'
        portfolio = f'http://{host}:{PORTA_MENU_DEV}{CAMINHO_PORTFOLIO_DEV}'
    else:
        cnpj = URL_CNPJ_PROD
        chat = URL_CHAT_PROD
        as400 = URL_AS400_PROD
        portfolio = URL_PORTFOLIO_PROD

    return {
        'cnpj': cnpj,
        'chat': chat,
        'as400': as400,
        'portfolio': portfolio,
        'empresas': f'{cnpj}/empresas/',
        'proximidade': f'{cnpj}/proximidade/',
        'estatisticas': f'{cnpj}/estatisticas/',
        'chat_ia': f'{chat}/chat/',
        'as400_app': f'{as400}/',
    }


# ============================================================
# CONTEUDO COMERCIAL
# ============================================================
# Os quatro cards da secao Solucoes. A descricao e' a versao comercial
# (sem jargao de engenharia) que ja existia na landing antiga; nome, icone
# e URL saem do proprio app.
SOLUCOES = (
    {
        'id': 'cnpj',
        'nome': 'Consulta Empresas · Receita Federal',
        'descricao': (
            'Consulte qualquer uma das 74,4 milhões de empresas da Receita '
            'Federal por nome, ramo de atividade, cidade ou situação cadastral, '
            'com endereço e sócios de cada uma.'
        ),
        'icone': 'bi bi-building',
        'chave_url': 'empresas',
    },
    {
        'id': 'chat_empresas',
        'nome': 'Chat com IA · Empresas (Receita Federal)',
        'descricao': (
            'Pergunte em português e receba a resposta pronta: a IA busca as '
            'empresas na base, confere os números e responde apenas com o que '
            'está registrado.'
        ),
        'icone': 'bi bi-chat-dots',
        'chave_url': 'chat_ia',
        'nota_demo': (
            'Login de demonstração: demo / demo — as consultas rodam em '
            'modo somente-leitura.'
        ),
    },
    {
        'id': 'proximidade',
        'nome': 'Empresas por proximidade',
        'descricao': (
            'Encontre estabelecimentos num raio de até 100 km a partir de um '
            'CNPJ, CEP, cidade ou coordenada, com a distância em km de cada '
            'resultado.'
        ),
        'icone': 'bi bi-geo-alt',
        'chave_url': 'proximidade',
    },
    {
        'id': 'estatisticas',
        'nome': 'Estatísticas da base',
        'descricao': (
            'Os totais da base prontos para consultar: quantas empresas estão '
            'ativas, quantas são do Simples/MEI, ranking de estados, porte, '
            'natureza jurídica e ramo de atividade.'
        ),
        'icone': 'bi bi-bar-chart-line',
        'chave_url': 'estatisticas',
    },
)


# Tres cards de servico (os mesmos da landing antiga).
SERVICOS = (
    {
        'icone': 'bi bi-database-gear',
        'titulo': 'Dados públicos sob medida',
        'texto': (
            'Carga, tratamento e cruzamento de bases abertas (Receita '
            'Federal, Correios, IBGE) com pipeline reproduzível e '
            'qualidade medida.'
        ),
    },
    {
        'icone': 'bi bi-code-slash',
        'titulo': 'Desenvolvimento web (Django)',
        'texto': (
            'Aplicações em Django com Postgres, busca paginada em bases '
            'grandes, autenticação e deploy em VPS com HTTPS e backup.'
        ),
    },
    {
        'icone': 'bi bi-hdd-network',
        'titulo': 'Integração com IBM i / DB2',
        'texto': (
            'Acesso a bases legadas em AS/400 via JDBC, APIs e telas '
            'modernas sobre o que já existe — sem reescrever o sistema.'
        ),
    },
)


# ============================================================
# STATUS DOS SISTEMAS (health check com cache)
# ============================================================
HEALTH_TIMEOUT = 1.5    # segundos por tentativa (a landing nao pode esperar)
LIMIAR_LENTO_MS = 1000  # acima disso o app esta no ar, mas lento
CACHE_TTL = 60          # segundos (online/lento)
CACHE_TTL_OFFLINE = 10  # segundos (offline recupera rapido)

# Cache em memoria do processo. Nao usa django.core.cache porque o backend
# configurado e' DatabaseCache: um F5 na landing nao deve bater no banco so
# para lembrar do health check.
_cache_health = {}


def _check_health(url):
    """
    GET no /health/deep/ do app e le o JSON.

    O endpoint responde HTTP 200 sempre; so conta como online quando o JSON
    traz {'status': 'ok'}. Devolve {'status': online|lento|offline, 'ms': int}.
    """
    inicio = time.monotonic()
    req = urllib.request.Request(url, headers={'User-Agent': 'landing-health/1.0'})

    try:
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
            code = resp.status
            corpo = resp.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as exc:
        return {'status': 'offline', 'code': exc.code,
                'ms': int((time.monotonic() - inicio) * 1000)}
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
        return {'status': 'offline', 'code': None,
                'ms': int((time.monotonic() - inicio) * 1000)}

    ms = int((time.monotonic() - inicio) * 1000)
    if code != 200:
        return {'status': 'offline', 'code': code, 'ms': ms}

    try:
        dados = json.loads(corpo)
    except (ValueError, TypeError):
        return {'status': 'offline', 'code': code, 'ms': ms}

    if dados.get('status') != 'ok':
        return {'status': 'offline', 'code': code, 'ms': ms}

    return {'status': 'online' if ms < LIMIAR_LENTO_MS else 'lento',
            'code': code, 'ms': ms}


def _check_health_cached(url):
    """Versao com cache em memoria: 60s online/lento, 10s offline."""
    agora = time.monotonic()
    guardado = _cache_health.get(url)
    if guardado is not None and guardado[0] > agora:
        return guardado[1]

    resultado = _check_health(url)
    ttl = CACHE_TTL_OFFLINE if resultado['status'] == 'offline' else CACHE_TTL
    _cache_health[url] = (agora + ttl, resultado)
    return resultado


def status_dos_sistemas(request):
    """
    Consulta o health check dos apps em paralelo e devolve o selo do hero.

    'todos_online' e' True somente quando todos respondem ok. Como a landing
    roda dentro do cnpj_interface, o proprio cnpj tambem e' checado: se o
    banco estiver fora, o selo cai para "Sistemas em operacao".
    """
    urls = urls_dos_apps(request)
    alvos = (
        ('cnpj', 'Consulta de CNPJ', f"{urls['cnpj']}/health/deep/", urls['empresas']),
        ('chat', 'Chat com IA', f"{urls['chat']}/chat/health/deep/", urls['chat_ia']),
        ('as400', 'IBM i · DB2', f"{urls['as400']}/health/deep/", urls['as400_app']),
    )

    with ThreadPoolExecutor(max_workers=len(alvos)) as executor:
        resultados = list(executor.map(
            lambda alvo: _check_health_cached(alvo[2]), alvos
        ))

    sistemas = []
    for (chave, nome, _health_url, url), resultado in zip(alvos, resultados):
        status = resultado['status']
        sistemas.append({
            'id': chave,
            'nome': nome,
            'url': url,
            'status': status,
            'rotulo': {'online': 'No ar', 'lento': 'Lento', 'offline': 'Fora'}[status],
            'status_cor': {
                'online': 'ok', 'lento': 'warn', 'offline': 'danger',
            }[status],
        })

    todos_online = all(s['status'] in ('online', 'lento') for s in sistemas)
    no_ar = sum(1 for s in sistemas if s['status'] in ('online', 'lento'))

    return {
        'sistemas': sistemas,
        'todos_online': todos_online,
        'no_ar': no_ar,
        'total': len(sistemas),
        'texto': 'Sistemas no ar' if todos_online else 'Sistemas em operação',
    }


# ============================================================
# AMBIENTE E VERSAO
# ============================================================
def ambiente_do_site():
    """Rotulo do ambiente, sem emoji: o visual do portal e' sobrio."""
    return 'Desenvolvimento local' if settings.DEBUG else 'Produção'


# ============================================================
# VIEW
# ============================================================
def landing_comercial(request):
    """Pagina do produto na raiz do dominio (GET)."""
    return render_landing(request)


def render_landing(request, form=None, erro=None, status=200):
    """
    Renderiza a pagina do produto.

    Usada pelo GET (landing_comercial) e pelo POST do formulario de contato
    (contato.views.enviar), que precisa devolver os erros e os dados digitados
    na mesma pagina. As quatro solucoes e os tres servicos vem das constantes
    deste modulo; as URLs absolutas dos apps sao resolvidas pelo ambiente do
    request. O health check alimenta o selo do hero.
    """
    from contato.forms import ContatoForm

    urls = urls_dos_apps(request)

    solucoes = []
    for solucao in SOLUCOES:
        solucoes.append({**solucao, 'url': urls[solucao['chave_url']]})

    apps_nav = (
        {'nome': 'Empresas', 'icone': 'bi bi-list-ul', 'url': urls['empresas']},
        {'nome': 'Proximidade', 'icone': 'bi bi-geo-alt', 'url': urls['proximidade']},
        {'nome': 'Estatísticas', 'icone': 'bi bi-bar-chart', 'url': urls['estatisticas']},
        {'nome': 'Chat IA', 'icone': 'bi bi-chat-dots', 'url': urls['chat_ia']},
    )

    contexto = {
        'solucoes': solucoes,
        'servicos': SERVICOS,
        'sistemas': urls,          # links do footer (portfolio, as400...)
        'apps_nav': apps_nav,
        'url_github': URL_GITHUB,
        'url_portfolio': urls['portfolio'],
        'status': status_dos_sistemas(request),
        'ambiente': ambiente_do_site(),
        'versao_django': django.get_version(),
        'versao': os.getenv('SITE_VERSAO', '').strip(),
        # Formulario de contato (contato app)
        'form_contato': form if form is not None else ContatoForm(),
        'contato_enviado': request.GET.get('enviado') == '1',
        'contato_erro': erro,
        'contato_destino': getattr(settings, 'CONTATO_EMAIL_DESTINO', '').strip(),
    }
    return render(request, 'core/landing.html', contexto, status=status)
