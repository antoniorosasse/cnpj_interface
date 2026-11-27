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


def _fernet_de(segredo):
    base = hashlib.sha256(('busquecnpj:ia:' + str(segredo)).encode('utf-8'))
    return Fernet(base64.urlsafe_b64encode(base.digest()))


def descriptografar(valor):
    """Devolve a chave, '' se vazia, ou None se nao for possivel decifrar.

    Tenta a chave atual e, se IA_CREDENTIALS_KEY estiver definida, tambem a
    derivacao antiga da SECRET_KEY. Isso permite migrar para uma chave-mestra
    propria sem perder os segredos ja cadastrados.
    """
    if not valor:
        return ''
    candidatos = [_fernet()]
    if os.getenv('IA_CREDENTIALS_KEY'):
        candidatos.append(_fernet_de(settings.SECRET_KEY))
    for fernet in candidatos:
        try:
            return fernet.decrypt(str(valor).encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError, TypeError):
            continue
    logger.error('Nao foi possivel descriptografar uma chave de IA')
    return None


def mascarar(valor):
    if not valor:
        return '(nao configurada)'
    valor = str(valor)
    if len(valor) <= 10:
        return valor[:2] + '...'
    return f'{valor[:6]}...{valor[-4:]}'
