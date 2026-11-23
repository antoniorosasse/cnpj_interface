"""
Exportacao de dados, creditos e limite diario do visitante anonimo.

Sem acentos no codigo.

App fora de 'core' de proposito: o CNPJRouter manda model de 'core' para o
bancocnpj (read-only). Aqui tudo grava no django_internal.
"""

from django.conf import settings
from django.db import models


class Cliente(models.Model):
    """Cliente pagante (1:1 com o usuario do admin, por enquanto)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                related_name='cliente')
    nome = models.CharField('nome', max_length=160)
    saldo_creditos = models.IntegerField('saldo de creditos', default=0)
    criado_em = models.DateTimeField('criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'cliente'
        verbose_name_plural = 'clientes'
        ordering = ('nome',)
        constraints = [
            models.CheckConstraint(condition=models.Q(saldo_creditos__gte=0),
                                   name='cliente_saldo_nao_negativo'),
        ]

    def __str__(self):
        return f'{self.nome} ({self.saldo_creditos} creditos)'


class MovimentoCredito(models.Model):
    """Livro-caixa: toda entrada e saida de credito passa por aqui."""

    TIPOS = (
        ('compra', 'Compra'),
        ('consumo', 'Consumo'),
        ('ajuste', 'Ajuste'),
        ('estorno', 'Estorno'),
    )

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='movimentos')
    tipo = models.CharField('tipo', max_length=10, choices=TIPOS)
    quantidade = models.IntegerField('quantidade')  # positivo entra, negativo sai
    referencia = models.CharField('referencia', max_length=120, blank=True)
    idempotency_key = models.CharField('chave de idempotencia', max_length=80,
                                       unique=True, null=True, blank=True)
    criado_em = models.DateTimeField('criado em', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'movimento de credito'
        verbose_name_plural = 'movimentos de credito'
        ordering = ('-criado_em',)


class Exportacao(models.Model):
    """Pedido de exportacao. O download e' registrado para auditoria."""

    ORIGENS = (('lista', 'Lista'), ('chat', 'Chat'), ('proximidade', 'Proximidade'))
    FORMATOS = (('csv', 'CSV'), ('json', 'JSON'))
    STATUS = (('pendente', 'Pendente'), ('baixada', 'Baixada'),
              ('falhou', 'Falhou'), ('expirada', 'Expirada'))

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, null=True, blank=True,
                                related_name='exportacoes')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='exportacoes')
    visitante = models.CharField('visitante', max_length=64, blank=True)
    origem = models.CharField('origem', max_length=12, choices=ORIGENS)
    formato = models.CharField('formato', max_length=4, choices=FORMATOS)
    filtros = models.JSONField('filtros', default=dict, blank=True)
    sql = models.TextField('sql', blank=True)
    n_linhas = models.IntegerField('linhas', default=0)
    creditos = models.IntegerField('creditos debitados', default=0)
    amostra = models.BooleanField('amostra', default=False)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    status = models.CharField('status', max_length=10, choices=STATUS, default='pendente')
    criado_em = models.DateTimeField('criado em', auto_now_add=True, db_index=True)
    expira_em = models.DateTimeField('expira em', null=True, blank=True)
    baixada_em = models.DateTimeField('baixada em', null=True, blank=True)

    class Meta:
        verbose_name = 'exportacao'
        verbose_name_plural = 'exportacoes'
        ordering = ('-criado_em',)


class LinhaExportada(models.Model):
    """CNPJ ja exportado por um cliente — base da deduplicacao de 30 dias."""

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='linhas_exportadas')
    cnpj = models.CharField('cnpj', max_length=14, db_index=True)
    criado_em = models.DateTimeField('criado em', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'linha exportada'
        verbose_name_plural = 'linhas exportadas'
        constraints = [
            models.UniqueConstraint(fields=['cliente', 'cnpj'],
                                    name='linha_exportada_unica_por_cliente'),
        ]


class PesquisaAnonima(models.Model):
    """Contador diario do visitante anonimo (cookie) e do IP."""

    visitante = models.CharField('visitante', max_length=64, db_index=True)
    dia = models.DateField('dia', db_index=True)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    total = models.IntegerField('total', default=0)

    class Meta:
        verbose_name = 'pesquisa anonima'
        verbose_name_plural = 'pesquisas anonimas'
        unique_together = (('visitante', 'dia'),)
        indexes = [models.Index(fields=['dia', 'ip'], name='pesquisa_dia_ip_idx')]
