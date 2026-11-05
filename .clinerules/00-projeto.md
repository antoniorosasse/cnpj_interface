# Regras do projeto CNPJ Interface

Este arquivo e lido automaticamente pelo Cline no inicio de **toda** conversa
neste workspace. Mantenha-o enxuto e versionado no git.

## Onde esta o que

| Assunto | Arquivo |
|---|---|
| Regras globais (valem nos 4 projetos: menu, as400, cnpj, carga) | `~/Documentos/Cline/Rules/00-sistema-arosasse.md` |
| Estado atual, pendencias, decisoes e arquitetura | `/home/neto/Projetos/docs/PROJETOS.md` |
| Comandos (dev/prod, queries, git, djLint) | `/home/neto/Projetos/docs/COMANDOS.md` |
| Historico detalhado (com commits e erros) | `/home/neto/Projetos/docs/HISTORICO.md` |
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
- Log de acesso (`chat.LogAcesso`, tabela `chat_logacesso`): as consultas ficam
  em `chat/relatorio.py::resumo_acesso`, usadas pelo comando
  `manage.py relatorio_acesso` e pelo painel de `/admin/chat/logacesso/`.
  Nao duplique esse SQL: mudou a metrica, muda ali. A tela **nao executa**
  comando nenhum (so mostra o texto para copiar).
- **Link do Google Maps**: `core/maps.py` (`montar_maps_url`). A ordem e
  **rua + numero -> endereco em texto** (`logradouro, numero, bairro, municipio,
  UF`), **sem numero -> coordenada** de `c_ceps` (JOIN `cc.cep = e.cep` em
  `sql_empresa`/`sql_filiais`), **sem coordenada -> texto**. Nao "conserte" isso
  voltando a coordenada primeiro: o pino de `c_ceps` e do **CEP**, isto e, a
  media do trecho que ele cobre. Na Rua Blumenau (Joinville/SC) o CEP 89204-251
  fica ~1,2 km do numero 3011 (o CEP aponta para a America, o numero esta em
  Santo Antonio), e no CEP generico (ex.: 89669-000 Ipira/SC) a coordenada e o
  centroide do municipio. O CEP **nao** entra no texto quando ha rua - com o CEP
  na busca o Maps nao acha a rua e, sem hifen, le "89204251" como numero de
  porta; ele so aparece formatado quando nao ha logradouro. No CEP generico
  (`c_ceps.generico = true`, CEP do municipio inteiro) sai tambem o bairro: na
  Receita vem "CENTRO" para tudo. Base: 90,1% dos estabelecimentos tem rua +
  numero (vao para o texto) e 9,9% ficam na coordenada. A URL
  `maps/search/?api=1&query=` e publica (nao precisa de chave).
  `c_ceps` e carregada pelo **ProjetoCargaDados** (`manage.py carregar_ceps`):
  este repo so le a tabela. Em **producao** a tabela existe desde 05/11/2026
  (1.256.551 linhas, 203 MB), criada por **restauracao** (CSV + `COPY`), porque
  o ProjetoCargaDados **nao existe na VPS**. Para atualizar: rodar o ETL local e
  reenviar com `~/Projetos/restaura_ceps.sh` (ver `docs/COMANDOS.md`). Nunca
  `pg_dump -Fc` entre as duas maquinas: o local e PG 18 e a VPS e PG 16.
- **Segundo link do Maps** (`core/maps.py`, `montar_maps_nome_url`): busca o
  estabelecimento **pelo nome** (nm_fantasia, senao razao social) + municipio +
  UF. Existe porque o endereco da Receita e **fiscal** e as vezes diverge do
  cadastro do Google, e ai nenhum texto de endereco posiciona certo. Caso
  medido: Condor Auto Posto Atuba (01.184.650/0001-05) - Receita
  `RODOVIA BR-116, 850, ATUBA, COLOMBO/PR, CEP 83413-000`; o CEP 83413-000 e
  do bairro **Maua** em Colombo e o Maps cadastra o posto como
  `BR-116, 87 - Atuba, Curitiba/PR`. O link por endereco cai a 1,8 km; o link
  pelo nome acerta o pino. Nao troque o link por endereco por nome+endereco
  nem por nome sozinho: medido em 8 casos, fundir nome+endereco piorou 3
  (Laguna Nautica 10 m -> 653 m, KM Log -> 11,5 km) e o nome sozinho cai em
  "Resultados" na maioria (Posto Prudente/Joinville a 2 km). O segundo link e
  aditivo: sem nome ou sem municipio ele nao aparece.

## Comandos do projeto

    source venv/bin/activate
    python manage.py runserver 8002          # app em http://localhost:8002/
    python manage.py test chat               # 245 testes em chat/tests/
    python manage.py test core               # 39 testes (detalhe, Maps, log de acesso)
    python manage.py test chat.tests.test_sql_validator   # teste isolado
    CHAT_USE_MONGO=1 python manage.py test chat.tests.test_integracao  # backend Mongo

- Use sempre o Python do `venv/` (`venv/bin/python`). Nunca instale pacotes
  globalmente e nao altere `requirements.txt` sem pedir.
- `CHAT_USE_MONGO=1` no teste de integracao **apaga** a collection
  `chat_conversas`: nunca rode isso contra dados reais.

## Regras de seguranca (nao negociaveis)

- Nunca commitar/alterar segredos: `.env`, `.env.*`, credenciais, `lib/jt400.jar`.
- Nunca rodar SQL de escrita/DDL no `bancocnpj` e nunca desativar as validacoes
  do `sql_validator` para "fazer funcionar". A unica excecao e a `c_ceps`, que
  e criada e carregada fora daqui, pelo `ProjetoCargaDados`.
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
- **Filtro por cidade em listagem de matrizes** usa semi-join
  `e.cnpj_basico IN (SELECT ... FROM c_estabelecimentos ... municipio ILIKE
  '%X%')` com `idx_estab_municipio_cnpj`. Nunca `= ANY(ARRAY(SELECT ...))`
  (medido: 8,5 s em Lages e travou em Curitiba). O custo restante depende do
  CNAE, nao do tamanho da cidade.
- No texto natural, semi-join de cidade vira "empresas com estabelecimento em
  X" (`extrair_cidade_semi_join`); a coluna `municipio` e a da matriz.
- Novas funcionalidades de consulta seguem o padrao de `core/views.py`;
  formatacao de numeros fica em `core/templatetags/num_filters.py`.
- Templates: `core/templates/core/`, `chat/templates/chat/`, base em
  `core/templates/core/base.html` (crispy-bootstrap5).
- Antes de finalizar qualquer tarefa que toque `chat/services/` ou modelos do
  chat: rodar `python manage.py test chat` e relatar o resultado.
- Prefira editar arquivos existentes em vez de criar novos modulos; se criar,
  siga a estrutura de pastas atual.
- Documente decisoes em `README.md` (ele e a documentacao oficial do projeto).
