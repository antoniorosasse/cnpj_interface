# cnpj_interface/chat/mongo_models.py
"""
Acesso ao MongoDB para o chat IA.

Substitui o ORM do Django (chat/models.py) para a persistencia
das conversas e mensagens.

Collection: chat_conversas (uma conversa = um documento)

Estrutura de um documento:

    {
      "_id": ObjectId,
      "usuario_id": int,
      "titulo": str,
      "criado_em": datetime,
      "atualizado_em": datetime,
      "mensagens": [
        {
          "papel": "user" | "assistant",
          "conteudo": str,
          "criado_em": datetime,

          # campos opcionais (so no assistant):
          "sql": str,
          "colunas": [str],
          "rows": [dict],
          "total_linhas": int,
          "truncado": bool,
          "tempo_ms": int,
          "resposta_texto": str,
          "erro": str | None
        }
      ]
    }

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.collection import Collection

from core.conexoes import ConexaoMongoDB


# Nome da collection (constante, pra nao repetir string)
COLECAO = 'chat_conversas'


# ============================================================
# CONEXAO
# ============================================================

def _get_collection() -> Collection:
    """Retorna a collection chat_conversas (singleton via ConexaoMongoDB)."""
    import sys
    mongo = ConexaoMongoDB()
    db = mongo.get_database()
    
    # Se estiver rodando testes, usa uma collection separada
    # para não apagar as conversas reais de desenvolvimento.
    if 'test' in sys.argv or 'pytest' in sys.modules:
        return db[COLECAO + '_test']
    
    return db[COLECAO]


# ============================================================
# HELPERS
# ============================================================

def _to_object_id(valor) -> Optional[ObjectId]:
    """Converte string/bytes/ObjectId para ObjectId. Retorna None se invalido."""
    if valor is None:
        return None
    if isinstance(valor, ObjectId):
        return valor
    if isinstance(valor, bytes):
        valor = valor.decode('utf-8', errors='ignore')
    try:
        return ObjectId(str(valor))
    except (InvalidId, TypeError):
        return None


def _serializar_valor(v):
    """
    Converte valores do Postgres (psycopg2) para tipos que o pymongo aceita.

    - Decimal       -> float
    - date (puro)   -> datetime (meia-noite)
    - outros        -> inalterados
    """
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date) and not isinstance(v, datetime):
        return datetime.combine(v, datetime.min.time())
    return v


def _serializar_rows(rows):
    """Serializa uma lista de dicts (rows de query) para o Mongo."""
    if not rows:
        return []
    return [
        {k: _serializar_valor(v) for k, v in row.items()}
        for row in rows
    ]


def _agora() -> datetime:
    """Retorna o datetime atual (aware, UTC)."""
    return datetime.now(timezone.utc)


# ============================================================
# FUNCOES PUBLICAS
# ============================================================

def criar_conversa(usuario_id: int, titulo: str = '') -> str:
    """
    Cria uma conversa vazia.

    Retorna o _id como string (para guardar na sessao do Django).
    """
    agora = _agora()
    doc = {
        'usuario_id': int(usuario_id),
        'titulo': titulo or '',
        'criado_em': agora,
        'atualizado_em': agora,
        'mensagens': [],
    }
    resultado = _get_collection().insert_one(doc)
    return str(resultado.inserted_id)


def buscar_conversa(conversa_id, usuario_id: int) -> Optional[dict]:
    """
    Busca uma conversa por _id, garantindo que pertence ao usuario.

    Retorna o documento (dict) ou None se nao existir / nao for do usuario.
    """
    oid = _to_object_id(conversa_id)
    if oid is None:
        return None

    return _get_collection().find_one({
        '_id': oid,
        'usuario_id': int(usuario_id),
    })


def listar_conversas(usuario_id: int, limite: int = 50) -> list[dict]:
    """
    Lista as conversas de um usuario, mais recentes primeiro.

    Retorna lista de documentos (sem o campo 'mensagens' completo -
    so o total, pra sidebar ficar leve).
    """
    cursor = _get_collection().aggregate([
        {'$match': {'usuario_id': int(usuario_id)}},
        {'$sort': {'atualizado_em': -1}},
        {'$limit': limite},
        {'$project': {
            'usuario_id': 1,
            'titulo': 1,
            'criado_em': 1,
            'atualizado_em': 1,
            'total_mensagens': {'$size': '$mensagens'},
        }},
    ])
    return list(cursor)


def adicionar_mensagem(
    conversa_id,
    papel: str,
    conteudo: str,
    **extras,
) -> bool:
    """
    Adiciona uma mensagem ao array 'mensagens' da conversa.

    extras: campos opcionais (sql, colunas, rows, total_linhas,
            truncado, tempo_ms, resposta_texto, erro).

    Retorna True se conseguiu, False se a conversa nao existe.
    """
    oid = _to_object_id(conversa_id)
    if oid is None:
        return False

    if papel not in ('user', 'assistant'):
        raise ValueError(f"papel invalido: {papel!r} (use 'user' ou 'assistant')")

    agora = _agora()
    mensagem = {
        'papel': papel,
        'conteudo': conteudo or '',
        'criado_em': agora,
    }

    # Campos opcionais (so adiciona se vierem)
    for campo in ('sql', 'colunas', 'rows', 'total_linhas',
                  'truncado', 'tempo_ms', 'resposta_texto', 'erro'):
        if campo in extras and extras[campo] is not None:
            valor = extras[campo]
            if campo == 'rows':
                valor = _serializar_rows(valor)
            mensagem[campo] = valor

    resultado = _get_collection().update_one(
        {'_id': oid},
        {
            '$push': {'mensagens': mensagem},
            '$set': {'atualizado_em': agora},
        },
    )
    return resultado.modified_count > 0


def atualizar_titulo(conversa_id, titulo: str) -> bool:
    """Atualiza o titulo de uma conversa. Retorna True se conseguiu."""
    oid = _to_object_id(conversa_id)
    if oid is None:
        return False

    resultado = _get_collection().update_one(
        {'_id': oid},
        {'$set': {'titulo': titulo or '', 'atualizado_em': _agora()}},
    )
    return resultado.modified_count > 0


def deletar_conversa(conversa_id, usuario_id: int) -> bool:
    """Deleta uma conversa (do usuario). Retorna True se conseguiu."""
    oid = _to_object_id(conversa_id)
    if oid is None:
        return False

    resultado = _get_collection().delete_one({
        '_id': oid,
        'usuario_id': int(usuario_id),
    })
    return resultado.deleted_count > 0


def tem_mensagens(conversa_id) -> bool:
    """Retorna True se a conversa tem pelo menos 1 mensagem."""
    oid = _to_object_id(conversa_id)
    if oid is None:
        return False

    doc = _get_collection().find_one(
        {'_id': oid},
        {'mensagens': {'$slice': 1}},
    )
    if not doc:
        return False
    return bool(doc.get('mensagens'))


def ultimas_mensagens(conversa_id, limite: int = 20) -> list[dict]:
    """
    Retorna as ultimas N mensagens da conversa, em ordem cronologica.

    Usado para montar o historico do LLM.
    """
    oid = _to_object_id(conversa_id)
    if oid is None:
        return []

    doc = _get_collection().find_one(
        {'_id': oid},
        {'mensagens': {'$slice': -limite}},
    )
    if not doc:
        return []
    return doc.get('mensagens', [])


def listar_conversas_vazias(usuario_id: int) -> list[dict]:
    """
    Lista conversas sem nenhuma mensagem (housekeeping).

    Usado pela view para limpar conversas vazias antigas.
    """
    cursor = _get_collection().find(
        {
            'usuario_id': int(usuario_id),
            'mensagens': {'$size': 0},
        },
        {'_id': 1, 'titulo': 1, 'atualizado_em': 1},
    )
    return list(cursor)


def listar_conversas_antigas_todos(dias: int = 30) -> list[dict]:
    """
    Lista conversas mais antigas que N dias, de TODOS os usuarios.

    Usado pelo comando de housekeeping (limpar_conversas_antigas).
    Diferente de listar_conversas_antigas, que exige usuario_id.

    Retorna lista de dicts com 'id', 'titulo', 'usuario_id', 'atualizado_em'.
    """
    from datetime import timedelta

    limite = _agora() - timedelta(days=dias)
    cursor = _get_collection().find(
        {'atualizado_em': {'$lt': limite}},
        {'_id': 1, 'titulo': 1, 'usuario_id': 1, 'atualizado_em': 1},
    ).sort('atualizado_em', 1)

    return [
        {
            'id': str(d['_id']),
            'titulo': d.get('titulo', ''),
            'usuario_id': d.get('usuario_id'),
            'atualizado_em': d.get('atualizado_em'),
        }
        for d in cursor
    ]


def limpar_conversas_antigas(usuario_id: int, dias: int = 30) -> int:
    """
    Remove conversas do usuario mais antigas que N dias.

    Retorna o numero de conversas removidas.
    """
    from datetime import timedelta
    limite = _agora() - timedelta(days=dias)
    resultado = _get_collection().delete_many({
        'usuario_id': int(usuario_id),
        'atualizado_em': {'$lt': limite},
    })
    return resultado.deleted_count
