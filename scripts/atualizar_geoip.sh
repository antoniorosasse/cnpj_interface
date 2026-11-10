#!/usr/bin/env bash
#
# Baixa/atualiza o GeoLite2 (padrao: City) da MaxMind.
#
# Uso:
#   scripts/atualizar_geoip.sh            # baixa so se a base mudou
#   scripts/atualizar_geoip.sh --checar   # so HEAD: diz se ha base nova
#   scripts/atualizar_geoip.sh --forcar   # baixa mesmo sem mudanca
#
# Credenciais (conta gratuita em https://www.maxmind.com): defina
# MAXMIND_ACCOUNT_ID e MAXMIND_LICENSE_KEY no ambiente ou no .env do projeto.
# Outra edicao: MAXMIND_EDICAO=GeoLite2-Country (default GeoLite2-City).
#
# O que este script respeita da documentacao da MaxMind:
#   - o link e' um permalink com redirect (R2 presigned URL): curl usa -L.
#     Firewall/proxy precisa liberar HTTPS 443 para
#     mm-prod-geoip-databases.a2649acb697e2c09b632799562c076f2.r2.cloudflarestorage.com
#   - HEAD no permalink devolve Last-Modified e NAO conta na cota de
#     downloads: so baixamos quando muda (data fica em <edicao>.last-modified)
#   - 429 = cota/limite de requisicoes da MaxMind: esperar e tentar depois
#   - 401/403 = conta/licenca: conferir Account ID e License Key
#   - a troca do .mmdb e' atomica (mv no mesmo filesystem), entao um processo
#     com o Reader aberto continua no inode antigo
#
# Automatico: crontab duas vezes por semana (a MaxMind publica tercas e
# sextas), por exemplo:
#   51 0 * * 1,4  cd ~/cnpj_interface && scripts/atualizar_geoip.sh >> geoip/atualizacao.log 2>&1
#   (geoip/ ja esta no .gitignore; o log fica junto da base, sem sujar o repo)
#
# Alternativa oficial para o formato binario: o programa `geoipupdate`
# (https://github.com/maxmind/geoipupdate) com GeoIP.conf; ele faz a mesma
# checagem de data e o download. Este script evita instalar pacote extra.
#
# Sem acentos no codigo. Comentarios em PT-BR sem acento.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EDICAO="${MAXMIND_EDICAO:-GeoLite2-City}"
DESTINO="${RAIZ}/geoip"
ARQUIVO="${DESTINO}/${EDICAO}.mmdb"
MARCA="${DESTINO}/${EDICAO}.last-modified"
URL="https://download.maxmind.com/geoip/databases/${EDICAO}/download?suffix=tar.gz"

# 1 = baixa se mudou, 0 = so checa, 2 = baixa sempre
MODO=1

for arg in "$@"; do
    case "$arg" in
        --checar) MODO=0 ;;
        --forcar) MODO=2 ;;
        -h|--help)
            awk 'NR > 2 && /^#/ { sub(/^# ?/, ""); print; next }
                 NR > 2 && !/^#/ { exit }' "$0"
            exit 0
            ;;
        *)
            echo "argumento desconhecido: $arg (use --checar, --forcar ou --help)" >&2
            exit 2
            ;;
    esac
done

# Credenciais: ambiente tem prioridade; senao, le do .env do projeto
if [[ -f "${RAIZ}/.env" ]]; then
    : "${MAXMIND_ACCOUNT_ID:=$(grep -E '^MAXMIND_ACCOUNT_ID=' "${RAIZ}/.env" | tail -1 | cut -d= -f2-)}"
    : "${MAXMIND_LICENSE_KEY:=$(grep -E '^MAXMIND_LICENSE_KEY=' "${RAIZ}/.env" | tail -1 | cut -d= -f2-)}"
fi

if [[ -z "${MAXMIND_ACCOUNT_ID:-}" || -z "${MAXMIND_LICENSE_KEY:-}" ]]; then
    echo "erro: defina MAXMIND_ACCOUNT_ID e MAXMIND_LICENSE_KEY (ambiente ou .env)." >&2
    echo "      conta gratuita: https://www.maxmind.com/en/geolite2/signup" >&2
    exit 1
fi

mkdir -p "${DESTINO}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

# --- 1. HEAD no permalink: data da base remota (nao consome cota) --------
codigo="$(curl -sSIL -u "${MAXMIND_ACCOUNT_ID}:${MAXMIND_LICENSE_KEY}" \
    -D "${TMP}/head.txt" -o /dev/null -w '%{http_code}' "${URL}" || true)"

case "$codigo" in
    200) ;;
    429)
        echo "erro: HTTP 429 - limite de downloads/requisicoes da MaxMind." >&2
        echo "      espere e tente mais tarde (ou use geoipupdate)." >&2
        exit 1
        ;;
    401|403)
        echo "erro: HTTP ${codigo} - confira MAXMIND_ACCOUNT_ID/MAXMIND_LICENSE_KEY" >&2
        echo "      e se a licenca da conta esta ativa." >&2
        exit 1
        ;;
    000|'')
        echo "aviso: nao consegui o HEAD (rede/firewall?). Tentando baixar." >&2
        ;;
    *)
        echo "aviso: HEAD devolveu HTTP ${codigo}. Tentando baixar." >&2
        ;;
esac

REMOTO="$(grep -i '^last-modified:' "${TMP}/head.txt" 2>/dev/null | tail -1 \
    | cut -d: -f2- | sed 's/^ *//; s/\r$//')"
LOCAL="$(cat "${MARCA}" 2>/dev/null || true)"

if [[ "$MODO" == 0 ]]; then
    echo "remoto: ${REMOTO:-?}"
    echo "local:  ${LOCAL:-(sem registro)}"
    if [[ -n "$REMOTO" && "$REMOTO" == "$LOCAL" ]]; then
        echo "situacao: atualizado"
    else
        echo "situacao: base nova disponivel (rode sem --checar para baixar)"
    fi
    exit 0
fi

if [[ "$MODO" == 1 && -n "$REMOTO" && -f "$ARQUIVO" && "$REMOTO" == "$LOCAL" ]]; then
    echo "ja atualizado: ${ARQUIVO} (${REMOTO})"
    exit 0
fi

# --- 2. Download do pacote (.tar.gz com o .mmdb dentro) -----------------
echo "baixando ${EDICAO} (base remota: ${REMOTO:-?})..."
if ! codigo="$(curl -fsS -L -u "${MAXMIND_ACCOUNT_ID}:${MAXMIND_LICENSE_KEY}" \
        -D "${TMP}/baixa.txt" -o "${TMP}/base.tar.gz" -w '%{http_code}' "${URL}")"; then
    echo "erro: download falhou (HTTP ${codigo:-?})." >&2
    if [[ "${codigo:-}" == 429 ]]; then
        echo "      429 = limite da MaxMind; tente mais tarde." >&2
    fi
    exit 1
fi

tar -xzf "${TMP}/base.tar.gz" -C "${TMP}"
NOVO="$(find "${TMP}" -name "${EDICAO}.mmdb" -print -quit)"
if [[ -z "$NOVO" ]]; then
    echo "erro: ${EDICAO}.mmdb nao encontrado no pacote baixado." >&2
    exit 1
fi
if [[ "$(stat -c %s "$NOVO")" -lt 1000000 ]]; then
    echo "erro: ${EDICAO}.mmdb pequeno demais (download incompleto)." >&2
    exit 1
fi

# --- 3. Troca atomica + marca da versao ---------------------------------
mv -f "${NOVO}" "${ARQUIVO}.novo"
mv -f "${ARQUIVO}.novo" "${ARQUIVO}"
if [[ -n "$REMOTO" ]]; then
    printf '%s\n' "$REMOTO" > "${MARCA}"
fi

echo "ok: ${ARQUIVO} ($(du -h "${ARQUIVO}" | cut -f1), build ${REMOTO:-?})"

# --- 4. Sanidade: abre a base com o geoip2 do venv, se existir ------------
if [[ -x "${RAIZ}/venv/bin/python" ]]; then
    "${RAIZ}/venv/bin/python" - "$ARQUIVO" <<'PY' || echo "aviso: nao abriu a base com geoip2 (rode o pip install -r requirements.txt)." >&2
import sys
from datetime import datetime

import geoip2.database

with geoip2.database.Reader(sys.argv[1]) as leitor:
    meta = leitor.metadata()
    build = datetime.fromtimestamp(meta.build_epoch).strftime('%d/%m/%Y')
    print(f'estrutura ok: {meta.database_type}, build {build}')
PY
fi
