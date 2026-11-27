# exportacao/async_export.py
"""Exportacao completa em background, com arquivo temporario e status.

O worker normaliza/valida a SQL ja' gravada, executa em modo read-only e grava
CSV/JSON em streaming. O download e' separado; creditos sao debitados somente
depois que o arquivo foi gerado com sucesso.
"""
import csv
import json
import logging
import os
import secrets
import threading
from datetime import timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Exportacao
from .servicos import SaldoInsuficiente, debitar

logger = logging.getLogger(__name__)


def _diretorio():
    pasta = Path(getattr(settings, 'EXPORTACAO_COMPLETA_DIR',
                         Path(settings.BASE_DIR) / 'exportacoes'))
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def limite_para(user):
    staff = bool(user is not None and user.is_authenticated
                 and (user.is_staff or user.is_superuser))
    if staff:
        return int(getattr(settings, 'EXPORTACAO_COMPLETA_MAX_ADMIN', 500000))
    return int(getattr(settings, 'EXPORTACAO_COMPLETA_MAX_CLIENTE', 50000))


def criar_job(user, cliente, sql, formato, pergunta, ip, total):
    """Cria um pedido pendente; nao executa e nao debita ainda."""
    token = secrets.token_urlsafe(32)
    expira = timezone.now() + timedelta(
        hours=int(getattr(settings, 'EXPORTACAO_COMPLETA_TTL_HORAS', 24)))
    return Exportacao.objects.create(
        cliente=cliente, usuario=user if user.is_authenticated else None,
        visitante='', origem='chat', formato=formato, sql=sql,
        filtros={'pergunta': pergunta or ''},
        n_linhas=total or 0, amostra=False, ip=ip, completo=True,
        token=token, status='pendente', expira_em=expira,
    )


def iniciar_job(job_id):
    """Dispara o worker localmente (producao pode usar o management command)."""
    t = threading.Thread(target=processar_job, args=(job_id,), daemon=True)
    t.start()


def _sql_final(job, cap):
    from chat.services.embedding_service import injetar_vetor, tem_placeholder
    from chat.services.sql_validator import ValidationResult, validar_sql
    from exportacao.views import _aumentar_limit

    validation = validar_sql(job.sql, max_rows=cap)
    sql = validation.sql
    if tem_placeholder(sql):
        pergunta = (job.filtros or {}).get('pergunta') or ''
        sql, erro = injetar_vetor(sql, pergunta)
        if erro:
            raise RuntimeError(erro)
    sql = _aumentar_limit(sql, cap)
    return sql


def _valor(linha, chave):
    valor = linha.get(chave)
    if valor is None:
        return ''
    if isinstance(valor, str):
        return valor.replace('\r', ' ').replace('\n', ' ').strip()
    return valor


def _gravar(job, cursor, colunas, arq, primeiro_lote=None):
    total = 0
    pendentes = list(primeiro_lote or [])
    if job.formato == 'json':
        arq.write('[')
        primeiro = True
    else:
        arq.write('\ufeff')
        escritor = csv.writer(arq, delimiter=';', quoting=csv.QUOTE_MINIMAL)
        escritor.writerow(colunas)
    while True:
        if pendentes:
            lote, pendentes = pendentes, []
        else:
            lote = cursor.fetchmany(1000)
            if not lote:
                break
        for bruto in lote:
            linha = dict(bruto)
            if job.formato == 'json':
                if not primeiro:
                    arq.write(',')
                primeiro = False
                arq.write(json.dumps(linha, ensure_ascii=False, default=str))
            else:
                escritor.writerow([_valor(linha, c) for c in colunas])
            total += 1
    if job.formato == 'json':
        arq.write(']')
    return total


def processar_job(job_id):
    """Processa um pedido. Idempotente: só pega status pendente."""
    agora = timezone.now()
    with transaction.atomic():
        atualizados = (Exportacao.objects
                       .filter(pk=job_id, completo=True, status='pendente')
                       .update(status='processando', iniciado_em=agora))
    if not atualizados:
        return False

    job = Exportacao.objects.get(pk=job_id)
    cap = limite_para(job.usuario)
    caminho = None
    try:
        from chat.services.sql_executor import _get_config

        sql = _sql_final(job, cap)
        cfg = _get_config()
        conn = psycopg2.connect(**cfg)
        conn.set_session(readonly=True, autocommit=False)
        try:
            timeout = int(getattr(settings, 'EXPORTACAO_COMPLETA_TIMEOUT', 600))
            # SET nao pode ser executado dentro de cursor nomeado (DECLARE).
            cur_set = conn.cursor()
            cur_set.execute(f'SET statement_timeout = {timeout * 1000}')
            cur_set.close()
            cur = conn.cursor(
                name='export_completo',
                cursor_factory=psycopg2.extras.RealDictCursor)
            cur.itersize = 1000
            cur.execute(sql)
            # Cursor nomeado so' preenche `description` depois do primeiro fetch.
            primeiro_lote = cur.fetchmany(1000)
            colunas = (list(primeiro_lote[0].keys())
                       if primeiro_lote else [])
            nome = f'exportacao-{job.pk}-{job.token[:8]}.{job.formato}'
            caminho = _diretorio() / nome
            with open(caminho, 'w', encoding='utf-8', newline='') as arq:
                total = _gravar(job, cur, colunas, arq, primeiro_lote)
            if total > cap:
                raise RuntimeError(f'Exportacao passou do teto de {cap} linhas.')
            cur.close()
            conn.rollback()
        finally:
            conn.close()

        creditos = 0
        if not (job.usuario and (job.usuario.is_staff or job.usuario.is_superuser)):
            if job.cliente is None:
                raise RuntimeError('Conta sem cliente/creditos.')
            try:
                creditos = debitar(
                    job.cliente, total, f'exportacao-completa:{job.pk}')
            except SaldoInsuficiente as exc:
                raise RuntimeError(
                    f'Saldo insuficiente: {exc.necessarios} necessarios, '
                    f'{exc.disponiveis} disponiveis.') from exc

        Exportacao.objects.filter(pk=job.pk).update(
            status='pronto', n_linhas=total, creditos=creditos,
            arquivo_nome=nome, concluido_em=timezone.now(), erro='')
    except Exception as exc:
        logger.exception('Falha na exportacao completa %s', job_id)
        Exportacao.objects.filter(pk=job.pk).update(
            status='falhou', erro=str(exc)[:2000],
            concluido_em=timezone.now())
        if caminho and caminho.exists():
            try:
                caminho.unlink()
            except OSError:
                pass
    return True
