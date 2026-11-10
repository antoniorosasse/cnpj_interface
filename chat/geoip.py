# cnpj_interface/chat/geoip.py
"""
Localizacao aproximada (cidade/UF/pais) dos IPs do log de acesso.

Regras:
  - nunca roda no request: quem chama e' `manage.py localizar_acessos`;
  - grava em chat.LocalizacaoIP (1 linha por IP), nao no LogAcesso;
  - cache por IP: localizacao mais nova que `revalidar_dias` nao e' consultada;
  - erro de rede/limite nao levanta: o IP fica para a proxima execucao.

A base .mmdb nao e' versionada (geoip/ esta no .gitignore). Tres formas de
colocar/atualizar o arquivo:
  1. scripts/atualizar_geoip.sh (download oficial, cron semanal);
  2. painel /admin/chat/logacesso/ -> "Baixar da MaxMind e atualizar"
     (roda o mesmo script pelo navegador, so superusuario);
  3. painel -> "Instalar arquivo enviado": aceita o .tar.gz do GeoLite2-City
     (o .zip "-CSV" nao serve, a fonte le o formato binario .mmdb).

Fonte configuravel em settings.GEOIP_FONTE (env GEOIP_FONTE):
  - 'maxmind' (padrao): GeoLite2-City.mmdb local. Nenhum IP sai do servidor,
    sem cota e sem rede. O arquivo vem de settings.GEOIP_CIDADE_MMDB
    (default: BASE_DIR/geoip/GeoLite2-City.mmdb) e e' atualizado por
    scripts/atualizar_geoip.sh (conta gratuita em maxmind.com).
  - 'ip-api': http://ip-api.com/batch, 100 IPs por POST, HTTP
  - 'ipapi':  https://ipapi.co/{ip}/json/, 1 IP por GET, HTTPS

As duas fontes HTTP entregam IP de visitante (dado pessoal, LGPD) a um
terceiro, e o plano gratuito do ip-api nem TLS tem. 'maxmind' evita isso.
A licenca do GeoLite2 exige o credito de ATRIBUICAO (rodape do site/admin).

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

import ipaddress
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from pathlib import Path

import requests
from django.conf import settings
from django.utils import timezone

from .models import LocalizacaoIP, LogAcesso

logger = logging.getLogger(__name__)


class GeoIPIndisponivel(Exception):
    """Fonte escolhida nao esta pronta (arquivo ausente, nome invalido)."""


FONTE_PADRAO = 'maxmind'
FONTES = ('maxmind', 'ip-api', 'ipapi')

# Atribuicao exigida pela licenca do GeoLite2 (texto exibido ao usuario,
# por isso com acentos). Usada no rodape do site e do admin.
ATRIBUICAO = (
    'Este produto inclui dados do GeoLite2 criados pela MaxMind, '
    'disponíveis em https://www.maxmind.com'
)

# Reader do MMDB aberto uma vez por processo (ver _leitor_maxmind)
_leitor = None

TIMEOUT = 8            # segundos por requisicao
LOTE_MAXIMO = 100      # limite do endpoint em lote do ip-api
CAMPOS_IP_API = 'status,message,country,countryCode,region,regionName,city,lat,lon'

# Quantos IPs localizar por execucao quando o comando nao informa --limite
LIMITE_PADRAO = 500

# Janela padrao de revalidacao (dias) de uma localizacao ja gravada
REVALIDAR_PADRAO = 30

# --- Base local (.mmdb): limites e tempo ---------------------------------

# Teto do upload manual pelo painel (o pacote City oficial tem ~27 MB)
UPLOAD_MAXIMO = 200 * 1024 * 1024

# Abaixo disso o .mmdb esta truncado (o GeoLite2-City real tem ~48 MB)
MMDB_MINIMO = 1_000_000

# Pacotes aceitos no upload do painel
UPLOAD_SUFIXOS = ('.tar.gz', '.tgz', '.gz')

# Tempo maximo do download/checagem disparado pelo painel (segundos).
# Fica abaixo do `--timeout 300` do gunicorn (cnpj.service): assim o
# Django ainda responde a mensagem de erro em vez de o worker ser
# morto no meio do download de ~50 MB.
TIMEOUT_SCRIPT = 240

# Diretorios padrao do sistema. Entram no PATH do script porque o
# `Environment=PATH=` dos .service de producao aponta so para o venv/bin e o
# systemd substitui (nao acrescenta) o PATH: sem isso `bash`, `curl` e `tar`
# nao existem dentro do gunicorn.
PATH_SISTEMA = (
    '/usr/local/sbin', '/usr/local/bin', '/usr/sbin', '/usr/bin',
    '/sbin', '/bin',
)

# Faixas que nenhuma base de geolocalizacao resolve: rede interna, loopback,
# link-local. Ficam fora da fila, senao apareceriam como "pendentes" sempre.
REDES_IGNORADAS = tuple(
    ipaddress.ip_network(rede) for rede in (
        '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
        '127.0.0.0/8', '169.254.0.0/16', '::1/128', 'fe80::/10', 'fc00::/7',
    )
)


def ip_consultavel(ip):
    """IP publico o bastante para ter localizacao? (False = rede interna)."""
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not any(endereco in rede for rede in REDES_IGNORADAS)


def fonte_ativa():
    """Fonte configurada (settings.GEOIP_FONTE), sempre em minusculas."""
    return (getattr(settings, 'GEOIP_FONTE', '') or FONTE_PADRAO).strip().lower()


def ips_para_localizar(dias=None, revalidar_dias=REVALIDAR_PADRAO, limite=None):
    """
    IPs distintos do log que ainda precisam de localizacao.

    Entram os IPs sem LocalizacaoIP e os que estao com localizacao mais velha
    que `revalidar_dias` (None = nao revalida, so os que faltam).
    """
    # O campo GenericIPAddressField normaliza '' para None: so o isnull importa
    qs = LogAcesso.objects.exclude(ip__isnull=True)
    if dias:
        qs = qs.filter(criado_em__gte=timezone.now() - timedelta(days=dias))
    # order_by() limpa o Meta.ordering: senao o DISTINCT traz criado_em no
    # SELECT e o mesmo IP voltaria uma vez por log
    ips = list(qs.order_by().values_list('ip', flat=True).distinct())
    ips = [ip for ip in ips if ip_consultavel(ip)]
    if not ips:
        return []

    conhecidos = dict(
        LocalizacaoIP.objects.filter(ip__in=ips).values_list('ip', 'atualizado_em')
    )
    vencido_antes = (
        timezone.now() - timedelta(days=revalidar_dias) if revalidar_dias else None
    )

    pendentes = []
    for ip in ips:
        quando = conhecidos.get(ip)
        if quando is None or (vencido_antes and quando < vencido_antes):
            pendentes.append(ip)

    return pendentes[:limite] if limite else pendentes


def _decimal(valor):
    """float/str -> Decimal(6 casas) ou None."""
    try:
        return Decimal(str(round(float(valor), 6)))
    except (TypeError, ValueError):
        return None


def _normalizar(item, fonte):
    """Converte a resposta de cada fonte no mesmo dict de LocalizacaoIP."""
    if fonte == 'ipapi':
        cidade = item.get('city') or ''
        regiao = item.get('region') or ''
        uf = item.get('region_code') or ''
        pais = item.get('country_name') or ''
        codigo = item.get('country_code') or ''
        lat, lon = item.get('latitude'), item.get('longitude')
    else:
        cidade = item.get('city') or ''
        regiao = item.get('regionName') or ''
        uf = item.get('region') or ''
        pais = item.get('country') or ''
        codigo = item.get('countryCode') or ''
        lat, lon = item.get('lat'), item.get('lon')
    return {
        'cidade': cidade[:120],
        'regiao': regiao[:120],
        'uf': uf[:40],
        'pais': pais[:60],
        'codigo_pais': codigo[:2],
        'latitude': _decimal(lat),
        'longitude': _decimal(lon),
        'fonte': fonte,
    }


def caminho_mmdb():
    """Caminho do arquivo .mmdb (settings.GEOIP_CIDADE_MMDB)."""
    padrao = Path(settings.BASE_DIR) / 'geoip' / 'GeoLite2-City.mmdb'
    configurado = getattr(settings, 'GEOIP_CIDADE_MMDB', '')
    return Path(configurado) if configurado else padrao


def _leitor_maxmind():
    """
    Reader do MMDB, aberto uma vez por processo.

    O geoip2 usa memory-map: o arquivo (~48 MB) nao e' copiado para a RAM,
    o sistema operacional pagina sob demanda.
    """
    global _leitor
    if _leitor is None:
        arquivo = caminho_mmdb()
        if not arquivo.exists():
            raise GeoIPIndisponivel(
                f'Base do MaxMind nao encontrada em {arquivo}. Rode '
                'scripts/atualizar_geoip.sh (conta gratuita em maxmind.com) '
                'ou use GEOIP_FONTE=ipapi.'
            )
        import geoip2.database  # import tardio: so quem usa maxmind carrega
        _leitor = geoip2.database.Reader(str(arquivo))
    return _leitor


def fechar_leitor():
    """Fecha o Reader aberto (limpeza em testes e scripts)."""
    global _leitor
    if _leitor is not None:
        _leitor.close()
        _leitor = None


def _normalizar_maxmind(registro):
    """Objeto devolvido por Reader.city() -> dict de LocalizacaoIP."""
    subdivisoes = registro.subdivisions
    uf = (subdivisoes[0].iso_code if subdivisoes else '') or ''
    regiao = (subdivisoes[0].name if subdivisoes else '') or ''
    return {
        'cidade': (registro.city.name or '')[:120],
        'regiao': regiao[:120],
        'uf': uf[:40],
        'pais': (registro.country.name or '')[:60],
        'codigo_pais': (registro.country.iso_code or '')[:2],
        'latitude': _decimal(registro.location.latitude),
        'longitude': _decimal(registro.location.longitude),
        'fonte': 'maxmind',
    }


def _consultar_maxmind(ips):
    """Consulta o banco local: sem rede, sem cota, nenhum IP sai daqui."""
    from geoip2.errors import AddressNotFoundError

    leitor = _leitor_maxmind()
    resultado = {}
    for ip in ips:
        try:
            registro = leitor.city(ip)
        except (AddressNotFoundError, ValueError) as erro:
            logger.info('maxmind sem dados para %s: %s', ip, erro)
            continue
        resultado[ip] = _normalizar_maxmind(registro)
    return resultado


def verificar_fonte(fonte=None):
    """
    Valida a fonte antes de comecar (erro claro em vez de "0 gravados").

    Devolve o nome normalizado; levanta GeoIPIndisponivel se nao der para usar.
    """
    fonte = (fonte or fonte_ativa()).lower()
    if fonte not in FONTES:
        raise GeoIPIndisponivel(
            f'GEOIP_FONTE invalida: {fonte!r} (use {FONTES})'
        )
    if fonte == 'maxmind' and not caminho_mmdb().exists():
        raise GeoIPIndisponivel(
            f'Base do MaxMind nao encontrada em {caminho_mmdb()}. Rode '
            'scripts/atualizar_geoip.sh (conta gratuita em maxmind.com) '
            'ou use GEOIP_FONTE=ipapi.'
        )
    return fonte


def _consultar_ip_api(ips):
    """POST http://ip-api.com/batch - ate 100 IPs por requisicao."""
    resultado = {}
    for inicio in range(0, len(ips), LOTE_MAXIMO):
        lote = ips[inicio:inicio + LOTE_MAXIMO]
        try:
            resposta = requests.post(
                'http://ip-api.com/batch',
                params={'fields': CAMPOS_IP_API},
                json=[{'query': ip} for ip in lote],
                timeout=TIMEOUT,
            )
            resposta.raise_for_status()
            dados = resposta.json()
        except (requests.RequestException, ValueError) as erro:
            logger.warning('ip-api falhou para %s IP(s): %s', len(lote), erro)
            continue
        for ip, item in zip(lote, dados):
            if item.get('status') == 'success':
                resultado[ip] = _normalizar(item, 'ip-api')
    return resultado


def _consultar_ipapi(ips):
    """GET https://ipapi.co/{ip}/json/ - um IP por requisicao."""
    resultado = {}
    for ip in ips:
        try:
            resposta = requests.get(
                f'https://ipapi.co/{ip}/json/', timeout=TIMEOUT,
            )
            resposta.raise_for_status()
            dados = resposta.json()
        except (requests.RequestException, ValueError) as erro:
            logger.warning('ipapi.co falhou para %s: %s', ip, erro)
            continue
        if dados.get('error'):
            logger.info('ipapi.co sem dados para %s: %s', ip, dados.get('reason'))
            continue
        resultado[ip] = _normalizar(dados, 'ipapi')
    return resultado


def consultar(ips, fonte=None):
    """Consulta a fonte ativa e devolve {ip: dict pronto para gravar}."""
    if not ips:
        return {}
    fonte = (fonte or fonte_ativa()).lower()
    if fonte not in FONTES:
        raise ValueError(f'GEOIP_FONTE invalida: {fonte!r} (use {FONTES})')
    if fonte == 'maxmind':
        return _consultar_maxmind(ips)
    if fonte == 'ipapi':
        return _consultar_ipapi(ips)
    return _consultar_ip_api(ips)


def localizar(ips, fonte=None):
    """
    Consulta e grava em LocalizacaoIP. Devolve (gravados, sem_dados).

    `update_or_create` porque o mesmo IP aparece em varias execucoes
    (revalidacao) - a linha e' unica por IP.
    """
    dados = consultar(ips, fonte=fonte)
    gravados = 0
    for ip, info in dados.items():
        LocalizacaoIP.objects.update_or_create(ip=ip, defaults=info)
        gravados += 1
    return gravados, len(ips) - gravados


def localizar_pendentes(
    dias=None, limite=LIMITE_PADRAO, revalidar_dias=REVALIDAR_PADRAO,
    dry_run=False, fonte=None,
):
    """
    Fluxo completo do comando: descobre os IPs, consulta e grava.

    Com `dry_run=True` para antes da consulta externa (nao faz rede).
    """
    pendentes = ips_para_localizar(
        dias=dias, revalidar_dias=revalidar_dias, limite=limite,
    )
    resumo = {
        'fonte': (fonte or fonte_ativa()),
        'pendentes': len(pendentes),
        'gravados': 0,
        'sem_dados': 0,
        'dry_run': dry_run,
        'ips': pendentes,
    }
    if dry_run or not pendentes:
        return resumo

    gravados, sem_dados = localizar(pendentes, fonte=fonte)
    resumo['gravados'] = gravados
    resumo['sem_dados'] = sem_dados
    return resumo


# --- Base local: estado, instalacao pelo painel e atualizacao -------------


def caminho_marca():
    """Arquivo com o Last-Modified da base remota (escrito pelo script)."""
    return caminho_mmdb().with_suffix('.last-modified')


def caminho_script():
    """Script oficial de download (scripts/atualizar_geoip.sh)."""
    return Path(settings.BASE_DIR) / 'scripts' / 'atualizar_geoip.sh'


def metadados_mmdb(caminho=None):
    """
    (database_type, build_epoch) do .mmdb, ou None se nao for MMDB valido.

    Abre um Reader proprio (nao o de cache): serve para validar o arquivo
    recebido ANTES de trocar o que esta em uso.
    """
    arquivo = Path(caminho) if caminho else caminho_mmdb()
    try:
        import geoip2.database
        with geoip2.database.Reader(str(arquivo)) as leitor:
            meta = leitor.metadata()
            return meta.database_type, int(meta.build_epoch)
    except Exception:   # ausente, truncado, invalido, sem permissao
        return None


def _data_build(epoch):
    """build_epoch -> 'dd/mm/aaaa' (None quando nao ha data)."""
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=dt_timezone.utc).strftime('%d/%m/%Y')


def estado_base():
    """
    Situacao da base local para o painel do admin (nunca levanta).

    Sem arquivo ou com arquivo invalido, `erro` diz o que fazer.
    """
    caminho = caminho_mmdb()
    marca = caminho_marca()
    estado = {
        'fonte': fonte_ativa(),
        'caminho': str(caminho),
        'nome': caminho.name,
        'pasta': str(caminho.parent),
        'existe': caminho.exists(),
        'tamanho_mb': None,
        'build': None,
        'tipo': None,
        'marca': None,
        'gravavel': False,
        'erro': None,
    }
    try:
        estado['gravavel'] = caminho.parent.is_dir() and os.access(
            caminho.parent, os.W_OK,
        )
    except OSError:
        estado['gravavel'] = False
    if marca.exists():
        try:
            estado['marca'] = marca.read_text(encoding='utf-8').strip() or None
        except OSError:
            estado['marca'] = None
    if not caminho.exists():
        estado['erro'] = (
            'Base não encontrada. Use "Baixar da MaxMind", envie o .tar.gz do '
            'GeoLite2-City ou rode scripts/atualizar_geoip.sh.'
        )
        return estado
    try:
        estado['tamanho_mb'] = round(caminho.stat().st_size / 1048576, 1)
    except OSError:
        pass
    meta = metadados_mmdb(caminho)
    if meta is None:
        estado['erro'] = (
            'O arquivo existe mas não abre como MMDB da MaxMind (truncado?).'
        )
    else:
        estado['tipo'], epoch = meta
        estado['build'] = _data_build(epoch)
    return estado


def _mmdb_do_pacote(nome, conteudo, pasta_tmp):
    """
    Extrai o .mmdb de um envio do painel (.mmdb puro ou .tar.gz).

    Devolve o caminho do arquivo extraido. Levanta ValueError com mensagem
    pronta para a tela quando o formato nao serve.
    """
    nome = Path(nome or 'arquivo').name
    baixo = nome.lower()
    if baixo.endswith('.zip'):
        raise ValueError(
            'Pacote .zip (CSV) não serve: a fonte maxmind lê o formato binário '
            '.mmdb. Baixe "GeoLite2-City" (tar.gz), não o "-CSV".'
        )
    if baixo.endswith('.mmdb'):
        alvo = Path(pasta_tmp) / 'enviado.mmdb'
        alvo.write_bytes(conteudo)
        return alvo
    if not baixo.endswith(UPLOAD_SUFIXOS):
        raise ValueError(
            'Envie o .mmdb ou o pacote .tar.gz do GeoLite2-City '
            f'(recebi "{nome}").'
        )
    origem = Path(pasta_tmp) / nome
    origem.write_bytes(conteudo)
    try:
        with tarfile.open(origem, mode='r:*') as pacote:
            membro = next(
                (m for m in pacote.getmembers()
                 if m.isfile() and m.name.lower().endswith('.mmdb')),
                None,
            )
            if membro is None:
                raise ValueError('Nenhum .mmdb dentro do pacote enviado.')
            # extractfile: so o .mmdb sai do pacote (sem extractall, sem
            # risco de caminho absoluto/../ dentro do tar)
            extraido = pacote.extractfile(membro)
            alvo = Path(pasta_tmp) / Path(membro.name).name
            with extraido, open(alvo, 'wb') as saida:
                shutil.copyfileobj(extraido, saida)
            return alvo
    except tarfile.TarError as erro:
        raise ValueError(f'Pacote inválido: {erro}') from erro


def instalar_arquivo(nome, conteudo):
    """
    Instala um .mmdb/.tar.gz enviado pela tela do admin.

    Valida tamanho e estrutura antes de trocar a base em uso; a troca e'
    atomica (os.replace) e fecha o Reader em cache. Devolve `estado_base()`
    atualizado. Levanta ValueError com mensagem para a tela.
    """
    if not conteudo:
        raise ValueError('Arquivo vazio.')
    if len(conteudo) > UPLOAD_MAXIMO:
        raise ValueError(
            f'Arquivo grande demais ({len(conteudo) / 1048576:.0f} MB); '
            f'o limite é {UPLOAD_MAXIMO // 1048576} MB.'
        )
    destino = caminho_mmdb()
    pasta_tmp = tempfile.mkdtemp(prefix='geoip-')
    try:
        novo = _mmdb_do_pacote(nome, conteudo, pasta_tmp)
        tamanho = novo.stat().st_size
        if tamanho < MMDB_MINIMO:
            raise ValueError(
                f'O .mmdb tem {tamanho / 1024:.0f} KB; abaixo de '
                f'{MMDB_MINIMO // 1024} KB o arquivo está truncado.'
            )
        if metadados_mmdb(novo) is None:
            raise ValueError('O arquivo não é um MMDB válido da MaxMind.')
        destino.parent.mkdir(parents=True, exist_ok=True)
        provisorio = destino.with_name(destino.name + '.novo')
        shutil.copyfile(novo, provisorio)
        os.replace(provisorio, destino)
        # A marca e' do download oficial. Sem ela o proximo
        # `atualizar_geoip.sh` baixa de novo e sincroniza a data de verdade.
        caminho_marca().unlink(missing_ok=True)
    finally:
        shutil.rmtree(pasta_tmp, ignore_errors=True)
    fechar_leitor()   # o proximo request abre a base nova
    return estado_base()


def ambiente_script():
    """
    Ambiente do `scripts/atualizar_geoip.sh` com PATH de sistema.

    O `Environment=PATH=` dos .service de producao aponta so para o venv/bin,
    e o systemd **substitui** o PATH (nao acrescenta): dentro do gunicorn nao
    existem `bash`, `curl` nem `tar`. Sem sanear, o painel devolvia 500
    (`FileNotFoundError: No such file or directory: 'bash'`) so no servidor -
    no terminal da mesma maquina o script rodava, porque o PATH era o completo.

    O venv/bin entra primeiro, os diretorios padrao do sistema depois e, por
    ultimo, o que veio herdado (sem repetir). Nenhuma outra variavel e'
    alterada.
    """
    ambiente = dict(os.environ)
    caminhos = [str(Path(settings.BASE_DIR) / 'venv' / 'bin'), *PATH_SISTEMA]
    herdado = ambiente.get('PATH') or ''
    caminhos += [
        pasta for pasta in herdado.split(os.pathsep)
        if pasta and pasta not in caminhos
    ]
    ambiente['PATH'] = os.pathsep.join(caminhos)
    return ambiente


def rodar_script(*opcoes, timeout=None):
    """
    Roda scripts/atualizar_geoip.sh (download/checagem na MaxMind).

    Devolve (ok, saida). Nao levanta por erro do script - a saida explica o
    motivo (401, 429, base ja atualizada). Levanta GeoIPIndisponivel se o
    script sumiu, se falta `bash`/`curl`/`tar` no PATH ou se estourou o tempo
    (todas as causas viram mensagem no painel, nunca erro 500).
    """
    script = caminho_script()
    if not script.exists():
        raise GeoIPIndisponivel(
            f'Script nao encontrado em {script} (scripts/atualizar_geoip.sh).'
        )
    limite = timeout or TIMEOUT_SCRIPT
    ambiente = ambiente_script()
    bash = shutil.which('bash', path=ambiente['PATH'])
    if bash is None:
        raise GeoIPIndisponivel(
            f'Nao achei o bash no PATH do servico ({ambiente["PATH"]}). '
            'Rode scripts/atualizar_geoip.sh pelo terminal do servidor.'
        )
    faltando = [
        nome for nome in ('curl', 'tar')
        if shutil.which(nome, path=ambiente['PATH']) is None
    ]
    if faltando:
        raise GeoIPIndisponivel(
            f'O script precisa de {", ".join(faltando)} e nao achei no PATH '
            'do servico. Instale o pacote ou rode o script pelo terminal.'
        )
    try:
        processo = subprocess.run(
            [bash, str(script), *opcoes],
            cwd=str(settings.BASE_DIR),
            capture_output=True, text=True, timeout=limite,
            env=ambiente,
        )
    except subprocess.TimeoutExpired as erro:
        raise GeoIPIndisponivel(
            f'A atualização passou de {limite}s e foi interrompida. '
            'Tente pelo terminal ou pelo cron.'
        ) from erro
    except OSError as erro:   # inclui FileNotFoundError e PermissionError
        raise GeoIPIndisponivel(
            f'Nao consegui executar o script: {erro}'
        ) from erro
    saida = '\n'.join(
        linha for linha in (processo.stdout + processo.stderr).splitlines()
        if linha.strip()
    )
    return processo.returncode == 0, saida
