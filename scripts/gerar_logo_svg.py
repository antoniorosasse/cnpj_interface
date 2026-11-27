#!/usr/bin/env python3
"""Gera os SVGs de marca com o wordmark contornado (paths, sem fonte).

Requer fontTools (ferramenta de build, nao runtime):
    venv/bin/pip install fonttools
Depois:
    venv/bin/python scripts/gerar_logo_svg.py
"""
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

BASE = Path(__file__).resolve().parent.parent
SAIDA = BASE / 'static' / 'img'
FONTE = '/usr/share/fonts/truetype/LiberationSans-Bold.ttf'

# Mark (pin + orbita + predios). Os paths ja eram vetoriais.
MARK = '''<ellipse cx="32" cy="30" rx="27" ry="11" fill="none" stroke="#A3E635" stroke-width="3.2" transform="rotate(-24 32 30)"/>
    <path d="M32 60C32 60 9 38 9 24a23 23 0 1 1 46 0c0 14-23 36-23 36Z" fill="#0F766E"/>
    <g fill="#FFFFFF">
      <rect x="24" y="27" width="6" height="17" rx="1.5"/>
      <rect x="32" y="21" width="8" height="23" rx="1.5"/>
      <rect x="42" y="30" width="5" height="14" rx="1.5"/>
    </g>
    <g fill="#B6F2E6">
      <rect x="25.5" y="30" width="1.5" height="1.5"/><rect x="28" y="30" width="1.5" height="1.5"/>
      <rect x="25.5" y="34" width="1.5" height="1.5"/><rect x="28" y="34" width="1.5" height="1.5"/>
      <rect x="33.5" y="25" width="1.8" height="1.8"/><rect x="36.8" y="25" width="1.8" height="1.8"/>
      <rect x="33.5" y="29" width="1.8" height="1.8"/><rect x="36.8" y="29" width="1.8" height="1.8"/>
      <rect x="33.5" y="33" width="1.8" height="1.8"/><rect x="36.8" y="33" width="1.8" height="1.8"/>
      <rect x="43.5" y="33" width="1.5" height="1.5"/><rect x="43.5" y="37" width="1.5" height="1.5"/>
    </g>
    <circle cx="57" cy="17" r="3.6" fill="#F59E0B"/>
    <circle cx="9" cy="48" r="2.6" fill="#A3E635"/>'''

FONT = TTFont(FONTE)
GLYPHS = FONT.getGlyphSet()
CMAP = FONT.getBestCmap()
HMTX = FONT['hmtx']
UPM = FONT['head'].unitsPerEm


def _glifo(caractere):
    nome = CMAP.get(ord(caractere))
    return nome if nome and nome in GLYPHS else None


def _avanco(nome):
    return HMTX[nome][0]


def _medida(texto, tamanho, espaco):
    escala = tamanho / UPM
    total = 0.0
    nomes = []
    for c in texto:
        nome = _glifo(c)
        if not nome:
            continue
        nomes.append(nome)
        total += _avanco(nome) * escala
    if len(nomes) > 1:
        total += espaco * (len(nomes) - 1)
    return total, nomes


def _caminho(texto, tamanho, x, y, cor, espaco=0.0):
    """Devolve <g> com cada glifo como path, na baseline (x, y)."""
    escala = tamanho / UPM
    cursor = 0.0
    partes = []
    for c in texto:
        nome = _glifo(c)
        if not nome:
            continue
        pen = SVGPathPen(GLYPHS)
        transform = (escala, 0, 0, -escala, x + cursor, y)
        GLYPHS[nome].draw(TransformPen(pen, transform))
        d = pen.getCommands()
        if d:
            partes.append(f'<path d="{d}"/>')
        cursor += _avanco(nome) * escala + espaco
    return f'<g fill="{cor}">' + ''.join(partes) + '</g>'


def _texto_central(texto, tamanho, centro_x, y, cor, espaco=0.0):
    largura, _ = _medida(texto, tamanho, espaco)
    return _caminho(texto, tamanho, centro_x - largura / 2, y, cor, espaco)


def mark(escala=1.0, x=0, y=0):
    return f'<g transform="translate({x} {y}) scale({escala})">{MARK}</g>'


def escrever(nome, viewbox, corpo):
    conteudo = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + viewbox +
                '" role="img" aria-label="BusqueCNPJ">\n  ' + corpo +
                '\n</svg>\n')
    (SAIDA / nome).write_text(conteudo, encoding='utf-8')


def horizontal():
    partes = [
        mark(1.7, 8, 18),
        _caminho('Busque', 72, 145, 94, '#1C2321', -3),
    ]
    largura_busque, _ = _medida('Busque', 72, -3)
    # a medida inclui o espaco depois do ultimo glifo; desconta um espaco
    x_cnpj = 145 + largura_busque + 3
    partes.append(_caminho('CNPJ', 72, x_cnpj, 94, '#B45309', -3))
    partes.append(_caminho('PROSPECÇÃO B2B INTELIGENTE', 17, 148, 128, '#5E6572', 2))
    escrever('logo-horizontal.svg', '0 0 620 160', '\n  '.join(partes))


def vertical():
    partes = [mark(1.85, 120, 18)]
    largura, _ = _medida('BusqueCNPJ', 54, -2)
    # split Busque/CNPJ mantendo centragem no conjunto
    largura_b, _ = _medida('Busque', 54, -2)
    x0 = 180 - largura / 2
    partes.append(_caminho('Busque', 54, x0, 208, '#1C2321', -2))
    partes.append(_caminho('CNPJ', 54, x0 + largura_b + 2, 208, '#B45309', -2))
    partes.append(_texto_central('PROSPECÇÃO B2B INTELIGENTE', 13, 180, 238, '#5E6572', 1.5))
    escrever('logo-vertical.svg', '0 0 360 300', '\n  '.join(partes))


def mono(nome, cor, mono_branco=False):
    partes = [mark(1.7, 8, 18)] if not mono_branco else [
        # marca em linha para o fundo escuro
        '<g transform="translate(8 18) scale(1.7)" fill="none" stroke="#FFFFFF" stroke-width="3.2">'
        '<ellipse cx="32" cy="30" rx="27" ry="11" transform="rotate(-24 32 30)"/>'
        '<path d="M32 60C32 60 9 38 9 24a23 23 0 1 1 46 0c0 14-23 36-23 36Z"/>'
        '<path d="M24 44V27h6v17M32 44V21h8v23M42 44V30h5v14" stroke-width="2.4" stroke-linejoin="round"/>'
        '<circle cx="57" cy="17" r="3.6" fill="#FFFFFF" stroke="none"/></g>'
    ]
    partes.append(_caminho('BusqueCNPJ', 72, 145, 94, cor, -3))
    partes.append(_caminho('PROSPECÇÃO B2B INTELIGENTE', 17, 148, 128, cor, 2))
    escrever(nome, '0 0 620 160', '\n  '.join(partes))


if __name__ == '__main__':
    SAIDA.mkdir(parents=True, exist_ok=True)
    horizontal()
    vertical()
    mono('logo-mono.svg', '#1C2321')
    mono('logo-mono-branco.svg', '#FFFFFF', mono_branco=True)
    print('SVGs gerados em', SAIDA)
