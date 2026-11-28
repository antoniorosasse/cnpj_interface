# /home/neto/ProjetosPython/cnpj_interface/core/estatisticas_pdf.py
"""
PDF da pagina /estatisticas/ (ReportLab puro, sem dependencia nova).

O PDF respeita os mesmos filtros da tela. A pagina HTML usa graficos; o PDF
usa tabelas equivalentes, que sao o que o cliente precisa para anexar em
proposta/relatorio.
"""
from datetime import datetime
from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

AZUL = colors.HexColor('#0f5132')
CINZA = colors.HexColor('#5c6b73')
LINHA = colors.HexColor('#d9e2e8')
FUNDO = colors.HexColor('#f4f7f9')
LARANJA = colors.HexColor('#b45309')
LOGO_PDF = Path(settings.BASE_DIR) / 'static' / 'img' / 'logo-busquecnpj.png'


def _cabecalho_rodape(canvas, doc):
    """Marca no topo de todas as paginas + rodape institucional."""
    largura, altura = A4
    canvas.saveState()
    if LOGO_PDF.exists():
        canvas.drawImage(str(LOGO_PDF), 16 * mm, altura - 19 * mm,
                         width=58 * mm, height=15 * mm,
                         preserveAspectRatio=True, mask='auto')
    else:
        canvas.setFillColor(AZUL)
        canvas.setFont('Helvetica-Bold', 16)
        canvas.drawString(16 * mm, altura - 15 * mm, 'BusqueCNPJ')
    canvas.setStrokeColor(AZUL)
    canvas.setLineWidth(1.1)
    canvas.line(16 * mm, altura - 22 * mm, largura - 16 * mm, altura - 22 * mm)
    canvas.setStrokeColor(LARANJA)
    canvas.setLineWidth(0.9)
    canvas.line(16 * mm, altura - 22.8 * mm, 46 * mm, altura - 22.8 * mm)

    canvas.setFillColor(CINZA)
    canvas.setFont('Helvetica', 7)
    canvas.drawString(16 * mm, 9 * mm,
                      'BusqueCNPJ · inteligência comercial de CNPJ · busquecnpj.com.br')
    canvas.drawRightString(largura - 16 * mm, 9 * mm,
                           f'Página {doc.page}')
    canvas.restoreState()


def _num(valor):
    try:
        return f'{int(valor):,}'.replace(',', '.')
    except (TypeError, ValueError):
        return str(valor or 0)


def _tabela(dados, larguras, estilo_cabecalho=True):
    tabela = Table(dados, colWidths=larguras, repeatRows=1 if estilo_cabecalho else 0)
    comandos = [
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#1d2b33')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LINEBELOW', (0, 0), (-1, -1), 0.3, LINHA),
    ]
    if estilo_cabecalho:
        comandos += [
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8.5),
        ]
    tabela.setStyle(TableStyle(comandos))
    return tabela


def gerar_pdf_estatisticas(dados, resumo=None, agora=None):
    """Devolve os bytes do PDF com KPIs, situacoes, portes e rankings."""
    agora = agora or datetime.now()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=30 * mm, bottomMargin=16 * mm,
        title='Estatisticas - BusqueCNPJ',
        author='BusqueCNPJ',
        subject='Panorama estatistico da base de CNPJ',
        creator='BusqueCNPJ',
    )
    base = getSampleStyleSheet()
    titulo = ParagraphStyle('titulo', parent=base['Title'], fontSize=16,
                            leading=19, textColor=AZUL, spaceAfter=2)
    sub = ParagraphStyle('sub', parent=base['Normal'], fontSize=8.5,
                         leading=11, textColor=CINZA, spaceAfter=8)
    h2 = ParagraphStyle('h2', parent=base['Heading2'], fontSize=11.5,
                        leading=14, textColor=AZUL, spaceBefore=10, spaceAfter=4)
    corpo = ParagraphStyle('corpo', parent=base['Normal'], fontSize=8.5,
                           leading=11)

    elementos = [
        Paragraph('Estatísticas da base de CNPJs', titulo),
        Paragraph('BusqueCNPJ — inteligência comercial com IA e geolocalização. '
                  f'Gerado em {agora:%d/%m/%Y às %H:%M}.', sub),
    ]
    if resumo:
        elementos.append(Paragraph(f'<b>Recorte:</b> {resumo}', corpo))
        elementos.append(Spacer(1, 4))
    else:
        elementos.append(Paragraph('<b>Recorte:</b> base completa (matrizes).',
                                   corpo))
        elementos.append(Spacer(1, 4))

    totais = dados.get('totais') or {}
    kpis = [
        ['Matrizes', 'Ativas', 'Inaptas', 'Baixadas', 'Suspensas', 'UFs'],
        [_num(totais.get('total_matrizes')), _num(totais.get('total_ativas')),
         _num(totais.get('total_inaptas')), _num(totais.get('total_baixadas')),
         _num(totais.get('total_suspensas')), _num(totais.get('total_ufs'))],
    ]
    elementos.append(_tabela(kpis, [30 * mm] * 6))

    elementos.append(Paragraph('Situação cadastral', h2))
    linhas = [['Situação', 'Matrizes']]
    for s in dados.get('situacoes') or []:
        linhas.append([s.get('rotulo') or s.get('situacao') or '',
                       _num(s.get('total'))])
    elementos.append(_tabela(linhas, [95 * mm, 45 * mm]))

    elementos.append(Paragraph('Porte da empresa', h2))
    linhas = [['Porte', 'Matrizes']]
    for p in dados.get('portes') or []:
        linhas.append([p.get('porte') or '', _num(p.get('total'))])
    elementos.append(_tabela(linhas, [95 * mm, 45 * mm]))

    elementos.append(Paragraph('Top 10 atividades (CNAE)', h2))
    linhas = [['CNAE', 'Atividade', 'Matrizes']]
    for c in dados.get('top_cnaes') or []:
        linhas.append([str(c.get('cnae_principal') or ''),
                       Paragraph(str(c.get('nm_cnae') or ''), corpo),
                       _num(c.get('total'))])
    elementos.append(_tabela(linhas, [24 * mm, 100 * mm, 22 * mm]))

    elementos.append(Paragraph('Top 10 municípios', h2))
    linhas = [['Município', 'UF', 'Matrizes']]
    for m in dados.get('top_municipios') or []:
        linhas.append([str(m.get('nome_municipio') or ''),
                       str(m.get('uf') or ''), _num(m.get('total'))])
    elementos.append(_tabela(linhas, [90 * mm, 18 * mm, 32 * mm]))

    elementos.append(Paragraph('Empresas por ano de abertura', h2))
    linhas = [['Ano', 'Matrizes']]
    for a in dados.get('por_ano') or []:
        linhas.append([str(a.get('ano') or ''), _num(a.get('total'))])
    elementos.append(_tabela(linhas, [45 * mm, 45 * mm]))

    elementos.append(Spacer(1, 8))
    elementos.append(Paragraph(
        'Fonte: base pública de CNPJ da Receita Federal, agregada. '
        'Os números são de matrizes (estabelecimento principal).',
        ParagraphStyle('fonte', parent=corpo, fontSize=7.5, textColor=CINZA)))

    doc.build(elementos, onFirstPage=_cabecalho_rodape,
              onLaterPages=_cabecalho_rodape)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf
