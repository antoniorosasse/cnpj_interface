# cnpj_interface/chat/atividade_log.py
"""Registro de resolucao de atividade (Fase 1 da KB autoaprendizavel)."""
import logging
import re
import unicodedata

from chat.models import AtividadeLog

logger = logging.getLogger(__name__)


def normalizar_termo(texto):
    decomposto = unicodedata.normalize('NFKD', str(texto or ''))
    limpo = ''.join(c for c in decomposto if not unicodedata.combining(c))
    limpo = re.sub(r'[^a-z0-9]+', ' ', limpo.lower())
    return re.sub(r'\s+', ' ', limpo).strip()[:200]


def registrar_atividade(termo, filtro=None, origem='', resultados=None,
                        usuario=None):
    """
    Grava uma resolucao. NUNCA levanta excecao: telemetria nao pode quebrar
    a consulta principal. Sem termo nao grava nada.
    """
    try:
        termo = str(termo or '').strip()[:200]
        if not termo:
            return None
        filtro = filtro or {}
        faixas = []
        for faixa in filtro.get('faixas') or []:
            try:
                faixas.append([int(faixa[0]), int(faixa[1])])
            except (TypeError, ValueError, IndexError):
                continue
        return AtividadeLog.objects.create(
            termo=termo,
            termo_normalizado=normalizar_termo(termo),
            origem=origem,
            fonte=filtro.get('fonte') or AtividadeLog.FONTE_NENHUMA,
            categorias=list(filtro.get('categorias') or []),
            cnaes=[int(c) for c in (filtro.get('cnaes') or [])],
            faixas=faixas,
            resultados=resultados,
            usuario=usuario,
        )
    except Exception:
        logger.exception('Falha ao registrar log de atividade')
        return None
