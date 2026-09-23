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

from .models import Conversa, Mensagem
from .services.sql_generator import gerar_sql
from .services.sql_executor import executar_sql
from chat.models import Conversa, Mensagem


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
    try:
        return Conversa.objects.get(pk=cid, usuario=request.user)
    except Conversa.DoesNotExist:
        # Conversa foi apagada (ou pertence a outro user)
        request.session.pop(SESSION_CONVERSA_ID, None)
        return None


def _historico_para_llm(conversa, limite=MAX_CONTEXTO):
    """
    Le as ultimas N mensagens da conversa no formato do LLM:
        [{'role': 'user', 'content': '...'}, {'role': 'assistant', 'content': '...'}]
    """
    if conversa is None:
        return []

    # Pega as ultimas (limite * 2) mensagens (user + assistant = 2 por turno)
    qs = conversa.mensagens.order_by('-criado_em')[: limite * 2]
    mensagens = list(reversed(qs))  # volta pra ordem cronologica

    historico = []
    for m in mensagens:
        if m.papel == Mensagem.PAPEL_USER:
            historico.append({'role': 'user', 'content': m.conteudo})
        elif m.papel == Mensagem.PAPEL_ASSISTANT:
            # Assistente: o conteudo e' o SQL gerado
            historico.append({'role': 'assistant', 'content': m.sql or m.conteudo})
    return historico


@login_required
def chat_view(request):
    """Renderiza a pagina do chat (com a conversa atual ou nova)."""
    # Limpa conversas vazias antigas deste usuario (housekeeping leve)
    Conversa.objects.filter(
        usuario=request.user,
        mensagens__isnull=True,   # sem mensagens
    ).exclude(
        pk=request.session.get(SESSION_CONVERSA_ID),
    ).delete()

    conversa = _conversa_atual(request)

    # Se nao tem conversa, cria uma nova
    if conversa is None:
        conversa = Conversa.objects.create(usuario=request.user)
        request.session[SESSION_CONVERSA_ID] = conversa.pk

    # Lista de todas as conversas do usuario (pra sidebar)
    conversas = Conversa.objects.filter(usuario=request.user)

    # Mensagens da conversa atual, serializadas pro JS
    mensagens_json = [
    {'papel': m.papel, 'conteudo': m.conteudo}
    for m in Mensagem.objects.filter(conversa=conversa)
    ]       

    return render(request, 'chat/chat.html', {
        'conversa': conversa,
        'conversas': conversas,
        'mensagens_json': mensagens_json,
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
        conversa = Conversa.objects.create(usuario=request.user)
        request.session[SESSION_CONVERSA_ID] = conversa.pk

    # Se for a primeira pergunta, define o titulo
    if not conversa.titulo:
        conversa.titulo = pergunta[:200]
        conversa.save(update_fields=['titulo', 'atualizado_em'])

    # --- historico para o LLM ---
    historico = _historico_para_llm(conversa)

    # --- 1) gera SQL ---
    geracao = gerar_sql(pergunta=pergunta, historico=historico)

    if not geracao['ok']:
        # Salva a pergunta do usuario mesmo assim (pra ele ver no historico)
        Mensagem.objects.create(
            conversa=conversa,
            papel=Mensagem.PAPEL_USER,
            conteudo=pergunta,
        )
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

    # --- 3) salva as duas mensagens ---
    Mensagem.objects.create(
        conversa=conversa,
        papel=Mensagem.PAPEL_USER,
        conteudo=pergunta,
    )

    if execucao['ok']:
        Mensagem.objects.create(
            conversa=conversa,
            papel=Mensagem.PAPEL_ASSISTANT,
            conteudo=validation.sql,
            sql=validation.sql,
            total_linhas=execucao['total_linhas'],
            tempo_ms=execucao['tempo_ms'],
        )
    else:
        Mensagem.objects.create(
            conversa=conversa,
            papel=Mensagem.PAPEL_ASSISTANT,
            conteudo=f"[ERRO] {execucao['erro']}",
            sql=validation.sql,
        )

    # Toca o atualizado_em da conversa (ordena o historico)
    conversa.save(update_fields=['atualizado_em'])

    resposta = {
        'ok': execucao['ok'],
        'etapa': 'execucao' if not execucao['ok'] else 'ok',
        'sql': validation.sql,
        'sql_original': validation.original_sql,
        'teve_limit': validation.teve_limit,
        'limit_valor': validation.limit_valor,
        'colunas': execucao['colunas'],
        'rows': execucao['rows'],
        'total_linhas': execucao['total_linhas'],
        'truncado': execucao['truncado'],
        'tempo_ms': execucao['tempo_ms'],
        'erro': execucao['erro'],
    }

    return JsonResponse(resposta)


@login_required
def chat_historico_view(request):
    """Lista todas as conversas do usuario."""
    conversas = Conversa.objects.filter(usuario=request.user)
    return render(request, 'chat/historico.html', {
        'conversas': conversas,
    })


@login_required
def chat_conversa_view(request, conversa_id: int):
    """Abre uma conversa especifica (troca a conversa atual)."""
    conversa = get_object_or_404(Conversa, pk=conversa_id, usuario=request.user)
    request.session[SESSION_CONVERSA_ID] = conversa.pk
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

    if atual is not None and not atual.mensagens.exists():  # type: ignore[attr-defined]
        # Ja esta numa conversa vazia: nao cria outra
        return redirect('chat:chat')

    conversa = Conversa.objects.create(usuario=request.user)
    request.session[SESSION_CONVERSA_ID] = conversa.pk
    return redirect('chat:chat')


@login_required
@require_POST
def chat_limpar_view(request):
    """(Legado) Limpa o id da conversa atual da sessao."""
    request.session.pop(SESSION_CONVERSA_ID, None)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def chat_deletar_view(request, conversa_id: int):
    """
    Deleta uma conversa do usuario.
    Se for a conversa atual, limpa a sessao e redireciona.
    """
    conversa = get_object_or_404(Conversa, pk=conversa_id, usuario=request.user)

    era_atual = (request.session.get(SESSION_CONVERSA_ID) == conversa.pk)

    conversa.delete()

    if era_atual:
        request.session.pop(SESSION_CONVERSA_ID, None)

    return redirect('chat:chat')
        