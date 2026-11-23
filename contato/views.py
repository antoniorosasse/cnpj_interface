"""
Recebe o formulario de contato da pagina do produto.

Fluxo: honeypot -> limite por IP -> valida -> grava o lead -> tenta avisar por
e-mail (so quando o SMTP esta' configurado) -> volta para a landing.

Nada aqui depende de SMTP para gravar: o lead fica em django_internal e pode
ser visto no admin (/admin/contato/leadcontato/), mesmo se o e-mail falhar.
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect

from core.landing import render_landing

from .forms import ContatoForm

logger = logging.getLogger(__name__)

LIMITE_POR_IP = 5          # mensagens por hora, por IP
JANELA_LIMITE = 3600       # segundos
ASSUNTO_EMAIL = 'Novo contato pelo site'


def _ip(request):
    encaminhado = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if encaminhado:
        return encaminhado.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _estourou_limite(ip: str) -> bool:
    if not ip:
        return False
    chave = f'contato-limite-{ip}'
    cache.add(chave, 0, JANELA_LIMITE)
    try:
        return cache.incr(chave) > LIMITE_POR_IP
    except ValueError:
        cache.set(chave, 1, JANELA_LIMITE)
        return False


def _avisar_por_email(lead) -> None:
    destino = getattr(settings, 'CONTATO_EMAIL_DESTINO', '').strip()
    if not destino or not getattr(settings, 'EMAIL_HOST', ''):
        logger.info('Contato %s gravado sem aviso por e-mail (SMTP nao configurado).', lead.pk)
        return
    corpo = (
        f'Nome: {lead.nome}\n'
        f'E-mail: {lead.email}\n'
        f'Empresa: {lead.empresa or "-"}\n'
        f'Telefone: {lead.telefone or "-"}\n\n'
        f'{lead.mensagem}\n'
    )
    try:
        send_mail(ASSUNTO_EMAIL, corpo, getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                  [destino], fail_silently=False)
    except Exception:  # noqa: BLE001 - o lead ja' esta' salvo; nao derruba a pagina
        logger.exception('Falha ao avisar por e-mail sobre o contato %s', lead.pk)


def enviar(request):
    """POST do formulario de contato."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # Campos fora do model: honeypot e consentimento.
    if request.POST.get('website'):
        # Robo: responde igual ao sucesso, mas nao grava nada.
        return redirect('/?enviado=1#contato')

    ip = _ip(request)
    if _estourou_limite(ip):
        return render_landing(
            request,
            form=ContatoForm(request.POST),
            erro='Recebemos varias mensagens deste acesso. Tente de novo em uma hora.',
            status=429,
        )

    form = ContatoForm(request.POST)
    if not form.is_valid():
        return render_landing(request, form=form, status=400)

    lead = form.save(commit=False)
    lead.ip = ip
    lead.origem = request.META.get('HTTP_REFERER', '')[:200]
    lead.save()
    _avisar_por_email(lead)
    return redirect('/?enviado=1#contato')
