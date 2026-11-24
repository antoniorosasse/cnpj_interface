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
# CONTADORES (lista, chat, exportacao)
# ============================================================

TIPOS = ('lista', 'chat', 'exportacao')
_ALIAS = {'pesquisa': 'lista', 'pagina': 'lista'}


def _normalizar_tipo(tipo: str) -> str:
    return _ALIAS.get(tipo, tipo if tipo in TIPOS else 'lista')


def configuracao():
    """Linha unica de parametros (cria com os defaults se nao existir)."""
    from .models import ConfiguracaoUso
    cfg = ConfiguracaoUso.objects.filter(pk=1).first()
    if cfg is None:
        cfg = ConfiguracaoUso(id=1)
        cfg.save()
    return cfg


def regras_para(user):
    """
    Regras efetivas por tipo, juntando global + override do usuario.

    {'lista': {'ativa': bool, 'limite': int, 'ilimitado': bool}, ...}
    Staff e usuario com ignorar_limites continuam sendo CONTADOS, mas nunca
    bloqueiam.
    """
    from .models import LimiteUsuario

    cfg = configuracao()
    autenticado = bool(user is not None and user.is_authenticated)
    lim = LimiteUsuario.objects.filter(user=user).first() if autenticado else None
    staff = bool(autenticado and (user.is_staff or user.is_superuser))

    regras = {}
    for tipo in TIPOS:
        ativa = getattr(cfg, f'{tipo}_ativa', True) and cfg.ativo_geral
        if lim is not None and lim.ativo is not None:
            ativa = ativa and lim.ativo
        sufixo = 'conta' if autenticado else 'anonimo'
        limite = getattr(cfg, f'{tipo}_dia_{sufixo}', 5)
        if lim is not None:
            override = getattr(lim, f'{tipo}_dia', None)
            if override is not None:
                limite = override
        regras[tipo] = {
            'ativa': ativa,
            'limite': limite,
            'ilimitado': staff or bool(lim is not None and lim.ignorar_limites),
        }
    return regras


def registrar_uso(request, tipo='lista') -> dict:
    """
    Conta um uso do dia e diz se pode continuar.

    Devolve {'permitido', 'usados', 'limite', 'contando', 'tipo'}.
    Contador desligado (global ou por usuario) = nao conta e nao bloqueia.
    """
    tipo = _normalizar_tipo(tipo)
    user = getattr(request, 'user', None)
    regra = regras_para(user)[tipo]
    if not regra['ativa']:
        return {'permitido': True, 'usados': 0, 'limite': 0,
                'contando': False, 'tipo': tipo}

    autenticado = bool(user is not None and user.is_authenticated)
    identidade = f'user:{user.pk}' if autenticado else visitante_id(request)
    ip = ip_do_request(request)
    dia = timezone.localdate()

    registro = None
    for _tentativa in range(2):
        try:
            with transaction.atomic():
                registro, _criado = PesquisaAnonima.objects.get_or_create(
                    visitante=identidade, tipo=tipo, dia=dia,
                    defaults={'ip': ip, 'total': 0})
                PesquisaAnonima.objects.filter(pk=registro.pk).update(
                    total=F('total') + 1, ip=ip)
            break
        except Exception:  # IntegrityError na primeira request simultanea
            if _tentativa == 1:
                logger.exception('Falha ao registrar uso (%s/%s)', tipo, identidade)
                return {'permitido': False, 'usados': 0, 'limite': regra['limite'],
                        'contando': True, 'tipo': tipo}

    registro.refresh_from_db()
    usados = registro.total
    limite = regra['limite']
    permitido = regra['ilimitado'] or usados <= limite
    return {'permitido': permitido, 'usados': usados, 'limite': limite,
            'contando': True, 'tipo': tipo, 'ilimitado': regra['ilimitado']}


def registrar_pesquisa(request) -> dict:
    """Compatibilidade: consulta da lista."""
    return registrar_uso(request, 'lista')


def registrar_navegacao(request, nova_pesquisa: bool = True) -> dict:
    """Lista conta qualquer navegacao como consulta (paginacao inclusive)."""
    return registrar_uso(request, 'lista')


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


def uso_atual(request, tipo='lista') -> dict:
    """Le o contador do dia sem incrementar (para exibir na tela)."""
    tipo = _normalizar_tipo(tipo)
    user = getattr(request, 'user', None)
    regra = regras_para(user)[tipo]
    if not regra['ativa']:
        return {'usados': 0, 'limite': 0, 'contando': False, 'ilimitado': True}
    autenticado = bool(user is not None and user.is_authenticated)
    identidade = f'user:{user.pk}' if autenticado else visitante_id(request)
    registro = PesquisaAnonima.objects.filter(
        visitante=identidade, tipo=tipo, dia=timezone.localdate()).first()
    return {'usados': registro.total if registro else 0,
            'limite': regra['limite'], 'contando': True,
            'ilimitado': regra['ilimitado']}
