# cnpj_interface/chat/repo_mongo.py
"""
Implementacao do repositorio do chat usando MongoDB.

Estas funcoes espelham as de repo_postgres.py. O dispatcher (repo.py)
escolhe qual usar baseado na flag CHAT_USE_MONGO.

Retornos uniformizados:
- buscar_conversa: dict ou None
- criar_conversa: str (ObjectId serializado)
- listar_conversas: list[dict] com chaves 'id', 'titulo', 'atualizado_em', 'total_mensagens'
- adicionar_mensagem: bool

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from bson import ObjectId
from bson.errors import InvalidId

from . import mongo_models as mm


def buscar_conversa(conversa_id, user):
    """Retorna o documento da conversa (dict) ou None."""
    usuario_id = user.id if user else None
    if usuario_id is None:
        return None
    return mm.buscar_conversa(conversa_id, usuario_id=usuario_id)


def criar_conversa(user, titulo=''):
    """Cria uma conversa nova. Retorna o id (str)."""
    return mm.criar_conversa(usuario_id=user.id, titulo=titulo or '')


def listar_conversas(user, limite=50):
    """Lista as conversas do usuario, mais recentes primeiro."""
    docs = mm.listar_conversas(usuario_id=user.id, limite=limite)
    return [
        {
            'id': str(d['_id']),
            'titulo': d.get('titulo', ''),
            'atualizado_em': d.get('atualizado_em'),
            'total_mensagens': d.get('total_mensagens', 0),
        }
        for d in docs
    ]


def listar_conversas_vazias(user):
    """Lista conversas sem nenhuma mensagem (housekeeping)."""
    docs = mm.listar_conversas_vazias(usuario_id=user.id)
    return [{'id': str(d['_id']), 'titulo': d.get('titulo', '')} for d in docs]


def adicionar_mensagem(conversa_id, papel, conteudo, **extras):
    """Adiciona uma mensagem. Retorna True se conseguiu."""
    return mm.adicionar_mensagem(conversa_id, papel, conteudo, **extras)


def atualizar_titulo(conversa_id, titulo):
    """Atualiza o titulo. Retorna True se conseguiu."""
    return mm.atualizar_titulo(conversa_id, titulo)


def deletar_conversa(conversa_id, user):
    """Deleta a conversa. Retorna True se conseguiu."""
    return mm.deletar_conversa(conversa_id, usuario_id=user.id)


def tem_mensagens(conversa_id):
    """Retorna True se a conversa tem pelo menos 1 mensagem."""
    return mm.tem_mensagens(conversa_id)


def ultimas_mensagens(conversa_id, limite=20):
    """Retorna as ultimas N mensagens (lista de dicts), em ordem cronologica."""
    return mm.ultimas_mensagens(conversa_id, limite=limite)


def mensagens_da_conversa(conversa_id):
    """
    Retorna todas as mensagens da conversa, em ordem cronologica.

    Usa acesso direto a collection (sem filtro de usuario) porque
    esta funcao e chamada por views ja autenticadas.
    """
    try:
        oid = ObjectId(str(conversa_id))
    except (InvalidId, TypeError):
        return []

    doc = mm._get_collection().find_one({'_id': oid})
    if not doc:
        return []
    return doc.get('mensagens', [])
