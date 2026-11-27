"""
Exportacao de dados da lista em CSV/JSON, com amostra, creditos e registro.

Sem acentos no codigo.

Regras da v1:
- anonimo baixa uma amostra (EXPORTACAO_AMOSTRA_LINHAS) sem consumir credito;
- cliente com saldo baixa ate o saldo (ou o teto de linhas) e e' debitado no
  fim do streaming; CNPJ ja exportado nos ultimos 30 dias nao e' cobrado de novo;
- o status 'baixada' so e' gravado quando o gerador termina de entregar o
  arquivo: e' assim que sabemos se o cliente realmente baixou ou nao.
"""

import csv
import io
import json
import re
import logging
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from .consulta import iterar_linhas, iterar_proximidade
from .models import Exportacao
from . import desafio
from .servicos import (SaldoInsuficiente, aplicar_cookie_visitante,
                       cliente_do_usuario, creditos_necessarios, debitar, estornar,
                       configuracao, gravar_linhas, ip_do_request,
                       registrar_uso, visitante_id)

from core.proximidade import RAIO_MAX_KM, RAIO_MIN_KM, RAIO_PADRAO_KM, resolver_origem
from core.views import (SITUACOES, filtros_proximidade,
                        get_postgres_connection)
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

ORIGEM = 'lista'
TTL_EXPORTACAO = timedelta(minutes=30)

PROX_COLUNAS = (
    ('cnpj_completo', 'CNPJ'),
    ('razao_social', 'RAZAO_SOCIAL'),
    ('nm_fantasia', 'NOME_FANTASIA'),
    ('logradouro', 'LOGRADOURO'),
    ('numero', 'NUMERO'),
    ('bairro', 'BAIRRO'),
    ('uf', 'UF'),
    ('municipio_nome', 'MUNICIPIO'),
    ('telefone_1', 'TELEFONE'),
    ('email', 'EMAIL'),
    ('cnae_principal', 'CNAE'),
    ('atividade_principal', 'ATIVIDADE'),
    ('situacao_descricao', 'SITUACAO'),
    ('km', 'DISTANCIA_KM'),
)

COLUNAS = (
    ('cnpj_completo', 'CNPJ'),
    ('razao_social', 'RAZAO_SOCIAL'),
    ('nm_fantasia', 'NOME_FANTASIA'),
    ('situacao_descricao', 'SITUACAO'),
    ('data_inicio_atividade', 'INICIO_ATIVIDADE'),
    ('logradouro', 'LOGRADOURO'),
    ('numero', 'NUMERO'),
    ('bairro', 'BAIRRO'),
    ('cep', 'CEP'),
    ('uf', 'UF'),
    ('municipio_nome', 'MUNICIPIO'),
    ('telefone_1', 'TELEFONE'),
    ('email', 'EMAIL'),
    ('cnae_principal', 'CNAE'),
    ('atividade_principal', 'ATIVIDADE'),
    ('op_simples', 'SIMPLES'),
    ('op_mei', 'MEI'),
)


def formatar_cnpj(valor: str) -> str:
    """XX.XXX.XXX/XXXX-XX — com pontuacao o Excel nao come o zero a esquerda."""
    digitos = ''.join(c for c in (valor or '') if c.isdigit())
    if len(digitos) != 14:
        return valor or ''
    return f'{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}'


def formatar_cep(valor: str) -> str:
    digitos = ''.join(c for c in (valor or '') if c.isdigit())
    if len(digitos) != 8:
        return valor or ''
    return f'{digitos[:5]}-{digitos[5:]}'


def _filtros(request):
    def txt(nome, upper=False):
        valor = request.GET.get(nome, '').strip()
        return valor.upper() if upper else valor

    municipio = txt('municipio')
    municipio_id = txt('municipio_id')
    return {
        'razao_social': txt('razao_social', True),
        'nome_fantasia': txt('nome_fantasia', True),
        'cnpj': txt('cnpj').replace('.', '').replace('/', '').replace('-', ''),
        'uf': txt('uf', True),
        'cnae': txt('cnae'),
        'situacao': txt('situacao', True),
        'simples': txt('simples'),
        'mei': txt('mei'),
        'municipio_id': (int(municipio_id) if municipio_id.isdigit()
                         else (int(municipio) if municipio.isdigit() else None)),
    }


def _nome_arquivo(filtros, formato, amostra=False):
    partes = ['empresas']
    if filtros.get('uf'):
        partes.append(filtros['uf'].lower())
    if filtros.get('municipio_id'):
        partes.append(f'municipio-{filtros["municipio_id"]}')
    partes.append(timezone.localdate().isoformat())
    if amostra:
        partes.append('amostra')
    return '-'.join(partes) + '.' + formato


REGEX_LIMIT = re.compile(r'\bLIMIT\s+(\d+)\b', re.IGNORECASE)


def _aumentar_limit(sql: str, novo: int) -> str:
    """
    Eleva o LIMIT do SQL (o gerador costuma criar LIMIT 100 para listagens).

    Mexe so' na ULTIMA ocorrencia, que e' o LIMIT externo da consulta; um
    LIMIT dentro de subquery fica como esta.
    """
    if not novo or novo <= 0:
        return sql
    ocorrencias = list(REGEX_LIMIT.finditer(sql))
    if not ocorrencias:
        return sql
    ultima = ocorrencias[-1]
    atual = int(ultima.group(1))
    if atual >= novo:
        return sql
    return sql[:ultima.start()] + f'LIMIT {novo}' + sql[ultima.end():]


COLUNAS_NOME_CNAE = ('nm_cnae', 'cnae_nome', 'cnae_descricao', 'descricao_cnae',
                     'atividade', 'atividade_principal', 'cnae')
COLUNAS_COD_CNAE = ('cnae_codigo', 'cnae_cod', 'cnae_principal', 'cd_cnae')


def _enriquecer_cnae(colunas, linhas):
    """
    Acrescenta o CODIGO do CNAE quando a consulta trouxe so' a descricao.

    Vale para respostas antigas do chat (o SQL ja' gravado nao muda). Consulta
    c_cnaes pelo nome, em uma unica query, com timeout curto.
    """
    if not linhas or not colunas:
        return colunas, linhas
    por_nome = {c.lower(): c for c in colunas}
    if any(chave in por_nome for chave in COLUNAS_COD_CNAE):
        return colunas, linhas
    # Coluna chamada 'cnae' com valores numericos ja' e' o codigo.
    for chave in ('cnae',):
        coluna = por_nome.get(chave)
        if coluna and linhas:
            valores = [str(l.get(coluna)).strip() for l in linhas if l.get(coluna) is not None]
            if valores and all(v.isdigit() for v in valores):
                return colunas, linhas
    alvo = next((por_nome[k] for k in COLUNAS_NOME_CNAE if k in por_nome), None)
    if alvo is None:
        return colunas, linhas
    nomes = {str(linha.get(alvo)).strip() for linha in linhas
             if linha.get(alvo) and not str(linha.get(alvo)).strip().isdigit()}
    if not nomes:
        return colunas, linhas

    mapa = {}
    conn = None
    try:
        conn = get_postgres_connection()
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '3s'")
            cur.execute("SELECT cnae, nm_cnae FROM c_cnaes WHERE nm_cnae = ANY(%s)",
                        (list(nomes),))
            mapa = {nome: codigo for codigo, nome in cur.fetchall()}
    except Exception:
        logger.exception('Falha ao buscar codigo do CNAE para o export')
        return colunas, linhas
    finally:
        if conn:
            conn.close()
    if not mapa:
        return colunas, linhas

    posicao = list(colunas).index(alvo)
    novas = list(colunas)
    if 'cnae_codigo' not in por_nome:
        novas.insert(posicao + 1, 'cnae_codigo')
    for linha in linhas:
        linha['cnae_codigo'] = mapa.get(str(linha.get(alvo)).strip(), '')
    return novas, linhas


def _celula_csv(valor):
    """Neutraliza formula de planilha (CWE-1236) em dado vindo da base."""
    if isinstance(valor, str) and valor[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + valor
    return valor


def _valor(linha, chave):
    valor = linha.get(chave)
    if valor is None:
        return ''
    if isinstance(valor, str):
        return valor.replace('\r', ' ').replace('\n', ' ').strip()
    return valor


def _linhas_csv(linhas, colunas):
    total = 0
    buffer = io.StringIO()
    buffer.write('\ufeff')  # BOM: Excel pt-BR le acento e separador ;
    escritor = csv.writer(buffer, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    escritor.writerow([rotulo for _, rotulo in colunas])
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for linha in linhas:
        valores = []
        for chave, _ in colunas:
            valor = _valor(linha, chave)
            if chave == 'cnpj_completo':
                valor = formatar_cnpj(valor)
            elif chave == 'cep':
                valor = formatar_cep(valor)
            elif chave == 'km' and valor != '':
                valor = f'{float(valor):.2f}'
            valores.append(_celula_csv(valor))
        escritor.writerow(valores)
        total += 1
        if total % 500 == 0:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
    if buffer.tell():
        yield buffer.getvalue()


def _linhas_json(linhas, colunas):
    primeiro = True
    yield '['
    for linha in linhas:
        registro = {}
        for chave, _ in colunas:
            valor = _valor(linha, chave)
            if chave == 'cnpj_completo':
                valor = formatar_cnpj(valor)
            elif chave == 'cep':
                valor = formatar_cep(valor)
            elif chave == 'km' and valor != '':
                valor = round(float(valor), 2)
            registro[chave] = valor
        yield ('' if primeiro else ',') + json.dumps(
            registro, ensure_ascii=False, default=str)
        primeiro = False
    yield ']'


def _colunas(request, padrao=None):
    """Campos escolhidos (?campos=cnpj_completo,razao_social); padrao = todos."""
    padrao = padrao or COLUNAS
    # Aceita ?campos=a,b (links) e ?campos=a&campos=b (checkbox da tela).
    pedidos = [p.strip() for p in request.GET.getlist('campos') if p.strip()]
    if not pedidos:
        return padrao
    chaves = []
    for pedido in pedidos:
        chaves.extend(parte.strip() for parte in pedido.split(',') if parte.strip())
    permitidos = dict(padrao)
    escolhidos = tuple((c, permitidos[c]) for c in dict.fromkeys(chaves) if c in permitidos)
    return escolhidos or padrao


def exportar_lista(request):
    """
    GET /exportar/lista/?<filtros>&formato=csv|json

    O arquivo e' montado ANTES de debitar e de enviar: o valor cobrado e' o
    numero real de linhas, e abortar o download nao sai de graca.
    """
    formato = ('json' if (request.POST.get('formato') or
                          request.GET.get('formato')) == 'json' else 'csv')
    filtros = _filtros(request)
    colunas = _colunas(request)
    user = getattr(request, 'user', None)
    cliente = cliente_do_usuario(user)
    visitante = visitante_id(request)
    staff = bool(user is not None and user.is_authenticated
                 and (user.is_staff or user.is_superuser))

    # Bot que descarta cookie gera uma identidade nova a cada request e fura o
    # limite diario. Navegador normal sempre devolve o cookie depois da 1a visita.
    if cliente is None and not staff and not request.COOKIES.get('visitante_id'):
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'cookies',
            'mensagem': 'Ative os cookies e recarregue a página para gerar o arquivo.',
        }, status=403)

    if desafio.precisa_desafio(configuracao(), cliente, staff) and not desafio.ticket_valido(request):
        from urllib.parse import quote
        return redirect('/exportar/desafio/?next=' + quote(request.get_full_path()))

    estado_uso = registrar_uso(request, 'exportacao')
    if not estado_uso['permitido']:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'limite',
            'mensagem': (f"Você gerou {estado_uso['usados']} de "
                         f"{estado_uso['limite']} arquivos hoje. Crie uma conta "
                         "gratuita ou compre créditos para continuar."),
        }, status=429)

    if staff:
        limite = getattr(settings, 'EXPORTACAO_MAX_LINHAS', 50000)
        amostra = False
    elif cliente is None:
        limite = getattr(settings, 'EXPORTACAO_AMOSTRA_LINHAS', 20)
        amostra = True
    else:
        if cliente.saldo_creditos <= 0:
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': 'Sua conta está sem créditos. '
                            'Compre um pacote para exportar a lista completa.',
            }, status=402)
        limite = min(getattr(settings, 'EXPORTACAO_MAX_LINHAS', 50000),
                     cliente.saldo_creditos)
        amostra = False

    try:
        linhas = list(iterar_linhas(filtros, limite))
    except Exception:
        logger.exception('Falha ao preparar exportacao da lista')
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'erro',
            'mensagem': 'A consulta demorou demais para preparar o arquivo. '
                        'Tente um filtro mais específico.',
        }, status=503)

    cnpjs = [_valor(linha, 'cnpj_completo') for linha in linhas]
    exportacao = Exportacao.objects.create(
        cliente=cliente,
        usuario=user if (user is not None and user.is_authenticated) else None,
        visitante=visitante, origem=ORIGEM, formato=formato, filtros=filtros,
        n_linhas=len(linhas), amostra=amostra, ip=ip_do_request(request),
        expira_em=timezone.now() + TTL_EXPORTACAO,
    )

    creditos = 0
    if cliente is not None and not staff:
        devidos = creditos_necessarios(cliente, cnpjs)
        try:
            creditos = debitar(cliente, devidos, f'exportacao:{exportacao.pk}')
        except SaldoInsuficiente as exc:
            Exportacao.objects.filter(pk=exportacao.pk).update(status='falhou')
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': (f'Esta exportação custa {exc.necessarios} créditos e sua '
                             f'conta tem {exc.disponiveis}. Compre mais para baixar.'),
            }, status=402)
        gravar_linhas(cliente, cnpjs)

    def gerar():
        try:
            if formato == 'json':
                yield from _linhas_json(linhas, colunas)
            else:
                yield from _linhas_csv(linhas, colunas)
        except Exception:
            logger.exception('Falha ao enviar exportacao da lista %s', exportacao.pk)
            if creditos:
                estornar(cliente, creditos, f'estorno:exportacao:{exportacao.pk}')
            Exportacao.objects.filter(pk=exportacao.pk).update(
                status='falhou', creditos=0)
            raise
        else:
            Exportacao.objects.filter(pk=exportacao.pk).update(
                status='baixada', baixada_em=timezone.now(), creditos=creditos)

    tipo = 'application/json' if formato == 'json' else 'text/csv'
    resposta = StreamingHttpResponse(gerar(), content_type=f'{tipo}; charset=utf-8')
    resposta['Content-Disposition'] = (
        f'attachment; filename="{_nome_arquivo(filtros, formato, amostra)}"'
    )
    return aplicar_cookie_visitante(request, resposta)


def personalizar(request):
    """Tela para escolher os campos do arquivo antes de baixar.

    Com ?origem=... ela entra no modo proximidade (raio/matrizes/situacao) e
    lista os campos proprios daquela busca.
    """
    proximidade = bool(request.GET.get('origem', '').strip())
    padrao = PROX_COLUNAS if proximidade else COLUNAS
    escolhidos = {c for c, _ in _colunas(request, padrao)} if request.GET.get('campos') else None
    if proximidade:
        filtros = {
            'origem': request.GET.get('origem', '').strip(),
            'raio': request.GET.get('raio', '').strip(),
            'matrizes': request.GET.get('matrizes', '').strip(),
            'situacao': request.GET.get('situacao', '').strip(),
        }
        acao = 'exportacao:exportar_proximidade'
        voltar_nome = 'core:proximidade'
    else:
        filtros = _filtros(request)
        acao = 'exportacao:exportar_lista'
        voltar_nome = 'core:lista'
    return render(request, 'exportacao/campos.html', {
        'colunas': padrao,
        'escolhidos': escolhidos,
        'filtros': filtros,
        'acao': acao,
        'voltar_nome': voltar_nome,
        'voltar': ('?' + request.GET.urlencode()) if request.GET else '',
    })


def exportar_proximidade(request):
    """GET /exportar/proximidade/?origem=...&raio=..&matrizes=1&formato=csv"""
    formato = 'json' if request.GET.get('formato') == 'json' else 'csv'
    colunas = _colunas(request, PROX_COLUNAS)
    origem_txt = request.GET.get('origem', '').strip()
    try:
        raio = int(float(request.GET.get('raio', RAIO_PADRAO_KM)))
    except (TypeError, ValueError):
        raio = RAIO_PADRAO_KM
    raio = max(RAIO_MIN_KM, min(RAIO_MAX_KM, raio))
    situacao = request.GET.get('situacao', '').strip().upper()
    if situacao not in SITUACOES:
        situacao = ''
    if request.GET.get('ativas'):
        situacao = 'ATIVA'
    somente_matrizes = ('matrizes' in request.GET) if request.GET else True
    filtros_extra = filtros_proximidade(request)
    if filtros_extra['erro']:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'filtro',
            'mensagem': filtros_extra['erro'],
        }, status=400)

    user = getattr(request, 'user', None)
    cliente = cliente_do_usuario(user)
    visitante = visitante_id(request)
    staff = bool(user is not None and user.is_authenticated
                 and (user.is_staff or user.is_superuser))

    # Bot que descarta cookie gera uma identidade nova a cada request e fura o
    # limite diario. Navegador normal sempre devolve o cookie depois da 1a visita.
    if cliente is None and not staff and not request.COOKIES.get('visitante_id'):
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'cookies',
            'mensagem': 'Ative os cookies e recarregue a página para gerar o arquivo.',
        }, status=403)

    if desafio.precisa_desafio(configuracao(), cliente, staff) and not desafio.ticket_valido(request):
        from urllib.parse import quote
        return redirect('/exportar/desafio/?next=' + quote(request.get_full_path()))

    estado_uso = registrar_uso(request, 'exportacao')
    if not estado_uso['permitido']:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'limite',
            'mensagem': (f"Você gerou {estado_uso['usados']} de "
                         f"{estado_uso['limite']} arquivos hoje. Crie uma conta "
                         "gratuita ou compre créditos para continuar."),
        }, status=429)

    if staff:
        limite = getattr(settings, 'EXPORTACAO_MAX_LINHAS', 50000)
        amostra = False
    elif cliente is None:
        limite = getattr(settings, 'EXPORTACAO_AMOSTRA_LINHAS', 20)
        amostra = True
    else:
        if cliente.saldo_creditos <= 0:
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': 'Sua conta está sem créditos. '
                            'Compre um pacote para exportar a lista completa.',
            }, status=402)
        limite = min(getattr(settings, 'EXPORTACAO_MAX_LINHAS', 50000),
                     cliente.saldo_creditos)
        amostra = False

    if not origem_txt:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'origem',
            'mensagem': 'Informe a origem (CNPJ, CEP, cidade, endereço ou coordenada) '
                        'para exportar.',
        }, status=400)

    conn = get_postgres_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET statement_timeout = '5s'")
            origem, erro = resolver_origem(cur, origem_txt)
    except Exception:
        logger.exception('Falha ao resolver origem da exportacao: %s', origem_txt)
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'origem',
            'mensagem': 'Não foi possível localizar a origem agora. Tente de novo.',
        }, status=503)
    finally:
        conn.close()
    if not origem:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'origem',
            'mensagem': erro or 'Não foi possível localizar a origem informada.',
        }, status=400)

    try:
        linhas = list(iterar_proximidade(
            origem['latitude'], origem['longitude'], raio, somente_matrizes,
            SITUACOES[situacao] if situacao else None, limite,
            faixas_cnae=filtros_extra['faixas'],
            cnaes=filtros_extra['cnaes'],
            uf=filtros_extra['uf'],
            idade_anos=filtros_extra['idade'],
            capital_min=filtros_extra['capital'],
            usar_endereco=(request.GET.get('ordem') != 'cep'),
        ))
    except Exception:
        logger.exception('Falha ao preparar exportacao de proximidade')
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'erro',
            'mensagem': 'A consulta demorou demais para preparar o arquivo. '
                        'Reduza o raio ou filtre por situação.',
        }, status=503)

    cnpjs = [_valor(linha, 'cnpj_completo') for linha in linhas]
    exportacao = Exportacao.objects.create(
        cliente=cliente,
        usuario=user if (user is not None and user.is_authenticated) else None,
        visitante=visitante, origem='proximidade', formato=formato,
        filtros={'origem': origem_txt, 'raio': raio, 'matrizes': somente_matrizes,
                 'situacao': situacao,
                 'atividade': filtros_extra['atividade'],
                 'uf': filtros_extra['uf'],
                 'idade': filtros_extra['idade'],
                 'capital': filtros_extra['capital']},
        n_linhas=len(linhas), amostra=amostra, ip=ip_do_request(request),
        expira_em=timezone.now() + TTL_EXPORTACAO,
    )

    creditos = 0
    if cliente is not None and not staff:
        devidos = creditos_necessarios(cliente, cnpjs)
        try:
            creditos = debitar(cliente, devidos, f'exportacao:{exportacao.pk}')
        except SaldoInsuficiente as exc:
            Exportacao.objects.filter(pk=exportacao.pk).update(status='falhou')
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': (f'Esta exportação custa {exc.necessarios} créditos e sua '
                             f'conta tem {exc.disponiveis}. Compre mais para baixar.'),
            }, status=402)
        gravar_linhas(cliente, cnpjs)

    def gerar():
        try:
            if formato == 'json':
                yield from _linhas_json(linhas, colunas)
            else:
                yield from _linhas_csv(linhas, colunas)
        except Exception:
            logger.exception('Falha ao enviar exportacao de proximidade %s', exportacao.pk)
            if creditos:
                estornar(cliente, creditos, f'estorno:exportacao:{exportacao.pk}')
            Exportacao.objects.filter(pk=exportacao.pk).update(status='falhou', creditos=0)
            raise
        else:
            Exportacao.objects.filter(pk=exportacao.pk).update(
                status='baixada', baixada_em=timezone.now(), creditos=creditos)

    tipo = 'application/json' if formato == 'json' else 'text/csv'
    resposta = StreamingHttpResponse(gerar(), content_type=f'{tipo}; charset=utf-8')
    sufixo = '-amostra' if amostra else ''
    nome = (f'proximidade-{raio}km-{timezone.localdate().isoformat()}'
            f'{sufixo}.{formato}')
    resposta['Content-Disposition'] = f'attachment; filename="{nome}"'
    return aplicar_cookie_visitante(request, resposta)


def _ler(msg, nome, default=''):
    """Le um campo da mensagem no backend Postgres (objeto) ou Mongo (dict)."""
    if isinstance(msg, dict):
        return msg.get(nome, default)
    return getattr(msg, nome, default)


def _exportar_chat_proximidade(request, alvo, formato, maximo):
    """
    Exporta a ultima consulta de PROXIMIDADE do chat.

    O plano (origem/raio/atividades/filtros) fica gravado em Mensagem.plano;
    aqui ele e' reexecutado com o teto do export (sem depender de SQL, que a
    consulta de proximidade nao tem).
    """
    from chat.services.proximidade_service import (
        COLUNAS as PROX_COLUNAS_CHAT,
        ROTULOS as PROX_ROTULOS_CHAT,
        consultar_proximidade,
    )

    plano_salvo = _ler(alvo, 'plano') or {}
    if not isinstance(plano_salvo, dict):
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'sem_lista',
            'mensagem': 'A consulta de proximidade nao tem plano para reexecutar.',
        }, status=404)

    origem = plano_salvo.get('origem') or {}
    origem_texto = origem.get('texto') if isinstance(origem, dict) else None
    if not origem_texto:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'origem',
            'mensagem': 'A consulta de proximidade nao guardou a origem.',
        }, status=400)

    plano = {
        'origem': origem_texto,
        'usar_contexto': False,
        'raio_km': plano_salvo.get('raio_km') or 10,
        'atividades': plano_salvo.get('atividades') or [],
        'situacao': plano_salvo.get('situacao') or 'todas',
        'uf': plano_salvo.get('uf'),
        'idade_min_anos': plano_salvo.get('idade_min_anos'),
        'capital_min': plano_salvo.get('capital_min'),
        'limite': maximo,
    }
    resultado = consultar_proximidade(plano, limite_max=maximo)
    if not resultado.get('ok'):
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'consulta',
            'mensagem': resultado.get('erro') or 'Nao foi possivel reexecutar a consulta.',
        }, status=503)

    linhas = list(resultado.get('rows') or [])[:maximo]
    colunas = list(PROX_COLUNAS_CHAT)
    user = getattr(request, 'user', None)
    staff = bool(user is not None and user.is_authenticated
                 and (user.is_staff or user.is_superuser))
    cliente = cliente_do_usuario(user)
    if not staff:
        if cliente is None:
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': 'Sua conta ainda nao tem creditos. '
                            'Compre um pacote para baixar a lista do chat.',
            }, status=402)
        if cliente.saldo_creditos < len(linhas):
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': (f'Esta lista tem {len(linhas)} linhas e sua conta tem '
                             f'{cliente.saldo_creditos} creditos. Compre mais para baixar.'),
            }, status=402)

    try:
        if formato == 'json':
            conteudo = json.dumps(linhas, ensure_ascii=False, default=str)
            tipo = 'application/json'
        else:
            buffer = io.StringIO()
            buffer.write('\ufeff')
            escritor = csv.writer(buffer, delimiter=';', quoting=csv.QUOTE_MINIMAL)
            escritor.writerow([PROX_ROTULOS_CHAT.get(c, c) for c in colunas])
            for linha in linhas:
                escritor.writerow([_valor(linha, c) for c in colunas])
            conteudo = buffer.getvalue()
            tipo = 'text/csv'
    except Exception:
        logger.exception('Falha ao serializar exportacao de proximidade do chat')
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'erro', 'mensagem': 'Nao foi possivel montar o arquivo agora.',
        }, status=500)

    exportacao = Exportacao.objects.create(
        cliente=cliente, usuario=user, visitante='', origem='chat', formato=formato,
        sql='', filtros={'plano': plano_salvo}, n_linhas=len(linhas), amostra=False,
        ip=ip_do_request(request), expira_em=timezone.now() + TTL_EXPORTACAO,
    )
    creditos = 0
    if not staff:
        try:
            creditos = debitar(cliente, len(linhas), f'exportacao:{exportacao.pk}')
        except SaldoInsuficiente as exc:
            Exportacao.objects.filter(pk=exportacao.pk).update(status='falhou')
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': (f'Esta lista custa {exc.necessarios} creditos e sua conta '
                             f'tem {exc.disponiveis}. Compre mais para baixar.'),
            }, status=402)

    Exportacao.objects.filter(pk=exportacao.pk).update(
        status='baixada', baixada_em=timezone.now(), creditos=creditos)

    resposta = HttpResponse(conteudo, content_type=f'{tipo}; charset=utf-8')
    resposta['Content-Disposition'] = (
        f'attachment; filename="chat-proximidade-'
        f'{timezone.localdate().isoformat()}.{formato}"')
    return resposta


def _alvo_lista_chat(request, user):
    """Devolve (sql_original, pergunta, cliente) da ultima lista SQL do chat."""
    from chat import repo
    from chat.views import SESSION_CONVERSA_ID

    cid = request.session.get(SESSION_CONVERSA_ID)
    conversa = repo.buscar_conversa(cid, user=user) if cid else None
    if conversa is None:
        return None, None, None
    conversa_id = conversa.pk if not isinstance(conversa, dict) else (
        conversa.get('pk') or conversa.get('id') or conversa.get('_id'))
    mensagens = list(repo.mensagens_da_conversa(conversa_id))
    alvo = None
    for msg in reversed(mensagens):
        if _ler(msg, 'papel') != 'assistant':
            continue
        if str(_ler(msg, 'sql') or '').strip():
            alvo = msg
            break
        if _ler(msg, 'plano'):
            return None, None, None  # export completo de proximidade: fase 2
    if alvo is None:
        return None, None, None
    posicao = mensagens.index(alvo)
    pergunta = ''
    for anterior in reversed(mensagens[:posicao]):
        if _ler(anterior, 'papel') == 'user':
            pergunta = str(_ler(anterior, 'conteudo') or '').strip()
            break
    from .servicos import cliente_do_usuario
    return str(_ler(alvo, 'sql')).strip(), pergunta, cliente_do_usuario(user)


def exportar_chat_completo(request):
    """Cria exportacao completa (background) da ultima lista do chat."""
    from chat.services.embedding_service import injetar_vetor, tem_placeholder
    from chat.services.sql_executor import contar_linhas
    from chat.services.sql_validator import SQLValidationError, validar_sql
    from .async_export import (creditos_exportacao_completa, criar_job,
                               iniciar_job, limite_para)
    from .servicos import ip_do_request

    formato = 'json' if request.GET.get('formato') == 'json' else 'csv'
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'login',
            'mensagem': 'Entre na sua conta para baixar a lista completa.',
        }, status=403)
    staff = bool(user.is_staff or user.is_superuser)
    sql, pergunta, cliente = _alvo_lista_chat(request, user)
    if not sql:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'sem_lista',
            'mensagem': 'A última resposta não tem uma lista SQL para exportar.',
        }, status=404)
    cap = limite_para(user)
    try:
        validation = validar_sql(sql, max_rows=cap)
    except SQLValidationError as exc:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'sql', 'mensagem': f'SQL inválido: {exc}',
        }, status=400)
    sql_exec = validation.sql
    if tem_placeholder(sql_exec):
        sql_exec, erro = injetar_vetor(sql_exec, pergunta)
        if erro:
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sql', 'mensagem': erro,
            }, status=400)
    total = contar_linhas(sql_exec, timeout_ms=8000)
    if total is None:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'consulta',
            'mensagem': 'Não foi possível calcular o total desta lista. '
                        'Refine a consulta (cidade/UF/atividade) e tente de novo.',
        }, status=400)
    if total > cap:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'limite',
            'mensagem': (f'Esta lista tem {total} linhas; o limite para exportação '
                         f'completa é {cap}. Refine a consulta.'),
        }, status=400)
    custo = 0 if staff else creditos_exportacao_completa(total)
    if not staff:
        if cliente is None:
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': 'Sua conta não tem créditos para exportar esta lista.',
            }, status=402)
        if cliente.saldo_creditos < custo:
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': (f'Esta exportação custa {custo} créditos ({total} linhas) '
                             f'e sua conta tem {cliente.saldo_creditos}.'),
            }, status=402)

    if request.method != 'POST':
        return render(request, 'exportacao/completa_confirmar.html', {
            'total': total,
            'custo': custo,
            'saldo': cliente.saldo_creditos if cliente else None,
            'staff': staff,
            'formato': formato,
            'cap': cap,
            'pergunta': pergunta,
            'por_linha': settings.EXPORTACAO_COMPLETA_CREDITOS_POR_LINHA,
        })

    job = criar_job(user, cliente, sql, formato, pergunta,
                    ip_do_request(request), total)
    iniciar_job(job.pk)
    return redirect('exportacao:completa_status', token=job.token)


def exportacao_completa_status(request, token):
    job = Exportacao.objects.filter(token=token, completo=True).first()
    if job is None:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'sem_lista', 'mensagem': 'Exportação não encontrada.',
        }, status=404)
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or (
            job.usuario_id and job.usuario_id != user.pk and not user.is_staff):
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'login', 'mensagem': 'Sem permissão para esta exportação.',
        }, status=403)
    return render(request, 'exportacao/completa_status.html', {'job': job})


def exportacao_completa_baixar(request, token):
    from django.http import FileResponse, Http404
    from pathlib import Path
    from django.conf import settings

    job = Exportacao.objects.filter(token=token, completo=True).first()
    if job is None:
        raise Http404()
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or (
            job.usuario_id and job.usuario_id != user.pk and not user.is_staff):
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'login', 'mensagem': 'Sem permissão para esta exportação.',
        }, status=403)
    if job.status != 'pronto' or not job.arquivo_nome:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'consulta', 'mensagem': 'O arquivo ainda não está pronto.',
        }, status=409)
    caminho = Path(getattr(settings, 'EXPORTACAO_COMPLETA_DIR',
                           Path(settings.BASE_DIR) / 'exportacoes')) / job.arquivo_nome
    if not caminho.is_file():
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'erro', 'mensagem': 'Arquivo expirado ou removido.',
        }, status=410)
    tipo = 'application/json' if job.formato == 'json' else 'text/csv'
    resposta = FileResponse(open(caminho, 'rb'), content_type=f'{tipo}; charset=utf-8')
    resposta['Content-Disposition'] = (
        f'attachment; filename="{job.arquivo_nome}"')
    Exportacao.objects.filter(pk=job.pk).update(
        status='baixada', baixada_em=timezone.now())
    return resposta


def exportar_chat(request):
    """
    Baixa a lista da ULTIMA resposta com tabela na conversa aberta.

    O SQL ja' foi gerado e validado quando o chat respondeu; aqui ele e'
    revalidado (defesa em profundidade) e reexecutado. O navegador nunca envia
    SQL. Custo: 1 credito por linha (sem dedup, porque a lista e' ad hoc).
    """
    from chat import repo
    from chat.services.sql_executor import executar_sql
    from chat.services.embedding_service import injetar_vetor, tem_placeholder
    from chat.services.sql_validator import (SQLValidationError, ValidationResult,
                                             validar_sql)
    from chat.views import SESSION_CONVERSA_ID

    formato = 'json' if request.GET.get('formato') == 'json' else 'csv'
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'login',
            'mensagem': 'Entre na sua conta para baixar a lista do chat.',
        }, status=403)

    staff = bool(user is not None and user.is_authenticated
                 and (user.is_staff or user.is_superuser))
    cid = request.session.get(SESSION_CONVERSA_ID)
    conversa = repo.buscar_conversa(cid, user=user) if cid else None
    if conversa is None:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'conversa',
            'mensagem': 'Não há uma conversa aberta. Faça uma pergunta no chat primeiro.',
        }, status=404)

    conversa_id = conversa.pk if not isinstance(conversa, dict) else (
        conversa.get('pk') or conversa.get('id') or conversa.get('_id'))
    mensagens = repo.mensagens_da_conversa(conversa_id)
    maximo = (getattr(settings, 'EXPORTACAO_MAX_LINHAS', 50000) if staff
              else getattr(settings, 'EXPORTACAO_CHAT_MAX_LINHAS', 5000))

    # A ultima resposta EXPORTAVEL da conversa: pode ser uma lista SQL ou uma
    # consulta de proximidade (plano). A mais recente vence, independente do
    # tipo - senao um SQL antigo seria exportado no lugar do recorte geo novo.
    alvo = None
    alvo_plano = None
    for msg in reversed(list(mensagens)):
        if _ler(msg, 'papel') != 'assistant':
            continue
        if str(_ler(msg, 'sql') or '').strip():
            alvo = msg
            break
        if _ler(msg, 'plano'):
            alvo_plano = msg
            break

    if alvo is None:
        if alvo_plano is not None:
            return _exportar_chat_proximidade(request, alvo_plano, formato, maximo)
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'sem_lista',
            'mensagem': 'A última resposta do chat não tem lista para baixar.',
        }, status=404)

    sql = str(_ler(alvo, 'sql')).strip()
    try:
        validation = validar_sql(sql, max_rows=maximo)
    except SQLValidationError as exc:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'sql', 'mensagem': f'A consulta não passou na validação: {exc}',
        }, status=400)

    # O SQL guardado mantem o placeholder %s do vetor: injeta de novo a partir
    # da pergunta que gerou a resposta (sem isso o Postgres ve um "%" solto).
    sql_final = sql
    if tem_placeholder(sql):
        pergunta = ''
        posicao = list(mensagens).index(alvo)
        for anterior in reversed(list(mensagens)[:posicao]):
            if _ler(anterior, 'papel') == 'user':
                pergunta = str(_ler(anterior, 'conteudo') or '').strip()
                break
        sql_final, erro_vetor = injetar_vetor(sql, pergunta)
        if erro_vetor:
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sql', 'mensagem': erro_vetor,
            }, status=400)

    # O gerador costuma criar LIMIT 100 nas listagens; no arquivo vale o teto
    # do export (a tela continua com o dela).
    sql_final = _aumentar_limit(sql_final, maximo)
    if sql_final != validation.sql:
        validation = ValidationResult(
            sql=sql_final,
            original_sql=validation.original_sql,
            teve_limit=validation.teve_limit,
            limit_valor=maximo,
        )

    execucao = executar_sql(validation, max_rows=maximo)
    if not execucao.get('ok'):
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'sql',
            'mensagem': f"Não foi possível executar a consulta: {execucao.get('erro', '')}",
        }, status=400)

    colunas = execucao.get('colunas') or []
    linhas = execucao.get('rows') or []
    colunas, linhas = _enriquecer_cnae(colunas, linhas)
    if not colunas:
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'sem_lista', 'mensagem': 'A última resposta não devolveu colunas.',
        }, status=404)

    cliente = cliente_do_usuario(user)
    if not staff:
        if cliente is None:
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': 'Sua conta ainda não tem créditos. '
                            'Compre um pacote para baixar a lista do chat.',
            }, status=402)
        if cliente.saldo_creditos < len(linhas):
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': (f'Esta lista tem {len(linhas)} linhas e sua conta tem '
                             f'{cliente.saldo_creditos} créditos. Compre mais para baixar.'),
            }, status=402)

    # Serializa ANTES de debitar: se algo falhar, ninguem paga.
    try:
        if formato == 'json':
            conteudo = json.dumps(linhas, ensure_ascii=False, default=str)
            tipo = 'application/json'
        else:
            buffer = io.StringIO()
            buffer.write('\ufeff')
            escritor = csv.writer(buffer, delimiter=';', quoting=csv.QUOTE_MINIMAL)
            escritor.writerow(colunas)
            for linha in linhas:
                escritor.writerow([_valor(linha, c) for c in colunas])
            conteudo = buffer.getvalue()
            tipo = 'text/csv'
    except Exception:
        logger.exception('Falha ao serializar exportacao do chat')
        return render(request, 'exportacao/bloqueado.html', {
            'motivo': 'erro', 'mensagem': 'Não foi possível montar o arquivo agora.',
        }, status=500)

    exportacao = Exportacao.objects.create(
        cliente=cliente, usuario=user, visitante='', origem='chat', formato=formato,
        sql=sql, filtros={}, n_linhas=len(linhas), amostra=False,
        ip=ip_do_request(request), expira_em=timezone.now() + TTL_EXPORTACAO,
    )
    creditos = 0
    if not staff:
        try:
            creditos = debitar(cliente, len(linhas), f'exportacao:{exportacao.pk}')
        except SaldoInsuficiente as exc:
            Exportacao.objects.filter(pk=exportacao.pk).update(status='falhou')
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'sem_creditos',
                'mensagem': (f'Esta lista custa {exc.necessarios} créditos e sua conta '
                             f'tem {exc.disponiveis}. Compre mais para baixar.'),
            }, status=402)

    Exportacao.objects.filter(pk=exportacao.pk).update(
        status='baixada', baixada_em=timezone.now(), creditos=creditos)

    resposta = HttpResponse(conteudo, content_type=f'{tipo}; charset=utf-8')
    resposta['Content-Disposition'] = (
        f'attachment; filename="chat-{timezone.localdate().isoformat()}.{formato}"')
    return resposta


def desafio_export(request):
    """Pagina do desafio (Turnstile) que emite o ticket do export anonimo."""
    from .servicos import configuracao as cfg_uso
    cfg = cfg_uso()
    proximo = request.POST.get('next') or request.GET.get('next') or '/exportar/lista/'
    if not proximo.startswith('/exportar/'):
        proximo = '/exportar/lista/'
    ativo = bool(cfg.desafio_ativo and desafio.configurado())

    erro = None
    if request.method == 'POST':
        if not ativo:
            resposta = redirect(proximo)
            desafio.emitir_ticket(resposta, visitante_id(request), cfg.desafio_validade_min)
            return resposta
        if not desafio.pode_emitir_ticket(request, cfg.desafio_tickets_ip_hora):
            return render(request, 'exportacao/bloqueado.html', {
                'motivo': 'limite',
                'mensagem': 'Muitas solicitações de acesso deste endereço. '
                            'Tente de novo em uma hora.',
            }, status=429)
        token = request.POST.get('cf-turnstile-response', '')
        if desafio.verificar_turnstile(token, ip_do_request(request)):
            resposta = redirect(proximo)
            desafio.emitir_ticket(resposta, visitante_id(request), cfg.desafio_validade_min)
            return resposta
        erro = 'Não foi possível confirmar que a sua sessão é humana. Tente novamente.'

    return render(request, 'exportacao/desafio.html', {
        'site_key': getattr(settings, 'TURNSTILE_SITE_KEY', ''),
        'ativo': ativo,
        'proximo': proximo,
        'erro': erro,
        'validade': cfg.desafio_validade_min,
    })
