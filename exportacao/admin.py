"""Admin dos contadores: parametros globais, override por usuario e painel."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.safestring import mark_safe
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.urls import path
from django.utils import timezone

from .models import (Cliente, ConfiguracaoUso, Exportacao, LimiteUsuario,
                     LinhaExportada, MovimentoCredito, PesquisaAnonima)


@admin.register(ConfiguracaoUso)
class ConfiguracaoUsoAdmin(admin.ModelAdmin):
    """Linha unica: o changelist cai direto no formulario de edicao."""

    fieldsets = (
        ('', {'fields': ('ativo_geral',),
              'description': mark_safe(
                  'Contadores de uso do sistema. '
                  '<a class="button" href="contadores/">Abrir o painel de contagem</a> '
                  '&nbsp; <a class="button" href="/admin/exportacao/limiteusuario/">'
                  'Limites por usuário</a>')}),
        ('Quais contar (continuam contando mesmo com os limites desligados)', {
            'fields': ('lista_ativa', 'proximidade_ativa', 'chat_ativa', 'exportacao_ativa'),
        }),
        ('Limites por dia — visitante anonimo', {
            'fields': ('lista_dia_anonimo', 'proximidade_dia_anonimo',
                       'chat_dia_anonimo', 'exportacao_dia_anonimo'),
        }),
        ('Limites por dia — conta', {
            'fields': ('lista_dia_conta', 'proximidade_dia_conta',
                       'chat_dia_conta', 'exportacao_dia_conta'),
        }),
        ('Desafio antiabuso (Cloudflare Turnstile)', {
            'fields': ('desafio_ativo', 'desafio_validade_min', 'desafio_tickets_ip_hora'),
            'description': ('Só funciona com TURNSTILE_SITE_KEY e TURNSTILE_SECRET_KEY '
                            'no .env. Contas logadas nunca passam pelo desafio.'),
        }),
        ('Conteúdo do chat', {
            'fields': ('chat_dicas_titulo', 'chat_dicas_texto'),
            'description': 'Texto exibido no bloco “Dicas” da página do chat.',
        }),
        ('Teto por IP (todas as identidades do mesmo IP, por dia)', {
            'fields': ('ip_dia_lista', 'ip_dia_proximidade', 'ip_dia_chat',
                       'ip_dia_exportacao'),
        }),
    )

    def has_add_permission(self, request):
        return not ConfiguracaoUso.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj, _ = ConfiguracaoUso.objects.get_or_create(pk=1)
        return redirect('admin:exportacao_configuracaouso_change', obj.pk)

    def get_urls(self):
        return [
            path('contadores/', self.admin_site.admin_view(self.painel_contadores),
                 name='exportacao_contadores'),
        ] + super().get_urls()

    def painel_contadores(self, request):
        hoje = timezone.localdate()
        contagens = {linha['tipo']: linha['total']
                     for linha in PesquisaAnonima.objects.filter(dia=hoje)
                     .values('tipo').annotate(total=Sum('total'))}
        rotulos = dict(PesquisaAnonima.TIPOS)
        por_tipo = [{'tipo': rotulos.get(tipo, tipo), 'total': contagens.get(tipo, 0)}
                    for tipo in rotulos]
        por_tipo.sort(key=lambda linha: linha['total'], reverse=True)
        usuarios = list(PesquisaAnonima.objects
                        .filter(dia=hoje, visitante__startswith='user:')
                        .values('visitante', 'tipo')
                        .annotate(total=Sum('total'))
                        .order_by('-total')[:20])
        contexto = {
            **self.admin_site.each_context(request),
            'titulo': 'Contadores de uso',
            'hoje': hoje,
            'por_tipo': por_tipo,
            'usuarios': usuarios,
            'anexos': (PesquisaAnonima.objects.filter(dia=hoje)
                       .exclude(visitante__startswith='user:')
                       .aggregate(total=Sum('total'))['total'] or 0),
            'config': ConfiguracaoUso.objects.filter(pk=1).first(),
            'limites': LimiteUsuario.objects.select_related('user').all(),
            'title': 'Contadores de uso',
        }
        return render(request, 'admin/contadores.html', contexto)


@admin.register(LimiteUsuario)
class LimiteUsuarioAdmin(admin.ModelAdmin):
    list_display = ('user', 'ativo', 'lista_dia', 'chat_dia', 'exportacao_dia',
                    'ignorar_limites')
    search_fields = ('user__username', 'user__email')
    list_filter = ('ativo', 'ignorar_limites')
    autocomplete_fields = ('user',)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nome', 'user', 'saldo_creditos', 'criado_em')
    search_fields = ('nome', 'user__username', 'user__email')
    readonly_fields = ('criado_em',)


@admin.register(MovimentoCredito)
class MovimentoCreditoAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'cliente', 'tipo', 'quantidade', 'referencia')
    list_filter = ('tipo',)
    search_fields = ('cliente__nome', 'referencia')
    readonly_fields = ('criado_em',)

    def has_add_permission(self, request):
        return False


@admin.register(Exportacao)
class ExportacaoAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'origem', 'formato', 'cliente', 'visitante',
                    'n_linhas', 'creditos', 'amostra', 'completo', 'status')
    list_filter = ('origem', 'formato', 'status', 'amostra', 'completo')
    search_fields = ('cliente__nome', 'visitante', 'usuario__username', 'token')
    readonly_fields = ('criado_em', 'baixada_em', 'expira_em', 'sql', 'filtros',
                       'token', 'arquivo_nome', 'erro', 'iniciado_em',
                       'concluido_em')


@admin.register(PesquisaAnonima)
class PesquisaAnonimaAdmin(admin.ModelAdmin):
    list_display = ('atualizado_em', 'dia', 'tipo', 'visitante', 'ip', 'total',
                    'user_agent_curto')
    list_filter = ('dia', 'tipo')
    search_fields = ('visitante', 'ip', 'user_agent')

    @admin.display(description='user agent')
    def user_agent_curto(self, obj):
        return (obj.user_agent or '')[:60]


@admin.register(LinhaExportada)
class LinhaExportadaAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'cliente', 'cnpj')
    search_fields = ('cnpj', 'cliente__nome')


# ============================================================
# Limites dentro da pagina do proprio usuario
# ============================================================
class LimiteUsuarioInline(admin.StackedInline):
    model = LimiteUsuario
    fk_name = 'user'
    extra = 0
    max_num = 1
    can_delete = True
    verbose_name = 'limites de uso (contadores)'
    verbose_name_plural = 'limites de uso (contadores)'
    fields = ('ativo', 'lista_dia', 'chat_dia', 'exportacao_dia',
              'ignorar_limites', 'atualizado_em')
    readonly_fields = ('atualizado_em',)


if admin.site.is_registered(User):
    admin.site.unregister(User)


@admin.register(User)
class UserComLimitesAdmin(UserAdmin):
    """User padrao + bloco de limites na mesma tela."""

    inlines = (LimiteUsuarioInline,)
