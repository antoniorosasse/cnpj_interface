# cnpj_interface/chat/repo_postgres.py
"""
Implementacao do repositorio do chat usando o ORM do Django (Postgres).

Estas funcoes espelham as de repo_mongo.py. O dispatcher (repo.py)
escolhe qual usar baseado na flag CHAT_USE_MONGO.

Retornos:
- buscar_conversa: dict-like (a instancia do modelo) ou None
- criar_conversa: str (id serializado) -- para uniformizar com o Mongo
- listar_conversas: list[dict] com chaves 'id', 'titulo', 'atualizado_em', 'total_mensagens'
- adicionar_mensagem: bool

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from .models import Conversa, Mensagem


def buscar_conversa(conversa_id, user):
    """Retorna a instancia da Conversa (ou None)."""
    try:
        return Conversa.objects.get(pk=conversa_id, usuario=user)
    except (Conversa.DoesNotExist, ValueError, TypeError):
        return None


def _buscar_conversa_interno(conversa_id):
    """Uso interno: busca a conversa SEM filtro de usuario."""
    try:
        return Conversa.objects.get(pk=conversa_id)
    except (Conversa.DoesNotExist, ValueError, TypeError):
        return None


def criar_conversa(user, titulo=''):
    """Cria uma conversa nova. Retorna o id (str)."""
    c = Conversa.objects.create(usuario=user, titulo=titulo or '')
    return str(c.pk)


def listar_conversas(user, limite=50):
    """Lista as conversas do usuario, mais recentes primeiro."""
    qs = Conversa.objects.filter(usuario=user).order_by('-atualizado_em')[:limite]
    return [
        {
            'id': str(c.pk),
            'titulo': c.titulo or '',
            'atualizado_em': c.atualizado_em,
            'total_mensagens': c.total_mensagens,
        }
        for c in qs
    ]


def listar_conversas_vazias(user):
    """Lista conversas sem nenhuma mensagem (housekeeping)."""
    qs = Conversa.objects.filter(usuario=user, mensagens__isnull=True)
    return [{'id': str(c.pk), 'titulo': c.titulo or ''} for c in qs]


def adicionar_mensagem(conversa_id, papel, conteudo, **extras):
    """Adiciona uma mensagem. Retorna True se conseguiu."""
    conversa = _buscar_conversa_interno(conversa_id)
    if conversa is None:
        return False

    campos = {'conversa': conversa, 'papel': papel, 'conteudo': conteudo or ''}
    for chave in ('sql', 'total_linhas', 'tempo_ms'):
        if chave in extras and extras[chave] is not None:
            campos[chave] = extras[chave]

    Mensagem.objects.create(**campos)
    conversa.save(update_fields=['atualizado_em'])
    return True


def atualizar_titulo(conversa_id, titulo):
    """Atualiza o titulo. Retorna True se conseguiu."""
    conversa = _buscar_conversa_interno(conversa_id)
    if conversa is None:
        return False
    conversa.titulo = titulo or ''
    conversa.save(update_fields=['titulo', 'atualizado_em'])
    return True


def deletar_conversa(conversa_id, user):
    """Deleta a conversa. Retorna True se conseguiu."""
    conversa = buscar_conversa(conversa_id, user)
    if conversa is None:
        return False
    conversa.delete()
    return True


def tem_mensagens(conversa_id):
    """Retorna True se a conversa tem pelo menos 1 mensagem."""
    try:
        return Mensagem.objects.filter(conversa_id=conversa_id).exists()
    except (ValueError, TypeError):
        return False


def ultimas_mensagens(conversa_id, limite=20):
    """Retorna as ultimas N mensagens, em ordem cronologica."""
    try:
        qs = Mensagem.objects.filter(conversa_id=conversa_id).order_by('-criado_em')[:limite]
        return list(reversed(list(qs)))
    except (ValueError, TypeError):
        return []


def mensagens_da_conversa(conversa_id):
    """Retorna todas as mensagens, em ordem cronologica (pra serializar pro JS)."""
    try:
        return list(Mensagem.objects.filter(conversa_id=conversa_id).order_by('criado_em'))
    except (ValueError, TypeError):
        return []
