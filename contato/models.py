"""
Lead do formulario de contato da pagina do produto.

Sem acentos no codigo. Este app NAO usa o router do bancocnpj: como o
app_label nao e' 'core', o CNPJRouter manda leitura e escrita para o banco
'django_internal' (o mesmo do admin e do chat). O bancocnpj continua read-only.
"""

from django.db import models


class LeadContato(models.Model):
    """Uma mensagem recebida pelo formulario de contato."""

    nome = models.CharField('nome', max_length=120)
    email = models.EmailField('e-mail')
    empresa = models.CharField('empresa', max_length=120, blank=True)
    telefone = models.CharField('telefone', max_length=40, blank=True)
    mensagem = models.TextField('mensagem', max_length=4000)
    origem = models.CharField('origem', max_length=200, blank=True)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    criado_em = models.DateTimeField('recebido em', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'mensagem de contato'
        verbose_name_plural = 'mensagens de contato'
        ordering = ('-criado_em',)

    def __str__(self):
        return f'{self.nome} <{self.email}> em {self.criado_em:%d/%m/%Y %H:%M}'
