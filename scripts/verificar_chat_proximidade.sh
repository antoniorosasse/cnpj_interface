#!/usr/bin/env bash
# Verificacao da integracao Chat IA <-> Proximidade (26/11/2026).
#
# Uso:
#   scripts/verificar_chat_proximidade.sh                # rapido (30-60 s)
#   scripts/verificar_chat_proximidade.sh --completo     # suites chat+core+exportacao
#   scripts/verificar_chat_proximidade.sh --smoke URL    # smoke HTTP (runserver no ar)
#
# Sai com 0 quando tudo passa; 1 na primeira falha. Sem acentos no codigo.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PY="venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "ERRO: $PY nao encontrado. Rode a partir do repo cnpj_interface com o venv." >&2
    exit 1
fi

MODO="${1:---rapido}"
URL="${2:-}"

titulo() { printf '\n== %s ==\n' "$1"; }

rodar() {
    echo "+ $*"
    "$@"
}

verificar_estatico() {
    titulo "check / migrations"
    rodar "$PY" manage.py check
    rodar "$PY" manage.py makemigrations --check --dry-run
}

suites_rapidas() {
    titulo "suites dirigidas"
    rodar "$PY" manage.py test --noinput \
        chat.tests.test_planner \
        chat.tests.test_proximidade_service \
        chat.tests.test_api_proximidade \
        chat.tests.test_sql_generator \
        core.tests_proximidade_filtrada \
        core.tests_proximidade_filtros \
        exportacao.tests_proximidade
}

suites_completas() {
    titulo "suites completas (chat, core, exportacao)"
    rodar "$PY" manage.py test --noinput chat core exportacao
}

smoke() {
    local base="${1%/}"
    titulo "smoke HTTP em $base"
    local falhou=0
    checar() {
        local url="$1" esperado="$2" nome="$3"
        local codigo
        codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$url" || true)"
        if printf '%s' "$esperado" | grep -qw "$codigo"; then
            echo "  OK   [$codigo] $nome"
        else
            echo "  FALHA [$codigo] $nome (esperado: $esperado) -> $url"
            falhou=1
        fi
    }
    checar "$base/proximidade/" "200" "pagina de proximidade"
    checar "$base/proximidade/?origem=Joinville/SC&raio=1" "200" "proximidade com origem/raio"
    checar "$base/chat/" "200 302" "chat (302 = login)"
    if curl -s -o /dev/null --max-time 10 "$base/health/deep/" 2>/dev/null; then
        checar "$base/health/deep/" "200 503" "health/deep"
    fi
    if [ "$falhou" -ne 0 ]; then
        echo "SMOKE FALHOU" >&2
        exit 1
    fi
}

case "$MODO" in
    --rapido)
        verificar_estatico
        suites_rapidas
        echo
        echo "OK - verificacao rapida concluida"
        ;;
    --completo)
        verificar_estatico
        suites_completas
        echo
        echo "OK - suites completas concluidas"
        ;;
    --smoke)
        if [ -z "$URL" ]; then
            echo "ERRO: informe a URL. Ex.: --smoke http://localhost:8002" >&2
            exit 1
        fi
        smoke "$URL"
        echo
        echo "OK - smoke concluido"
        ;;
    -h|--help)
        sed -n '4,9p' "$0"
        ;;
    *)
        echo "ERRO: modo desconhecido: $MODO (use --rapido, --completo, --smoke URL)" >&2
        exit 1
        ;;
esac
