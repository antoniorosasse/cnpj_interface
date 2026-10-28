# CNPJ Interface

Consulta de empresas da Receita Federal (dados publicos de CNPJ) + Chat IA
que traduz perguntas em linguagem natural para SQL.

## Stack

- Django 6.1.1
- Python 3.13
- PostgreSQL 15 (dois bancos: `django_internal` e `bancocnpj`)
- MongoDB 8 (backend alternativo do chat, via `CHAT_USE_MONGO=1`)
- psycopg2-binary, pymongo
- DeepSeek (chat IA via `openai` SDK)
- Gunicorn + systemd (producao)

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
    |   +-- tests/                  # 170 testes
    |   |   +-- test_integracao.py
    |   |   +-- test_models.py
    |   |   +-- test_mongo_models.py
    |   |   +-- test_resposta_natural.py
    |   |   +-- test_sql_executor.py
    |   |   +-- test_sql_generator.py
    |   |   +-- test_sql_validator.py
    |   +-- management/commands/
    |   |   +-- limpar_conversas_antigas.py
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

## Como rodar local

Pre-requisitos:

- Python 3.13
- PostgreSQL 15 rodando em `localhost:5432`
- MongoDB 8 rodando em `localhost:27017` (so se for usar `CHAT_USE_MONGO=1`)

Passos:

    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env            # preencher valores
    (baixar lib/jt400.jar - ver lib/README.md)
    python manage.py migrate
    python manage.py runserver 8002

Acessa em http://localhost:8002/

Observacoes:

- O projeto assume **dois bancos PostgreSQL**: `django_internal` (auth,
  sessions, admin, chat) e `bancocnpj` (dados da RFB). Ambos precisam
  existir antes do `migrate`.
- O `.env.example` tem todas as variaveis. Sem `DEEPSEEK_API_KEY`, o chat
  nao funciona (mas o resto sim).

  
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
com filtros avancados, paginacao rapida e detalhamento completo de cada
empresa.

## O que da pra buscar

A pagina inicial (`/`) lista empresas (matrizes) com filtros combinaveis:

| Filtro | O que aceita | Exemplo |
|---|---|---|
| Razao social | Texto parcial (case-insensitive) | `MORAIS` |
| Nome fantasia | Texto parcial (case-insensitive) | `MERCADO` |
| CNPJ | 8 digitos (basico) ou 14 (completo) | `12345678` ou `12345678000199` |
| UF | 2 letras | `SP` |
| Situacao | Nula, Ativa, Suspensa, Inapta, Baixada | `ATIVA` |
| Simples | S ou N | `S` |
| MEI | S ou N | `S` |

### Ordenacao

A lista pode ser ordenada por:

- **CNPJ** (padrao) - mais rapido
- **Razao social** - usa INNER JOIN com `c_empresas`
- **Nome fantasia** - mesmo esquema

### Paginacao

Usa **keyset pagination** (nao OFFSET). Isso significa que a pagina 5000
carrega tao rapido quanto a pagina 1, mesmo com 38 milhoes de linhas.

O botao "Proxima" passa o ultimo CNPJ/razao da pagina atual como ponto de
partida. O botao "Anterior" faz o mesmo, ao contrario. A contagem total
usa a tabela de metadados `c_estatisticas_totais` quando nao ha filtros,
e cache de 15min com timeout de 800ms quando ha filtros combinados.

## O que aparece no detalhe da empresa

A pagina `/empresa/<cnpj>/` mostra **tudo** que a Receita publica sobre
aquela empresa. Dividido em secoes:

### Identificacao

- CNPJ formatado (`00.000.000/0000-00`)
- Razao social
- Nome fantasia
- Tipo: **MATRIZ** ou **FILIAL**

### Dados cadastrais

- **Natureza juridica** - codigo + descricao (ex: `206-2 - Sociedade Empresaria Limitada`)
- **Porte** - ME (Micro Empresa), EPP (Empresa de Pequeno Porte), Demais, ou Nao informado
- **Capital social** - em R$
- **Data de abertura** (`dt_ini`)
- **Qualificacao do responsavel** - codigo

### Situacao cadastral

- **Situacao** - Ativa, Suspensa, Inapta, Baixada, Nula
- **Data da situacao** (`dt_sit_cadastro`)
- **Motivo** - codigo + descricao (ex: `01 - Extincao por Encerramento de Liquidacao`)

### Simples Nacional e MEI

Dois cards lado a lado:

- **Simples Nacional**: status (SIM/NAO), data de opcao, data de exclusao
- **MEI**: status (SIM/NAO), data de opcao, data de exclusao

### Atividade economica (CNAE)

- **CNAE principal** - codigo + descricao
- **CNAE secundarios** - lista de codigos separados por virgula

### Endereco e contato

- Logradouro (tipo + nome), numero, complemento, bairro
- **CEP**
- **Municipio** - codigo IBGE + nome
- **UF**
- **Pais** - codigo + nome (Brasil, na maioria)
- **Telefone 1** e **Telefone 2** - formatados com DDD
- **Fax**
- **Email**
- **Situacao especial** + data (se houver)

### Estabelecimentos (matriz + filiais)

Tabela com **todos** os estabelecimentos daquela empresa:

- CNPJ completo de cada um
- Tipo (Matriz / Filial)
- Nome fantasia
- Situacao cadastral
- Endereco resumido
- UF
- Telefone
- Email
- Data de abertura
- CNAE principal

A matriz aparece destacada (linha azul). As filiais vem em seguida,
ordenadas por `cnpj_ordem`.

### Socios

Tabela com todos os socios:

- **Nome** (ou razao social, se for PJ)
- **Tipo** - PF (Pessoa Fisica), PJ (Pessoa Juridica) ou Estrangeiro
- **CPF/CNPJ** - descaracterizado pela RFB (so os 6 digitos do meio)
- **Qualificacao** - codigo + descricao (ex: `49 - Socio-Administrador`)
- **Data de entrada** na sociedade
- **Faixa etaria** - 0-12, 13-20, ..., 81+ anos
- **Pais** (se estrangeiro)
- **Representante legal** (se houver) - nome + qualificacao

## Estatisticas (`/estatisticas/`)

Pagina com visao agregada da base inteira:

- **Totais**: matrizes, ativas, inaptas, baixadas, suspensas, nulas, UFs
- **Top 10 UFs** - com percentual
- **Distribuicao por situacao** - com percentual
- **Distribuicao por porte** - ME, EPP, Demais, Nao informado
- **Top 10 naturezas juridicas** - codigo + descricao
- **Top 10 CNAEs** - codigo + descricao
- **Optantes Simples / MEI** - total
- **Empresas por ano de abertura** - ultimos 15 anos (com barra de progresso)
- **Top 10 municipios** - codigo IBGE + nome + UF
- **Total de socios** + empresas com socios

Os dados vem da tabela `c_estatisticas_totais`, populada pela stored
procedure `sp_recalcular_estatisticas_totais()`. Superusuario pode
disparar o recalculo pelo botao na propria pagina.

## Performance

A base tem ~38M de linhas em `c_estabelecimentos`. Pra aguentar isso:

- **Indices parciais** com `WHERE cd_matriz_filial = 1` (matrizes sao ~10% da tabela)
- **Keyset pagination** em vez de OFFSET
- **Tabela de metadados** `c_estatisticas_totais` pra contagens instantaneas
- **ILIKE** em vez de `UPPER(col) LIKE` (aproveita indices GIN trigram)
- **Timeout de seguranca** de 10s em ordenacoes por nome fantasia
- **Fast-count** com timeout de 800ms e cache de 15min em filtros combinados

O tempo de resposta tipico e:

- Primeira pagina sem filtro: ~5ms (le da tabela de metadados)
- Paginas seguintes (keyset): ~200ms
- Estatisticas: ~5ms (le da tabela de metadados)
- Recálculo completo: ~15-30s

---

# Chat IA

Traduz perguntas em portugues pra SQL e executa no banco, em modo
read-only. Tem historico de conversas e contexto.

## Pipeline

    Pergunta (PT-BR)
      -> sql_generator    (DeepSeek gera o SQL)
      -> sql_validator    (4 camadas de seguranca)
      -> sql_executor     (executa read-only)
      -> resposta_natural (1-3 frases em PT-BR via DeepSeek)
      -> resposta final

### 1. `sql_generator`

- Monta prompt com o **schema real** do banco (tabelas, colunas, tamanhos, codigos)
- Chama o DeepSeek via `llm_client.gerar_resposta()`
- Retenta ate `max_tentativas` se o SQL vier invalido
- Passa o historico da conversa como contexto (ultimas 10 mensagens)
- Pode responder `RECUSAR: <motivo>` quando a pergunta pede dado que nao
  existe no schema (ex: faturamento, numero de funcionarios)
  ### 1.1 Busca semântica (vetorial)

  Quando a pergunta for sobre um **conceito** (ex: "empresas de energia solar") e não houver um código CNAE explícito, o `sql_generator` pode gerar um SQL com a cláusula:

  ```sql
  ORDER BY embedding <=> %s::halfvec(384)

  O chat/services/embedding_service.py gera o vetor da pergunta (usando o modelo calneymgp/braza-embedding-ptbr-v1) e substitui o %s antes da execução. A tabela c_cnaes tem a coluna embedding (halfvec(384)) populada com os vetores das descrições dos CNAEs. O schema_context.py documenta essa coluna e o operador <=> para o LLM.

  Custo de disco: < 2 MB (apenas 1.359 CNAEs).
  Performance: ~30ms em produção.

### 2. `sql_validator`

Aplica **4 camadas de seguranca**:

1. **So permite SELECT** - bloqueia DDL/DML (DROP, INSERT, UPDATE, DELETE, etc)
2. **Nao permite multiplas queries** - bloqueia `;` fora de strings
3. **Forca LIMIT** - garante que nenhuma query retorne mais que `DEFAULT_MAX_ROWS` (1000);
   exceto em agregacoes (GROUP BY ou COUNT/SUM/AVG/MIN/MAX), que nao ganham LIMIT
4. **Bloqueia funcoes perigosas** - `pg_sleep`, `dblink`, `pg_read_file`, etc

### 3. `sql_executor`

- Conecta no Postgres como usuario read-only (`chat_ia`, so tem `GRANT SELECT`)
- `SET LOCAL statement_timeout` por query (`CHAT_SQL_TIMEOUT`, padrao 10s)
- Limita linhas com `CHAT_SQL_MAX_ROWS` (padrao: 1000)
- Fecha conexao sempre (`finally`)
- Detecta truncamento (busca `max_rows + 1` linhas)

### 4. `resposta_natural`

- Recebe pergunta + colunas + rows + flag `truncado`
- Gera 1-3 frases em PT-BR citando o total real
- Se falhar, cai num fallback simples (nunca levanta excecao)

### 5. Persistencia (Repository pattern)

Backend alternavel via `CHAT_USE_MONGO` no `.env`:

- `CHAT_USE_MONGO=0` -> Postgres (`django_internal`, ORM)
- `CHAT_USE_MONGO=1` -> MongoDB (`bancocnpj.chat_conversas`, pymongo)

O dispatcher fica em `chat/repo.py`. As views chamam `repo.*` e nao sabem
qual backend esta ativo. Retencao: comando `limpar_conversas_antigas`
(padrao: 30 dias).

### Retry automatico

Se o Postgres rejeitar o SQL por coluna/tabela inexistente, o
`chat_api_view` faz **1 retry** passando a mensagem de erro pro LLM
corrigir. Isso resolve a maioria dos casos de nomes errados.

## Endpoints do chat

| Metodo | URL | O que faz |
|---|---|---|
| GET | `/chat/` | Pagina do chat |
| POST | `/chat/api/` | Endpoint AJAX (pergunta -> SQL -> resultado) |
| GET | `/chat/historico/` | Lista as conversas do usuario |
| GET | `/chat/conversa/<id>/` | Abre uma conversa especifica |
| POST | `/chat/nova/` | Comeca conversa nova |
| POST | `/chat/deletar/<id>/` | Deleta uma conversa |
| GET | `/chat/health/deep/` | Health check profundo (Mongo + Postgres) |

---

# Endpoints (todos)

| Metodo | URL | O que faz |
|---|---|---|
| GET | `/` | Lista de empresas |
| GET | `/busca-rapida/` | Busca rapida via AJAX (JSON) |
| GET | `/empresa/<cnpj>/` | Detalhe da empresa |
| GET | `/estatisticas/` | Estatisticas gerais |
| POST | `/estatisticas/recalcular/` | Recalcula `c_estatisticas_totais` (superuser) |
| GET | `/chat/` | Chat IA |
| POST | `/chat/api/` | API do chat |
| GET | `/chat/historico/` | Historico de conversas |
| GET | `/chat/conversa/<id>/` | Abre conversa |
| POST | `/chat/nova/` | Nova conversa |
| POST | `/chat/deletar/<id>/` | Deleta conversa |
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
e nao passa pelo ORM.

# Testes

    python manage.py test chat

Roda **170 testes** em `chat/tests/`:

- `test_models.py` - modelos `Conversa` + `Mensagem` (Postgres)
- `test_mongo_models.py` - funcoes pymongo (`chat_conversas`)
- `test_sql_generator.py` - geracao de SQL + retry + historico
- `test_sql_validator.py` - 4 camadas de seguranca + agregacao sem LIMIT
- `test_sql_executor.py` - execucao + timeout + truncamento
- `test_resposta_natural.py` - geracao de texto (com mock do LLM)
- `test_integracao.py` - fluxo fim-a-fim (roda nos 2 backends)

Os testes usam mock do DeepSeek e do `psycopg2.connect`. **`test_mongo_models`
e `test_integracao` (no modo Mongo) usam o MongoDB real e limpam a collection
`chat_conversas`** - nao rodar com dados reais.

# Variaveis de ambiente

Ver `.env.example` para a lista completa. As principais:

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` - Django padrao
- `DJANGO_DB_*` - banco `django_internal`
- `PG_*` - banco `bancocnpj` (dados da RFB)
- `CHAT_PG_*` - usuario read-only `chat_ia`
- `CHAT_SQL_TIMEOUT` - timeout por query (padrao: 10s)
- `CHAT_SQL_MAX_ROWS` - limite de linhas (padrao: 1000)
- `CHAT_USE_MONGO` - `0` = Postgres (padrao), `1` = MongoDB
- `MONGO_*` - conexao MongoDB (host, porta, db, user, password)
- `DEEPSEEK_API_KEY` - chave da API do chat
- `CHAT_DEEPSEEK_MODEL` - modelo (padrao: `deepseek-chat`)

# Deploy

Na maquina NoteNeto:

    ./deploy.sh cnpj

# Ambiente

- **Local:** http://localhost:8002
- **Producao:** https://cnpj.arosasse.tech
- **Chat isolado:** https://chat.arosasse.tech

# Scripts de manutencao

Os scripts `cnpj_project/atualizar_dt_ini_*.py` atualizam a coluna `dt_ini`
da tabela `c_estabelecimentos` a partir de arquivos Parquet. Sao
executados pontualmente, nao fazem parte do runtime da aplicacao.

O script `testar_simple.py` testa as conexoes com AS/400, PostgreSQL e
MongoDB de uma vez.

# Repositorios relacionados

- [ProjetoCargaDados](https://github.com/antoniorosasse/ProjetoCargaDados) - carrega os dados que este projeto consulta
- [as400_interface](https://github.com/antoniorosasse/as400_interface)
- [menu_interface](https://github.com/antoniorosasse/menu_interface)
