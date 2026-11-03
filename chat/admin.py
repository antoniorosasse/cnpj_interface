# cnpj_interface/chat/admin.py
"""
Configuracao do admin do Django para o app chat.

LogAcesso: somente leitura (auditoria). Ninguem cria, edita ou apaga
pelo admin. Filtros por tipo/data, busca por IP, navegacao por data.

A lista (/admin/chat/logacesso/) abre com um painel de resumo: mesmos
numeros de `manage.py relatorio_acesso` (via chat/relatorio.py), a lista
dos ultimos logins (usuario, IP, navegador) e os comandos prontos para
copiar e colar. O periodo do painel vem do lookup `?criado_em__gte=AAAA-MM-DD`
(os links de 1/7/30/90 dias montam essa URL); sem ele, ultimos 7 dias.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib import admin
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .models import LogAcesso
from .relatorio import PERIODOS, resumo_acesso

# Host/pasta usados nos comandos exibidos na tela (copiar e colar).
HOST_PROD = 'srv1870314.hstgr.cloud'
PASTA_PROD = '~/cnpj_interface'

# Periodo mostrado quando a URL nao traz `criado_em__gte`
DIAS_PADRAO = 7

# Nome do lookup usado no link de periodo. Precisa ser um lookup que o
# ChangeList do admin aceite (senao o Django redireciona com ?e=1 e o
# filtro e' descartado). `criado_em` esta em list_filter/date_hierarchy.
PARAM_DESDE = 'criado_em__gte'


def _parse_desde(valor):
    """Le 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM[:ss]' e devolve datetime aware."""
    if not valor:
        return None
    quando = parse_datetime(valor)
    if quando is None:
        dia = parse_date(valor)
        if dia is None:
            return None
        quando = datetime.combine(dia, time.min)
    if timezone.is_naive(quando):
        quando = timezone.make_aware(quando)
    return quando


@admin.register(LogAcesso)
class LogAcessoAdmin(admin.ModelAdmin):
    change_list_template = 'admin/chat/logacesso/change_list.html'
    list_display = ('criado_em', 'tipo', 'usuario', 'ip', 'navegador', 'resumo_detalhe')
    list_filter = ('tipo', 'criado_em')
    search_fields = ('ip', 'detalhe', 'usuario__username')
    date_hierarchy = 'criado_em'
    ordering = ('-criado_em',)
    list_per_page = 100

    readonly_fields = (
        'usuario', 'tipo', 'ip', 'user_agent', 'detalhe', 'criado_em',
    )

    fieldsets = (
        ('Evento', {
            'fields': ('tipo', 'criado_em'),
        }),
        ('Origem', {
            'fields': ('usuario', 'ip', 'user_agent'),
        }),
        ('Detalhe', {
            'fields': ('detalhe',),
        }),
    )

    @admin.display(description='Detalhe')
    def resumo_detalhe(self, obj):
        """Mostra um preview curto do detalhe na lista."""
        if not obj.detalhe:
            return '-'
        return obj.detalhe[:60] + ('...' if len(obj.detalhe) > 60 else '')

    @admin.display(description='Navegador')
    def navegador(self, obj):
        """User-agent resumido na lista (o completo fica no detalhe)."""
        if not obj.user_agent:
            return '-'
        return obj.user_agent[:40] + ('...' if len(obj.user_agent) > 40 else '')

    # --- Painel de resumo no topo da lista ---

    def changelist_view(self, request, extra_context=None):
        """
        Injeta no template o resumo do periodo, as linhas prontas para as
        tabelas e os comandos de terminal. O admin nao executa comando:
        apenas mostra o texto para copiar e colar.
        """
        extra_context = extra_context or {}

        # Janela do painel. O link de periodo usa `criado_em__gte`, lookup que
        # o ChangeList tambem aplica na lista - painel e tabela mostram o
        # mesmo periodo. Sem o parametro: ultimos DIAS_PADRAO dias.
        desde = _parse_desde(request.GET.get(PARAM_DESDE))
        if desde is None:
            dias = DIAS_PADRAO
        else:
            dias = max(1, (timezone.now() - desde).days)

        # Filtro de tipo que o proprio admin ja aplica (?tipo__exact=)
        tipo = request.GET.get('tipo__exact') or None
        if tipo not in dict(LogAcesso.TIPO_CHOICES):
            tipo = None

        resumo = resumo_acesso(dias=dias, tipo=tipo, desde=desde)

        # Links de periodo preservando os filtros/busca que estiverem na URL
        params = request.GET.copy()
        params.pop(PARAM_DESDE, None)
        params.pop('dias', None)   # parametro antigo: nao e' lookup valido
        params.pop('p', None)      # volta para a primeira pagina
        valor_atual = request.GET.get(PARAM_DESDE)
        links_periodo = []
        for opcao in PERIODOS:
            novos = params.copy()
            valor = (
                timezone.localtime() - timedelta(days=opcao)
            ).isoformat(sep=' ', timespec='seconds')
            novos[PARAM_DESDE] = valor
            links_periodo.append({
                'dias': opcao,
                'url': '?' + urlencode(novos),
                'atual': valor_atual == valor or (
                    valor_atual is None and opcao == DIAS_PADRAO
                ),
            })

        # Totais por tipo com o rotulo legivel
        rotulos = dict(LogAcesso.TIPO_CHOICES)
        totais = [
            {
                'tipo': linha['tipo'],
                'rotulo': rotulos.get(linha['tipo'], linha['tipo']),
                'total': linha['total'],
            }
            for linha in resumo['total_por_tipo']
        ]

        # Por dia ja alinhado com as colunas dos tipos presentes
        tipos_presentes = resumo['tipos_presentes']
        linhas_dia = [
            {
                'dia': dia,
                'celulas': [
                    resumo['por_dia'][dia].get(t, 0) for t in tipos_presentes
                ],
                'total': sum(resumo['por_dia'][dia].values()),
            }
            for dia in resumo['dias_ordenados']
        ]

        extra_context.update({
            'resumo': resumo,
            'totais': totais,
            'tipos_presentes': tipos_presentes,
            'linhas_dia': linhas_dia,
            'links_periodo': links_periodo,
            'dias_atual': dias,
            'comandos': self._comandos(dias),
        })
        return super().changelist_view(request, extra_context=extra_context)

    @staticmethod
    def _comandos(dias):
        """Comandos exibidos na tela para copiar e colar (texto puro)."""
        shell = (
            'from chat.models import LogAcesso\n'
            "for l in LogAcesso.objects.filter(tipo='login')"
            ".select_related('usuario')[:15]:\n"
            "    print(l.criado_em.strftime('%d/%m %H:%M'), "
            "l.usuario.username if l.usuario else '(anonimo)', "
            "l.ip, l.user_agent[:30])"
        )
        return [
            {
                'id': 'cmd-relatorio-prod',
                'titulo': 'Relatório resumido — servidor',
                'descricao': 'Mesmos números do painel, no terminal da VPS.',
                'codigo': (
                    f'ssh neto@{HOST_PROD} "cd {PASTA_PROD} && '
                    f'venv/bin/python manage.py relatorio_acesso --dias {dias}"'
                ),
            },
            {
                'id': 'cmd-relatorio-local',
                'titulo': 'Relatório resumido — local',
                'descricao': 'Roda na máquina de desenvolvimento.',
                'codigo': (
                    f'cd ~/cnpj_interface && venv/bin/python manage.py '
                    f'relatorio_acesso --dias {dias}'
                ),
            },
            {
                'id': 'cmd-shell-prod',
                'titulo': 'Últimos 15 logins — servidor',
                'descricao': 'Uma linha por login: data/hora, usuário, IP, navegador.',
                'codigo': (
                    f'ssh neto@{HOST_PROD} "cd {PASTA_PROD} && '
                    f"venv/bin/python manage.py shell -c \\\"{shell}\\\"\""
                ),
            },
            {
                'id': 'cmd-shell-local',
                'titulo': 'Últimos 15 logins — local',
                'descricao': 'Mesma consulta, sem SSH.',
                'codigo': (
                    f'cd ~/cnpj_interface && venv/bin/python manage.py '
                    f'shell <<\'EOF\'\n{shell}\nEOF'
                ),
            },
        ]

    # --- Bloqueia criacao / edicao / exclusao ---

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
