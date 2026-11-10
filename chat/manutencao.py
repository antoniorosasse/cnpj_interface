# cnpj_interface/chat/manutencao.py
"""
Manutencao do log de acesso (chat.LogAcesso): contar e apagar registros.

Usado pelo painel do admin (/admin/chat/logacesso/) e pelo comando
`manage.py limpar_acessos`. A exclusao tem idade minima (MIN_DIAS): um
"--dias 1" digitado por engano nao pode zerar a auditoria.

A exclusao e' feita em lotes de `TAMANHO_LOTE` ids: um DELETE unico em
centenas de milhares de linhas seguraria lock e transacao longa no Postgres.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from .models import LogAcesso

# Idade minima (em dias) aceita para apagar. Abaixo disso: ValueError.
MIN_DIAS = 7

# Quantos ids entram em cada DELETE
TAMANHO_LOTE = 5000


def _queryset(dias, tipo=None):
    """Logs mais antigos que `dias` (opcionalmente de um tipo) + a data de corte."""
    antes_de = timezone.now() - timedelta(days=dias)
    qs = LogAcesso.objects.filter(criado_em__lt=antes_de)
    if tipo:
        qs = qs.filter(tipo=tipo)
    return qs, antes_de


def _validar(dias, tipo):
    """Levanta ValueError para janela curta demais ou tipo desconhecido."""
    if not isinstance(dias, int) or dias < MIN_DIAS:
        raise ValueError(f'dias deve ser inteiro >= {MIN_DIAS} (recebido: {dias!r})')
    if tipo and tipo not in dict(LogAcesso.TIPO_CHOICES):
        raise ValueError(f'tipo invalido: {tipo!r}')


def contar_acessos(dias, tipo=None):
    """
    Conta (sem apagar) quantos logs seriam removidos.

    Devolve dict com `dias`, `tipo`, `antes_de`, `total` e `por_tipo`
    (lista de {'tipo', 'total'}, do maior para o menor).
    """
    _validar(dias, tipo)
    qs, antes_de = _queryset(dias, tipo)
    return {
        'dias': dias,
        'tipo': tipo,
        'antes_de': antes_de,
        'total': qs.count(),
        'por_tipo': list(
            qs.values('tipo').annotate(total=Count('id')).order_by('-total')
        ),
    }


def limpar_acessos(dias, tipo=None, dry_run=False, lote=TAMANHO_LOTE):
    """
    Apaga os logs mais antigos que `dias` (>= MIN_DIAS), em lotes de `lote` ids.

    Com `dry_run=True` so conta. O retorno e' o mesmo dict de contar_acessos
    mais `removidos` (0 no dry-run) e `dry_run`.
    """
    resumo = contar_acessos(dias, tipo)
    removidos = 0

    if not dry_run and resumo['total']:
        qs, _ = _queryset(dias, tipo)
        qs = qs.order_by()          # sem ORDER BY: o LIMIT do lote fica barato
        tamanho = max(1, int(lote))
        while True:
            ids = list(qs.values_list('id', flat=True)[:tamanho])
            if not ids:
                break
            apagados, _ = LogAcesso.objects.filter(id__in=ids).delete()
            removidos += apagados
            if len(ids) < tamanho:  # ultimo lote
                break

    resumo['removidos'] = removidos
    resumo['dry_run'] = dry_run
    return resumo
