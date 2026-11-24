"""
Desafio antiabuso do export anonimo (Cloudflare Turnstile) + ticket curto.

Fluxo: export sem ticket -> /exportar/desafio/ -> Turnstile valida no servidor
-> cookie 'export_ticket' assinado (15 min, preso ao visitante_id) -> export.

Sem chaves configuradas o desafio nao age (dev e ambientes sem Cloudflare).
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache
from django.core.signing import BadSignature, dumps, loads

from .servicos import ip_do_request, visitante_id

logger = logging.getLogger(__name__)

SAL_TICKET = 'exportacao.ticket'
COOKIE_TICKET = 'export_ticket'
VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


def configurado() -> bool:
    return bool(getattr(settings, 'TURNSTILE_SITE_KEY', '')
                and getattr(settings, 'TURNSTILE_SECRET_KEY', ''))


def precisa_desafio(config, cliente, staff) -> bool:
    return bool(config is not None and config.desafio_ativo
                and configurado() and cliente is None and not staff)


def emitir_ticket(response, visitante: str, validade_min: int) -> None:
    expira = int(time.time()) + validade_min * 60
    response.set_cookie(
        COOKIE_TICKET,
        dumps({'v': visitante, 'exp': expira}, salt=SAL_TICKET),
        max_age=validade_min * 60,
        httponly=True, samesite='Lax', secure=not settings.DEBUG,
    )


def ticket_valido(request) -> bool:
    bruto = request.COOKIES.get(COOKIE_TICKET)
    if not bruto:
        return False
    try:
        dados = loads(bruto, salt=SAL_TICKET, max_age=24 * 3600)
    except BadSignature:
        return False
    if not isinstance(dados, dict):
        return False
    if int(dados.get('exp', 0)) < int(time.time()):
        return False
    return dados.get('v') == visitante_id(request)


def verificar_turnstile(token: str, ip) -> bool:
    """Valida o token no siteverify da Cloudflare (5 s de timeout)."""
    if not token:
        return False
    dados = urllib.parse.urlencode({
        'secret': settings.TURNSTILE_SECRET_KEY,
        'response': token,
        'remoteip': ip or '',
    }).encode()
    try:
        with urllib.request.urlopen(VERIFY_URL, data=dados, timeout=5) as resp:
            corpo = json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, ValueError):
        logger.exception('Falha ao validar Turnstile')
        return False
    return bool(corpo.get('success'))


def pode_emitir_ticket(request, limite_hora: int) -> bool:
    """Limita a emissao de tickets por IP (padrao 20/hora)."""
    if not limite_hora:
        return True
    ip = ip_do_request(request) or 'sem-ip'
    chave = f'ticket-emitido:{ip}:{int(time.time()) // 3600}'
    total = cache.get(chave, 0)
    if total >= limite_hora:
        return False
    cache.set(chave, total + 1, 3600)
    return True
