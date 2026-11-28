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
    STATUS = (('pendente', 'Pendente'), ('processando', 'Processando'),
              ('pronto', 'Pronto'), ('baixada', 'Baixada'),
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
    status = models.CharField('status', max_length=12, choices=STATUS, default='pendente')
    criado_em = models.DateTimeField('criado em', auto_now_add=True, db_index=True)
    expira_em = models.DateTimeField('expira em', null=True, blank=True)
    baixada_em = models.DateTimeField('baixada em', null=True, blank=True)
    # Exportacao completa/assincrona (tabela unica; as antigas ficam null)
    completo = models.BooleanField('exportacao completa', default=False)
    token = models.CharField('token', max_length=64, unique=True, null=True,
                             blank=True)
    arquivo_nome = models.CharField('nome do arquivo', max_length=255,
                                  blank=True, default='')
    erro = models.TextField('erro', blank=True, default='')
    iniciado_em = models.DateTimeField('iniciado em', null=True, blank=True)
    concluido_em = models.DateTimeField('concluido em', null=True, blank=True)

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


class ConfiguracaoUso(models.Model):
    """Parametros globais dos contadores (linha unica, pk=1)."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    ativo_geral = models.BooleanField(
        'aplicar limites (bloquear)', default=True,
        help_text='Desligado: continua contando, mas ninguem e bloqueado.')

    lista_ativa = models.BooleanField('contar consultas de empresas', default=True)
    proximidade_ativa = models.BooleanField('contar consultas de proximidade', default=True)
    chat_ativa = models.BooleanField('contar consultas do chat', default=True)
    exportacao_ativa = models.BooleanField('contar geracao de arquivos', default=True)

    lista_dia_anonimo = models.PositiveIntegerField('empresas/dia (anonimo)', default=5)
    lista_dia_conta = models.PositiveIntegerField('empresas/dia (conta)', default=10)
    proximidade_dia_anonimo = models.PositiveIntegerField('proximidade/dia (anonimo)', default=5)
    proximidade_dia_conta = models.PositiveIntegerField('proximidade/dia (conta)', default=10)
    chat_dia_anonimo = models.PositiveIntegerField('chat/dia (anonimo)', default=3)
    chat_dia_conta = models.PositiveIntegerField('chat/dia (conta)', default=20)
    exportacao_dia_anonimo = models.PositiveIntegerField('exportacao/dia (anonimo)', default=5)
    exportacao_dia_conta = models.PositiveIntegerField('exportacao/dia (conta)', default=50)

    desafio_ativo = models.BooleanField(
        'exigir desafio (Turnstile) no export anonimo', default=False,
        help_text='So tem efeito com TURNSTILE_SITE_KEY e TURNSTILE_SECRET_KEY no .env.')
    desafio_validade_min = models.PositiveIntegerField('validade do ticket (min)', default=15)
    desafio_tickets_ip_hora = models.PositiveIntegerField('tickets por IP por hora', default=20)

    chat_dicas_titulo = models.CharField(
        'titulo das dicas do chat', max_length=80, default='Dicas')
    chat_dicas_texto = models.TextField(
        'texto das dicas do chat', blank=True,
        default=('Diga o ramo de atividade, a cidade/UF e o que quer ver '
                 '(data de abertura, capital, situação).\n'
                 'Seja específico sobre "matriz" (só a sede) ou '
                 '"estabelecimentos" (sede + filiais).\n'
                 'Você pode pedir ordenação: "por capital", "mais antigas", '
                 '"maiores".\n'
                 'A lista mostra 100 linhas; use "Exportar tudo" para gerar o '
                 'arquivo completo.'))

    ip_dia_lista = models.PositiveIntegerField('empresas/dia por IP', default=300)
    ip_dia_proximidade = models.PositiveIntegerField('proximidade/dia por IP', default=300)
    ip_dia_chat = models.PositiveIntegerField('chat/dia por IP', default=60)
    ip_dia_exportacao = models.PositiveIntegerField('arquivos/dia por IP', default=30)

    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'parametros dos contadores'
        verbose_name_plural = 'parametros dos contadores'

    def save(self, *args, **kwargs):
        self.id = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return 'Parametros dos contadores'


class LimiteUsuario(models.Model):
    """Sobrescreve os parametros globais para um usuario (tudo opcional)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='limite_uso')
    ativo = models.BooleanField('contadores para este usuario', null=True, blank=True,
                                help_text='Em branco = herda o global.')
    lista_dia = models.PositiveIntegerField('lista/dia', null=True, blank=True)
    chat_dia = models.PositiveIntegerField('chat/dia', null=True, blank=True)
    exportacao_dia = models.PositiveIntegerField('exportacao/dia', null=True, blank=True)
    ignorar_limites = models.BooleanField('sempre ignora os limites', default=False,
                                          help_text='Continua contando, mas nunca bloqueia.')
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'limite por usuario'
        verbose_name_plural = 'limites por usuario'
        ordering = ('user__username',)

    def __str__(self):
        return f'Limites de {self.user}'

    def salvar_contagem(self):
        return self.lista_dia, self.chat_dia, self.exportacao_dia


class PesquisaAnonima(models.Model):
    """Contador diario por identidade (cookie, usuario) e tipo de uso."""

    TIPOS = (('lista', 'Empresas'), ('proximidade', 'Proximidade'),
             ('chat', 'Chat'), ('exportacao', 'Arquivos'))

    visitante = models.CharField('visitante', max_length=64, db_index=True)
    tipo = models.CharField('tipo', max_length=12, choices=TIPOS, default='lista')
    dia = models.DateField('dia', db_index=True)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    user_agent = models.CharField('user agent', max_length=200, blank=True)
    total = models.IntegerField('total', default=0)
    atualizado_em = models.DateTimeField('ultimo uso', auto_now=True, db_index=True)

    class Meta:
        verbose_name = 'pesquisa anonima'
        verbose_name_plural = 'pesquisas anonimas'
        unique_together = (('visitante', 'dia', 'tipo'),)
        indexes = [
            models.Index(fields=['dia', 'ip'], name='pesquisa_dia_ip_idx'),
            models.Index(fields=['dia', 'tipo'], name='pesquisa_dia_tipo_idx'),
        ]
