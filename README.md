# CNPJ Interface

Consulta de empresas da Receita Federal (dados públicos de CNPJ) + Chat IA
que traduz perguntas em linguagem natural para SQL.

## Índice

- [Stack](#stack)
- [Estrutura](#estrutura)
- [Como rodar local](#como-rodar-local)
- [Acesso de demonstração](#acesso-de-demonstração)
- [Requisitos de dados](#requisitos-de-dados)
- [Consulta de CNPJ](#consulta-de-cnpj)
  - [O que dá pra buscar](#o-que-dá-pra-buscar)
  - [O que aparece no detalhe da empresa](#o-que-aparece-no-detalhe-da-empresa)
  - [Estatísticas (`/estatisticas/`)](#estatísticas-estatisticas)
  - [Performance](#performance)
- [Chat IA](#chat-ia)
  - [Pipeline](#pipeline)
  - [Endpoints do chat](#endpoints-do-chat)
- [Endpoints (todos)](#endpoints-todos)
- [Bancos de dados](#bancos-de-dados)
- [Log de acesso (`chat_logacesso`)](#log-de-acesso-chat_logacesso)
- [Testes](#testes)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Deploy](#deploy)
- [Ambiente](#ambiente)
- [Scripts de manutenção](#scripts-de-manutenção)
- [Repositórios relacionados](#repositórios-relacionados)

## Stack

- Django 6.1.1
- Python 3.13
- PostgreSQL 15 (dois bancos: `django_internal` e `bancocnpj`)
- MongoDB 8 (backend alternativo do chat, via `CHAT_USE_MONGO=1`)
- psycopg2-binary, pymongo
- DeepSeek (chat IA via `openai` SDK)
- Gunicorn + systemd (produção)

## Estrutura

    cnpj_interface/
    +-- core/                       # Consulta de empresas
    |   +-- templates/core/
    |   |   +-- base.html
    |   |   +-- lista.html
    |   |   +-- detalhe.html
    |   |   +-- estatisticas.html
    |   +-- templatetags/
    |   |   +-- num_filters.py      # Filtros de formatacao de numero
    |   +-- conexoes.py             # Conexoes (AS/400, PG, Mongo)
    |   +-- routers.py              # CNPJRouter (default vs postgres)
    |   +-- urls.py
    |   +-- views.py                # lista, detalhe, estatisticas
    +-- chat/                       # Chat IA
    |   +-- services/
    |   |   +-- llm_client.py       # Wrapper OpenAI apontando pro DeepSeek
    |   |   +-- schema_context.py   # Contexto do schema (tabelas, codigos, indices)
    |   |   +-- sql_generator.py    # Pergunta -> SQL (com retry)
    |   |   +-- sql_validator.py    # 4 camadas de seguranca
    |   |   +-- sql_executor.py     # Executa SQL read-only
    |   |   +-- resposta_natural.py # Gera 1-3 frases em PT-BR a partir dos dados
    |   +-- templates/chat/
    |   |   +-- chat.html
    |   |   +-- historico.html
    |   +-- tests/                  # 218 testes
    |   |   +-- test_embedding_service.py
    |   |   +-- test_integracao.py
    |   |   +-- test_models.py
    |   |   +-- test_mongo_models.py
    |   |   +-- test_resposta_natural.py
    |   |   +-- test_sql_executor.py
    |   |   +-- test_sql_generator.py
    |   |   +-- test_sql_validator.py
    |   +-- management/commands/
    |   |   +-- limpar_conversas_antigas.py   # Retencao do historico (padrao 30 dias)
    |   |   +-- relatorio_acesso.py           # Logins, chat e empresas (chat_logacesso)
    |   +-- models.py               # Conversa + Mensagem (Postgres)
    |   +-- mongo_models.py         # Conversa + Mensagem (MongoDB)
    |   +-- repo.py                 # Dispatcher (Mongo | Postgres)
    |   +-- repo_postgres.py        # Implementacao ORM
    |   +-- repo_mongo.py           # Implementacao pymongo
    |   +-- urls.py
    |   +-- views.py
    +-- cnpj_project/               # Configuracao Django
    |   +-- settings.py
    |   +-- urls.py
    |   +-- wsgi.py
    |   +-- atualizar_dt_ini_csv.py       # Script de manutencao
    |   +-- atualizar_dt_ini_direto.py
    |   +-- atualizar_dt_ini_todos.py
    +-- templates/registration/     # Login, trocar senha
    +-- lib/
    |   +-- README.md               # Instrucoes pra baixar jt400.jar
    +-- manage.py
    +-- requirements.txt
    +-- testar_simple.py            # Testa conexoes (AS/400, PG, Mongo)

## Front-end (tema e CSS)

- Bootstrap 5.3 via CDN (`core/templates/core/base.html`), com tema claro/escuro
  nativo (`data-bs-theme`) alternado pelo botão da navbar. A escolha fica em
  `localStorage['sistema-tema']`; `?tema=dark|light` força o tema pela URL e o
  script do `<head>` aplica antes do CSS (sem flash branco).
- `static/css/sistema.css` concentra a linguagem visual das tabelas
  (`.sis-tabela`, `.sis-thead`, `.sis-pill`, `.sis-num`, `.sis-cnpj`) e a
  transformação de tabela em cartões no celular (`.tabela-cards`), que usa o
  `data-label` de cada `<td>`: o mesmo markup serve desktop e mobile, sem
  tabela duplicada. As cores saem das variáveis do Bootstrap
  (`--bs-*-bg-subtle`, `--bs-border-color`, `--bs-tertiary-bg`), então o tema
  escuro não precisa de CSS próprio.
- O chat (`chat/templates/chat/chat.html`) segue a mesma regra: o `<style>` do
  template não tem cor fixa (só variáveis `--bs-*`) e a tabela de resultados é
  gerada em JS com `class="table sis-tabela"` + `thead.sis-thead`, além do
  `data-label` de cada célula. Foi o que corrigiu o cabeçalho "apagado" no tema
  escuro (antes o `th` tinha `background: #f3f4f6` fixo e o texto herdado era
  claro).

## Como rodar local

Pre-requisitos:

- Python 3.13
- PostgreSQL 15 rodando em `localhost:5432`
- MongoDB 8 rodando em `localhost:27017` (só se for usar `CHAT_USE_MONGO=1`)

Passos:

    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env            # preencher valores
    (baixar lib/jt400.jar - ver lib/README.md)
    python manage.py migrate
    python manage.py runserver 8002

Acessa em http://localhost:8002/

Observações:

- O projeto assume **dois bancos PostgreSQL**: `django_internal` (auth,
  sessions, admin, chat) e `bancocnpj` (dados da RFB). Ambos precisam
  existir antes do `migrate`.
- O `.env.example` tem todas as variáveis. Sem `DEEPSEEK_API_KEY`, o chat
  não funciona (mas o resto sim).

## Acesso de demonstração

O projeto está publicado como portfólio: `cnpj.arosasse.tech` (consulta de
empresas, **aberta**) e `chat.arosasse.tech` (chat IA, **exige login**).

Conta de demonstração:

| Usuário | Senha |
|---|---|
| `demo` | `demo` |

- A **consulta de CNPJ não pede login** — o `@login_required` está só no app
  `chat` (`chat/views.py`).
- A tela de login (`/accounts/login/`) mostra um aviso com essas credenciais e
  um botão **Preencher com demo** (`templates/registration/login.html`).
- `demo` é um usuário comum (`is_staff=False`, `is_superuser=False`), **sem
  acesso ao `/admin`**.
- O chat só executa SQL em **modo somente-leitura** (validador de 4 camadas +
  transação read-only). O que a conta `demo` pode fazer é criar e apagar as
  **próprias conversas** — como todos os visitantes compartilham o mesmo
  usuário, o histórico da sidebar é comum a eles.
- O aviso aparece em qualquer host que sirva `/accounts/login/` e **não** é
  condicionado ao host `chat.` de propósito: em local o chat roda na mesma
  porta da consulta (`localhost:8002`), e a condição esconderia o aviso
  justamente no ambiente de teste.

## Requisitos de dados

Este projeto **não inclui os dados da Receita Federal**. O banco
`bancocnpj` precisa ser populado separadamente. Sem isso, nem a lista
de empresas nem o chat funcionam.

De onde baixar os dados brutos:

- **Parquet** (recomendado, é o que o `ProjetoCargaDados` lê):
  `https://www.kaggle.com/datasets/jurandifranca/cnpj-brasil`
- **CSV zipado** (fonte oficial, no portal de dados abertos):
  `https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj`

Passos:

1. Baixar os Parquet da RFB (link acima).
2. Rodar o [ProjetoCargaDados](https://github.com/antoniorosasse/ProjetoCargaDados)
   para popular as 10 tabelas `c_*` no `bancocnpj`.
3. Rodar `CALL sp_recalcular_estatisticas_totais();` no `bancocnpj`
   (ou usar o botão em `/estatisticas/recalcular/`) para popular a
   tabela `c_estatisticas_totais` (contagens instantâneas).

O schema completo das tabelas (colunas, tipos, códigos, índices
relevantes) fica em `chat/services/schema_context.py` — é o mesmo
texto que o LLM lê para gerar SQL.  
  
  
---

# Consulta de CNPJ

A parte principal do projeto: consulta de empresas da Receita Federal,
com filtros avançados, paginação rápida e detalhamento completo de cada
empresa.

## O que dá pra buscar

A página inicial (`/`) lista empresas (matrizes) com filtros combináveis:

| Filtro | O que aceita | Exemplo |
|---|---|---|
| Razão social | Texto parcial (case-insensitive) | `MORAIS` |
| Nome fantasia | Texto parcial (case-insensitive) | `MERCADO` |
| CNPJ | 8 dígitos (básico) ou 14 (completo) | `12345678` ou `12345678000199` |
| UF | 2 letras | `SP` |
| Situação | Nula, Ativa, Suspensa, Inapta, Baixada | `ATIVA` |
| Simples | S ou N | `S` |
| MEI | S ou N | `S` |

### Ordenação

A lista pode ser ordenada por:

- **CNPJ** (padrão) - mais rápido
- **Razão social** - usa INNER JOIN com `c_empresas`
- **Nome fantasia** - mesmo esquema

### Paginação

Usa **keyset pagination** (não OFFSET). Isso significa que a página 5000
carrega tão rápido quanto a página 1, mesmo com 38 milhões de linhas.

O botão "Próxima" passa o último CNPJ/razão da página atual como ponto de
partida. O botão "Anterior" faz o mesmo, ao contrário. A contagem total
usa a tabela de metadados `c_estatisticas_totais` quando não há filtros,
e cache de 15min com timeout de 800ms quando há filtros combinados.

## O que aparece no detalhe da empresa

A página `/empresa/<cnpj>/` mostra **tudo** que a Receita pública sobre
aquela empresa. Dividido em seções:

### Identificação

- CNPJ formatado (`00.000.000/0000-00`)
- Razão social
- Nome fantasia
- Tipo: **MATRIZ** ou **FILIAL**

### Dados cadastrais

- **Natureza jurídica** - código + descrição (ex: `206-2 - Sociedade Empresaria Limitada`)
- **Porte** - ME (Micro Empresa), EPP (Empresa de Pequeno Porte), Demais, ou Não informado
- **Capital social** - em R$
- **Data de abertura** (`dt_ini`)
- **Qualificação do responsável** - código

### Situação cadastral

- **Situação** - Ativa, Suspensa, Inapta, Baixada, Nula
- **Data da situação** (`dt_sit_cadastro`)
- **Motivo** - código + descrição (ex: `01 - Extincao por Encerramento de Liquidacao`)

### Simples Nacional e MEI

Dois cards lado a lado:

- **Simples Nacional**: status (SIM/NAO), data de opção, data de exclusão
- **MEI**: status (SIM/NAO), data de opção, data de exclusão

### Atividade econômica (CNAE)

- **CNAE principal** - código + descrição
- **CNAE secundários** - lista de códigos separados por vírgula

### Endereço e contato

- Logradouro (tipo + nome), número, complemento, bairro
- **CEP**
- **Município** - código IBGE + nome
- **UF**
- **País** - código + nome (Brasil, na maioria)
- **Telefone 1** e **Telefone 2** - formatados com DDD
- **Fax**
- **Email**
- **Situação especial** + data (se houver)

### Estabelecimentos (matriz + filiais)

Tabela com **todos** os estabelecimentos daquela empresa:

- CNPJ completo de cada um
- Tipo (Matriz / Filial)
- Nome fantasia
- Situação cadastral
- Endereço resumido
- UF
- Telefone
- Email
- Data de abertura
- CNAE principal

A matriz aparece destacada (linha azul). As filiais vêm em seguida,
ordenadas por `cnpj_ordem`.

### Sócios

Tabela com todos os sócios:

- **Nome** (ou razão social, se for PJ)
- **Tipo** - PF (Pessoa Física), PJ (Pessoa Jurídica) ou Estrangeiro
- **CPF/CNPJ** - descaracterizado pela RFB (só os 6 dígitos do meio)
- **Qualificação** - código + descrição (ex: `49 - Socio-Administrador`)
- **Data de entrada** na sociedade
- **Faixa etária** - 0-12, 13-20, ..., 81+ anos
- **País** (se estrangeiro)
- **Representante legal** (se houver) - nome + qualificação

## Estatísticas (`/estatisticas/`)

Página com visão agregada da base inteira:

- **Totais**: matrizes, ativas, inaptas, baixadas, suspensas, nulas, UFs
- **Top 10 UFs** - com percentual
- **Distribuição por situação** - com percentual
- **Distribuição por porte** - ME, EPP, Demais, Não informado
- **Top 10 naturezas jurídicas** - código + descrição
- **Top 10 CNAEs** - código + descrição
- **Optantes Simples / MEI** - total
- **Empresas por ano de abertura** - últimos 15 anos (com barra de progresso)
- **Top 10 municípios** - código IBGE + nome + UF
- **Total de sócios** + empresas com sócios

Os dados vêm da tabela `c_estatisticas_totais`, populada pela stored
procedure `sp_recalcular_estatisticas_totais()`. Superusuário pode
disparar o recálculo pelo botão na própria página.

## Performance

A base tem ~38M de linhas em `c_estabelecimentos`. Pra aguentar isso:

- **Índices parciais** com `WHERE cd_matriz_filial = 1` (matrizes são ~10% da tabela)
- **Índice de cidade** `idx_estab_municipio_cnpj (municipio, cnpj_basico)` (1,1 GB, sem filtro de matriz, cobre sede e filial) para o semi-join "tem estabelecimento na cidade" do chat. Criado sem bloquear a tabela:

      psql -h localhost -p 5432 -U arosasse -d bancocnpj -v ON_ERROR_STOP=1 <<'SQL'
      SET maintenance_work_mem = '1GB';
      CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_municipio_cnpj
          ON c_estabelecimentos (municipio, cnpj_basico);
      ANALYZE c_estabelecimentos;
      SQL

  (38,5M linhas em ~1min44s. `CONCURRENTLY` exige autocommit: nada de `psql -1`/`BEGIN`.)
- **Keyset pagination** em vez de OFFSET
- **Tabela de metadados** `c_estatisticas_totais` pra contagens instantâneas
- **ILIKE** em vez de `UPPER(col) LIKE` (aproveita índices GIN trigram)
- **Timeout de segurança** de 10s em ordenações por nome fantasia
- **Fast-count** com timeout de 800ms e cache de 15min em filtros combinados

O tempo de resposta típico é:

- Primeira página sem filtro: ~5ms (lê da tabela de metadados)
- Páginas seguintes (keyset): ~200ms
- Estatísticas: ~5ms (lê da tabela de metadados)
- Recálculo completo: ~15-30s

---

# Chat IA

Traduz perguntas em português pra SQL e executa no banco, em modo
read-only. Tem histórico de conversas e contexto.

O pipeline **é um RAG** (retrieval-augmented generation) — mas um RAG sobre
**dados estruturados**, não sobre documentos: a recuperação acontece por busca
vetorial no CNAE + execução de SQL, e o que entra no prompt do LLM são as
**linhas recuperadas**, não pedaços de texto de uma base vetorial. Detalhes na
seção [1.2 RAG](#12-rag--o-que-é-recuperado-e-o-que-vai-no-prompt) e no
`~/Projetos/docs/PROJETOS.md` (seção 7.1).

## Pipeline

    Pergunta (PT-BR)
      -> sql_generator    (DeepSeek gera o SQL)
      -> sql_validator    (4 camadas de seguranca)
      -> sql_executor     (executa read-only)
      -> resposta_natural (1-3 frases em PT-BR via DeepSeek)
      -> resposta final

### 1. `sql_generator`

- Monta prompt com o **schema real** do banco (tabelas, colunas, tamanhos, códigos)
- Chama o DeepSeek via `llm_client.gerar_resposta()`
- Retenta até `max_tentativas` se o SQL vier inválido
- Passa o histórico da conversa como contexto (últimas 10 mensagens)
- Pode responder `RECUSAR: <motivo>` quando a pergunta pede dado que não
  existe no schema (ex: faturamento, número de funcionários)

### 1.1 Busca semântica (vetorial)

Quando a pergunta for sobre um **conceito** (ex: "empresas de energia solar") e não houver um código CNAE explícito, o `sql_generator` gera um SQL com `ILIKE` **e** com a cláusula:

```sql
ORDER BY embedding <=> %s::halfvec(384)
```

O `chat/services/embedding_service.py` gera o vetor da pergunta (usando o modelo calneymgp/braza-embedding-ptbr-v1) e substitui o `%s` antes da execução. A tabela c_cnaes tem a coluna embedding (halfvec(384)) populada com os vetores das descrições dos CNAEs. O schema_context.py documenta essa coluna e o operador <=> para o LLM.

**O vetor é apenas fallback do `ILIKE`**: a parte vetorial só devolve linhas quando o `ILIKE` não encontra nenhum CNAE, através de um `NOT EXISTS (SELECT 1 FROM c_cnaes WHERE nm_cnae ILIKE ...)`. Sem esse guarda, o `UNION` mistura CNAEs "vizinhos" com o CNAE exato e o `LIMIT 100` enche de categorias erradas (ex: "supermercados de picarras" trazia 121 minimercados/mercearias junto com os 4 supermercados). O `sql_generator` rejeita e pede correção ao LLM quando o SQL usa `%s` sem esse `NOT EXISTS`.

**Atividade + nome (marca)**: quando a pergunta cita um tipo de negócio **e** um nome ("liste os mercados KOCH"), o SQL precisa dos **dois** filtros ligados por `AND`: o filtro de CNAE (`ILIKE '%mercado%'`) e o `EXISTS` de `nm_fantasia ILIKE '%KOCH%'`. Se o LLM descarta o filtro de CNAE, a query vira "qualquer empresa com o nome", o `LIMIT 100` corta a lista e a empresa certa não aparece (eram 100 KOCHs de outros ramos, sem o KOCH HIPERMERCADO S/A, CNAE 4691500). Pelo mesmo motivo, toda listagem com `LIMIT` deve ter `ORDER BY` (ex: `ORDER BY emp.razao_social`) para o corte ser determinístico. As regras estão no `SYSTEM_PROMPT` (regras 9 e 10) e na seção `ATIVIDADE + NOME (MARCA)` do `schema_context.py`.

**Filtro por cidade = "tem estabelecimento na cidade"**: em listagens, o filtro de cidade não pode olhar só a matriz. A rede pode ter a sede em uma cidade e as lojas em outra: o KOCH HIPERMERCADO S/A (`cnpj_basico` 02831172) tem a matriz em Tijucas (CNAE 4691500) e 4 filiais em Balneário de Picarras, então com `e.municipio IN (...picarras...)` a empresa desaparecia de "atacadistas de picarras". A regra 13 do `SYSTEM_PROMPT` e a seção `FILTRO POR CIDADE` do `schema_context.py` mandam usar semi-join por `cnpj_basico`:

```sql
AND e.cnpj_basico IN (
      SELECT f.cnpj_basico FROM c_estabelecimentos f
      WHERE f.municipio IN (SELECT cd_municipio FROM c_municipios
                            WHERE municipio ILIKE '%PICARRAS%'))
```

O `EXISTS` correlacionado para a cidade é proibido: medido no banco real, "atacadistas de picarras" = 0,3 s com o `IN` e 3,3 s com o `EXISTS` (o `IN` deixa o planner montar um hash dos `cnpj_basico` da cidade). O semi-join agora usa o índice `idx_estab_municipio_cnpj (municipio, cnpj_basico)` (criado com `CREATE INDEX CONCURRENTLY` em 31/10/2026: 38,5M linhas, 1,1 GB, ~1min44s; `maintenance_work_mem=1GB`), e os 632 mil `cnpj_basico` de Curitiba saem num `Index Only Scan` em ~165 ms. Com ele os tempos caíram para cerca de metade (atacadistas de picarras 0,3 s; supermercados de picarras 0,3-0,9 s; padarias de picarras 4-6 s; padarias de Lages 4-5 s; padarias de Curitiba 5-7 s, contra 0,01-0,2 s do filtro direto na matriz). O gargalo que sobrou é o CNAE, não a cidade: `ILIKE '%padaria%'` casa com 3 CNAEs e o planner varre as ~405 mil matrizes de padaria do Brasil uma vez por CNAE (~1,7 s cada). Por isso a regra mantém o filtro direto em capitais/metrópoles, em atividades muito comuns e em contagens puras.

Não troque o `IN` por `= ANY(ARRAY(SELECT ...))` tentando "otimizar": em Picarras fica 0,3 s (13x mais rápido), mas o planner perde a estimativa de linhas e o plano desanda (8,5 s em padarias de Lages na primeira execução e Curitiba não terminou nem em minutos).

Custo de disco dos embeddings: < 2 MB (apenas 1.359 CNAEs).
Performance da geração do vetor: ~30ms em produção.

### Contagem de estabelecimentos por empresa

Quando a listagem mostra quantos estabelecimentos cada empresa tem, o SQL usa
uma subconsulta correlacionada:

```sql
(SELECT COUNT(*) FROM c_estabelecimentos x
   WHERE x.cnpj_basico = e.cnpj_basico) AS qtd_estabelecimentos
```

O `COUNT` usa a PK (`cnpj_basico` na frente), então é barato por linha (a listagem de "mercados KOCH" roda em ~100 ms). O `resposta_natural` cita o número no texto ("o KOCH HIPERMERCADO S/A tem 115 estabelecimentos") e o front-end mostra a coluna como **Estabelecimentos**. Na tabela, tanto o CNPJ quanto a razão social são links para `/empresa/<cnpj>/?next=/chat/` (regra 11 do `SYSTEM_PROMPT` e regra 8 do `resposta_natural`).

### 1.2 RAG — o que é recuperado e o que vai no prompt

O chat **é um RAG**, mas um RAG sobre **dados estruturados**, não sobre
documentos. A recuperação acontece em quatro pontos, e cada um entra no pipeline
num lugar diferente:

| Fonte | Classe | O que recupera | Onde entra |
|---|---|---|---|
| `schema_context.py` | **KB fixa** (escrita à mão) | DDL real, tamanhos, códigos de porte/situação, índices e regras de performance | direto no `SYSTEM_PROMPT` do `sql_generator`, em **toda** pergunta |
| `embedding_service.py` + pgvector | vetorial (`<=>`, cosseno) | até **2 CNAEs** vizinhos do termo, com `halfvec(384)` (Braza) | **dentro do SQL**, como filtro `cnae_principal IN (...)` — e só quando o `ILIKE` não acha nenhum CNAE |
| `sql_executor.py` | SQL (a consulta em si) | as linhas que a consulta devolveu (até `CHAT_SQL_MAX_ROWS`, padrão 1000) | as **20 primeiras** (`LIMITE_LINHAS`) viram `Dados (JSON)` no prompt do `resposta_natural`, junto do **total real** e do aviso de truncamento |
| histórico da conversa | memória | as últimas 10 interações (`MAX_CONTEXTO`, até 20 mensagens) | no prompt do `sql_generator`, para continuidade ("e dessas em SC?") |

**Por que isso é RAG:** a geração final não responde de memória — ela recebe o
resultado da recuperação e é obrigada a usá-lo (`"Responda APENAS com base nos
dados fornecidos"`, `"NUNCA invente números"`), com temperatura `0.2`,
`max_tokens` 300 e `fallback` sem LLM (`_fallback_texto`) quando a chamada falha.
O efeito é o de RAG clássico: menos alucinação e resposta rastreável até a linha
do banco (o SQL executado fica visível na tela e em `chat_logacesso`).

**O que não é RAG aqui** (decisão registrada, não esquecimento):

- O **schema não é recuperado por similaridade**: `SCHEMA_CONTEXT` é fixo
  (decisão de 12/10/2026 — previsibilidade e custo de token).
- O **vetor não vai para o prompt**: ele vira filtro SQL (`<=>`), então o LLM
  nunca "lê" os CNAEs vizinhos; quem define o contexto é a consulta executada.
  E o vetor é **fallback** do `ILIKE`, nunca o caminho principal.
- Não há base de documentos/chunks nem framework de RAG (LangChain, LlamaIndex
  etc.): são serviços próprios (~250 linhas em `embedding_service.py`) + pgvector
  0.8.1.

**Na tela (o RAG visível):** o subtítulo do chat diz que a resposta é um RAG
sobre dados estruturados e, abaixo de cada resposta, um rodapé mostra a
procedência: `RAG · 12.345 linha(s) recuperada(s) pelo SQL, 20 enviada(s) ao
modelo`, com `· resultado truncado pelo LIMIT` quando for o caso. O número de
linhas enviadas vêm de `LIMITE_LINHAS` (a view passa como `linhas_no_prompt`),
então não há "20" duplicado no HTML/JS. Sem linha recuperada (pergunta recusada)
o rodapé não aparece — não existe geração ancorada para exibir. A lógica está em
`renderRodapeRag()` no `chat/templates/chat/chat.html`.

### 2. `sql_validator`

Aplica **4 camadas de segurança**:

1. **Só permite SELECT** - bloqueia DDL/DML (DROP, INSERT, UPDATE, DELETE, etc)
2. **Não permite múltiplas queries** - bloqueia `;` fora de strings
3. **Forca LIMIT** - garante que nenhuma query retorne mais que `DEFAULT_MAX_ROWS` (1000);
   exceto em agregações (GROUP BY ou COUNT/SUM/AVG/MIN/MAX), que não ganham LIMIT
4. **Bloqueia funções perigosas** - `pg_sleep`, `dblink`, `pg_read_file`, etc

### 3. `sql_executor`

- Conecta no Postgres como usuário read-only (`chat_ia`, só tem `GRANT SELECT`)
- `SET LOCAL statement_timeout` por query (`CHAT_SQL_TIMEOUT`, padrão 10s)
- Limita linhas com `CHAT_SQL_MAX_ROWS` (padrão: 1000)
- Fecha conexão sempre (`finally`)
- Detecta truncamento (busca `max_rows + 1` linhas)

### 4. `resposta_natural`

- Recebe pergunta + colunas + rows + flag `truncado` (e o `sql` executado)
- Gera 1-3 frases em PT-BR citando o total real
- Se falhar, cai num fallback simples (nunca levanta exceção)
- **Filtro por cidade (semi-join):** quando o SQL filtra com
  `e.cnpj_basico IN (SELECT ... FROM c_estabelecimentos ... município ILIKE
  '%X%')`, `extrair_cidade_semi_join()` descobre a cidade e o prompt avisa o
  LLM de que a lista é de **empresas com estabelecimento em X** (qualquer
  filial), não "sediadas em X". Sem esse aviso o texto citava a coluna
  `municipio` da matriz (ex: KOCH de Tijucas) como se o filtro tivesse
  falhado. Filtro direto (`e.municipio IN (...)`) não gera aviso: ali a
  coluna `municipio` é a própria cidade pedida.


### 5. Persistência (Repository pattern)

Backend alternável via `CHAT_USE_MONGO` no `.env`:

- `CHAT_USE_MONGO=0` -> Postgres (`django_internal`, ORM)
- `CHAT_USE_MONGO=1` -> MongoDB (`bancocnpj.chat_conversas`, pymongo)

O dispatcher fica em `chat/repo.py`. As views chamam `repo.*` e não sabem
qual backend está ativo. Retenção: comando `limpar_conversas_antigas`
(padrão: 30 dias).

### Retry automático

Se o Postgres rejeitar o SQL por coluna/tabela inexistente, o
`chat_api_view` faz **1 retry** passando a mensagem de erro pro LLM
corrigir. Isso resolve a maioria dos casos de nomes errados.

## Endpoints do chat

| Método | URL | O que faz |
|---|---|---|
| GET | `/chat/` | Página do chat |
| POST | `/chat/api/` | Endpoint AJAX (pergunta -> SQL -> resultado) |
| GET | `/chat/historico/` | Lista as conversas do usuário |
| GET | `/chat/conversa/<id>/` | Abre uma conversa específica |
| POST | `/chat/nova/` | Começa conversa nova |
| POST | `/chat/deletar/<id>/` | Deleta uma conversa |
| GET | `/chat/health/deep/` | Health check profundo (Mongo + Postgres) |

---

# Endpoints (todos)

| Método | URL | O que faz |
|---|---|---|
| GET | `/` | Lista de empresas |
| GET | `/busca-rapida/` | Busca rápida via AJAX (JSON) |
| GET | `/empresa/<cnpj>/` | Detalhe da empresa |
| GET | `/estatisticas/` | Estatísticas gerais |
| POST | `/estatisticas/recalcular/` | Recalcula `c_estatisticas_totais` (superuser) |
| GET | `/chat/` | Chat IA |
| POST | `/chat/api/` | API do chat |
| GET | `/chat/historico/` | Histórico de conversas |
| GET | `/chat/conversa/<id>/` | Abre conversa |
| POST | `/chat/nova/` | Nova conversa |
| POST | `/chat/deletar/<id>/` | Deleta conversa |
| POST | `/chat/limpar/` | (legado) limpa a conversa da sessão |
| GET | `/chat/health/deep/` | Health check profundo do chat |
| GET/POST | `/accounts/login/` | Login |
| POST | `/accounts/logout/` | Logout |
| GET/POST | `/admin/password_change/` | Trocar senha |
| GET | `/health/` | Health check simples |
| GET | `/health/deep/` | Health check profundo (Postgres) |

# Bancos de dados

O projeto usa **dois bancos PostgreSQL**:

| Alias | Banco | Para que |
|---|---|---|
| `default` | `django_internal` | auth, sessions, admin, chat (Postgres) |
| `postgres` | `bancocnpj` | dados da RFB (read-only para o chat) |

O `CNPJRouter` (`core/routers.py`) decide qual usar:

- Modelos do app `core` -> `postgres`
- Todo o resto -> `default`

O MongoDB (opcional, via `CHAT_USE_MONGO=1`) fica em `bancocnpj.chat_conversas`
e não passa pelo ORM.

# Log de acesso (`chat_logacesso`)

Tabela única de auditoria (em `default` / `django_internal`): `chat.LogAcesso`
em `chat/models.py`. No admin é **somente leitura** (ninguém cria, edita ou
apaga), com filtro por tipo/data, busca por IP e navegação por data. A lista
(`/admin/chat/logacesso/`) abre com um **painel de resumo** — ver "Painel na
tela do admin" abaixo.

| Tipo | Gravado por | Quando |
|---|---|---|
| `login` | `chat/signals.py` | login bem-sucedido (signal `user_logged_in`) |
| `chat_pergunta` | `chat/views.py` | pergunta enviada no chat |
| `empresas_lista` | `core/log_acesso.py` | abertura da lista (`/`) |
| `empresas_detalhe` | `core/log_acesso.py` | abertura do detalhe (`/empresa/<cnpj>/`) |

Cada linha guarda `usuario` (nullable), `ip`, `user_agent` (300 chars),
`detalhe` (até 500 chars — filtros usados na lista ou o CNPJ consultado) e
`criado_em`.

**IP**: vem do `X-Forwarded-For` do Nginx, mas do **último** valor do header.
O Nginx usa `$proxy_add_x_forwarded_for`, que **anexa** o IP real no fim — o
primeiro elemento é o que o visitante mandou e pode ser forjado (testado em
prod: um `X-Forwarded-For: 203.0.113.77` inventado virava o IP do registro).
Premissa: um único proxy confiável na frente do gunicorn; se entrar CDN, o
último elemento passa a ser o IP do CDN. Mesma regra em `chat/signals.py`
(`_pegar_ip`), usada no `login` e no `chat_pergunta`.

**Usuário**: a consulta de empresas é **pública**, então o evento fica com
`usuario = NULL` quando não há sessão autenticada. O log nunca "inventa" quem
acessou — quem identifica o visitante é IP + user agent.

**Sessão entre subdomínios** (`SESSION_COOKIE_DOMAIN`): em produção o `.env` do
servidor define `.arosasse.tech` (aplicado em 03/11/2026), e o login feito em um
subdomínio passa a valer nos outros. Consequência: quem loga no chat aparece
autenticado na consulta e o evento é gravado com o usuário real (`demo`) em vez de
anônimo. Localmente a variável fica **vazia** (cookie por host) — com domínio fixo
o navegador recusa o cookie em `localhost` e o login local para de funcionar. O
`CSRF_COOKIE_DOMAIN` continua **por host**, de propósito (token CSRF compartilhado
amplia a superfície de ataque sem necessidade). Rollback: apagar a linha do `.env`
e `systemctl restart cnpj.service`.

**Decisão registrada**: *não* atribuir visitas anônimas ao usuário `demo`
automaticamente. O `demo` só aparece no log quando alguém está **de fato**
logado (sessão compartilhada). Carimbar `demo` em todo mundo zeraria a métrica
de IPs distintos e faria o relatório dizer "1 usuário" para sempre — parece
auditoria, mas não é.

**Deduplicação**: `core/log_acesso.py` grava no máximo um evento por
`(tipo, IP, detalhe)` a cada **10 minutos** (chave no cache), pra paginação,
F5 e robôs não inflarem a tabela. Falha de log nunca derruba a consulta
(`try/except` + `logger.exception`), e a janela é liberada pra nova tentativa.

Relatório:

    python manage.py relatorio_acesso                  # últimos 7 dias
    python manage.py relatorio_acesso --dias 30
    python manage.py relatorio_acesso --tipo empresas_lista

Mostra totais por tipo, evolução por dia, usuários distintos e top 10 IPs
(chat e empresas). Os números vêm de `chat/relatorio.py::resumo_acesso`, a
mesma função que alimenta o painel do admin — comando e tela não divergem.

## Painel na tela do admin

`/admin/chat/logacesso/` (superusuário) mostra, **antes** da lista de
registros, um painel com:

- cartões: eventos no período, usuários que logaram, que perguntaram ao chat e
  logados que viram empresas;
- totais por tipo, top 10 IPs (perguntas no chat / acessos a empresas);
- tabela por dia (uma coluna por tipo presente + total);
- **últimos logins** do período linha a linha: data/hora, usuário, IP e
  navegador (o que o antigo `manage.py shell -c "..."` imprimia);
- bloco **Comandos (copiar e colar)** com o relatório (servidor e local) e o
  shell de últimos logins (servidor e local), cada um com botão **Copiar**.

A tela **não executa** comando nenhum — só mostra o texto (executar shell via
HTTP seria execução remota de código com sessão de superusuário).

Período: links de **1 / 7 / 30 / 90 dias** (padrão: 7). Eles montam
`?criado_em__gte=<data com offset>`, lookup que o próprio `ChangeList` aceita e
aplica na lista — painel e tabela mostram o mesmo período, e os filtros/busca
já ativos na URL são preservados. **Não** usar um parâmetro próprio (ex.:
`?dias=90`): o admin só aceita lookups válidos e responde `302 ...?e=1`
descartando o filtro.

Arquivos: `chat/relatorio.py` (consultas), `chat/admin.py`
(`LogAcessoAdmin.changelist_view` + `_comandos`) e
`chat/templates/admin/chat/logacesso/change_list.html` (markup/estilo).
Testes em `chat/tests/test_admin_log.py`.

# Testes

    python manage.py test chat       # 245 testes do chat
    python manage.py test core       # 14 testes do core

Roda **245 testes** em `chat/tests/` (+ 14 em `core/tests.py`) = 259 no total:

- `test_embedding_service.py` - geração de vetor, sinônimos e substituição do `%s` no SQL
- `test_models.py` - modelos `Conversa` + `Mensagem` (Postgres)
- `test_mongo_models.py` - funções pymongo (`chat_conversas`)
- `test_sql_generator.py` - geração de SQL + retry + histórico
- `test_sql_validator.py` - 4 camadas de segurança + agregação sem LIMIT
- `test_sql_executor.py` - execução + timeout + truncamento
- `test_resposta_natural.py` - geração de texto (com mock do LLM)
- `test_integracao.py` - fluxo fim-a-fim (roda nos 2 backends)
- `test_signals.py` - extração de IP do `X-Forwarded-For` (login/pergunta)
- `test_admin_log.py` - `resumo_acesso`, comando `relatorio_acesso` e painel do admin
- `core/tests.py` - template do detalhe (município/UF) + log de acesso das páginas de empresas

Os testes usam mock do DeepSeek e do `psycopg2.connect`. **`test_mongo_models`
e `test_integracao` (no modo Mongo) usam o MongoDB real e limpam a collection
`chat_conversas`** - não rodar com dados reais.

# Variáveis de ambiente

Ver `.env.example` para a lista completa. As principais:

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` - Django padrão
- `CSRF_TRUSTED_ORIGINS` - origens confiáveis para POST (produção: os dois subdomínios)
- `SESSION_COOKIE_DOMAIN` - domínio do cookie de sessão (produção: `.arosasse.tech`;
  vazio = cookie por host, obrigatório para o login funcionar em `localhost`)
- `DJANGO_DB_*` - banco `django_internal`
- `PG_*` - banco `bancocnpj` (dados da RFB)
- `CHAT_PG_*` - usuário read-only `chat_ia`
- `CHAT_SQL_TIMEOUT` - timeout por query (padrão: 10s)
- `CHAT_SQL_MAX_ROWS` - limite de linhas (padrão: 1000)
- `CHAT_USE_MONGO` - `0` = Postgres (padrão), `1` = MongoDB
- `MONGO_*` - conexão MongoDB (host, porta, db, user, password)
- `DEEPSEEK_API_KEY` - chave da API do chat
- `CHAT_DEEPSEEK_MODEL` - modelo (padrão: `deepseek-chat`)
- `CHAT_DEEPSEEK_BASE_URL` - endpoint compatível OpenAI (padrão: `https://api.deepseek.com`)
- `AS400_*` - conexão com o IBM i (`AS400_JDBC_JAR` aponta pro `lib/jt400.jar`; usada pelo `testar_simple.py` e pelas conexões de `core/conexoes.py`)

# Deploy

Na máquina NoteNeto:

    ./deploy.sh cnpj

# Ambiente

- **Local:** http://localhost:8002
- **Produção:** https://cnpj.arosasse.tech
- **Chat isolado:** https://chat.arosasse.tech

# Scripts de manutenção

Os scripts `cnpj_project/atualizar_dt_ini_*.py` atualizam a coluna `dt_ini`
da tabela `c_estabelecimentos` a partir de arquivos Parquet. São
executados pontualmente, não fazem parte do runtime da aplicação.

O script `testar_simple.py` testa as conexões com AS/400, PostgreSQL e
MongoDB de uma vez.

# Repositórios relacionados

- [ProjetoCargaDados](https://github.com/antoniorosasse/ProjetoCargaDados) - carrega os dados que este projeto consulta
- [as400_interface](https://github.com/antoniorosasse/as400_interface)
- [menu_interface](https://github.com/antoniorosasse/menu_interface)
