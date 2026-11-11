# core/dossie_pdf.py
"""
Gerador do dossie em PDF usando ReportLab puro (nao depende de pango/cairo,
ao contrario do WeasyPrint, entao roda no servidor sem pacote de sistema).

Entra o dict montado por core.consultas_empresa.carregar_dossie; sai o PDF em
bytes, pronto para HttpResponse.

Regras do documento:
  - CNPJ sempre formatado, datas em dd/mm/aaaa, dinheiro em R$;
  - CPF de socio MASCARADO (LGPD): o dossie identifica o socio pelo nome e
    pela qualificacao, nao publica o CPF;
  - o rodape diz a fonte e a data de geracao e avisa que nao e certidao.

Sem acentos no codigo. O texto que vai para o papel tem acento.
"""

import re
from datetime import date, datetime
from io import BytesIO
from decimal import Decimal, InvalidOperation

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

AZUL = colors.HexColor('#0d3b66')
CINZA = colors.HexColor('#555555')
LINHA = colors.HexColor('#d9d9d9')
FUNDO = colors.HexColor('#f4f6f8')

MARGEM = 15 * mm
LARGURA_UTIL = A4[0] - 2 * MARGEM

ESTILO_TITULO = ParagraphStyle(
    'titulo', fontName='Helvetica-Bold', fontSize=18, leading=21, textColor=AZUL,
)
ESTILO_SUB = ParagraphStyle(
    'sub', fontName='Helvetica', fontSize=10.5, leading=13, textColor=CINZA,
)
ESTILO_SECAO = ParagraphStyle(
    'secao', fontName='Helvetica-Bold', fontSize=11, leading=14,
    textColor=colors.white, backColor=AZUL, borderPadding=(3, 5, 3, 5),
    spaceBefore=6, spaceAfter=4,
)
ESTILO_CELULA = ParagraphStyle(
    'celula', fontName='Helvetica', fontSize=8.5, leading=11,
)
ESTILO_ROTULO = ParagraphStyle(
    'rotulo', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=CINZA,
)
ESTILO_NOTA = ParagraphStyle(
    'nota', fontName='Helvetica', fontSize=7.5, leading=10, textColor=CINZA,
)
ESTILO_DESTAQUE = ParagraphStyle(
    'destaque', fontName='Helvetica-Bold', fontSize=12, leading=15,
)


def esc(valor, padrao='-'):
    """Escapa o minimo para o Paragraph (que interpreta <, > e &)."""
    texto = valor
    if texto is None or texto == '':
        return padrao
    texto = str(texto).strip()
    if not texto:
        return padrao
    return texto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def formatar_cnpj(valor):
    d = _digitos(valor)
    if len(d) != 14:
        return str(valor or '-')
    return f'{d[0:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}'


def mascarar_cpf(valor):
    """CPF (11 digitos) sai mascarado; CNPJ de socio PJ sai formatado."""
    d = _digitos(valor)
    if len(d) == 14:
        return formatar_cnpj(d)
    if len(d) == 11:
        return f'***.***.{d[6:9]}-**'
    return '-'


def formatar_cnpj_basico(valor):
    """
    A base entrega do socio PJ apenas o cnpj_basico (8 digitos, sem ordem/DV).
    Formata como 41.246.569 e deixa claro que e a raiz do CNPJ.
    """
    d = _digitos(valor)
    if len(d) == 8:
        return f'{d[0:2]}.{d[2:5]}.{d[5:8]}'
    return str(valor or '-')



def formatar_data(valor):
    if valor is None or valor == '':
        return '-'
    if isinstance(valor, (date, datetime)):
        return valor.strftime('%d/%m/%Y')
    return str(valor)


def formatar_moeda(valor):
    if valor is None or valor == '':
        return '-'
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return str(valor)
    inteiro, _, centavos = f'{numero:,.2f}'.partition('.')
    return 'R$ ' + inteiro.replace(',', '.') + ',' + centavos


def _tabela(linhas, larguras=None, zebra=True, cabecalho=None):
    """Tabela simples; linhas = [[Paragraph, Paragraph], ...]."""
    dados = list(linhas)
    if cabecalho:
        dados = [cabecalho] + dados
    tabela = Table(dados, colWidths=larguras or [35 * mm, LARGURA_UTIL - 35 * mm],
                   repeatRows=1 if cabecalho else 0)
    comandos = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.4, LINHA),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    if cabecalho:
        comandos += [
            ('BACKGROUND', (0, 0), (-1, 0), FUNDO),
        ]
    if zebra:
        inicio = 1 if cabecalho else 0
        for i in range(inicio, len(dados)):
            if (i - inicio) % 2 == 1:
                comandos.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#fafafa')))
    tabela.setStyle(TableStyle(comandos))
    return tabela


def _secao(titulo):
    return Paragraph(titulo, ESTILO_SECAO)


def _linhas_pares(pares, larguras=None):
    """pares = [(rotulo, valor), ...] -> tabela de duas colunas."""
    linhas = [
        [Paragraph(esc(rotulo), ESTILO_ROTULO), Paragraph(esc(valor), ESTILO_CELULA)]
        for rotulo, valor in pares
    ]
    return _tabela(linhas, larguras=larguras)


def _endereco(reg):
    """Endereco em uma linha, sem repetir separador quando o campo e vazio."""
    partes = []
    via = ' '.join(p for p in [reg.get('tip_logradouro'), reg.get('logradouro')] if p)
    if via:
        numero = reg.get('numero') or 'S/N'
        partes.append(f'{via}, {numero}')
    for campo in ('complemento', 'bairro'):
        if reg.get(campo):
            partes.append(str(reg[campo]))
    if reg.get('cep'):
        partes.append(f'CEP {reg["cep"]}')
    municipio = reg.get('municipio_descricao') or reg.get('nm_cidade_ext')
    uf = reg.get('uf')
    if municipio and uf:
        partes.append(f'{municipio}/{uf}')
    elif municipio:
        partes.append(str(municipio))
    pais = reg.get('pais_descricao')
    if pais and pais.upper() not in ('BRASIL', 'BRAZIL'):
        partes.append(str(pais))
    return ' - '.join(partes) or '-'


def _telefones(reg):
    fones = []
    for ddd, numero in (('ddd_1', 'tel_1'), ('ddd_2', 'tel_2')):
        if reg.get(numero):
            fones.append(f'({reg[ddd]}) {reg[numero]}' if reg.get(ddd) else reg[numero])
    if reg.get('fax'):
        prefixo = f'Fax ({reg["ddd_fax"]})' if reg.get('ddd_fax') else 'Fax'
        fones.append(f'{prefixo} {reg["fax"]}')
    return ' / '.join(fones) or '-'


def _cabecalho(empresa, agora):
    titulo = Paragraph('Dossiê cadastral de empresa', ESTILO_TITULO)
    razao = Paragraph(esc(empresa.get('razao_social'), '(sem razão social)'), ESTILO_DESTAQUE)
    cnpj = Paragraph(f'CNPJ {formatar_cnpj(empresa.get("cnpj_completo"))}', ESTILO_SUB)
    gerado = Paragraph(
        f'Emitido em {agora:%d/%m/%Y} às {agora:%H:%M} · fonte: base pública da Receita Federal',
        ESTILO_NOTA,
    )
    return [titulo, Spacer(1, 3 * mm), razao, cnpj, Spacer(1, 2 * mm), gerado]


def _bloco_situacao(empresa):
    situacao = empresa.get('situacao_descricao') or '-'
    motivo = _motivo_situacao(empresa)
    desde = formatar_data(empresa.get('dt_sit_cadastro'))
    texto = f'<b>Situação cadastral: {esc(situacao)}</b>'
    if motivo != '-' and situacao != 'ATIVA':
        texto += f' — motivo: {esc(motivo)}'
    if desde != '-':
        texto += f' (desde {desde})'
    cor = colors.HexColor('#1b7a3d') if situacao == 'ATIVA' else colors.HexColor('#a11c1c')
    estilo = ParagraphStyle('sit', fontName='Helvetica', fontSize=9.5, leading=12, textColor=cor)
    tabela = Table([[Paragraph(texto, estilo)]], colWidths=[LARGURA_UTIL])
    tabela.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, cor),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return tabela


def _motivo_situacao(empresa):
    """'SEM MOTIVO' (codigo 0) e ruido no papel: vira '-'. Tambem no bloco 1."""
    motivo = (empresa.get('motivo_sit_cadastro_descricao') or '').strip()
    if not motivo or motivo.upper() == 'SEM MOTIVO':
        return '-'
    return motivo


def _secao_identificacao(empresa):
    natureza = '-'
    if empresa.get('nat_juridica'):
        descricao = empresa.get('natureza_juridica_descricao') or ''
        natureza = f"{empresa['nat_juridica']} - {descricao}".strip(' -')
    qual_resp = '-'
    if empresa.get('qual_resp'):
        descricao = empresa.get('qual_resp_descricao') or ''
        qual_resp = f"{empresa['qual_resp']} - {descricao}".strip(' -')
    pares = [
        ('Razão social', empresa.get('razao_social')),
        ('Nome fantasia', empresa.get('nm_fantasia')),
        ('CNPJ', formatar_cnpj(empresa.get('cnpj_completo'))),
        ('Natureza jurídica', natureza),
        ('Porte', empresa.get('porte_empresa_descricao')),
        ('Capital social', formatar_moeda(empresa.get('capital_social'))),
        ('Data de abertura', formatar_data(empresa.get('dt_ini'))),
        ('Situação cadastral', empresa.get('situacao_descricao')),
        ('Data da situação', formatar_data(empresa.get('dt_sit_cadastro'))),
        ('Motivo da situação', _motivo_situacao(empresa)),
        ('Situação especial', empresa.get('sit_esp')),
        ('Data da situação especial', formatar_data(empresa.get('dt_sit_esp'))),
        ('Qualificação do responsável', qual_resp),
        ('Órgão público responsável', empresa.get('ente_fed_resp')),
    ]
    return [Spacer(1, 4 * mm), _secao('1. Identificação'), _linhas_pares(pares)]




def _secao_atividade(empresa):
    cnae = '-'
    if empresa.get('cnae_principal'):
        descricao = empresa.get('cnae_principal_descricao') or ''
        cnae = f"{empresa['cnae_principal']} - {descricao}".strip(' -')
    secundarios = str(empresa.get('cnae_secundario') or '')
    lista_sec = [c for c in secundarios.replace(',', ' ').split() if c.strip()]
    resumo_sec = f'{len(lista_sec)} atividade(s) secundária(s) registrada(s)'
    if lista_sec:
        resumo_sec += ': ' + ', '.join(lista_sec[:12])
        if len(lista_sec) > 12:
            resumo_sec += f' ... (+{len(lista_sec) - 12})'
    pares = [
        ('Atividade principal (CNAE)', cnae),
        ('Atividades secundárias', resumo_sec),
    ]
    return [Spacer(1, 4 * mm), _secao('2. Atividade econômica'), _linhas_pares(pares)]


def _secao_simples(empresa):
    pares = [
        ('Simples Nacional', 'Optante' if empresa.get('op_simples') == 'S' else 'Não optante'),
        ('Opção pelo Simples', formatar_data(empresa.get('dt_op_simples'))),
        ('Exclusão do Simples', formatar_data(empresa.get('dt_exc_simples'))),
        ('MEI', 'Optante' if empresa.get('op_mei') == 'S' else 'Não optante'),
        ('Opção pelo MEI', formatar_data(empresa.get('dt_op_mei'))),
        ('Exclusão do MEI', formatar_data(empresa.get('dt_exc_mei'))),
    ]
    return [Spacer(1, 4 * mm), _secao('3. Simples Nacional e MEI'), _linhas_pares(pares)]


def _secao_contato(empresa):
    pares = [
        ('Endereço', _endereco(empresa)),
        ('Telefones', _telefones(empresa)),
        ('E-mail', empresa.get('email')),
    ]
    return [Spacer(1, 4 * mm), _secao('4. Endereço e contato'), _linhas_pares(pares)]


def _secao_socios(socios):
    if not socios:
        corpo = Paragraph(
            'Nenhum sócio registrado na base para este CNPJ '
            '(comum em MEI, empresário individual e entidades públicas).',
            ESTILO_CELULA,
        )
        return [Spacer(1, 4 * mm), _secao('5. Quadro societário'), corpo]

    linhas = []
    for socio in socios:
        nome = esc(socio.get('nome'))
        if socio.get('nome_representante'):
            nome += f'<br/><font size="7">repr.: {esc(socio.get("nome_representante"))}</font>'
        if str(socio.get('id_tipo_socio')) == '1':
            raiz = formatar_cnpj_basico(socio.get('cnpj_cpf'))
            nome += f'<br/><font size="7">CNPJ {raiz}/****-**</font>'
        faixa = esc(socio.get('faixa_etaria_descricao'))
        if faixa.upper() == 'NAO INFORMADA':
            faixa = '-'
        linhas.append([
            Paragraph(nome, ESTILO_CELULA),
            Paragraph(esc(socio.get('qualificacao_descricao')), ESTILO_CELULA),
            Paragraph(formatar_data(socio.get('data_entrada')), ESTILO_CELULA),
            Paragraph(faixa, ESTILO_CELULA),
        ])
    cabecalho = [
        Paragraph('<b>Sócio</b>', ESTILO_CELULA),
        Paragraph('<b>Qualificação</b>', ESTILO_CELULA),
        Paragraph('<b>Entrada</b>', ESTILO_CELULA),
        Paragraph('<b>Faixa etária</b>', ESTILO_CELULA),
    ]
    larguras = [LARGURA_UTIL * 0.40, LARGURA_UTIL * 0.30,
                LARGURA_UTIL * 0.15, LARGURA_UTIL * 0.15]
    nota = Paragraph(
        'CPF de sócio pessoa física é omitido deste relatório (LGPD). '
        'O CNPJ de sócio pessoa jurídica é público e aparece na íntegra.',
        ESTILO_NOTA,
    )
    return [Spacer(1, 4 * mm), _secao('5. Quadro societário'),
            _tabela(linhas, larguras=larguras, cabecalho=cabecalho),
            Spacer(1, 1.5 * mm), nota]


def _secao_estabelecimentos(estabelecimentos, total_filiais, limite=25):
    linhas = []
    for est in estabelecimentos[:limite]:
        municipio = esc(est.get('municipio_descricao'), '-')
        if est.get('cd_pais') not in (None, 105):
            municipio = esc(est.get('nm_cidade_ext') or est.get('pais_descricao'), '-')
        linhas.append([
            Paragraph(formatar_cnpj(est.get('cnpj_completo')), ESTILO_CELULA),
            Paragraph(esc(est.get('tipo')), ESTILO_CELULA),
            Paragraph(esc(est.get('situacao_descricao')), ESTILO_CELULA),
            Paragraph(f'{municipio}/{esc(est.get("uf"), "-")}', ESTILO_CELULA),
            Paragraph(formatar_data(est.get('data_abertura')), ESTILO_CELULA),
        ])
    cabecalho = [
        Paragraph('<b>CNPJ</b>', ESTILO_CELULA),
        Paragraph('<b>Tipo</b>', ESTILO_CELULA),
        Paragraph('<b>Situação</b>', ESTILO_CELULA),
        Paragraph('<b>Município/UF</b>', ESTILO_CELULA),
        Paragraph('<b>Abertura</b>', ESTILO_CELULA),
    ]
    larguras = [LARGURA_UTIL * 0.30, LARGURA_UTIL * 0.14, LARGURA_UTIL * 0.16,
                LARGURA_UTIL * 0.26, LARGURA_UTIL * 0.14]
    total = len(estabelecimentos)
    if total > limite:
        resumo = (f'{total} estabelecimento(s): 1 matriz e {total_filiais} filial(is). '
                  f'Listadas as {limite} primeiras (por ordem do CNPJ).')
    else:
        resumo = f'{total} estabelecimento(s): 1 matriz e {total_filiais} filial(is).'
    return [Spacer(1, 4 * mm), _secao('6. Estabelecimentos (matriz e filiais)'),
            Paragraph(resumo, ESTILO_NOTA), Spacer(1, 1.5 * mm),
            _tabela(linhas, larguras=larguras, cabecalho=cabecalho)]


def gerar_pdf_dossie(dados, agora=None):
    """Recebe o dict de carregar_dossie e devolve o PDF em bytes."""
    empresa = dados.get('empresa') or {}
    estabelecimentos = dados.get('estabelecimentos') or []
    socios = dados.get('socios') or []
    total_filiais = dados.get('total_filiais', 0)
    agora = agora or datetime.now()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGEM,
        rightMargin=MARGEM,
        topMargin=MARGEM,
        bottomMargin=MARGEM,
        title=f"Dossie cadastral - {empresa.get('razao_social') or 'empresa'}",
        author='Consulta de CNPJ',
        subject='Dossie cadastral (dados publicos da Receita Federal)',
    )

    aviso = Paragraph(
        'Dados públicos da Receita Federal do Brasil (base CNPJ). '
        'Este relatório não é certidão nem substitui consulta oficial. '
        'Sócios pessoa física aparecem com CPF omitido (LGPD).',
        ESTILO_NOTA,
    )

    partes = []
    partes += _cabecalho(empresa, agora)
    partes += [Spacer(1, 3 * mm), _bloco_situacao(empresa)]
    partes += _secao_identificacao(empresa)
    partes += _secao_atividade(empresa)
    partes += _secao_simples(empresa)
    partes += _secao_contato(empresa)
    partes += _secao_socios(socios)
    partes += _secao_estabelecimentos(estabelecimentos, total_filiais)
    partes += [Spacer(1, 6 * mm), aviso]

    def _rodape(canvas, documento):
        canvas.saveState()
        canvas.setStrokeColor(LINHA)
        canvas.setLineWidth(0.4)
        canvas.line(MARGEM, 13 * mm, A4[0] - MARGEM, 13 * mm)
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(CINZA)
        canvas.drawString(MARGEM, 9 * mm, 'Dossiê cadastral · Consulta de CNPJ')
        canvas.drawRightString(A4[0] - MARGEM, 9 * mm, f'página {documento.page}')
        canvas.restoreState()

    doc.build(partes, onFirstPage=_rodape, onLaterPages=_rodape)
    return buffer.getvalue()

