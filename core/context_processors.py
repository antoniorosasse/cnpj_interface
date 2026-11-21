# /home/neto/cnpj_interface/core/context_processors.py
"""
Variaveis de site comuns a todos os templates (core/base.html).

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.conf import settings


# Porta do runserver local do cnpj_interface: e' ele quem serve a landing
# do produto em /site/ (o menu_interface fica na 8000, so portfolio).
PORTA_CNPJ_DEV = 8002

# Producao: a landing e' o dominio raiz do produto (servido por este app).
SITE_HOME_PRODUCAO = 'https://busquecnpj.com.br/'


def url_inicio_do_produto(request=None):
    """
    URL da pagina inicial do produto (landing comercial em /site/).

    Ordem de resolucao:
      1. SITE_HOME_URL do .env, quando definida (vence sempre);
      2. DEBUG=True  -> request.build_absolute_uri('/site/')
                        (ex.: http://127.0.0.1:8002/site/ no runserver);
                        sem request -> http://localhost:8002/site/
      3. producao    -> https://busquecnpj.com.br/

    A landing agora e' servida por este proprio app (core:landing), entao o
    link do dev nao depende mais do menu_interface nem da porta 8000. No
    deploy o dominio raiz continua sendo busquecnpj.com.br.

    Usada pelo context processor (marca e item Inicio de base.html) e pela
    view core.views.inicio_do_produto (destino apos o login).
    """
    url = getattr(settings, 'SITE_HOME_URL', '').strip()
    if url:
        return url
    if settings.DEBUG:
        if request is not None:
            return request.build_absolute_uri('/site/')
        return f'http://localhost:{PORTA_CNPJ_DEV}/site/'
    return SITE_HOME_PRODUCAO


def site_home(request):
    """Expoe site_home_url para os templates (marca e item Inicio do menu)."""
    return {'site_home_url': url_inicio_do_produto(request)}
