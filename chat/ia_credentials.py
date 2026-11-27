# cnpj_interface/chat/ia_credentials.py
"""
Criptografia das chaves de API de IA guardadas no banco pelo admin.

A chave-mestra vem de IA_CREDENTIALS_KEY (opcional) ou e' derivada da
SECRET_KEY do Django. O valor da API nunca e' gravado em texto puro.
"""
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _fernet():
    segredo = os.getenv('IA_CREDENTIALS_KEY') or settings.SECRET_KEY
    base = hashlib.sha256(('busquecnpj:ia:' + str(segredo)).encode('utf-8'))
    chave = base64.urlsafe_b64encode(base.digest())
    return Fernet(chave)


def criptografar(valor):
    if not valor:
        return ''
    return _fernet().encrypt(str(valor).encode('utf-8')).decode('ascii')


def descriptografar(valor):
    if not valor:
        return ''
    try:
        return _fernet().decrypt(str(valor).encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, TypeError):
        logger.exception('Falha ao descriptografar chave de IA')
        return ''


def mascarar(valor):
    if not valor:
        return '(nao configurada)'
    valor = str(valor)
    if len(valor) <= 10:
        return valor[:2] + '...'
    return f'{valor[:6]}...{valor[-4:]}'
