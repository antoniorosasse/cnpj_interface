#!/usr/bin/env bash
#
# Alarme de disco: avisa quando o uso de um ponto de montagem passa do limite.
#
# Uso:
#   scripts/checar_disco.sh                # limite padrao 85% em /
#   scripts/checar_disco.sh --limite 90    # outro limite
#   scripts/checar_disco.sh --caminho /home/neto/backups
#
# Sai com 1 (e escreve no log) quando o uso passa do limite, para o cron
# marcar a tarefa como falha. Se ALERTA_WEBHOOK estiver no .env, dispara
# tambem um POST com o texto do alerta (Slack/Telegram-compativel:
# --data-urlencode "text=...").
#
# Motivo: com a base de 50 GB + dumps no mesmo disco, disco cheio para o
# Postgres de escrever WAL e derruba todos os sites. O aviso tem que vir
# antes, nao no 500.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIMITE=85
CAMINHO="/"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --limite)   LIMITE="${2:?falta o valor de --limite}"; shift 2 ;;
    --caminho)  CAMINHO="${2:?falta o valor de --caminho}"; shift 2 ;;
    -h|--help)  sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done

# Le uma chave do .env SEM executar o arquivo (SECRET_KEY tem "(...)" e o
# 'source' do bash quebraria com erro de sintaxe).
ler_env() {
  local chave="$1" valor
  [[ -f "$RAIZ/.env" ]] || return 0
  valor="$(grep -E "^[[:space:]]*${chave}=" "$RAIZ/.env" | tail -1 | cut -d= -f2- | tr -d '\r')"
  valor="${valor%\"}"; valor="${valor#\"}"
  valor="${valor%\'}"; valor="${valor#\'}"
  printf '%s' "$valor"
}

USO="$(df --output=pcent "$CAMINHO" | tail -1 | tr -dc '0-9')"
if [[ -z "$USO" ]]; then
  echo "ERRO: nao consegui ler o uso de $CAMINHO" >&2
  exit 1
fi

if (( USO >= LIMITE )); then
  LIVRE="$(df -h --output=avail "$CAMINHO" | tail -1 | tr -d ' ')"
  MSG="ALERTA disco: ${CAMINHO} em ${USO}% (limite ${LIMITE}%, livre ${LIVRE}) em $(hostname) - $(date '+%F %T')"
  echo "$MSG" >&2
  logger -t alerta_disco "$MSG" || true
  WEBHOOK="$(ler_env ALERTA_WEBHOOK)"
  if [[ -n "$WEBHOOK" ]]; then
    curl -fsS -m 10 --data-urlencode "text=$MSG" "$WEBHOOK" >/dev/null \
      || echo "aviso: webhook nao respondeu" >&2
  fi
  exit 1
fi

echo "disco ok: ${CAMINHO} em ${USO}% (limite ${LIMITE}%)"
