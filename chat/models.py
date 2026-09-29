# cnpj_interface/chat/models.py
"""
Modelos do chat IA.

- Conversa: uma sessao de chat (thread)
- Mensagem: cada turno (pergunta do usuario + resposta do assistente)

Ficam no banco 'default' (django_internal, Postgres).

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.conf import settings
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
    total_linhas = models.IntegerField(null=True, blank=True)
    tempo_ms = models.IntegerField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em']
        verbose_name = 'Mensagem'
        verbose_name_plural = 'Mensagens'

    def __str__(self):
        preview = self.conteudo[:50]
        return f'[{self.papel}] {preview}'