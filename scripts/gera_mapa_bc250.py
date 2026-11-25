#!/usr/bin/env python3
"""Gera o fundo do mapa da /proximidade/ a partir do BC250 (IBGE, 1:250.000).

Por que existe: o servidor publico do OSM bloqueia rajadas (403 "Access
blocked" em HTTP 200) e nao da para depender dele num produto comercial. O
BC250 ja esta em ~/ProjetosPython/DataSetsTeste/bc250_2026-03-03.gpkg e tem o
que o raio de 1-100 km precisa: limite de municipio, mancha urbana e rodovia.
O mapa deixa de pedir tile a terceiro: o Leaflet desenha estes GeoJSON.

Sem GDAL/shapely na maquina: o WKB do GeoPackage e lido na mao (struct) e
simplificado com Douglas-Peucker em Python puro.

Saida (static/mapa/):
    municipios.geojson  ~5.571 poligonos, tolerancia 0.01 grau (~1,1 km)
    urbano.geojson      ~8.394 manchas urbanas, 0.004 grau (~440 m)
    rodovias.geojson    trechos rodoviarios federais/estaduais, 0.004 grau

Uso:
    venv/bin/python scripts/gera_mapa_bc250.py [--gpkg CAMINHO] [--saida DIR]
"""
import argparse
import json
import math
import sqlite3
import struct
import time
from pathlib import Path

UF_SIGLA = {
    '11': 'RO', '12': 'AC', '13': 'AM', '14': 'RR', '15': 'PA', '16': 'AP',
    '17': 'TO', '21': 'MA', '22': 'PI', '23': 'CE', '24': 'RN', '25': 'PB',
    '26': 'PE', '27': 'AL', '28': 'SE', '29': 'BA', '31': 'MG', '32': 'ES',
    '33': 'RJ', '35': 'SP', '41': 'PR', '42': 'SC', '43': 'RS', '50': 'MS',
    '51': 'MT', '52': 'GO', '53': 'DF',
}

GPKG_PADRAO = Path('/home/neto/ProjetosPython/DataSetsTeste/bc250_2026-03-03.gpkg')
SAIDA_PADRAO = Path(__file__).resolve().parent.parent / 'static' / 'mapa'


def ler_geom(blob):
    """WKB de dentro do blob do GeoPackage (pula magic, srs e envelope)."""
    if blob[:2] != b'GP':
        raise ValueError('nao e GeoPackage')
    env = (blob[3] >> 1) & 0x07
    return blob[8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]:]


def ler_wkb(buf, tipo_esperado):
    """Polygon, MultiPolygon ou LineString -> lista de aneis/linhas de (x, y)."""
    pos = 0
    ordem = '<' if buf[0] == 1 else '>'
    pos += 1
    tipo = struct.unpack_from(ordem + 'I', buf, pos)[0]
    pos += 4
    dim = 2 + bool(tipo & 0x80000000) + bool(tipo & 0x40000000)
    tipo &= 0x0FFFFFFF

    def anel(pos):
        n = struct.unpack_from(ordem + 'I', buf, pos)[0]
        pos += 4
        pts = []
        for _ in range(n):
            x, y = struct.unpack_from(ordem + 'dd', buf, pos)
            pos += 8 * dim
            pts.append((x, y))
        return pts, pos

    if tipo == 1:  # Point
        x, y = struct.unpack_from(ordem + 'dd', buf, pos)
        return [[(x, y)]]
    if tipo == 2:  # LineString
        return [anel(pos)[0]]
    if tipo == 3:  # Polygon
        n = struct.unpack_from(ordem + 'I', buf, pos)[0]
        pos += 4
        aneis = []
        for _ in range(n):
            pts, pos = anel(pos)
            aneis.append(pts)
        return aneis
    if tipo == 6:  # MultiPolygon
        npol = struct.unpack_from(ordem + 'I', buf, pos)[0]
        pos += 4
        aneis = []
        for _ in range(npol):
            pos += 1
            t2 = struct.unpack_from(ordem + 'I', buf, pos)[0]
            pos += 4
            if (t2 & 0x0FFFFFFF) != 3:
                raise ValueError(f'poligono interno tipo {t2}')
            n = struct.unpack_from(ordem + 'I', buf, pos)[0]
            pos += 4
            for _ in range(n):
                pts, pos = anel(pos)
                aneis.append(pts)
        return aneis
    raise ValueError(f'tipo {tipo} != {tipo_esperado}')


def simplifica(pts, tol):
    """Douglas-Peucker iterativo (evita recursao profunda em anel grande)."""
    if len(pts) < 3 or tol <= 0:
        return pts
    manter = [False] * len(pts)
    manter[0] = manter[-1] = True
    pilha = [(0, len(pts) - 1)]
    while pilha:
        ini, fim = pilha.pop()
        if fim <= ini + 1:
            continue
        ax, ay = pts[ini]
        bx, by = pts[fim]
        dx, dy = bx - ax, by - ay
        norma = dx * dx + dy * dy
        dmax, idx = 0.0, ini
        for i in range(ini + 1, fim):
            px, py = pts[i]
            if norma == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / norma))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > dmax:
                dmax, idx = d, i
        if dmax > tol:
            manter[idx] = True
            pilha.append((ini, idx))
            pilha.append((idx, fim))
    return [p for p, k in zip(pts, manter) if k]


def arredonda(pts):
    return [[round(x, 5), round(y, 5)] for x, y in pts]


def feicao_poligono(aneis, props, tol):
    """GeoJSON Polygon (primeiro anel externo; buracos preservados)."""
    externo = arredonda(simplifica(aneis[0], tol))
    if len(externo) < 4:
        externo.append(externo[0])
    buracos = []
    for anel in aneis[1:]:
        s = arredonda(simplifica(anel, tol))
        if len(s) >= 4:
            buracos.append(s)
    return {'type': 'Feature', 'properties': props,
            'geometry': {'type': 'Polygon', 'coordinates': [externo] + buracos}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gpkg', default=str(GPKG_PADRAO))
    ap.add_argument('--saida', default=str(SAIDA_PADRAO))
    args = ap.parse_args()
    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(f'file:{args.gpkg}?mode=ro', uri=True)
    cur = con.cursor()

    # 27 UF como poligonos (para achar a UF de mancha urbana e de rodovia)
    ufs = []
    cur.execute('select sigla, geom from lml_unidade_federacao_a')
    for sigla, geom in cur.fetchall():
        aneis = ler_wkb(ler_geom(geom), 'poligono')
        externo = aneis[0]
        xs = [p[0] for p in externo]; ys = [p[1] for p in externo]
        ufs.append((sigla, externo, (min(xs), min(ys), max(xs), max(ys))))

    def uf_do_ponto(x, y):
        for sigla, anel, (x0, y0, x1, y1) in ufs:
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            dentro = False
            j = len(anel) - 1
            for i in range(len(anel)):
                xi, yi = anel[i]; xj, yj = anel[j]
                if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                    dentro = not dentro
                j = i
            if dentro:
                return sigla
        return None

    def uf_do_geocodigo(cod):
        return UF_SIGLA.get(str(cod)[:2])

    def centroide(aneis):
        pts = max(aneis, key=len)
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    camadas = [
        ('lml_municipio_a', 'municipios', 0.012, 'poligono',
         lambda r: {'nome': r['nome']}, lambda r, aneis: uf_do_geocodigo(r['geocodigo'])),
        ('lml_area_densamente_edificada_a', 'urbano', 0.006, 'poligono',
         lambda r: {'nome': r['nome']}, lambda r, aneis: uf_do_ponto(*centroide(aneis))),
    ]
    for tabela, prefixo, tol, tipo, props, qual_uf in camadas:
        t = time.time()
        cols = [c[1] for c in cur.execute(f'pragma table_info("{tabela}")')]
        cur.execute(f'select * from "{tabela}"')
        por_uf = {}
        for linha in cur.fetchall():
            reg = dict(zip(cols, linha))
            try:
                aneis = ler_wkb(ler_geom(reg['geom']), tipo)
                feicao = feicao_poligono(aneis, props(reg), tol)
                sigla = qual_uf(reg, aneis)
            except Exception:
                continue
            if sigla is None or len(feicao['geometry']['coordinates'][0]) < 4:
                continue
            por_uf.setdefault(sigla, []).append(feicao)
        total_kb = 0
        for sigla, lista in por_uf.items():
            arq = saida / f'{prefixo}_{sigla}.geojson'
            arq.write_text(json.dumps({'type': 'FeatureCollection', 'features': lista},
                                      ensure_ascii=False), encoding='utf-8')
            total_kb += arq.stat().st_size / 1024
        print(f'{prefixo}: {sum(len(v) for v in por_uf.values())} feicoes em {len(por_uf)} UFs, '
              f'{total_kb:.0f} KB ({time.time()-t:.0f}s)')

    # cidades: pontos com nome, para o mapa ter referencia (Joinville, etc.)
    t = time.time()
    cols = [c[1] for c in cur.execute('pragma table_info("lml_cidade_p")')]
    cur.execute('select * from "lml_cidade_p"')
    por_uf = {}
    for linha in cur.fetchall():
        reg = dict(zip(cols, linha))
        try:
            aneis = ler_wkb(ler_geom(reg['geom']), 'ponto')
        except Exception:
            continue
        x, y = aneis[0][0]
        sigla = uf_do_ponto(x, y)
        if sigla is None:
            continue
        por_uf.setdefault(sigla, []).append({
            'type': 'Feature', 'properties': {'nome': reg.get('nome')},
            'geometry': {'type': 'Point', 'coordinates': [round(x, 5), round(y, 5)]}})
    total_kb = 0
    for sigla, lista in por_uf.items():
        arq = saida / f'cidades_{sigla}.geojson'
        arq.write_text(json.dumps({'type': 'FeatureCollection', 'features': lista},
                                  ensure_ascii=False), encoding='utf-8')
        total_kb += arq.stat().st_size / 1024
    print(f'cidades: {sum(len(v) for v in por_uf.values())} pontos em {len(por_uf)} UFs, '
          f'{total_kb:.0f} KB ({time.time()-t:.0f}s)')

    # rodovias: federais/estaduais, simplificadas e separadas por UF
    t = time.time()
    cols = [c[1] for c in cur.execute('pragma table_info("rod_trecho_rodoviario_l")')]
    cur.execute('select * from "rod_trecho_rodoviario_l"')
    por_uf = {}
    for linha in cur.fetchall():
        reg = dict(zip(cols, linha))
        juris = str(reg.get('jurisdicao') or '').lower()
        if juris and not any(x in juris for x in ('federal', 'estadual')):
            continue
        try:
            linhas = ler_wkb(ler_geom(reg['geom']), 'linha')
            pts = arredonda(simplifica(linhas[0], 0.008))
        except Exception:
            continue
        if len(pts) < 2:
            continue
        x, y = pts[len(pts) // 2]
        sigla = uf_do_ponto(x, y)
        if sigla is None:
            continue
        por_uf.setdefault(sigla, []).append({
            'type': 'Feature', 'properties': {'tipovia': reg.get('tipovia')},
            'geometry': {'type': 'LineString', 'coordinates': pts}})
    total_kb = 0
    for sigla, lista in por_uf.items():
        arq = saida / f'rodovias_{sigla}.geojson'
        arq.write_text(json.dumps({'type': 'FeatureCollection', 'features': lista},
                                  ensure_ascii=False), encoding='utf-8')
        total_kb += arq.stat().st_size / 1024
    print(f'rodovias: {sum(len(v) for v in por_uf.values())} feicoes em {len(por_uf)} UFs, '
          f'{total_kb:.0f} KB ({time.time()-t:.0f}s)')
    con.close()
    print('total:', sum(p.stat().st_size for p in saida.glob('*.geojson')) // 1024, 'KB')


if __name__ == '__main__':
    main()
