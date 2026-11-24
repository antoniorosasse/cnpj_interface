"""Admin dos contadores: parametros globais, override por usuario e painel."""

from django.contrib import admin
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
              'description': ('Contadores de uso do sistema. '
                              '<a href="contadores/">Abrir o painel de contagem</a>')}),
        ('Quais contar', {'fields': ('lista_ativa', 'chat_ativa', 'exportacao_ativa')}),
        ('Limites por dia — visitante anonimo', {
            'fields': ('lista_dia_anonimo', 'chat_dia_anonimo', 'exportacao_dia_anonimo'),
        }),
        ('Limites por dia — conta', {
            'fields': ('lista_dia_conta', 'chat_dia_conta', 'exportacao_dia_conta'),
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
        por_tipo = (PesquisaAnonima.objects
                    .filter(dia=hoje)
                    .values('tipo')
                    .annotate(total=Sum('total'))
                    .order_by('-total'))
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
                    'n_linhas', 'creditos', 'amostra', 'status')
    list_filter = ('origem', 'formato', 'status', 'amostra')
    search_fields = ('cliente__nome', 'visitante', 'usuario__username')
    readonly_fields = ('criado_em', 'baixada_em', 'expira_em', 'sql', 'filtros')


@admin.register(PesquisaAnonima)
class PesquisaAnonimaAdmin(admin.ModelAdmin):
    list_display = ('dia', 'tipo', 'visitante', 'ip', 'total')
    list_filter = ('dia', 'tipo')


@admin.register(LinhaExportada)
class LinhaExportadaAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'cliente', 'cnpj')
    search_fields = ('cnpj', 'cliente__nome')
