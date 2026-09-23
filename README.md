# CNPJ Interface

Consulta de empresas da Receita Federal (dados publicos de CNPJ) + Chat IA
que traduz perguntas em linguagem natural para SQL.

## Stack

- Django 5.1.7
- Python 3.13
- PostgreSQL 15 (dois bancos: `django_internal` e `bancocnpj`)
- psycopg2-binary
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
    |   +-- models.py               # EmpresaCompleta (view read-only)
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
    |   +-- templates/chat/
    |   |   +-- chat.html
    |   |   +-- historico.html
    |   +-- tests/                  # 72 testes
    |   |   +-- test_models.py
    |   |   +-- test_sql_generator.py
    |   |   +-- test_sql_validator.py
    |   |   +-- test_sql_executor.py
    |   +-- management/commands/
    |   |   +-- limpar_conversas_antigas.py
    |   +-- models.py               # Conversa + Mensagem
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

    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env            # preencher valores
    (baixar lib/jt400.jar - ver lib/README.md)
    python manage.py migrate
    python manage.py runserver 8002

Acessa em http://localhost:8002/

## Endpoints

### Consulta de empresas

| Metodo | URL | O que faz |
|---|---|---|
| GET | `/` | Lista de empresas (com filtros e paginacao) |
| GET | `/busca-rapida/` | Busca rapida via AJAX |
| GET | `/empresa/<cnpj>/` | Detalhe da empresa (socios, filiais, CNAE) |
| GET | `/estatisticas/` | Estatisticas gerais (com cache de 1h) |
| GET | `/health/` | Health check |

### Chat IA

| Metodo | URL | O que faz |
|---|---|---|
| GET | `/chat/` | Pagina do chat (conversa atual) |
| POST | `/chat/api/` | Endpoint AJAX (pergunta -> SQL -> resultado) |
| GET | `/chat/historico/` | Lista as conversas do usuario |
| GET | `/chat/conversa/<id>/` | Abre uma conversa especifica |
| POST | `/chat/nova/` | Comeca conversa nova |
| POST | `/chat/deletar/<id>/` | Deleta uma conversa |

### Autenticacao

| Metodo | URL | O que faz |
|---|---|---|
| GET/POST | `/accounts/login/` | Login |
| POST | `/accounts/logout/` | Logout |
| GET/POST | `/admin/password_change/` | Trocar senha |

## Pipeline do chat IA

O chat tem 4 estagios:

    Pergunta (PT-BR)
      -> sql_generator    (DeepSeek gera o SQL)
      -> sql_validator    (4 camadas de seguranca)
      -> sql_executor     (executa read-only)
      -> resposta final

### 1. `sql_generator`

- Monta prompt com o **schema real** do banco (tabelas, colunas, tamanhos, codigos)
- Chama o DeepSeek via `llm_client.gerar_resposta()`
- Retenta ate `max_tentativas` se o SQL vier invalido
- Passa o historico da conversa como contexto

### 2. `sql_validator`

Aplica **4 camadas de seguranca**:

1. **So permite SELECT** - bloqueia DDL/DML (DROP, INSERT, UPDATE, DELETE, etc)
2. **Nao permite multiplas queries** - bloqueia `;` fora de strings
3. **Forca LIMIT** - garante que nenhuma query retorne mais que `DEFAULT_MAX_ROWS` (1000)
4. **Bloqueia funcoes perigosas** - `pg_sleep`, `dblink`, `pg_read_file`, etc

### 3. `sql_executor`

- Conecta no Postgres como usuario read-only (`chat_ia`, so tem `GRANT SELECT`)
- `SET LOCAL statement_timeout` por query (configuravel via `CHAT_SQL_TIMEOUT`)
- Limita linhas com `CHAT_SQL_MAX_ROWS` (padrao: 1000)
- Fecha conexao sempre (`finally`)
- Detecta truncamento (busca `max_rows + 1` linhas)

### 4. Persistencia

- `Conversa` e `Mensagem` ficam no banco `default` (`django_internal`)
- Cada mensagem do assistente guarda o SQL executado, total de linhas e tempo
- Retencao: comando `limpar_conversas_antigas` (padrao: 30 dias)

## Bancos de dados

O projeto usa **dois bancos PostgreSQL**:

| Alias | Banco | Para que |
|---|---|---|
| `default` | `django_internal` | auth, sessions, admin, chat |
| `postgres` | `bancocnpj` | dados da RFB (read-only para o chat) |

O `CNPJRouter` (`core/routers.py`) decide qual usar:

- Modelos do app `core` -> `postgres`
- Todo o resto -> `default`

## Testes

    python manage.py test chat

Roda **72 testes**:

- `test_models.py` - 9 testes (Conversa + Mensagem)
- `test_sql_generator.py` - 20 testes (geracao + retry + historico)
- `test_sql_validator.py` - 24 testes (4 camadas de seguranca)
- `test_sql_executor.py` - 17 testes (execucao + timeout + truncamento)

Os testes usam mock do DeepSeek e do `psycopg2.connect` - nao chamam
a API real nem o banco real.

## Variaveis de ambiente

Ver `.env.example` para a lista completa. As principais:

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` - Django padrao
- `DJANGO_DB_*` - banco `django_internal`
- `PG_*` - banco `bancocnpj` (dados da RFB)
- `CHAT_PG_*` - usuario read-only `chat_ia`
- `CHAT_SQL_TIMEOUT` - timeout por query (padrao: 10s)
- `CHAT_SQL_MAX_ROWS` - limite de linhas (padrao: 1000)
- `DEEPSEEK_API_KEY` - chave da API do chat
- `CHAT_DEEPSEEK_MODEL` - modelo (padrao: `deepseek-chat`)

## Deploy

Na maquina NoteNeto:

    ./deploy.sh cnpj

## Ambiente

- **Local:** http://localhost:8002
- **Producao:** https://cnpj.arosasse.tech
- **Chat isolado:** https://chat.arosasse.tech

## Scripts de manutencao

Os scripts `cnpj_project/atualizar_dt_ini_*.py` atualizam a coluna `dt_ini`
da tabela `c_estabelecimentos` a partir de arquivos Parquet. Sao
executados pontualmente, nao fazem parte do runtime da aplicacao.

## Repositorios relacionados

- [ProjetoCargaDados](https://github.com/antoniorosasse/ProjetoCargaDados) - carrega os dados que este projeto consulta
- [as400_interface](https://github.com/antoniorosasse/as400_interface)
- [menu_interface](https://github.com/antoniorosasse/menu_interface)
- [Projetos](https://github.com/antoniorosasse/Projetos) - scripts e manual de git
