# cnpj_interface/chat/models.py
"""
Modelos do chat IA.

- Conversa: uma sessao de chat (thread)
- Mensagem: cada turno (pergunta do usuario + resposta do assistente)

Ficam no banco 'default' (django_internal, Postgres).

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class Conversa(models.Model):
    """Uma sessao de conversa (thread)."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversas',
    )
    titulo = models.CharField(max_length=200, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-atualizado_em']
        verbose_name = 'Conversa'
        verbose_name_plural = 'Conversas'

    def __str__(self):
        return self.titulo or f'Conversa #{self.pk}'

    @property
    def total_mensagens(self) -> int:
        return self.mensagens.count()  # type: ignore[attr-defined]


class Mensagem(models.Model):
    """Uma mensagem (turno) dentro de uma conversa."""

    PAPEL_USER = 'user'
    PAPEL_ASSISTANT = 'assistant'
    PAPEL_CHOICES = [
        (PAPEL_USER, 'Usuario'),
        (PAPEL_ASSISTANT, 'Assistente'),
    ]

    conversa = models.ForeignKey(
        Conversa,
        on_delete=models.CASCADE,
        related_name='mensagens',
    )
    papel = models.CharField(max_length=10, choices=PAPEL_CHOICES)
    conteudo = models.TextField()

    # Campos preenchidos apenas para mensagens do assistente
    sql = models.TextField(blank=True, default='')
    resposta_texto = models.TextField(blank=True, default='')
    # Resultado da consulta: persistido tambem no Postgres (antes so o Mongo
    # guardava; com F5 a tabela sumia do historico). `rows` usa DjangoJSONEncoder
    # para aceitar Decimal/date vindos do executor.
    colunas = models.JSONField(null=True, blank=True)
    rows = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    total_linhas = models.IntegerField(null=True, blank=True)
    truncado = models.BooleanField(null=True, blank=True)
    tempo_ms = models.IntegerField(null=True, blank=True)
    erro = models.TextField(blank=True, default='')

    # Plano da consulta de proximidade que gerou a resposta (JSON simples:
    # origem, raio, filtros). Fica na propria mensagem para o follow-up do
    # tipo "e deste endereco?" reusar a consulta anterior. Null = sem plano.
    plano = models.JSONField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em']
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'

    def __str__(self):
        preview = self.conteudo[:50]
        return f'[{self.papel}] {preview}'



class LogAcesso(models.Model):
    """
    Registro de eventos de acesso/uso do chat e da consulta de empresas.

    Tipos:
      - 'login':             alguem logou (signal user_logged_in)
      - 'chat_pergunta':     alguem enviou uma pergunta no chat
      - 'empresas_lista':    alguem abriu a lista de empresas (/)
      - 'empresas_detalhe':  alguem abriu o detalhe de uma empresa

    Os dois ultimos sao gravados a partir do app 'core' (core/log_acesso.py).
    A pagina de empresas e' publica: sem login, usuario fica nulo.

    Fica no banco 'default' (django_internal, Postgres), independente
    do backend de persistencia do chat (Postgres ORM ou MongoDB).
    """

    TIPO_LOGIN = 'login'
    TIPO_CHAT_PERGUNTA = 'chat_pergunta'
    TIPO_EMPRESAS_LISTA = 'empresas_lista'
    TIPO_EMPRESAS_DETALHE = 'empresas_detalhe'
    TIPO_CHOICES = [
        (TIPO_LOGIN, 'Login'),
        (TIPO_CHAT_PERGUNTA, 'Pergunta no chat'),
        (TIPO_EMPRESAS_LISTA, 'Lista de empresas'),
        (TIPO_EMPRESAS_DETALHE, 'Detalhe de empresa'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_acesso',
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    detalhe = models.TextField(blank=True, default='')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'Log de acesso'
        verbose_name_plural = 'Logs de acesso'
        indexes = [
            models.Index(fields=['tipo', '-criado_em']),
            models.Index(fields=['-criado_em']),
        ]

    def __str__(self):
        quem = self.usuario.username if self.usuario else '(anonimo)'
        return f'[{self.tipo}] {quem} @ {self.criado_em:%Y-%m-%d %H:%M}'


class LocalizacaoIP(models.Model):
    """
    Cidade/UF/pais de um IP do log de acesso (1 linha por IP).

    Preenchida por `manage.py localizar_acessos` (chat/geoip.py) - nunca no
    request. Fica em tabela propria para a mesma consulta externa nao ser
    repetida para cada LogAcesso: o admin faz um Subquery por esta tabela.
    """

    ip = models.GenericIPAddressField(unique=True)
    cidade = models.CharField(max_length=120, blank=True, default='')
    regiao = models.CharField(max_length=120, blank=True, default='')
    uf = models.CharField(max_length=40, blank=True, default='')
    pais = models.CharField(max_length=60, blank=True, default='')
    codigo_pais = models.CharField(max_length=2, blank=True, default='')
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
    )
    fonte = models.CharField(max_length=40, blank=True, default='')
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ip']
        verbose_name = 'Localização de IP'
        verbose_name_plural = 'Localizações de IP'

    def __str__(self):
        return f'{self.ip}: {self.resumo or "(sem localização)"}'

    @property
    def resumo(self) -> str:
        """Texto curto usado na coluna do admin: 'Sao Paulo/SP, Brasil'."""
        local = '/'.join(p for p in (self.cidade, self.uf) if p) or self.regiao
        partes = [p for p in (local, self.pais) if p]
        return ', '.join(partes)


class AtividadeLog(models.Model):
    """Uma resolucao de atividade -> CNAE (chat ou pagina de proximidade).

    Base da Fase 1 da KB autoaprendizavel: guarda o termo, a fonte que
    resolveu (KB/codigo/ILIKE/vetor/nenhuma), os CNAEs e quantos resultados
    a consulta devolveu. Nao altera o comportamento do chat.
    """

    ORIGEM_CHAT = 'chat_proximidade'
    ORIGEM_PAGINA = 'pagina_proximidade'
    ORIGENS = [
        (ORIGEM_CHAT, 'Chat/proximidade'),
        (ORIGEM_PAGINA, 'Pagina/proximidade'),
    ]

    FONTE_KB = 'kb'
    FONTE_CODIGO = 'codigo'
    FONTE_ILIKE = 'iliKe'
    FONTE_VETOR = 'vetor'
    FONTE_NENHUMA = 'nenhuma'
    FONTES = [
        (FONTE_KB, 'KB'),
        (FONTE_CODIGO, 'Codigo CNAE'),
        (FONTE_ILIKE, 'ILIKE'),
        (FONTE_VETOR, 'Vetor'),
        (FONTE_NENHUMA, 'Nenhuma'),
    ]

    termo = models.CharField('termo', max_length=200)
    termo_normalizado = models.CharField(
        'termo normalizado', max_length=200, db_index=True)
    origem = models.CharField('origem', max_length=24, choices=ORIGENS)
    fonte = models.CharField('fonte', max_length=12, choices=FONTES)
    categorias = models.JSONField('categorias', default=list, blank=True)
    cnaes = models.JSONField('cnaes', default=list, blank=True)
    faixas = models.JSONField('faixas', default=list, blank=True)
    resultados = models.IntegerField('resultados', null=True, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='atividades_log',
    )
    criado_em = models.DateTimeField('criado em', auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'log de atividade'
        verbose_name_plural = 'logs de atividade'
        indexes = [
            models.Index(fields=['termo_normalizado', '-criado_em']),
            models.Index(fields=['fonte', '-criado_em']),
        ]

    def __str__(self):
        return f'{self.termo} -> {self.cnaes or "sem CNAE"} ({self.fonte})'


class CnaeAliasCandidato(models.Model):
    """Candidato a alias de atividade na KB (Fase 1: nunca aplica sozinho)."""

    STATUS_PENDENTE = 'pendente'
    STATUS_APROVADO = 'aprovado'
    STATUS_REJEITADO = 'rejeitado'
    STATUS = [
        (STATUS_PENDENTE, 'Pendente'),
        (STATUS_APROVADO, 'Aprovado'),
        (STATUS_REJEITADO, 'Rejeitado'),
    ]

    termo = models.CharField('termo', max_length=200)
    termo_normalizado = models.CharField(
        'termo normalizado', max_length=200, unique=True)
    cnaes = models.JSONField('cnaes', default=list, blank=True)
    categorias = models.JSONField('categorias', default=list, blank=True)
    fontes = models.JSONField('fontes', default=dict, blank=True)
    ocorrencias = models.PositiveIntegerField('ocorrencias', default=0)
    resultados_medios = models.FloatField('resultados medios', null=True, blank=True)
    evidencia = models.JSONField('evidencia', default=dict, blank=True)
    status = models.CharField('status', max_length=10, choices=STATUS,
                              default=STATUS_PENDENTE, db_index=True)
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        ordering = ['-ocorrencias', 'termo_normalizado']
        verbose_name = 'candidato a alias CNAE'
        verbose_name_plural = 'candidatos a alias CNAE'

    def __str__(self):
        return f'{self.termo} -> {self.cnaes} ({self.status})'
