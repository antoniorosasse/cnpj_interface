# /home/neto/cnpj_interface/core/context_processors.py
"""
Variaveis de site comuns a todos os templates (core/base.html).

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.conf import settings


# Porta do menu_interface no runserver local: e' ele quem serve a landing
# do produto (busquecnpj.com.br) em /site/.
PORTA_MENU_DEV = 8000

# Producao: a landing e' o dominio raiz, servido pelo menu.service.
SITE_HOME_PRODUCAO = 'https://busquecnpj.com.br/'


def url_inicio_do_produto(request=None):
    """
    URL da pagina inicial do produto (landing do menu_interface).

    Ordem de resolucao:
      1. SITE_HOME_URL do .env, quando definida (vence sempre);
      2. DEBUG=True  -> http://<host>:8000/site/  (preview da landing no dev);
      3. producao    -> https://busquecnpj.com.br/

    O caminho /site/ responde em qualquer Host, entao o link do dev nao
    depende do HOSTS_COMERCIAL configurado no menu_interface.

    Usada pelo context processor (marca e item Inicio de base.html) e pela
    view core.views.inicio_do_produto (destino apos o login).
    """
    url = getattr(settings, 'SITE_HOME_URL', '').strip()
    if url:
        return url
    if settings.DEBUG:
        host = request.get_host().split(':')[0] if request else 'localhost'
        return f'http://{host}:{PORTA_MENU_DEV}/site/'
    return SITE_HOME_PRODUCAO


def site_home(request):
    """Expoe site_home_url para os templates (marca e item Inicio do menu)."""
    return {'site_home_url': url_inicio_do_produto(request)}
