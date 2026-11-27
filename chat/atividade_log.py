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
        from chat.models import AtividadeLog
        fonte = filtro.get('fonte') or AtividadeLog.FONTE_NENHUMA
        if fonte not in dict(AtividadeLog.FONTES):
            fonte = AtividadeLog.FONTE_NENHUMA
        categorias = [str(c) for c in (filtro.get('categorias') or [])]
        cnaes = []
        for c in filtro.get('cnaes') or []:
            try:
                cnaes.append(int(c))
            except (TypeError, ValueError):
                continue
        usuario_log = (usuario
                       if getattr(usuario, 'is_authenticated', False) else None)
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
            fonte=fonte,
            categorias=categorias,
            cnaes=cnaes,
            faixas=faixas,
            resultados=resultados,
            score=filtro.get('score'),
            usuario=usuario_log,
        )
    except Exception:
        logger.exception('Falha ao registrar log de atividade')
        return None
