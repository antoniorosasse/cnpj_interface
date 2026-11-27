#!/usr/bin/env python3
"""Avalia modelos de embedding para atividade -> CNAE no dominio BusqueCNPJ.

Uso:
    venv/bin/python scripts/avaliar_atividade_cnae.py \
        --gold docs/gold_atividade_cnae.json \
        --model lexical,braza,brazembed

O catalogo (`c_cnaes`) vem do banco configurado no Django. As metricas sao
top-1 (acertou o CNAE aceito na 1a posicao), top-3, MRR e cobertura. Termos
negativos/ambiguos ficam fora das metricas de ranking; eles servem para a
avaliacao da IA de triagem da KB.
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cnpj_project.settings')

import django  # noqa: E402
django.setup()

import numpy as np  # noqa: E402
import psycopg2  # noqa: E402
from django.conf import settings  # noqa: E402

MODELOS = {
    'braza': 'calneymgp/braza-embedding-ptbr-v1',
    'brazembed': 'tardellirs/brazembed-pt-br',
    'e5-small': 'intfloat/multilingual-e5-small',
    'e5-large': 'intfloat/multilingual-e5-large',
    'bge-m3': 'BAAI/bge-m3',
    'qwen3-0.6b': 'Qwen/Qwen3-Embedding-0.6B',
}


def normalizar(texto):
    decomposto = unicodedata.normalize('NFKD', str(texto or ''))
    limpo = ''.join(c for c in decomposto if not unicodedata.combining(c))
    limpo = re.sub(r'[^a-z0-9]+', ' ', limpo.lower())
    return re.sub(r'\s+', ' ', limpo).strip()


def carregar_catalogo():
    cfg = settings.DATABASES['postgres']
    conn = psycopg2.connect(
        host=cfg['HOST'], port=cfg['PORT'], dbname=cfg['NAME'],
        user=cfg['USER'], password=cfg['PASSWORD'],
    )
    try:
        cur = conn.cursor()
        cur.execute('SELECT cnae, nm_cnae FROM c_cnaes ORDER BY cnae')
        linhas = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return [(int(c), n) for c, n in linhas]


def expandir_aceitos(item, catalogo):
    aceitos = set(int(c) for c in item.get('aceitos') or [])
    for faixa in item.get('faixas') or []:
        inicio, fim = int(faixa[0]), int(faixa[1])
        aceitos.update(c for c, _ in catalogo if inicio <= c <= fim)
    return aceitos


def carregar_gold(caminho, catalogo):
    itens = json.loads(Path(caminho).read_text(encoding='utf-8'))
    codigos = {c for c, _ in catalogo}
    faltando = set()
    for item in itens:
        aceitos = expandir_aceitos(item, catalogo)
        faltando.update(c for c in aceitos if c not in codigos)
    if faltando:
        print(f'AVISO: {len(faltando)} CNAE(s) do gold nao existem no catalogo: '
              f'{sorted(faltando)[:10]}', file=sys.stderr)
    return itens


def ranking_lexical(termo, catalogo):
    alvo = normalizar(termo)
    pontuados = []
    for cnae, nome in catalogo:
        base = normalizar(nome)
        # similaridade de sequencia + bonus por conter a palavra inteira
        score = SequenceMatcher(None, alvo, base).ratio()
        if alvo and alvo in base:
            score += 0.25
        pontuados.append((score, cnae))
    pontuados.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in pontuados]


def carregar_embeddings_modelo(nome, catalogo, cache_dir):
    from sentence_transformers import SentenceTransformer
    modelo_id = MODELOS.get(nome, nome)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^a-zA-Z0-9_.-]+', '_', nome)
    arquivo = cache / f'{slug}.npy'
    if arquivo.exists():
        matriz = np.load(arquivo)
        if matriz.shape[0] == len(catalogo):
            return None, matriz
    print(f'Carregando modelo {nome} -> {modelo_id}...', file=sys.stderr)
    modelo = SentenceTransformer(modelo_id, trust_remote_code=True)
    docs = [f'{nome} ({cnae})' for cnae, nome in catalogo]
    inicio = time.monotonic()
    matriz = modelo.encode(
        docs, batch_size=64, normalize_embeddings=True,
        convert_to_numpy=True, show_progress_bar=True,
    )
    np.save(arquivo, matriz)
    print(f'  {len(docs)} descricoes em {time.monotonic()-inicio:.1f}s; '
          f'dim={matriz.shape[1]}; cache={arquivo}', file=sys.stderr)
    return modelo, matriz


def ranking_embedding(termo, modelo, matriz, catalogo, com_prefixo_e5=False):
    if com_prefixo_e5:
        termo = 'query: ' + termo
    vetor = modelo.encode([termo], normalize_embeddings=True, convert_to_numpy=True)[0]
    scores = matriz @ vetor
    ordem = np.argsort(-scores)
    return [catalogo[i][0] for i in ordem]


def metricas(itens, catalogo, ranking_fn, topk=3):
    por_grupo = {}
    detalhes = []
    for item in itens:
        if item.get('tipo') != 'atividade':
            continue
        aceitos = expandir_aceitos(item, catalogo)
        ordem = ranking_fn(item['termo'])
        rank = next((i + 1 for i, c in enumerate(ordem) if c in aceitos), None)
        top1 = 1 if rank == 1 else 0
        top3 = 1 if rank is not None and rank <= topk else 0
        mrr = (1.0 / rank) if rank else 0.0
        detalhes.append({
            'id': item['id'], 'termo': item['termo'], 'grupo': item['grupo'],
            'dificuldade': item['dificuldade'], 'aceitos': sorted(aceitos),
            'top1': top1, 'top3': top3, 'mrr': mrr,
            'rank': rank, 'primeiro': ordem[0] if ordem else None,
        })
        g = por_grupo.setdefault(item['grupo'], {'n': 0, 'top1': 0, 'top3': 0, 'mrr': 0.0})
        g['n'] += 1
        g['top1'] += top1
        g['top3'] += top3
        g['mrr'] += mrr
    n = len(detalhes)
    resumo = {
        'n': n,
        'top1': sum(d['top1'] for d in detalhes) / n if n else 0,
        'top3': sum(d['top3'] for d in detalhes) / n if n else 0,
        'mrr': sum(d['mrr'] for d in detalhes) / n if n else 0,
    }
    for g in por_grupo.values():
        g['top1'] /= g['n']
        g['top3'] /= g['n']
        g['mrr'] /= g['n']
    return resumo, por_grupo, detalhes


def imprimir(nome, resumo, por_grupo, detalhes, mostrar_erros=12):
    print(f'\n## {nome}')
    print(f"n={resumo['n']}  top1={resumo['top1']:.1%}  "
          f"top3={resumo['top3']:.1%}  MRR={resumo['mrr']:.3f}")
    print('grupo              n   top1   top3   MRR')
    for grupo, g in sorted(por_grupo.items()):
        print(f'{grupo:<16} {g["n"]:>3}  {g["top1"]:>5.1%}  {g["top3"]:>5.1%}  {g["mrr"]:.3f}')
    erros = [d for d in detalhes if not d['top1']]
    if erros:
        print('\nTop-1 errados (amostra):')
        for d in erros[:mostrar_erros]:
            print(f"  {d['id']} {d['termo']!r}: esperado {d['aceitos'][:4]}, "
                  f"veio CNAE {d['primeiro']} (rank={d['rank']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--gold', default=str(BASE / 'docs' / 'gold_atividade_cnae.json'))
    ap.add_argument('--model', default='lexical,braza',
                    help='lista separada por virgula; nomes em MODELOS ou path HF')
    ap.add_argument('--cache-dir', default='/tmp/avaliacao_cnae')
    ap.add_argument('--topk', type=int, default=3)
    ap.add_argument('--json-out', default='')
    args = ap.parse_args()

    catalogo = carregar_catalogo()
    itens = carregar_gold(args.gold, catalogo)
    print(f'catalogo={len(catalogo)} CNAEs; gold={len(itens)} termos; '
          f'atividade={sum(1 for i in itens if i.get("tipo") == "atividade")}')

    resultados = {}
    modelos = [m.strip() for m in args.model.split(',') if m.strip()]
    for nome in modelos:
        if nome == 'lexical':
            fn = lambda termo: ranking_lexical(termo, catalogo)
            resumo, grupos, detalhes = metricas(itens, catalogo, fn, args.topk)
            resultados[nome] = {'resumo': resumo, 'grupos': grupos, 'detalhes': detalhes}
            imprimir('lexical', resumo, grupos, detalhes)
            continue
        modelo, matriz = carregar_embeddings_modelo(nome, catalogo, args.cache_dir)
        if modelo is None:
            # cache carregado; para consulta tambem precisamos do modelo
            from sentence_transformers import SentenceTransformer
            modelo = SentenceTransformer(MODELOS.get(nome, nome), trust_remote_code=True)
        prefixo = nome.startswith('e5')
        fn = lambda termo, m=modelo, mat=matriz, pref=prefixo: ranking_embedding(
            termo, m, mat, catalogo, com_prefixo_e5=pref)
        resumo, grupos, detalhes = metricas(itens, catalogo, fn, args.topk)
        resultados[nome] = {'resumo': resumo, 'grupos': grupos, 'detalhes': detalhes}
        imprimir(nome, resumo, grupos, detalhes)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(resultados, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\nJSON: {args.json_out}')


if __name__ == '__main__':
    main()
