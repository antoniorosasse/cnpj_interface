# cnpj_interface/chat/services/llm_client.py
"""
Cliente LLM do chat, com credenciais gerenciadas pelo admin.

Ordem de resolucao para uma `finalidade`:
1. `chat.ConfiguracaoIA` ativa e padrao da finalidade (chave criptografada);
2. fallback das variaveis DEEPSEEK_* do .env (compatibilidade/bootstrap).

O segredo nunca e' logado nem devolvido pela API do admin.
"""
import logging
import os
import time
from typing import Generator, Optional

from openai import OpenAI
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = getattr(settings, 'DEEPSEEK_API_KEY', None) or os.getenv('DEEPSEEK_API_KEY')
DEFAULT_MODEL = os.getenv('CHAT_DEEPSEEK_MODEL', 'deepseek-chat')
DEEPSEEK_BASE_URL = os.getenv('CHAT_DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
DEFAULT_TIMEOUT = 60

FINALIDADE_CHAT = 'chat_cliente'
FINALIDADE_KB = 'kb_interna'


class LLMError(Exception):
    """Erro generico do LLM."""


class LLMConfigError(LLMError):
    """Erro de configuracao (ex: API key faltando)."""


_clientes: dict[tuple, OpenAI] = {}


def limpar_cache():
    """Chamado pelo admin quando uma configuracao de IA muda."""
    _clientes.clear()


def _configuracoes_do_banco(finalidade):
    """Le todas as configuracoes ativas da finalidade; nao quebra sem banco."""
    try:
        from chat.models import ConfiguracaoIA
        return list(ConfiguracaoIA.objects
                    .filter(finalidade=finalidade, ativo=True)
                    .order_by('-padrao', 'nome'))
    except Exception:
        logger.exception('Falha ao ler ConfiguracaoIA do banco')
        return []


def _resolver(finalidade, config=None):
    """Devolve dict com api_key/base_url/modelo/timeout/config."""
    from chat.ia_credentials import descriptografar

    configs = [config] if config is not None else _configuracoes_do_banco(finalidade)
    for conf in configs:
        chave = descriptografar(conf.api_key_encrypted)
        if chave:
            return {
                'api_key': chave,
                'base_url': conf.base_url or DEEPSEEK_BASE_URL,
                'modelo': conf.modelo or DEFAULT_MODEL,
                'timeout': max(5, min(int(conf.timeout or DEFAULT_TIMEOUT), 120)),
                'config': conf,
                'finalidade': finalidade,
                'origem': 'admin',
            }
        logger.error(
            'ConfiguracaoIA %s (%s) sem chave valida; tentando a proxima',
            conf.pk, finalidade)
    return {
        'api_key': DEEPSEEK_API_KEY,
        'base_url': DEEPSEEK_BASE_URL,
        'modelo': DEFAULT_MODEL,
        'timeout': DEFAULT_TIMEOUT,
        'config': None,
        'finalidade': finalidade,
        'origem': 'env',
    }


def _client_para(dados):
    chave = dados.get('api_key')
    if not chave:
        raise LLMConfigError(
            'Nenhuma API de IA configurada. Cadastre em Admin → '
            'Configuracoes de IA ou defina DEEPSEEK_API_KEY no .env.')
    cache_key = (dados.get('base_url'), dados.get('timeout'),
                 abs(hash(chave)))
    cliente = _clientes.get(cache_key)
    if cliente is None:
        cliente = OpenAI(
            api_key=chave,
            base_url=dados.get('base_url') or DEEPSEEK_BASE_URL,
            timeout=max(5, min(int(dados.get('timeout') or DEFAULT_TIMEOUT), 120)),
            max_retries=1,
        )
        _clientes[cache_key] = cliente
    return cliente


def get_client(finalidade=FINALIDADE_CHAT) -> OpenAI:
    """Mantido para compatibilidade: devolve o cliente da finalidade."""
    return _client_para(_resolver(finalidade))


def _registrar_uso(dados, modelo, duracao_ms, resposta=None, erro=''):
    try:
        from chat.models import UsoIA
        uso = getattr(resposta, 'usage', None) if resposta is not None else None
        tokens_prompt = int(getattr(uso, 'prompt_tokens', 0) or 0)
        tokens_resposta = int(getattr(uso, 'completion_tokens', 0) or 0)
        tokens_total = int(getattr(uso, 'total_tokens', 0) or 0)
        with transaction.atomic(using='default'):
            UsoIA.objects.create(
                configuracao=dados.get('config'),
                finalidade=dados.get('finalidade') or FINALIDADE_CHAT,
                provedor=(dados.get('config').provedor
                          if dados.get('config') else 'deepseek'),
                modelo=modelo,
                tokens_prompt=tokens_prompt,
                tokens_resposta=tokens_resposta,
                tokens_total=tokens_total or (tokens_prompt + tokens_resposta),
                duracao_ms=max(0, int(duracao_ms)),
                sucesso=not bool(erro),
                erro=str(erro or '')[:1000],
            )
    except Exception:
        logger.exception('Falha ao registrar UsoIA')


def gerar_resposta(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 2000,
    finalidade: str = FINALIDADE_CHAT,
) -> str:
    """Chama o LLM e retorna a resposta completa (nao-streaming)."""
    dados = _resolver(finalidade)
    client = _client_para(dados)
    modelo = model or dados['modelo']
    inicio = time.monotonic()

    try:
        resposta = client.chat.completions.create(
            model=modelo,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        _registrar_uso(dados, modelo, (time.monotonic() - inicio) * 1000,
                       erro=str(e))
        logger.exception('Erro ao chamar o provedor de IA')
        raise LLMError(f'Erro na chamada ao LLM: {e}')

    if not resposta.choices:
        _registrar_uso(dados, modelo, (time.monotonic() - inicio) * 1000,
                       resposta=resposta, erro='Resposta vazia')
        raise LLMError('Resposta vazia do LLM')

    _registrar_uso(dados, modelo, (time.monotonic() - inicio) * 1000,
                   resposta=resposta)
    return resposta.choices[0].message.content or ''


def gerar_resposta_stream(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 2000,
    finalidade: str = FINALIDADE_CHAT,
) -> Generator[str, None, None]:
    """Chama o LLM com streaming e vai retornando pedacos de texto."""
    dados = _resolver(finalidade)
    client = _client_para(dados)
    modelo = model or dados['modelo']
    inicio = time.monotonic()

    try:
        stream = client.chat.completions.create(
            model=modelo,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

        _registrar_uso(dados, modelo, (time.monotonic() - inicio) * 1000)
    except Exception as e:
        _registrar_uso(dados, modelo, (time.monotonic() - inicio) * 1000,
                       erro=str(e))
        logger.exception('Erro no streaming do provedor de IA')
        raise LLMError(f'Erro no streaming do LLM: {e}')


def testar_conexao(finalidade=FINALIDADE_CHAT, config=None) -> tuple[bool, str]:
    """Testa a conexao com a configuracao (ou com a padrao da finalidade)."""
    try:
        dados = _resolver(finalidade, config=config)
        client = _client_para(dados)
        inicio = time.monotonic()
        resposta_obj = client.chat.completions.create(
            model=dados['modelo'],
            messages=[
                {'role': 'system',
                 'content': 'Voce e um assistente util. Responda de forma curta.'},
                {'role': 'user', 'content': 'Responda apenas: OK'},
            ],
            temperature=0.0,
            max_tokens=10,
        )
        _registrar_uso(dados, dados['modelo'],
                       (time.monotonic() - inicio) * 1000, resposta=resposta_obj)
        resposta = resposta_obj.choices[0].message.content or ''
        if 'OK' in resposta.upper():
            return True, f'Conexao OK - resposta: {resposta.strip()!r}'
        return True, f'Conexao OK - resposta inesperada: {resposta.strip()!r}'
    except LLMConfigError as e:
        return False, f'Erro de configuracao: {e}'
    except LLMError as e:
        return False, f'Erro de comunicacao: {e}'
    except Exception as e:
        return False, f'Erro inesperado: {e}'
