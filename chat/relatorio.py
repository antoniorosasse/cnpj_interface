# cnpj_interface/chat/relatorio.py
"""
Resumo do log de acesso (chat.LogAcesso).

Extraido de `manage.py relatorio_acesso` para que o comando de terminal e a tela
do admin (/admin/chat/logacesso/) mostrem exatamente os mesmos numeros, sem
duplicar SQL. So leitura: nada aqui grava, altera ou apaga.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import LogAcesso

# Periodos oferecidos no painel do admin (em dias)
PERIODOS = (1, 7, 30, 90)

# Tipos que representam navegacao na consulta de empresas
TIPOS_EMPRESAS = (LogAcesso.TIPO_EMPRESAS_LISTA, LogAcesso.TIPO_EMPRESAS_DETALHE)


def resumo_acesso(dias=7, tipo=None, ultimos_logins=15, desde=None):
    """
    Monta o resumo do periodo pedido.

    `dias`: janela em dias (contada a partir de agora).
    `desde`: data/hora inicial explicita; quando informada, vence `dias`.
    `tipo`: opcional; filtra os blocos de "totais" e "por dia". Os blocos de
            usuarios distintos e de top IPs seguem a mesma regra do comando
            original (consideram a janela inteira, sem o filtro de tipo).
    """
    if desde is None:
        desde = timezone.now() - timedelta(days=dias)

    qs = LogAcesso.objects.filter(criado_em__gte=desde)
    if tipo:
        qs = qs.filter(tipo=tipo)

    total = qs.count()

    total_por_tipo = list(
        qs.values('tipo').annotate(total=Count('id')).order_by('-total')
    )

    # {data: {tipo: total}}
    por_dia = {}
    linhas_por_dia = (
        qs.annotate(dia=TruncDate('criado_em'))
        .values('dia', 'tipo')
        .annotate(total=Count('id'))
        .order_by('dia', 'tipo')
    )
    for row in linhas_por_dia:
        por_dia.setdefault(row['dia'], {})[row['tipo']] = row['total']

    # So as colunas que apareceram no periodo
    tipos_presentes = [
        valor for valor, _ in LogAcesso.TIPO_CHOICES
        if any(valor in linha for linha in por_dia.values())
    ]

    def distintos(**filtro):
        return (
            LogAcesso.objects
            .filter(criado_em__gte=desde, **filtro)
            .values('usuario')
            .distinct()
            .count()
        )

    usuarios = {
        'logaram': distintos(tipo=LogAcesso.TIPO_LOGIN),
        'perguntaram': distintos(tipo=LogAcesso.TIPO_CHAT_PERGUNTA),
        'viram_empresas': (
            LogAcesso.objects
            .filter(criado_em__gte=desde, tipo__in=TIPOS_EMPRESAS)
            .exclude(usuario__isnull=True)
            .values('usuario')
            .distinct()
            .count()
        ),
    }

    def top_ips(**filtro):
        return list(
            LogAcesso.objects
            .filter(criado_em__gte=desde, **filtro)
            .exclude(ip__isnull=True)
            .values('ip')
            .annotate(total=Count('id'))
            .order_by('-total')[:10]
        )

    # Ultimos logins linha a linha (usuario, IP, navegador) - o que o comando
    # de shell mostrado na tela imprime.
    ultimos = list(
        LogAcesso.objects
        .filter(criado_em__gte=desde, tipo=LogAcesso.TIPO_LOGIN)
        .select_related('usuario')
        .order_by('-criado_em')[:ultimos_logins]
    )

    return {
        'dias': dias,
        'tipo': tipo,
        'desde': desde,
        'total': total,
        'total_por_tipo': total_por_tipo,
        'por_dia': por_dia,
        'dias_ordenados': sorted(por_dia.keys()),
        'tipos_presentes': tipos_presentes,
        'usuarios': usuarios,
        'top_ips_chat': top_ips(tipo=LogAcesso.TIPO_CHAT_PERGUNTA),
        'top_ips_empresas': top_ips(tipo__in=TIPOS_EMPRESAS),
        'ultimos_logins': ultimos,
    }
