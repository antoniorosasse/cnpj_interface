# Regras do projeto CNPJ Interface

Este arquivo e lido automaticamente pelo Cline no inicio de **toda** conversa
neste workspace. Mantenha-o enxuto e versionado no git.

## Onde esta o que

| Assunto | Arquivo |
|---|---|
| Regras globais (valem nos 4 projetos: menu, as400, cnpj, carga) | `~/Documentos/Cline/Rules/00-sistema-arosasse.md` |
| Estado atual, pendencias, decisoes e arquitetura | `/home/neto/Projetos/docs/PROJETOS.md` |
| Comandos (dev/prod, queries, git, djLint) | `/home/neto/Projetos/docs/COMANDOS.md` |
| Historico detalhado (blocos 1-32, com commits e erros) | `/home/neto/Projetos/docs/HISTORICO.md` |
| Briefing portatil (para colar em outra IA) | `/home/neto/Projetos/docs/TODOS_PROJETOS.md` |
| Este arquivo | regras especificas do `cnpj_interface` |

Nao repita aqui o que ja esta nas regras globais (R1-R16) nem duplique o estado
atual: se algo mudar, atualize o arquivo correspondente.

## Idioma e estilo

- Responda sempre em **portugues do Brasil (pt-BR)**.
- Codigo, comentarios e docstrings: **sem acentos** (ex: `situacao`, `nao`,
  `codigo`). Prosa em Markdown (.md) pode usar acentos normalmente.
- Comentarios e docstrings em PT-BR. Nao traduza nomes de tabelas/colunas da
  RFB (`c_estabelecimentos`, `cd_matriz_filial`, etc.).
- Siga o estilo existente dos arquivos vizinhos (4 espacos, aspas simples,
  sem aspas em SQL quando o arquivo ja usa triple-quote).

## Stack e arquitetura

- Django 6.1.1, Python 3.13, PostgreSQL 15 (**dois bancos**:
  `django_internal` para auth/chat e `bancocnpj` com as tabelas `c_*`),
  MongoDB 8 opcional (`CHAT_USE_MONGO=1`), DeepSeek via SDK `openai`.
- Apps: `core` (consulta de CNPJ: views lista/detalhe/estatisticas) e
  `chat` (pergunta em PT-BR -> SQL -> resposta natural).
- Pacote Django: **`cnpj_project/`** (`settings.py`, `urls.py`, `wsgi.py`).
  `cnpj_interface` e o repositorio/pasta, nao o pacote.
- Chat em camadas, nao pule etapas:
  `sql_generator` -> `sql_validator` (4 camadas de seguranca) -> `sql_executor`
  (read-only) -> `resposta_natural`.
- Persistencia do chat e polimorfica: `chat/repo.py` despacha para
  `repo_postgres.py` ou `repo_mongo.py`. **Toda mudanca de modelo deve ser
  feita nos dois backends** (`models.py` + `mongo_models.py` +
  `repo_*.py`) e coberta por teste nos dois modos.

## Comandos do projeto

    source venv/bin/activate
    python manage.py runserver 8002          # app em http://localhost:8002/
    python manage.py test chat               # 170 testes em chat/tests/
    python manage.py test chat.tests.test_sql_validator   # teste isolado
    CHAT_USE_MONGO=1 python manage.py test chat.tests.test_integracao  # backend Mongo

- Use sempre o Python do `venv/` (`venv/bin/python`). Nunca instale pacotes
  globalmente e nao altere `requirements.txt` sem pedir.
- `CHAT_USE_MONGO=1` no teste de integracao **apaga** a collection
  `chat_conversas`: nunca rode isso contra dados reais.

## Regras de seguranca (nao negociaveis)

- Nunca commitar/alterar segredos: `.env`, `.env.*`, credenciais, `lib/jt400.jar`.
- Nunca rodar SQL de escrita/DDL no `bancocnpj` e nunca desativar as validacoes
  do `sql_validator` para "fazer funcionar".
- Nao execute `git commit`, `git push`, `migrate` em producao, `collectstatic`
  ou `sp_recalcular_estatisticas_totais()` sem pedir confirmacao explicita.
- Proibido criar arquivos de backup manuais (`*.bak`, `*.bak_*`) no repo.

## Convencoes de implementacao

- **Schema e fonte da verdade do LLM**: `chat/services/schema_context.py`.
  Se uma coluna, codigo, indice ou tabela mudar, atualize esse arquivo e o
  README na mesma tarefa.
- **Performance** (base com ~38M linhas em `c_estabelecimentos`): mantenha
  keyset pagination (nunca OFFSET), indices parciais com
  `cd_matriz_filial = 1`, `ILIKE` (nunca `UPPER(col) LIKE`) e leitura de
  contagens em `c_estatisticas_totais`.
- Novas funcionalidades de consulta seguem o padrao de `core/views.py`;
  formatacao de numeros fica em `core/templatetags/num_filters.py`.
- Templates: `core/templates/core/`, `chat/templates/chat/`, base em
  `core/templates/core/base.html` (crispy-bootstrap5).
- Antes de finalizar qualquer tarefa que toque `chat/services/` ou modelos do
  chat: rodar `python manage.py test chat` e relatar o resultado.
- Prefira editar arquivos existentes em vez de criar novos modulos; se criar,
  siga a estrutura de pastas atual.
- Documente decisoes em `README.md` (ele e a documentacao oficial do projeto).
