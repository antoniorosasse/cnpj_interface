# cnpj_interface/chat/views.py
"""
Views do chat IA.

- chat_view:           pagina HTML do chat
- chat_api_view:       endpoint AJAX do pipeline (pergunta -> SQL -> resultado)
- chat_historico_view: lista as conversas do usuario
- chat_conversa_view:  abre uma conversa especifica
- chat_nova_view:      comeca uma conversa nova
- chat_limpar_view:    (legado) limpa a sessao

Persistencia:
- Conversa / Mensagem ficam no banco 'default' (django_internal)
- A sessao Django guarda apenas o id da conversa atual

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST


from .services.sql_generator import gerar_sql
from .services.sql_executor import executar_sql
from .services.resposta_natural import gerar_texto_resposta
#from chat.models import Conversa, Mensagem
from . import repo


logger = logging.getLogger(__name__)


# Chave da sessao que guarda o id da conversa atual
SESSION_CONVERSA_ID = 'chat_conversa_id'

# Quantas mensagens (turnos) enviar como contexto pro LLM
MAX_CONTEXTO = 10


def _conversa_atual(request):
    """Retorna a Conversa atual do usuario (ou None)."""
    cid = request.session.get(SESSION_CONVERSA_ID)
    if not cid:
        return None

    conversa = repo.buscar_conversa(cid, user=request.user)
    if conversa is None:
        # Conversa foi apagada (ou pertence a outro user)
        request.session.pop(SESSION_CONVERSA_ID, None)
    return conversa


def _conversa_id(conversa):
    """Extrai o id da conversa (str), independente do backend."""
    if conversa is None:
        return None
    if isinstance(conversa, dict):
        # Mongo: dict com '_id'
        return str(conversa['_id'])
    # Postgres: instancia de Conversa com 'pk'
    return str(conversa.pk)


def _conversa_titulo(conversa):
    """Extrai o titulo da conversa, independente do backend."""
    if conversa is None:
        return ''
    if isinstance(conversa, dict):
        return conversa.get('titulo', '')
    return conversa.titulo


def _historico_para_llm(conversa, limite=MAX_CONTEXTO):
    """
    Le as ultimas N mensagens da conversa no formato do LLM:
        [{'role': 'user', 'content': '...'}, {'role': 'assistant', 'content': '...'}]
    """
    if conversa is None:
        return []

    cid = _conversa_id(conversa)
    if cid is None:
        return []

    # Pega as ultimas (limite) mensagens (ja em ordem cronologica)
    mensagens = repo.ultimas_mensagens(cid, limite=limite * 2)

    historico = []
    for m in mensagens:
        # 'm' pode ser instancia (Postgres) ou dict (Mongo)
        if isinstance(m, dict):
            papel = m.get('papel')
            conteudo = m.get('conteudo', '')
            sql = m.get('sql', '')
        else:
            papel = m.papel
            conteudo = m.conteudo
            sql = m.sql or ''

        if papel == 'user':
            historico.append({'role': 'user', 'content': conteudo})
        elif papel == 'assistant':
            # Assistente: o conteudo e' o SQL gerado
            historico.append({'role': 'assistant', 'content': sql or conteudo})
    return historico


@login_required
def chat_view(request):
    """Renderiza a pagina do chat (com a conversa atual ou nova)."""
    # Limpa conversas vazias antigas deste usuario (housekeeping leve)
    cid_atual = request.session.get(SESSION_CONVERSA_ID)
    for c in repo.listar_conversas_vazias(user=request.user):
        if c['id'] != cid_atual:
            repo.deletar_conversa(c['id'], user=request.user)

    conversa = _conversa_atual(request)

    # Se nao tem conversa, cria uma nova
    if conversa is None:
        novo_id = repo.criar_conversa(user=request.user)
        request.session[SESSION_CONVERSA_ID] = novo_id
        conversa = repo.buscar_conversa(novo_id, user=request.user)

    # Lista de todas as conversas do usuario (pra sidebar)
    conversas = repo.listar_conversas(user=request.user)

    # Mensagens da conversa atual, serializadas pro JS
    cid = _conversa_id(conversa)
    mensagens = repo.mensagens_da_conversa(cid) if cid else []

    def _serializar_msg(m):
        """Converte uma mensagem (dict do Mongo ou obj do Postgres) em dict."""
        if isinstance(m, dict):
            d = {
                'papel': m.get('papel', ''),
                'conteudo': m.get('conteudo', ''),
            }
            for chave in ('sql', 'resposta_texto', 'colunas', 'rows',
                          'total_linhas', 'truncado', 'tempo_ms', 'erro'):
                if chave in m and m[chave] is not None:
                    d[chave] = m[chave]
            return d
        else:
            # Instancia do model Postgres
            d = {
                'papel': m.papel,
                'conteudo': m.conteudo,
            }
            if m.sql:
                d['sql'] = m.sql
            if m.resposta_texto:
                d['resposta_texto'] = m.resposta_texto
            if m.total_linhas is not None:
                d['total_linhas'] = m.total_linhas
            if m.tempo_ms is not None:
                d['tempo_ms'] = m.tempo_ms
            return d

    mensagens_json = [_serializar_msg(m) for m in mensagens]

    return render(request, 'chat/chat.html', {
        'conversa': conversa,
        'conversa_id_atual': _conversa_id(conversa),
        'conversas': conversas,
        'mensagens_json': json.dumps(mensagens_json, default=str),
    })


@login_required
@require_POST
def chat_api_view(request):
    """
    Recebe {'pergunta': str}, gera SQL, executa e salva o par
    (Mensagem user + Mensagem assistant) na conversa atual.

    Se nao houver conversa na sessao, cria uma.
    """
    # --- parse do body ---
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse(
            {'ok': False, 'erro': 'JSON invalido no corpo da requisicao.'},
            status=400,
        )

    pergunta = (body.get('pergunta') or '').strip()
    if not pergunta:
        return JsonResponse(
            {'ok': False, 'erro': 'Pergunta vazia.'},
            status=400,
        )

    # --- conversa atual (ou cria) ---
    conversa = _conversa_atual(request)
    if conversa is None:
        novo_id = repo.criar_conversa(user=request.user)
        request.session[SESSION_CONVERSA_ID] = novo_id
        conversa = repo.buscar_conversa(novo_id, user=request.user)

    cid = _conversa_id(conversa)

    # Se for a primeira pergunta, define o titulo
    if not _conversa_titulo(conversa):
        repo.atualizar_titulo(cid, pergunta[:200])

    # --- historico para o LLM ---
    historico = _historico_para_llm(conversa)

    # --- 1) gera SQL ---
    geracao = gerar_sql(pergunta=pergunta, historico=historico)

    if not geracao['ok']:
        # Salva a pergunta do usuario mesmo assim (pra ele ver no historico)
        repo.adicionar_mensagem(cid, 'user', pergunta)
        return JsonResponse({
            'ok': False,
            'etapa': 'geracao',
            'sql': geracao.get('sql', ''),
            'erro': geracao.get('erro') or 'Nao foi possivel gerar SQL.',
            'tentativas': geracao.get('tentativas', 0),
        })

    validation = geracao['validation']

    # --- 2) executa SQL ---
    execucao = executar_sql(validation)

    # --- 2b) Se o Postgres rejeitou por coluna/tabela inexistente,
    # tenta 1x mais passando o erro pro LLM corrigir.
    if not execucao['ok'] and execucao.get('erro') and any(
        s in execucao['erro'].lower() for s in
        ('nao existe', 'não existe', 'does not exist', "doesn't exist")
    ):
        logger.warning('Retry por erro de SQL: %s', execucao['erro'])

        historico_corrigido = list(historico) + [
            {'role': 'assistant', 'content': validation.sql},
            {'role': 'user', 'content': (
                f'O SQL acima deu erro no Postgres: {execucao["erro"]}. '
                'Corrija e responda APENAS com o SQL, sem markdown.'
            )},
        ]
        geracao2 = gerar_sql(pergunta=pergunta, historico=historico_corrigido)

        if geracao2['ok']:
            validation = geracao2['validation']
            execucao = executar_sql(validation)

    # --- 3) gera texto natural (so se houve dados) ---
    resposta_texto = ''
    if execucao['ok'] and execucao.get('rows'):
        resposta_texto = gerar_texto_resposta(
            pergunta=pergunta,
            colunas=execucao['colunas'],
            rows=execucao['rows'],
            truncado=execucao['truncado'],
            total_linhas_exibidas=execucao['total_linhas'],
        )

    # --- 4) salva as duas mensagens ---
    repo.adicionar_mensagem(cid, 'user', pergunta)

    if execucao['ok']:
        repo.adicionar_mensagem(
            cid,
            'assistant',
            validation.sql,
            sql=validation.sql,
            resposta_texto=resposta_texto,
            colunas=execucao['colunas'],
            rows=execucao['rows'],
            total_linhas=execucao['total_linhas'],
            truncado=execucao['truncado'],
            tempo_ms=execucao['tempo_ms'],
        )
    else:
        repo.adicionar_mensagem(
            cid,
            'assistant',
            f"[ERRO] {execucao['erro']}",
            sql=validation.sql,
            erro=execucao['erro'],
        )

    resposta = {
        'ok': execucao['ok'],
        'etapa': 'execucao' if not execucao['ok'] else 'ok',
        'sql': validation.sql,
        'sql_original': validation.original_sql,
        'teve_limit': validation.teve_limit,
        'limit_valor': validation.limit_valor,
        'resposta_texto': resposta_texto,
        'colunas': execucao['colunas'],
        'rows': execucao['rows'],
        'total_linhas': execucao['total_linhas'],
        'truncado': execucao['truncado'],
        'tempo_ms': execucao['tempo_ms'],
        'erro': execucao['erro'],
    }

    return JsonResponse(resposta)


@login_required
@login_required
def chat_historico_view(request):
    """Lista todas as conversas do usuario."""
    conversas = repo.listar_conversas(user=request.user, limite=200)
    return render(request, 'chat/historico.html', {
        'conversas': conversas,
    })


@login_required
def chat_conversa_view(request, conversa_id):
    """Abre uma conversa especifica (troca a conversa atual)."""
    conversa = repo.buscar_conversa(conversa_id, user=request.user)
    if conversa is None:
        raise Http404('Conversa nao encontrada')
    request.session[SESSION_CONVERSA_ID] = _conversa_id(conversa)
    return redirect('chat:chat')


@login_required
@require_POST
def chat_nova_view(request):
    """
    Comeca uma conversa nova.

    Se a conversa atual ja estiver vazia (sem mensagens), reusa ela
    em vez de criar outra (evita conversas vazias duplicadas).
    """
    atual = _conversa_atual(request)

    if atual is not None:
        cid = _conversa_id(atual)
        if cid and not repo.tem_mensagens(cid):
            # Ja esta numa conversa vazia: nao cria outra
            return redirect('chat:chat')

    novo_id = repo.criar_conversa(user=request.user)
    request.session[SESSION_CONVERSA_ID] = novo_id
    return redirect('chat:chat')


@login_required
@require_POST
def chat_limpar_view(request):
    """(Legado) Limpa o id da conversa atual da sessao."""
    request.session.pop(SESSION_CONVERSA_ID, None)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def chat_deletar_view(request, conversa_id):
    """
    Deleta uma conversa do usuario.
    Se for a conversa atual, limpa a sessao e redireciona.
    """
    conversa = repo.buscar_conversa(conversa_id, user=request.user)
    if conversa is None:
        raise Http404('Conversa nao encontrada')

    era_atual = (request.session.get(SESSION_CONVERSA_ID) == _conversa_id(conversa))

    repo.deletar_conversa(conversa_id, user=request.user)

    if era_atual:
        request.session.pop(SESSION_CONVERSA_ID, None)

    return redirect('chat:chat')
    