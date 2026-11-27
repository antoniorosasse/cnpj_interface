# Exportação completa em background

**Criado em:** 27/11/2026 · **Status:** implementado localmente; produção pendente

## Quando usar

O painel do chat tem o botão **“Exportar tudo”** (além do CSV/JSON das linhas
exibidas):

- CSV/JSON da tabela: baixa **somente as linhas carregadas** no painel;
- **Exportar tudo**: reexecuta o SQL da última lista no servidor, sem o
  `LIMIT 100` do preview, e gera o arquivo completo em background.

## Fluxo

```text
botão Exportar tudo
  → valida login/SQL/limite
  → calcula o total real
  → cria Exportacao(status=pendente, completo=True, token)
  → dispara worker
  → status: processando → pronto (arquivo em disco)
  → página /exportar/completo/<token>/
  → download (status baixada)
```

A página de status atualiza sozinha enquanto processa e mostra o erro em caso
de falha.

## Limites padrão

| Perfil | Teto |
|---|---:|
| Admin/staff | **500.000 linhas** |
| Cliente com créditos | **50.000 linhas** |

Configuráveis no `.env`:

```env
EXPORTACAO_COMPLETA_MAX_ADMIN=500000
EXPORTACAO_COMPLETA_MAX_CLIENTE=50000
EXPORTACAO_COMPLETA_TTL_HORAS=24
EXPORTACAO_COMPLETA_TIMEOUT=600
EXPORTACAO_COMPLETA_DIR=/caminho/para/exportacoes
```

- O limite é conferido **antes** de criar o job; acima dele a consulta é recusada
  com pedido de refinar os filtros.
- Para cliente, o saldo precisa cobrir o total; o débito acontece **depois** que
  o arquivo foi gerado com sucesso.
- Staff não paga créditos, mas continua contabilizado nos contadores.

## Worker

Em desenvolvimento, a view dispara uma thread e o processo local gera o arquivo.
Em produção, use o comando periodicamente (systemd timer, cron ou supervisor),
para não depender do processo do gunicorn:

```bash
venv/bin/python manage.py processar_exportacoes --esperar 5
```

Ele pega jobs `pendente`, marca `processando`, gera o arquivo e atualiza o
status. O comando é idempotente: job já processando/pronto não é refeito.

## Arquivos e expurgo

- Diretório padrão: `BASE_DIR/exportacoes` (no `.gitignore`).
- Nome: `exportacao-<id>-<token-curto>.<csv|json>`.
- O link expira em `EXPORTACAO_COMPLETA_TTL_HORAS` (padrão 24 h).
- `manage.py limpar_exportacoes --dias N` remove registros antigos **e** os
  arquivos completos correspondentes.

## Segurança

- O token é aleatório e só o dono do job (ou staff) acessa status/download.
- O SQL é revalidado no worker, com executação read-only e `statement_timeout`
  próprio (padrão 600 s).
- Arquivo só é servido quando `status=pronto`.
- Não há SQL enviado pelo navegador: o worker usa o SQL já gravado na conversa.
