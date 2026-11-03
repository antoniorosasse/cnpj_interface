# cnpj_interface/core/log_acesso.py
"""
Registro de acesso as paginas de consulta de empresas (app core).

Grava em chat.models.LogAcesso (banco 'default' = django_internal), a mesma
tabela usada pelos eventos do chat, para existir um unico lugar de auditoria
(admin somente leitura + comando `manage.py relatorio_acesso`).

A pagina de empresas e' PUBLICa: quando nao ha usuario autenticado o evento
e' gravado com usuario nulo. O log nunca "inventa" quem acessou - o que
identifica o visitante e' o IP + user agent + filtros usados.

Observacao: o cookie de sessao so' atravessa subdominios (chat. x cnpj.) quando
SESSION_COOKIE_DOMAIN esta' definido no ambiente (producao usa '.arosasse.tech');
sem isso o usuario aparece nulo na consulta mesmo tendo logado no chat.

Deduplicacao: um mesmo evento (tipo + IP + detalhe) e' gravado no maximo
uma vez por JANELA_DEDUPE_SEGUNDOS. Evita enxurrada de linhas quando o
visitante pagina, recarrega a pagina ou um robo varre o site.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import hashlib
import ipaddress
import logging

from django.core.cache import cache

from chat.models import LogAcesso


logger = logging.getLogger(__name__)

# Reexportados para as views do core nao precisarem importar chat.models.
TIPO_LISTA_EMPRESAS = LogAcesso.TIPO_EMPRESAS_LISTA
TIPO_DETALHE_EMPRESA = LogAcesso.TIPO_EMPRESAS_DETALHE

JANELA_DEDUPE_SEGUNDOS = 600  # 10 minutos
PREFIXO_CHAVE_CACHE = 'log_acesso_empresas'
TAMANHO_MAX_DETALHE = 500


def pegar_ip(request):
    """
    IP do cliente. Respeita X-Forwarded-For (Nginx na frente do gunicorn).

    O Nginx usa `$proxy_add_x_forwarded_for`, ou seja, ele ANEXA o IP real ao
    final do header que o cliente mandou. Pegar o PRIMEIRO elemento seria
    aceitar o valor enviado pelo visitante (comprovado: mandando
    `X-Forwarded-For: 203.0.113.77` o log gravava esse IP falso). Portanto o
    que vale e' o ULTIMO elemento - o unico escrito pelo nosso proxy.

    Premissa: exatamente um proxy confiavel na frente do gunicorn (Nginx).
    Se entrar CDN/balanceador, o ultimo elemento passa a ser o IP do CDN.

    Retorna None se o valor nao for um IP valido - o campo e'
    GenericIPAddressField e nao aceita lixo.
    """
    if request is None:
        return None

    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    bruto = xff.split(',')[-1].strip() if xff else request.META.get('REMOTE_ADDR')

    try:
        return str(ipaddress.ip_address(bruto))
    except (TypeError, ValueError):
        return None


def _chave_dedupe(tipo, ip, detalhe):
    """Chave curta de cache para a janela de deduplicacao."""
    base = f'{tipo}|{ip or ""}|{detalhe}'
    resumo = hashlib.md5(base.encode('utf-8', 'ignore')).hexdigest()
    return f'{PREFIXO_CHAVE_CACHE}:{resumo}'


def registrar_acesso(request, tipo, detalhe=''):
    """
    Grava um LogAcesso para um acesso a pagina de empresas.

    Nunca levanta excecao: falha de log nao pode derrubar a consulta.
    Retorna True quando gravou, False quando deduplicou ou falhou.
    """
    detalhe = (detalhe or '').strip()[:TAMANHO_MAX_DETALHE]
    ip = pegar_ip(request)
    chave = _chave_dedupe(tipo, ip, detalhe)

    try:
        # cache.add so "ganha" a primeira vez dentro da janela
        if not cache.add(chave, 1, timeout=JANELA_DEDUPE_SEGUNDOS):
            return False
    except Exception:
        # Cache indisponivel nao pode impedir o registro.
        logger.exception('Falha no cache de dedupe do log de acesso')
        chave = None

    try:
        usuario = None
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            usuario = user

        LogAcesso.objects.create(
            usuario=usuario,
            tipo=tipo,
            ip=ip,
            user_agent=(request.META.get('HTTP_USER_AGENT', '') or '')[:300],
            detalhe=detalhe,
        )
        return True
    except Exception:
        logger.exception('Falha ao registrar log de acesso (%s)', tipo)
        if chave:
            # Libera a janela para uma nova tentativa.
            cache.delete(chave)
        return False
