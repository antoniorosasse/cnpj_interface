from django.contrib import admin

from .models import Cliente, Exportacao, LinhaExportada, MovimentoCredito, PesquisaAnonima


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
    list_display = ('criado_em', 'origem', 'formato', 'cliente', 'visitante_nome',
                    'n_linhas', 'creditos', 'amostra', 'status')
    list_filter = ('origem', 'formato', 'status', 'amostra')
    search_fields = ('cliente__nome', 'visitante')
    readonly_fields = ('criado_em', 'baixada_em', 'expira_em', 'sql', 'filtros')

    @admin.display(description='visitante')
    def visitante_nome(self, obj):
        return obj.visitante[:8] if obj.visitante else '-'


@admin.register(PesquisaAnonima)
class PesquisaAnonimaAdmin(admin.ModelAdmin):
    list_display = ('dia', 'visitante', 'ip', 'total')
    list_filter = ('dia',)


@admin.register(LinhaExportada)
class LinhaExportadaAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'cliente', 'cnpj')
    search_fields = ('cnpj', 'cliente__nome')
