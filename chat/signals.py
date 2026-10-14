# cnpj_interface/chat/signals.py
"""
Signals do chat: registra eventos de acesso na tabela LogAcesso.

Por enquanto:
  - user_logged_in: registra cada login bem-sucedido.

Os logs de 'chat_pergunta' sao registrados direto na view (chat_api_view),
nao por signal.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .models import LogAcesso


logger = logging.getLogger(__name__)


def _pegar_ip(request):
    """Extrai o IP do cliente do request (respeita X-Forwarded-For)."""
    if request is None:
        return None

    # Se estiver atras de proxy (Nginx), o IP real vem no X-Forwarded-For.
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        # X-Forwarded-For pode ter varios IPs separados por virgula.
        # O primeiro e' o cliente real.
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


@receiver(user_logged_in)
def registrar_login(sender, request, user, **kwargs):
    """Grava um LogAcesso do tipo 'login' quando alguem loga."""
    try:
        LogAcesso.objects.create(
            usuario=user,
            tipo=LogAcesso.TIPO_LOGIN,
            ip=_pegar_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '') or '')[:300] if request else '',
        )
    except Exception:
        # Nunca deixe o log quebrar o login do usuario.
        logger.exception('Falha ao registrar log de login')