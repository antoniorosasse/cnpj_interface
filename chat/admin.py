# cnpj_interface/chat/admin.py
"""
Configuracao do admin do Django para o app chat.

LogAcesso: a lista e' somente leitura (auditoria). As unicas escritas sao o
expurgo por idade (/admin/chat/logacesso/expurgar/, superusuario, POST), que
tambem registra a acao no LogEntry do admin, e as acoes da base GeoIP
(geoip/checar|baixar|enviar/, superusuario, POST) - elas mexem no arquivo
geoip/GeoLite2-City.mmdb, nunca no log. Filtros por tipo/data,
busca por IP, navegacao por data e coluna "Localizacao" (cidade/UF/pais,
preenchida por `manage.py localizar_acessos` - nunca no request).

A lista (/admin/chat/logacesso/) abre com um painel de resumo: mesmos
numeros de `manage.py relatorio_acesso` (via chat/relatorio.py), a lista
dos ultimos logins (usuario, IP, navegador) e os comandos prontos para
copiar e colar. O periodo do painel vem do lookup `?criado_em__gte=AAAA-MM-DD`
(os links de 1/7/30/90 dias montam essa URL); sem ele, ultimos 7 dias.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, DELETION, LogEntry
from django.core.exceptions import PermissionDenied
from django.db.models import OuterRef, Subquery
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .geoip import (
    ATRIBUICAO,
    GeoIPIndisponivel,
    estado_base,
    fechar_leitor,
    instalar_arquivo,
    rodar_script,
)
from .manutencao import MIN_DIAS, contar_acessos, limpar_acessos
from .models import LocalizacaoIP, LogAcesso
from .relatorio import PERIODOS, resumo_acesso

# Host/pasta usados nos comandos exibidos na tela (copiar e colar).
HOST_PROD = 'srv1870314.hstgr.cloud'
PASTA_PROD = '~/cnpj_interface'

# Periodo mostrado quando a URL nao traz `criado_em__gte`
DIAS_PADRAO = 7

# Janela padrao do bloco de expurgo (dias)
DIAS_EXPURGO = 90

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


def _url_filtro(request, **lookups):
    """
    Query string de um link do painel de resumo.

    Mantem o que ja esta na URL (inclusive o periodo, `criado_em__gte`, e a
    busca `q`), descarta a pagina atual e aplica os lookups pedidos. Valor
    None remove o lookup - assim um link pode devolver a lista sem filtro.
    """
    params = request.GET.copy()
    params.pop('dias', None)   # parametro antigo: nao e' lookup valido
    params.pop('p', None)      # volta para a primeira pagina
    for chave, valor in lookups.items():
        if valor is None:
            params.pop(chave, None)
        else:
            params[chave] = valor
    return '?' + urlencode(params)


def _parse_dias_expurgo(valor, padrao=DIAS_EXPURGO):
    """
    Le ?expurgo_dias= do bloco de expurgo.

    Valor invalido ou abaixo de MIN_DIAS cai no padrao: o painel nunca
    oferece janela curta demais (mesma trava de chat/manutencao).
    """
    try:
        dias = int(valor)
    except (TypeError, ValueError):
        return padrao
    return dias if dias >= MIN_DIAS else padrao


@admin.register(LogAcesso)
class LogAcessoAdmin(admin.ModelAdmin):
    change_list_template = 'admin/chat/logacesso/change_list.html'
    list_display = (
        'criado_em', 'tipo', 'usuario', 'ip', 'localizacao', 'navegador',
        'resumo_detalhe',
    )
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

    def get_queryset(self, request):
        """
        Anota cidade/UF/pais do IP num Subquery: 1 consulta para a pagina
        inteira (nada de N+1 nem chamada externa no request).
        """
        qs = super().get_queryset(request)
        local = LocalizacaoIP.objects.filter(ip=OuterRef('ip'))
        return qs.annotate(
            _loc_cidade=Subquery(local.values('cidade')[:1]),
            _loc_uf=Subquery(local.values('uf')[:1]),
            _loc_regiao=Subquery(local.values('regiao')[:1]),
            _loc_pais=Subquery(local.values('pais')[:1]),
        )

    @admin.display(description='Localização')
    def localizacao(self, obj):
        """
        Cidade/UF/pais do IP, quando `localizar_acessos` ja rodou.

        Sem LocalizacaoIP a coluna mostra '-' - a lista nunca consulta a
        fonte externa para preencher isso.
        """
        local = '/'.join(p for p in (obj._loc_cidade, obj._loc_uf) if p)
        local = local or obj._loc_regiao
        partes = [p for p in (local, obj._loc_pais) if p]
        return ', '.join(partes) if partes else '-'

    # --- Expurgo (unica escrita do LogAcesso) ---

    def get_urls(self):
        """
        Soma as URLs proprias da lista do admin:
          - expurgar/ (POST): apaga logs antigos
          - geoip/checar|baixar|enviar/ (POST): base de localizacao
        """
        urls = super().get_urls()
        extras = [
            path(
                'expurgar/',
                self.admin_site.admin_view(self.expurgar_view),
                name='chat_logacesso_expurgar',
            ),
            path(
                'geoip/checar/',
                self.admin_site.admin_view(self.geoip_checar_view),
                name='chat_logacesso_geoip_checar',
            ),
            path(
                'geoip/baixar/',
                self.admin_site.admin_view(self.geoip_baixar_view),
                name='chat_logacesso_geoip_baixar',
            ),
            path(
                'geoip/enviar/',
                self.admin_site.admin_view(self.geoip_enviar_view),
                name='chat_logacesso_geoip_enviar',
            ),
        ]
        return extras + urls

    def expurgar_view(self, request):
        """
        Apaga de verdade os logs mais antigos que `dias` (POST, superusuario).

        A lista e' somente leitura; esta e' a unica escrita do LogAcesso e
        fica registrada no LogEntry do admin (quem apagou e quanto).
        """
        if not request.user.is_superuser:
            raise PermissionDenied

        destino = reverse('admin:chat_logacesso_changelist')
        if request.method != 'POST':
            self.message_user(
                request,
                'O expurgo só roda pelo botão do painel (POST).',
                messages.WARNING,
            )
            return HttpResponseRedirect(destino)

        dias = _parse_dias_expurgo(request.POST.get('dias'))
        tipo = request.POST.get('tipo') or None
        if tipo not in dict(LogAcesso.TIPO_CHOICES):
            tipo = None

        resultado = limpar_acessos(dias=dias, tipo=tipo)
        LogEntry.objects.create(
            user_id=request.user.pk,
            object_id='',
            object_repr='Expurgo de logs de acesso'[:200],
            action_flag=DELETION,
            change_message=(
                f'{resultado["removidos"]} registro(s) com mais de {dias} dia(s)'
                + (f' do tipo {tipo}' if tipo else ', todos os tipos')
            ),
        )
        self.message_user(
            request,
            f'Expurgo concluído: {resultado["removidos"]} log(s) com mais de '
            f'{dias} dia(s) apagado(s).',
            messages.SUCCESS,
        )
        return HttpResponseRedirect(destino)

    # --- Base GeoIP: baixar/checar/enviar pelo painel ---

    def _geoip_post(self, request, rotulo):
        """
        Porta de entrada das acoes de GeoIP: so superusuario, so POST.

        Devolve a URL de volta para a lista, ou None quando ja respondeu com
        o aviso "use o botao do painel" (GET).
        """
        if not request.user.is_superuser:
            raise PermissionDenied
        if request.method != 'POST':
            self.message_user(
                request,
                f'{rotulo} só roda pelo botão do painel (POST).',
                messages.WARNING,
            )
            return None
        return reverse('admin:chat_logacesso_changelist')

    def _registrar_geoip(self, request, mensagem):
        """Registra a troca da base no LogEntry do admin (auditoria)."""
        LogEntry.objects.create(
            user_id=request.user.pk,
            object_id='',
            object_repr='Base de localizacao (GeoIP)'[:200],
            action_flag=CHANGE,
            change_message=(mensagem or 'atualizada')[:1000],
        )

    def geoip_checar_view(self, request):
        """HEAD no permalink da MaxMind: diz se ha base nova (nao baixa)."""
        destino = self._geoip_post(request, 'A checagem da base GeoIP')
        if destino is None:
            destino = reverse('admin:chat_logacesso_changelist')
        try:
            ok, saida = rodar_script('--checar')
        except GeoIPIndisponivel as erro:
            self.message_user(request, str(erro), messages.ERROR)
            return HttpResponseRedirect(destino)
        self.message_user(
            request,
            saida or 'O script não devolveu nada.',
            messages.INFO if ok else messages.ERROR,
        )
        return HttpResponseRedirect(destino)

    def geoip_baixar_view(self, request):
        """Roda scripts/atualizar_geoip.sh pelo painel (pacote tem ~27 MB)."""
        destino = self._geoip_post(request, 'O download da base GeoIP')
        if destino is None:
            destino = reverse('admin:chat_logacesso_changelist')
        opcoes = ['--forcar'] if request.POST.get('forcar') else []
        self.message_user(
            request,
            'Baixando a base da MaxMind; isso pode levar alguns minutos.',
            messages.INFO,
        )
        try:
            ok, saida = rodar_script(*opcoes)
        except GeoIPIndisponivel as erro:
            self.message_user(request, str(erro), messages.ERROR)
            return HttpResponseRedirect(destino)
        if ok:
            fechar_leitor()   # o proximo request abre a base nova
            self._registrar_geoip(request, saida or 'base atualizada')
        self.message_user(
            request,
            saida or 'Sem resposta do script.',
            messages.SUCCESS if ok else messages.ERROR,
        )
        return HttpResponseRedirect(destino)

    def geoip_enviar_view(self, request):
        """Instala o .mmdb/.tar.gz enviado no formulario (validado antes)."""
        destino = self._geoip_post(request, 'A instalação da base GeoIP')
        if destino is None:
            destino = reverse('admin:chat_logacesso_changelist')
        enviado = request.FILES.get('arquivo')
        if enviado is None:
            self.message_user(
                request,
                'Escolha o arquivo .mmdb ou .tar.gz do GeoLite2-City.',
                messages.WARNING,
            )
            return HttpResponseRedirect(destino)
        try:
            estado = instalar_arquivo(enviado.name, enviado.read())
        except (ValueError, OSError) as erro:
            self.message_user(
                request, f'Não instalei o arquivo: {erro}', messages.ERROR,
            )
            return HttpResponseRedirect(destino)
        self._registrar_geoip(
            request,
            f'arquivo {enviado.name} instalado (build {estado["build"]})',
        )
        aviso = '' if estado['fonte'] == 'maxmind' else (
            f' Atenção: GEOIP_FONTE está em "{estado["fonte"]}", '
            'não em maxmind.'
        )
        self.message_user(
            request,
            f'Base instalada: {estado["nome"]} '
            f'({estado["tamanho_mb"] or "?"} MB, '
            f'build {estado["build"] or "sem data"}).{aviso}',
            messages.SUCCESS,
        )
        return HttpResponseRedirect(destino)

    # --- Painel de resumo no topo da lista ---

    def changelist_view(self, request, extra_context=None):
        """
        Injeta no template o resumo do periodo, as linhas prontas para as
        tabelas e os comandos de terminal. O admin nao executa comando:
        apenas mostra o texto para copiar e colar.
        """
        extra_context = extra_context or {}

        # Params proprios do bloco de expurgo. Nao sao lookups do admin: se
        # ficarem na URL, o ChangeList redireciona com ?e=1 e perde o filtro.
        get_limpo = request.GET.copy()
        valor_expurgo_dias = get_limpo.pop('expurgo_dias', None)
        valor_expurgo_tipo = get_limpo.pop('expurgo_tipo', None)
        request.GET = get_limpo
        dias_expurgo = _parse_dias_expurgo(
            valor_expurgo_dias[0] if valor_expurgo_dias else None
        )
        tipo_expurgo = (valor_expurgo_tipo or [None])[0] or None

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

        # Totais por tipo com o rotulo legivel e link que ja filtra a lista
        rotulos = dict(LogAcesso.TIPO_CHOICES)
        totais = [
            {
                'tipo': linha['tipo'],
                'rotulo': rotulos.get(linha['tipo'], linha['tipo']),
                'total': linha['total'],
                'url': _url_filtro(request, tipo__exact=linha['tipo']),
            }
            for linha in resumo['total_por_tipo']
        ]

        # Top IPs: cada IP vira link para a busca por aquele IP
        for chave in ('top_ips_chat', 'top_ips_empresas'):
            resumo[chave] = [
                {**ip, 'url': _url_filtro(request, q=ip['ip'])}
                for ip in resumo[chave]
            ]

        # Por dia ja alinhado com as colunas dos tipos presentes. A data vira
        # link com os lookups de dia que o ChangeList aceita (year/month/day).
        tipos_presentes = resumo['tipos_presentes']
        linhas_dia = [
            {
                'dia': dia,
                'url': _url_filtro(
                    request,
                    criado_em__year=dia.year,
                    criado_em__month=dia.month,
                    criado_em__day=dia.day,
                ),
                'celulas': [
                    resumo['por_dia'][dia].get(t, 0) for t in tipos_presentes
                ],
                'total': sum(resumo['por_dia'][dia].values()),
            }
            for dia in resumo['dias_ordenados']
        ]

        # Expurgo: o GET so conta (nada e' apagado aqui). Quem apaga e' o
        # POST em /admin/chat/logacesso/expurgar/ (superusuario).
        if tipo_expurgo not in dict(LogAcesso.TIPO_CHOICES):
            tipo_expurgo = None
        expurgo = contar_acessos(dias=dias_expurgo, tipo=tipo_expurgo)
        expurgo['tipos'] = LogAcesso.TIPO_CHOICES

        extra_context.update({
            'resumo': resumo,
            'totais': totais,
            'tipos_presentes': tipos_presentes,
            'linhas_dia': linhas_dia,
            'links_periodo': links_periodo,
            'dias_atual': dias,
            'comandos': self._comandos(dias),
            'expurgo': expurgo,
            'expurgo_min_dias': MIN_DIAS,
            # Estado da base .mmdb (build, tamanho, marca) para o bloco GeoIP
            'geoip_base': estado_base(),
            # Credito exigido pela licenca do GeoLite2 (coluna Localizacao).
            # Vai no bloco `footer` do template: o Django 6 nao tem mais
            # AdminSite.site_footer.
            'geoip_atribuicao': ATRIBUICAO,
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
