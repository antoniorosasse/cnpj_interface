# cnpj_interface/chat/repo.py
"""
Dispatcher do repositorio do chat.

Escolhe entre Postgres (Django ORM) e MongoDB baseado na flag
CHAT_USE_MONGO do .env:

    CHAT_USE_MONGO=0  -> usa repo_postgres (padrao)
    CHAT_USE_MONGO=1  -> usa repo_mongo

As funcoes tem a mesma assinatura nos dois backends. As views
chamam repo.<funcao>() e nao sabem qual backend esta ativo.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import os


USE_MONGO = os.getenv('CHAT_USE_MONGO', '0') == '1'


if USE_MONGO:
    from . import repo_mongo as _impl
else:
    from . import repo_postgres as _impl


# Reexporta todas as funcoes do backend escolhido
buscar_conversa = _impl.buscar_conversa
criar_conversa = _impl.criar_conversa
listar_conversas = _impl.listar_conversas
listar_conversas_vazias = _impl.listar_conversas_vazias
adicionar_mensagem = _impl.adicionar_mensagem
atualizar_titulo = _impl.atualizar_titulo
deletar_conversa = _impl.deletar_conversa
tem_mensagens = _impl.tem_mensagens
ultimas_mensagens = _impl.ultimas_mensagens
mensagens_da_conversa = _impl.mensagens_da_conversa
listar_conversas_antigas_todos = _impl.listar_conversas_antigas_todos
deletar_conversa_por_id = _impl.deletar_conversa_por_id


def backend_ativo() -> str:
    """Retorna 'mongo' ou 'postgres' (util pra debug)."""
    return 'mongo' if USE_MONGO else 'postgres'
