from django.contrib import admin

from .models import LeadContato


@admin.register(LeadContato)
class LeadContatoAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'nome', 'email', 'empresa', 'telefone')
    list_filter = ('criado_em',)
    search_fields = ('nome', 'email', 'empresa', 'mensagem')
    date_hierarchy = 'criado_em'
    readonly_fields = ('criado_em', 'ip', 'origem')

    def has_add_permission(self, request):
        return False
