# Arquitetura: API do chat separada da API do sistema financeiro

**Criado em:** 27/11/2026 · **Status:** proposta para decisão

## Problema atual

Hoje o navegador chama `POST /chat/api/` com **sessão + CSRF**. Esse endpoint:

- é a API da **interface web**, não uma API pública;
- usa `registrar_uso(request, 'chat')` (contador/limite), não o ledger de
  créditos;
- não tem API key, idempotência, escopo, request-id nem evento financeiro;
- não deve ser consumido por clientes externos ou pelo sistema financeiro.

Misturar isso com uma API de sistema torna impossível saber **quem consumiu o quê**
e **quanto cobrar**.

## Separação proposta

### 1. API da interface web (mantém)

- `POST /chat/api/` — mesma origem, sessão + CSRF.
- Uso: apenas o frontend do `chat.busquecnpj.com.br`.
- Continua com contadores/limites do site.
- Pode, no futuro, debitar créditos do usuário logado, mas **não** é a API de
  integração.

### 2. API de integração (nova)

- Namespace próprio: `POST /api/v1/chat/consultas` (e futuramente
  `/api/v1/empresas`, `/api/v1/proximidade`).
- Autenticação por **API key** (sem sessão/CSRF):
  - chave exibida uma vez, guardada só como hash;
  - prefixo identificável (`bcnpj_live_...`), escopos e rotação;
  - `Authorization: Bearer <chave>`.
- Cada key pertence a um **cliente financeiro** (`Cliente`) e tem:
  - créditos/cota própria;
  - rate limit por minuto/dia;
  - ambiente (`live`/`test`);
  - `idempotency-key` por requisição.
- Toda chamada grava `UsoApi` imutável:
  `request_id`, cliente, key, endpoint, créditos, status, latência, nº de linhas,
  erro, IP, horário.
- Resposta sempre devolve `request_id` e `uso` (créditos debitados).

### 3. API do sistema financeiro (sua)

Você cria e mantém essa API. Ela **não é chamada no caminho crítico** de cada
pergunta, para não adicionar latência/indisponibilidade.

Fluxo recomendado:

```text
cliente externo
   → /api/v1/chat/consultas (nosso)
      → debita/ reserva crédito no ledger local (transação)
      → grava UsoApi
      → publica EventoFinanceiro (outbox)
   → worker envia evento assinado para a SUA API financeira
      → ela concilia, fatura, emite NF, mede margem, etc.
```

A sua API financeira pode também expor consulta de saldo/fatura; o nosso sistema
faz reconciliação periódica, não chamada síncrona por request.

**Fonte da verdade:** ledger local é a fonte transacional da autorização; sua API
financeira é a fonte fiscal/comercial. Divergência vira alerta de reconciliação.

## Endpoints propostos

| Método | Rota | Uso |
|---|---|---|
| POST | `/api/v1/chat/consultas` | pergunta síncrona (timeout curto) |
| POST | `/api/v1/chat/consultas/async` | 202 + `request_id` para consultas longas |
| GET | `/api/v1/chat/consultas/<request_id>` | polling do resultado |
| GET | `/api/v1/uso` | consumo por período/key |
| POST | `/api/v1/keys` | (admin) criar/rotacionar key |
| GET | `/api/v1/saldo` | saldo/cota do cliente |
| POST | `/api/v1/webhooks/financeiro` | eventos da sua API (assinados) |

O chat pode levar 5–60 s; para integração externa, **assíncrono é o padrão
recomendado** para não estourar timeout do cliente.

## Modelo de dados proposto

- `ApiCliente` (ou reutilizar `Cliente` com campos de API).
- `ApiKey`: hash, prefixo, escopos, ativa, última utilização, rotação.
- `UsoApi`: ledger de consumo por request, imutável.
- `EventoFinanceiro`: outbox (`pendente`, `enviado`, `falhou`, `concluido`),
  payload assinado, tentativas, próximo retry.
- `IdempotencyKey`: unique por cliente + chave, resposta guardada por 24 h.

**Não** usar `PesquisaAnonima` para API key: ela é contador de visitante, não
ledger financeiro.

## Segurança e limites

- API key nunca em log; hash Argon2/`sha256` com pepper.
- Escopos (`chat:read`, `empresas:read`, `export:write`).
- Rate limit por key + IP; bloqueio por cota.
- Request ID obrigatório para suporte e conciliação.
- Webhook assinado (HMAC), retry exponencial, dead-letter.
- Kill switch por cliente/key.
- LGPD: não guardar prompt/resultado no ledger além do necessário; metadados e
  hash, com retenção definida.

## Fases da separação

### API-0 — Decisão de propriedade financeira (você)

Definir:
1. A API do sistema financeiro **chama** a nossa API ou **recebe eventos**?
2. Quem é a fonte da verdade do saldo: ledger local, sua API ou ambos com
   reconciliação?
3. A API externa é vendida por **crédito por consulta** ou por **assinatura +
   excedente**?

Sem isso, o modelo de dados pode ficar errado.

### API-1 — Ledger e autenticação

- `ApiKey`, `UsoApi`, `IdempotencyKey`, migrations e admin.
- Middleware de API key; sem endpoint público ainda.
- Testes de autenticação, rotação e isolamento por cliente.

### API-2 — Endpoint síncrono + cobrança local

- `POST /api/v1/chat/consultas` reutilizando o pipeline existente (serviço
  interno, não a view web).
- Débito/reserva transacional; resposta com `request_id`/`uso`.
- Rate limit e idempotência.

### API-3 — Outbox + API financeira

- `EventoFinanceiro` e worker de entrega.
- Webhook assinado, retry, dead-letter, reconciliação.
- Sua API passa a receber os eventos.

### API-4 — Assíncrono e polling

- `202` + `request_id`; worker de execução; resultado armazenado com retenção.
- Webhook opcional de conclusão.

### API-5 — Canário e produção

- Keys de teste; cliente interno; métricas de uso/latência/erro.
- Só depois abrir para clientes externos.

## O que não fazer

- Expor `/chat/api/` a clientes externos.
- Cobrar em `PesquisaAnonima`.
- Chamar a API financeira de forma síncrona em cada pergunta.
- Deixar a mesma API key para web e integração.
- Debitar sem idempotência.

## Decisão pendente

**Pergunta única:** a sua API do sistema financeiro vai **receber eventos** do
nosso sistema (recomendado) ou será o **gateway único** que chama a nossa API?

Com essa resposta, a Fase API-1 já pode começar sem retrabalho.
