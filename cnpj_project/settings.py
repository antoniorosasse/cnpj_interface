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

# ==================== CSRF ====================
# Origens confiaveis para requisicoes POST com CSRF (Django 4+)
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()
]

# ==================== APPS ====================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',  # App principal
    'chat',  # Chat IA
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

# ==================== SUBPATH (produção vs local) ====================
# Em produção, o app roda em https://arosasse.tech/cnpj/
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
LOGIN_REDIRECT_URL = '/chat/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# ==================== CONFIGURAÇÕES DOS BANCOS (para conexões diretas, se necessário) ====================
AS400_HOST = os.getenv('AS400_HOST')
AS400_USER = os.getenv('AS400_USER')
AS400_PASSWORD = os.getenv('AS400_PASSWORD')
AS400_LIBRARY = os.getenv('AS400_LIBRARY')
AS400_JDBC_JAR = os.getenv('AS400_JDBC_JAR')

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
