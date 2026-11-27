# cnpj_project/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv

# ==================== CARREGAR .env ====================
BASE_DIR = Path(__file__).resolve().parent.parent

env_path = BASE_DIR / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()
    if not os.getenv('PG_HOST'):
        print("⚠️  ATENÇÃO: Arquivo .env não encontrado ou vazio!")

# ==================== SEGURANÇA ====================
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("A variável SECRET_KEY não está definida no .env")

DEBUG = os.getenv('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# ==================== PAGINA INICIAL DO PRODUTO (landing) ====================
# O "Inicio" do menu e a marca (core/base.html) apontam para a landing
# comercial, servida por este proprio app na raiz do dominio (desde 23/11/2026);
# a lista de empresas fica em /empresas/. Vazio = padrao por ambiente,
# resolvido em core/context_processors.py: dev http://<host>:8002/ e producao
# https://busquecnpj.com.br/.
SITE_HOME_URL = os.getenv('SITE_HOME_URL', '').strip()

# ==================== CONTATO (formulario da pagina do produto) ====================
# O lead e' gravado no banco 'django_internal' pelo app 'contato'; o e-mail de
# aviso so' e' enviado quando as variaveis abaixo existem no .env.
# Sem CONTATO_EMAIL_DESTINO ou sem EMAIL_HOST, o formulario continua gravando
# e o aviso fica so' no log (nada se perde).
CONTATO_EMAIL_DESTINO = os.getenv('CONTATO_EMAIL_DESTINO', '').strip()
EMAIL_HOST = os.getenv('EMAIL_HOST', '').strip()
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587') or 587)
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '').strip()
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
DEFAULT_FROM_EMAIL = os.getenv(
    'DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'no-reply@busquecnpj.com.br'
)

# ==================== EXPORTACAO E LIMITE DIARIO ====================
# Amostra que o anonimo baixa (sem consumir credito) e teto por arquivo.
EXPORTACAO_AMOSTRA_LINHAS = int(os.getenv('EXPORTACAO_AMOSTRA_LINHAS', '20') or 20)
EXPORTACAO_MAX_LINHAS = int(os.getenv('EXPORTACAO_MAX_LINHAS', '50000') or 50000)
# Pesquisas/dia: anonimo, conta gratuita e teto agregado por IP.
PESQUISAS_DIA_ANONIMO = int(os.getenv('PESQUISAS_DIA_ANONIMO', '5') or 5)
PESQUISAS_DIA_CONTA = int(os.getenv('PESQUISAS_DIA_CONTA', '10') or 10)
PESQUISAS_DIA_IP = int(os.getenv('PESQUISAS_DIA_IP', '20') or 20)
# Paginacao: teto bem maior, para navegar sem punicao, mas sem permitir varrer
# a base 20 linhas por request.
PAGINAS_DIA_ANONIMO = int(os.getenv('PAGINAS_DIA_ANONIMO', '300') or 300)
PAGINAS_DIA_CONTA = int(os.getenv('PAGINAS_DIA_CONTA', '3000') or 3000)
PAGINAS_DIA_IP = int(os.getenv('PAGINAS_DIA_IP', '3000') or 3000)
# Teto de linhas no export do chat (o executor do chat corta em CHAT_SQL_MAX_ROWS).
EXPORTACAO_CHAT_MAX_LINHAS = int(os.getenv('EXPORTACAO_CHAT_MAX_LINHAS', '5000') or 5000)

# ==================== DESAFIO (Cloudflare Turnstile) ====================
# Chaves gratuitas em https://dash.cloudflare.com -> Turnstile.
# Sem as chaves, o desafio fica desativado (o export segue com cookie + cotas).
TURNSTILE_SITE_KEY = os.getenv('TURNSTILE_SITE_KEY', '').strip()
TURNSTILE_SECRET_KEY = os.getenv('TURNSTILE_SECRET_KEY', '').strip()

# ==================== CSRF ====================
# Origens confiaveis para requisicoes POST com CSRF (Django 4+)
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()
]

# ==================== SESSAO (dominio do cookie) ====================
# Ausente/vazio = cookie por host (padrao do Django).
#
# Em PRODUCAO vale '.busquecnpj.com.br': o login feito em um subdominio passa a
# valer nos outros (chat.busquecnpj.com.br <-> cnpj.busquecnpj.com.br). Efeito: quem
# loga no chat aparece autenticado na consulta de empresas, e o log grava o
# usuario real em vez de anonimo.
#
# ATENCAO: NAO fixe o dominio aqui. Com dominio fixo o navegador recusa o
# cookie em localhost e o login local para de funcionar (o valor fica no
# .env do servidor).
#
# CSRF_COOKIE_DOMAIN continua por host, de proposito: compartilhar o token
# CSRF entre subdominios amplia a superficie de ataque sem necessidade.
SESSION_COOKIE_DOMAIN = os.getenv('SESSION_COOKIE_DOMAIN', '').strip() or None

# ==================== APPS ====================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',  # App principal
    'chat',
    'contato',  # Formulario de contato da pagina do produto
    'exportacao',  # Exportacao CSV/JSON, creditos e limite diario
]

# ==================== CACHE ====================
#CACHES = {
#    'default': {
#        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
#        'LOCATION': 'unique-snowflake',
#        'TIMEOUT': 3600,  # 1 hora
#    }
#}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'cache_table',
        'TIMEOUT': 3600,
        'OPTIONS': {
            'MAX_ENTRIES': 1000,
        }
    }
}


# ==================== MIDDLEWARE DE CACHE ====================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # Cache de pagina DESABILITADO: atrapalha paginas dinamicas (chat)
    #'django.middleware.cache.UpdateCacheMiddleware',  # ← NOVO (antes do Common)
    'django.middleware.common.CommonMiddleware',
    #'django.middleware.cache.FetchFromCacheMiddleware',  # ← NOVO (depois do Common)
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Cache de páginas (opcional)
CACHE_MIDDLEWARE_ALIAS = 'default'
CACHE_MIDDLEWARE_SECONDS = 300  # 5 minutos
CACHE_MIDDLEWARE_KEY_PREFIX = 'cnpj_interface'

ROOT_URLCONF = 'cnpj_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # Pagina inicial do produto (landing): usada pela marca e pelo
                # "Inicio" de core/base.html.
                'core.context_processors.site_home',
            ],
        },
    },
]

WSGI_APPLICATION = 'cnpj_project.wsgi.application'

# ==================== BANCOS DE DADOS ====================
#DATABASES = {
#    'default': {
#        'ENGINE': 'django.db.backends.sqlite3',
#        'NAME': BASE_DIR / 'db.sqlite3',  # Admin, sessões, auth
#    },
#    'postgres': {
#        'ENGINE': 'django.db.backends.postgresql',
#        'NAME': os.getenv('PG_DB', 'bancocnpj'),
#        'USER': os.getenv('PG_USER', 'arosasse'),
#        'PASSWORD': os.getenv('PG_PASSWORD'),
#        'HOST': os.getenv('PG_HOST', 'localhost'),
#        'PORT': os.getenv('PG_PORT', '5432'),
#    }
#}

# ==================== BANCOS DE DADOS ====================
# default     -> Postgres "django_internal" (auth, sessions, chat, admin)
# postgres    -> Postgres "bancocnpj"       (dados da RFB, read-only)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DJANGO_DB', 'django_internal'),
        'USER': os.getenv('DJANGO_DB_USER', 'django_app'),
        'PASSWORD': os.getenv('DJANGO_DB_PASSWORD'),
        'HOST': os.getenv('DJANGO_DB_HOST', 'localhost'),
        'PORT': os.getenv('DJANGO_DB_PORT', '5432'),
    },
    'postgres': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('PG_DB', 'bancocnpj'),
        'USER': os.getenv('PG_USER', 'arosasse'),
        'PASSWORD': os.getenv('PG_PASSWORD'),
        'HOST': os.getenv('PG_HOST', 'localhost'),
        'PORT': os.getenv('PG_PORT', '5432'),
    }
}

# ==================== ROUTER ====================
# Diz ao Django qual banco usar para cada modelo
DATABASE_ROUTERS = ['core.routers.CNPJRouter']

# ==================== AUTENTICAÇÃO ====================
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# ==================== INTERNACIONALIZAÇÃO ====================
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# Datas compactas (d/m/Y) em todo o sistema, inclusive no admin: economiza
# espaco em tabelas e listas. Antes o admin mostrava "24 de Novembro de 2026".
FORMAT_MODULE_PATH = ['formats']
DATE_FORMAT = 'd/m/Y'
SHORT_FORMAT_MODULE_PATH = ['formats']
DATE_FORMAT = 'd/m/Y'
DATETIME_FORMAT = 'd/m/Y H:i'
SHORT_DATETIME_FORMAT = 'd/m/Y H:i'
TIME_FORMAT = 'H:i'

# ==================== SUBPATH (produção vs local) ====================
# Em produção, o app roda em https://cnpj.busquecnpj.com.br/
# Em desenvolvimento, roda em http://localhost:8002/
#
# Controlado por variável de ambiente SCRIPT_NAME_PREFIX:
#   - Se vazio ou ausente  → rodando na raiz (local)
#   - Se "cnpj"            → rodando em /cnpj/ (produção)
#
#SCRIPT_NAME_PREFIX = os.getenv('SCRIPT_NAME_PREFIX', '').strip('/')

#if SCRIPT_NAME_PREFIX:
#    # -------- Produção (subpath) --------
#    FORCE_SCRIPT_NAME = f'/{SCRIPT_NAME_PREFIX}'
#    STATIC_URL = f'/{SCRIPT_NAME_PREFIX}/static/'
#    MEDIA_URL  = f'/{SCRIPT_NAME_PREFIX}/media/'
#
#    # Cookies restritos ao subpath (evita colisão se outros apps usarem /)
#    CSRF_COOKIE_PATH    = f'/{SCRIPT_NAME_PREFIX}'
#    SESSION_COOKIE_PATH = f'/{SCRIPT_NAME_PREFIX}'
#
#    # Proxy HTTPS atrás do Nginx
#    USE_X_FORWARDED_HOST = True
#    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
#else:
#    # -------- Local (raiz) --------
#    FORCE_SCRIPT_NAME = None
#    STATIC_URL = '/static/'
#    MEDIA_URL  = '/media/'

# ==================== SUBPATH (sempre raiz agora) ====================
# Estamos usando subdomínios, então não precisamos de FORCE_SCRIPT_NAME.
FORCE_SCRIPT_NAME = None
STATIC_URL = '/static/'
MEDIA_URL  = '/media/'

# Proxy HTTPS atrás do Nginx
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# ==================== ARQUIVOS ESTÁTICOS ====================
#STATIC_URL = 'static/'
# Ajusta o caminho dos arquivos estáticos para incluir o prefixo.
#STATIC_URL = '/cnpj/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

#MEDIA_URL = '/cnpj/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==================== AUTENTICACAO ====================
LOGIN_URL = '/accounts/login/'
# Depois de entrar, o usuario vai para a pagina inicial do produto (landing),
# nao direto para o chat. /inicio/ resolve por ambiente em
# core/context_processors.py: SITE_HOME_URL do .env, :8000/site/ no dev e
# https://busquecnpj.com.br/ em producao.
LOGIN_REDIRECT_URL = '/inicio/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# ==================== CONFIGURAÇÕES DOS BANCOS (para conexões diretas, se necessário) ====================
# AS400_* saiu daqui junto com a classe ConexaoAS400: nenhuma view usa o IBM i.
PG_HOST = os.getenv('PG_HOST')
PG_PORT = os.getenv('PG_PORT')
PG_DB = os.getenv('PG_DB')
PG_USER = os.getenv('PG_USER')
PG_PASSWORD = os.getenv('PG_PASSWORD')

MONGO_HOST = os.getenv('MONGO_HOST')
MONGO_PORT = os.getenv('MONGO_PORT')
MONGO_DB = os.getenv('MONGO_DB')
MONGO_USER = os.getenv('MONGO_USER')
MONGO_PASSWORD = os.getenv('MONGO_PASSWORD')

# ==================== MONGODB (CONFIG) ====================
# Dict usado pela classe ConexaoMongoDB (core/conexoes.py)
# As variaveis soltas MONGO_* continuam existindo (usadas por outros modulos)
MONGO_CONFIG = {
    'HOST': os.getenv('MONGO_HOST', 'localhost'),
    'PORT': int(os.getenv('MONGO_PORT', '27017')),
    'DATABASE': os.getenv('MONGO_DB', 'bancocnpj'),
    'USER': os.getenv('MONGO_USER', ''),
    'PASSWORD': os.getenv('MONGO_PASSWORD', ''),
}

# ==================== GEOLOCALIZACAO DOS IPs ====================
# Fonte usada por `manage.py localizar_acessos` (chat/geoip.py).
#   'maxmind' = GeoLite2-City.mmdb local (padrao: sem rede, nenhum IP sai do
#               servidor; atualize com scripts/atualizar_geoip.sh)
#   'ip-api'  = http://ip-api.com/batch (100 IPs por POST, sem HTTPS)
#   'ipapi'   = https://ipapi.co/{ip}/json/ (HTTPS, 1 IP por requisicao)
GEOIP_FONTE = os.getenv('GEOIP_FONTE', 'maxmind')

# ==================== KB DINAMICA / CANARIO ====================
# v1 = comportamento historico (sem aliases publicados); v2 = consulta
# CnaeAlias aprovado no admin. Em producao o padrao e' v1 ate o canario.
ATIVIDADE_RESOLVER = os.getenv(
    'ATIVIDADE_RESOLVER', 'v2' if DEBUG else 'v1').lower()
# Autoaprovacao de alias pela IA so' com a flag ligada e criterios estritos.
KB_AUTO_APPROVE = os.getenv('KB_AUTO_APPROVE', '0') == '1'

# Banco local do MaxMind (cidade/UF/pais). Nao entra no git (.gitignore).
GEOIP_CIDADE_MMDB = os.getenv(
    'GEOIP_CIDADE_MMDB', str(BASE_DIR / 'geoip' / 'GeoLite2-City.mmdb'),
)

# ==================== CARGA DA RFB (ProjetoCargaDados) ====================
# Admin do ProjetoCargaDados, onde fica a pagina de estatisticas da carga
# (tempos e linhas de cada execucao). Ele roda so em dev, na porta 8003; em
# producao nao existe, entao com a URL vazia o botao da /estatisticas/ nao
# aparece. Para apontar para outro host, defina CARGA_ADMIN_URL no .env.
CARGA_ADMIN_URL = os.getenv(
    'CARGA_ADMIN_URL',
    'http://127.0.0.1:8003' if DEBUG else '',
).rstrip('/')

