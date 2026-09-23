# cnpj_interface/chat/services/llm_client.py
"""
Cliente LLM para o chat IA.

Wrapper em torno do SDK da OpenAI, apontando para a API do DeepSeek.
"""

import os
import logging
from typing import Generator, Optional

from openai import OpenAI
from django.conf import settings


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

DEEPSEEK_API_KEY = getattr(settings, 'DEEPSEEK_API_KEY', None) or os.getenv('DEEPSEEK_API_KEY')
DEFAULT_MODEL = os.getenv('CHAT_DEEPSEEK_MODEL', 'deepseek-chat')
DEEPSEEK_BASE_URL = os.getenv('CHAT_DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
DEFAULT_TIMEOUT = 60


# ============================================================
# EXCEÇÕES
# ============================================================

class LLMError(Exception):
    """Erro genérico do LLM."""
    pass


class LLMConfigError(LLMError):
    """Erro de configuração (ex: API key faltando)."""
    pass


# ============================================================
# CLIENTE
# ============================================================

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    """Retorna um cliente OpenAI configurado para o DeepSeek (singleton)."""
    global _client

    if not DEEPSEEK_API_KEY:
        raise LLMConfigError('DEEPSEEK_API_KEY não configurada no .env')

    if _client is None:
        _client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=DEFAULT_TIMEOUT,
        )

    return _client


# ============================================================
# CHAMADAS
# ============================================================

def gerar_resposta(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 2000,
) -> str:
    """Chama o LLM e retorna a resposta completa (não-streaming)."""
    client = get_client()
    modelo = model or DEFAULT_MODEL

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
        logger.exception('Erro ao chamar DeepSeek')
        raise LLMError(f'Erro na chamada ao LLM: {e}')

    if not resposta.choices:
        raise LLMError('Resposta vazia do LLM')

    return resposta.choices[0].message.content or ''


def gerar_resposta_stream(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 2000,
) -> Generator[str, None, None]:
    """Chama o LLM com streaming e vai retornando pedaços de texto."""
    client = get_client()
    modelo = model or DEFAULT_MODEL

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

    except Exception as e:
        logger.exception('Erro no streaming do DeepSeek')
        raise LLMError(f'Erro no streaming do LLM: {e}')


# ============================================================
# TESTE DE CONEXÃO
# ============================================================

def testar_conexao() -> tuple[bool, str]:
    """Testa a conexão com o DeepSeek."""
    try:
        resposta = gerar_resposta(
            system_prompt='Você é um assistente útil. Responda de forma curta.',
            user_prompt='Responda apenas: OK',
            max_tokens=10,
        )
        if 'OK' in resposta.upper():
            return True, f'Conexão OK — resposta: {resposta.strip()!r}'
        return True, f'Conexão OK — resposta inesperada: {resposta.strip()!r}'
    except LLMConfigError as e:
        return False, f'Erro de configuração: {e}'
    except LLMError as e:
        return False, f'Erro de comunicação: {e}'
    except Exception as e:
        return False, f'Erro inesperado: {e}'