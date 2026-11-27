#!/usr/bin/env python3
"""Reembeda as descricoes de CNAE com o modelo escolhido (BrazEmbed 768d).

Exemplo (depois de criar a coluna embedding_768):
    venv/bin/python scripts/reembedding_cnae.py --coluna embedding_768
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cnpj_project.settings')

import django  # noqa: E402
django.setup()

import psycopg2  # noqa: E402
import torch  # noqa: E402
from django.conf import settings  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

MODELO = 'tardellirs/brazembed-pt-br'
DIM = 768


def _doc(cnae, nome, formato):
    if formato == 'nome':
        return str(nome)
    return f'{nome} ({cnae})'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--coluna', default='embedding_768')
    ap.add_argument('--formato', choices=('nome', 'nome-codigo'),
                    default='nome')
    ap.add_argument('--batch-size', type=int, default=64)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', args.coluna):
        raise SystemExit('nome de coluna invalido')

    cfg = settings.DATABASES['postgres']
    conn = psycopg2.connect(
        host=cfg['HOST'], port=cfg['PORT'], dbname=cfg['NAME'],
        user=cfg['USER'], password=cfg['PASSWORD'],
    )
    try:
        cur = conn.cursor()
        cur.execute('SELECT cnae, nm_cnae FROM c_cnaes ORDER BY cnae')
        linhas = [(int(c), n) for c, n in cur.fetchall()]
        print(f'CNAEs: {len(linhas)}; formato={args.formato}; coluna={args.coluna}')
        if args.dry_run:
            for cnae, nome in linhas[:3]:
                print(cnae, repr(_doc(cnae, nome, args.formato)))
            return
        print('Carregando modelo (cache local)...')
        try:
            modelo = SentenceTransformer(MODELO, local_files_only=True)
        except TypeError:
            modelo = SentenceTransformer(MODELO)
        modelo.max_seq_length = 128
        torch.set_num_threads(1)
        docs = [_doc(c, n, args.formato) for c, n in linhas]
        inicio = time.monotonic()
        matriz = modelo.encode(
            docs, batch_size=args.batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=True,
        )
        print(f'encode: {time.monotonic()-inicio:.1f}s; shape={matriz.shape}')
        if matriz.shape[1] != DIM:
            raise SystemExit(f'dimensao inesperada: {matriz.shape[1]}')
        sql = f'UPDATE c_cnaes SET {args.coluna} = %s::halfvec({DIM}) WHERE cnae = %s'
        for i, (cnae, _nome) in enumerate(linhas):
            vetor = '[' + ','.join(f'{x:.6f}' for x in matriz[i]) + ']'
            cur.execute(sql, (vetor, cnae))
        conn.commit()
        cur.execute(f'SELECT count(*), count({args.coluna}) FROM c_cnaes')
        print('verificacao (total, preenchidos):', cur.fetchone())
        cur.execute(
            f'SELECT vector_dims({args.coluna}), count(*) FROM c_cnaes '
            f'WHERE {args.coluna} IS NOT NULL GROUP BY 1')
        print('dimensoes:', cur.fetchall())
    finally:
        conn.close()


if __name__ == '__main__':
    main()
