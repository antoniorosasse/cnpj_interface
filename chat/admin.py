# cnpj_interface/chat/admin.py
"""
Configuracao do admin do Django para o app chat.

LogAcesso: somente leitura (auditoria). Ninguem cria, edita ou apaga
pelo admin. Filtros por tipo/data, busca por IP, navegacao por data.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.contrib import admin

from .models import LogAcesso


@admin.register(LogAcesso)
class LogAcessoAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'tipo', 'usuario', 'ip', 'resumo_detalhe')
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

    # --- Bloqueia criacao / edicao / exclusao ---

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
