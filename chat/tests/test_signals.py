# cnpj_interface/chat/tests/test_signals.py
"""
Testes do helper de IP usado no log de acesso (login e pergunta do chat).

Roda com:
    python manage.py test chat.tests.test_signals

O Nginx usa `$proxy_add_x_forwarded_for` (ANEXA o IP real no fim do header),
entao o IP que vale e' o ULTIMO elemento - o primeiro e' o que o visitante
mandou e pode ser forjado.
"""

from django.test import SimpleTestCase

from chat.signals import _pegar_ip


class _RequestFake:
    """Request minimo: so o que o helper le' (request.META)."""

    def __init__(self, **meta):
        self.META = dict(meta)


class PegarIpTestCase(SimpleTestCase):

    def test_usa_ultimo_elemento_do_x_forwarded_for(self):
        request = _RequestFake(HTTP_X_FORWARDED_FOR='203.0.113.9, 198.51.100.20')
        self.assertEqual(_pegar_ip(request), '198.51.100.20')

    def test_x_forwarded_for_com_um_valor(self):
        request = _RequestFake(HTTP_X_FORWARDED_FOR='198.51.100.21')
        self.assertEqual(_pegar_ip(request), '198.51.100.21')

    def test_sem_x_forwarded_for_usa_remote_addr(self):
        request = _RequestFake(REMOTE_ADDR='198.51.100.22')
        self.assertEqual(_pegar_ip(request), '198.51.100.22')

    def test_request_none(self):
        self.assertIsNone(_pegar_ip(None))
