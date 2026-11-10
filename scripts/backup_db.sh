#!/usr/bin/env bash
#
# Backup do banco interno do Django (Postgres 'django_internal').
#
# Uso:
#   scripts/backup_db.sh                  # dump + rotacao de 14 dias
#   scripts/backup_db.sh --manter 30      # outra retencao
#   scripts/backup_db.sh --dir /tmp/bkp   # outro destino
#
# As credenciais vem do .env do projeto (DJANGO_DB*), entao roda no cron sem
# depender de ~/.pgpass. Se o pg_dump falhar ou o gzip sair vazio, o arquivo
# parcial e' apagado e o script sai com 1 - backup de 0 byte nao passa.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR_BACKUP="/home/neto/backups"
MANTER_DIAS=14

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)    DIR_BACKUP="${2:?falta o valor de --dir}"; shift 2 ;;
    --manter) MANTER_DIAS="${2:?falta o valor de --manter}"; shift 2 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Opcao desconhecida: $1" >&2; exit 2 ;;
  esac
done

# Le uma chave do .env SEM executar o arquivo. O SECRET_KEY do Django tem
# "(...)" no valor e o 'source' do bash quebra com erro de sintaxe.
ler_env() {
  local chave="$1" valor
  [[ -f "$RAIZ/.env" ]] || return 0
  valor="$(grep -E "^[[:space:]]*${chave}=" "$RAIZ/.env" | tail -1 | cut -d= -f2- | tr -d '\r')"
  valor="${valor%\"}"; valor="${valor#\"}"
  valor="${valor%\'}"; valor="${valor#\'}"
  printf '%s' "$valor"
}

BANCO="$(ler_env DJANGO_DB)";              BANCO="${BANCO:-django_internal}"
USUARIO="$(ler_env DJANGO_DB_USER)";        USUARIO="${USUARIO:-django_app}"
HOST="$(ler_env DJANGO_DB_HOST)";           HOST="${HOST:-localhost}"
PORTA="$(ler_env DJANGO_DB_PORT)";          PORTA="${PORTA:-5432}"
SENHA="$(ler_env DJANGO_DB_PASSWORD)"

if [[ -z "$SENHA" ]]; then
  echo "ERRO: DJANGO_DB_PASSWORD vazio (confira $RAIZ/.env)" >&2
  exit 1
fi
export PGPASSWORD="$SENHA"

mkdir -p "$DIR_BACKUP"
ARQUIVO="$DIR_BACKUP/${BANCO}_$(date +%Y%m%d).sql.gz"

echo "[$(date '+%F %T')] dump $BANCO@$HOST:$PORTA -> $ARQUIVO"

if ! pg_dump -U "$USUARIO" -h "$HOST" -p "$PORTA" "$BANCO" | gzip > "$ARQUIVO"; then
  rm -f "$ARQUIVO"
  echo "ERRO: pg_dump falhou; arquivo parcial removido" >&2
  exit 1
fi

if [[ ! -s "$ARQUIVO" ]]; then
  rm -f "$ARQUIVO"
  echo "ERRO: dump saiu vazio; arquivo removido" >&2
  exit 1
fi

echo "  ok: $(du -h "$ARQUIVO" | cut -f1)"

qtd=0
while IFS= read -r antigo; do
  rm -f "$antigo"
  qtd=$((qtd + 1))
done < <(find "$DIR_BACKUP" -maxdepth 1 -name "${BANCO}_*.sql.gz" -mtime +"$MANTER_DIAS" -print)
echo "  rotacao: $qtd arquivo(s) com mais de $MANTER_DIAS dias removido(s)"
