"""
Identidade do visitante, limite diario e creditos.

Sem acentos no codigo.

Regras (revisao de 23/11, apos auditoria):
- o debito acontece ANTES de enviar o arquivo; se o streaming falhar, ha'
  estorno e a exportacao fica como 'falhou';
- saldo e' alterado com UPDATE condicional (F()), nunca read-modify-write;
- conta logada sem Cliente TAMBEM tem limite (a chave e' o usuario);
- paginacao tem teto proprio, maior que o de pesquisa nova, para nao permitir
  varrer a base 20 linhas por request.
"""

import ipaddress
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.signing import BadSignature, dumps, loads
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Cliente, LinhaExportada, MovimentoCredito, PesquisaAnonima

logger = logging.getLogger(__name__)

COOKIE = 'visitante_id'
SAL = 'exportacao.visitante'
DEDUP_DIAS = 30
LOTE_CNPJ = 5000


class SaldoInsuficiente(Exception):
    """Saldo menor que o custo da exportacao."""

    def __init__(self, necessarios, disponiveis):
        self.necessarios = necessarios
        self.disponiveis = disponiveis
        super().__init__(f'Saldo insuficiente: {necessarios} necessarios, {disponiveis} disponiveis.')


# ============================================================
# IDENTIDADE
# ============================================================

def visitante_id(request) -> str:
    """Le (ou cria) o identificador anonimo assinado no cookie."""
    bruto = request.COOKIES.get(COOKIE, '')
    if bruto:
        try:
            return loads(bruto, salt=SAL, max_age=365 * 24 * 3600)
        except BadSignature:
            pass
    novo = secrets.token_urlsafe(16)
    request._visitante_novo = novo  # a view grava o cookie na resposta
    return novo


def _ip_valido(valor):
    try:
        return str(ipaddress.ip_address(str(valor).strip()))
    except (ValueError, TypeError):
        return None


def ip_do_request(request):
    """
    IP do cliente.

    O nginx define X-Real-IP = $remote_addr (valor confiavel, sob nosso
    controle). X-Forwarded-For NAO e' usado: com $proxy_add_x_forwarded_for o
    cliente escolhe o primeiro item, e usa-lo permitiria burlar o teto por IP.
    Sem X-Real-IP (dev), cai no REMOTE_ADDR.
    """
    candidato = _ip_valido(request.META.get('HTTP_X_REAL_IP'))
    if candidato:
        return candidato
    return _ip_valido(request.META.get('REMOTE_ADDR'))


def aplicar_cookie_visitante(request, response):
    """Grava o cookie assinado quando visitante_id foi criado nesta request."""
    novo = getattr(request, '_visitante_novo', None)
    if novo:
        response.set_cookie(COOKIE, dumps(novo, salt=SAL), max_age=365 * 24 * 3600,
                            httponly=True, samesite='Lax',
                            secure=not getattr(settings, 'DEBUG', False))
    return response


# ============================================================
# LIMITE DIARIO
# ============================================================

def _limite(user, tipo):
    if tipo == 'pagina':
        return (getattr(settings, 'PAGINAS_DIA_CONTA', 3000)
                if user is not None and user.is_authenticated
                else getattr(settings, 'PAGINAS_DIA_ANONIMO', 300))
    return (getattr(settings, 'PESQUISAS_DIA_CONTA', 10)
            if user is not None and user.is_authenticated
            else getattr(settings, 'PESQUISAS_DIA_ANONIMO', 5))


def _chave(user, request, tipo):
    if user is not None and user.is_authenticated:
        return f'{tipo}:user:{user.pk}'
    return f'{tipo}:{visitante_id(request)}'


def registrar_uso(request, tipo='pesquisa') -> dict:
    """
    Conta um uso do dia (tipo 'pesquisa' ou 'pagina').

    Devolve {'permitido': bool, 'usados': int, 'limite': int}. Cliente com
    cadastro (mesmo sem saldo) nao passa por aqui: o limite dele e' o credito.
    """
    user = getattr(request, 'user', None)
    if cliente_do_usuario(user) is not None:
        return {'permitido': True, 'usados': 0, 'limite': 0}

    chave = _chave(user, request, tipo)
    dia = timezone.localdate()
    ip = ip_do_request(request)

    for _tentativa in range(2):
        try:
            with transaction.atomic():
                registro, _criado = PesquisaAnonima.objects.get_or_create(
                    visitante=chave, dia=dia, defaults={'ip': ip, 'total': 0})
                PesquisaAnonima.objects.filter(pk=registro.pk).update(
                    total=F('total') + 1, ip=ip)
            break
        except Exception:  # IntegrityError na primeira request simultanea do dia
            if _tentativa == 1:
                logger.exception('Falha ao registrar uso (%s)', chave)
                return {'permitido': False, 'usados': 0, 'limite': _limite(user, tipo)}

    registro.refresh_from_db()
    limite = _limite(user, tipo)

    total_ip = 0
    if ip:
        from django.db.models import Sum
        total_ip = (PesquisaAnonima.objects.filter(dia=dia, ip=ip)
                    .aggregate(total=Sum('total'))['total'] or 0)
    teto_ip = (getattr(settings, 'PAGINAS_DIA_IP', 3000) if tipo == 'pagina'
               else getattr(settings, 'PESQUISAS_DIA_IP', 20))

    permitido = registro.total <= limite and total_ip <= teto_ip
    return {'permitido': permitido, 'usados': registro.total, 'limite': limite}


def registrar_pesquisa(request) -> dict:
    """Compatibilidade: conta pesquisa nova."""
    return registrar_uso(request, 'pesquisa')


def registrar_navegacao(request, nova_pesquisa: bool) -> dict:
    """Conta navegacao da lista: pesquisa nova ou apenas uma pagina."""
    return registrar_uso(request, 'pesquisa' if nova_pesquisa else 'pagina')


# ============================================================
# CLIENTE E CREDITOS
# ============================================================

def cliente_do_usuario(user):
    if user is None or not user.is_authenticated:
        return None
    return Cliente.objects.filter(user=user).first()


def creditos_necessarios(cliente, cnpjs) -> int:
    """Quantos CNPJs ainda nao foram exportados nos ultimos 30 dias."""
    if not cnpjs:
        return 0
    if cliente is None:
        return len(cnpjs)
    desde = timezone.now() - timedelta(days=DEDUP_DIAS)
    ja = set(
        LinhaExportada.objects.filter(cliente=cliente, criado_em__gte=desde,
                                      cnpj__in=cnpjs).values_list('cnpj', flat=True)
    )
    return len([c for c in cnpjs if c not in ja])


def debitar(cliente, quantidade: int, referencia: str = '') -> int:
    """
    Debita creditos com UPDATE condicional (sem read-modify-write).

    Levanta SaldoInsuficiente quando nao ha' saldo. E' atomico com o movimento.
    """
    if cliente is None or quantidade <= 0:
        return 0
    with transaction.atomic():
        atualizadas = Cliente.objects.filter(
            pk=cliente.pk, saldo_creditos__gte=quantidade,
        ).update(saldo_creditos=F('saldo_creditos') - quantidade)
        if not atualizadas:
            atual = Cliente.objects.filter(pk=cliente.pk).values_list('saldo_creditos', flat=True).first() or 0
            raise SaldoInsuficiente(quantidade, atual)
        MovimentoCredito.objects.create(
            cliente=cliente, tipo='consumo', quantidade=-quantidade,
            referencia=referencia[:120])
        cliente.refresh_from_db(fields=['saldo_creditos'])
    return quantidade


def estornar(cliente, quantidade: int, referencia: str = '') -> int:
    """Devolve creditos (usado quando o streaming falha depois do debito)."""
    if cliente is None or quantidade <= 0:
        return 0
    with transaction.atomic():
        Cliente.objects.filter(pk=cliente.pk).update(
            saldo_creditos=F('saldo_creditos') + quantidade)
        MovimentoCredito.objects.create(
            cliente=cliente, tipo='estorno', quantidade=quantidade,
            referencia=referencia[:120])
        cliente.refresh_from_db(fields=['saldo_creditos'])
    return quantidade


def gravar_linhas(cliente, cnpjs) -> int:
    """Grava os CNPJs exportados (dedup de 30 dias), em lotes."""
    if cliente is None or not cnpjs:
        return 0
    desde = timezone.now() - timedelta(days=DEDUP_DIAS)
    ja = set(LinhaExportada.objects.filter(
        cliente=cliente, criado_em__gte=desde, cnpj__in=cnpjs,
    ).values_list('cnpj', flat=True))
    novos = [c for c in dict.fromkeys(cnpjs) if c not in ja]
    with transaction.atomic():
        for inicio in range(0, len(novos), LOTE_CNPJ):
            LinhaExportada.objects.bulk_create(
                [LinhaExportada(cliente=cliente, cnpj=c)
                 for c in novos[inicio:inicio + LOTE_CNPJ]],
                ignore_conflicts=True,
            )
    return len(novos)


def registrar_consumo(cliente, exportacao, cnpjs) -> int:
    """
    Debita os creditos devidos e grava as linhas para a deduplicacao.

    Chamado ANTES do envio do arquivo (ver views). So' grava CNPJ novo.
    """
    if cliente is None:
        return 0
    devidos = creditos_necessarios(cliente, cnpjs)
    if devidos:
        debitar(cliente, devidos, referencia=f'exportacao:{exportacao.pk}')
    desde = timezone.now() - timedelta(days=DEDUP_DIAS)
    ja = set(LinhaExportada.objects.filter(
        cliente=cliente, criado_em__gte=desde, cnpj__in=cnpjs,
    ).values_list('cnpj', flat=True))
    novos = [c for c in dict.fromkeys(cnpjs) if c not in ja]
    with transaction.atomic():
        for inicio in range(0, len(novos), LOTE_CNPJ):
            LinhaExportada.objects.bulk_create(
                [LinhaExportada(cliente=cliente, cnpj=c)
                 for c in novos[inicio:inicio + LOTE_CNPJ]],
                ignore_conflicts=True,
            )
    return devidos


def registrar_consumo_linhas(cliente, exportacao, quantidade: int) -> int:
    """Debita N creditos (chat: 1 credito por linha, sem dedup)."""
    if cliente is None or quantidade <= 0:
        return 0
    return debitar(cliente, quantidade, referencia=f'exportacao:{exportacao.pk}')
