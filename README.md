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
    - [Link do Google Maps](#link-do-google-maps)
    - [Buscar pelo nome (segundo link)](#buscar-pelo-nome-segundo-link)
  - [Estatísticas (`/estatisticas/`)](#estatísticas-estatisticas)
  - [Performance](#performance)
- [Chat IA](#chat-ia)
  - [Pipeline](#pipeline)
  - [Endpoints do chat](#endpoints-do-chat)
- [Endpoints (todos)](#endpoints-todos)
- [Bancos de dados](#bancos-de-dados)
- [Log de acesso (`chat_logacesso`)](#log-de-acesso-chat_logacesso)
  - [Localização dos IPs (GeoLite2)](#localização-dos-ips-geolite2)
  - [Expurgo do log](#expurgo-do-log)
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
    |   |   +-- proximidade.html
    |   |   +-- detalhe.html
    |   |   +-- estatisticas.html
    |   +-- templatetags/
    |   |   +-- num_filters.py      # Filtros de formatacao de numero
    |   +-- conexoes.py             # Conexoes (AS/400, PG, Mongo)
    |   +-- maps.py                 # Links do Google Maps (endereco + busca pelo nome)
    |   +-- proximidade.py          # Busca por raio em km (bbox + haversine, sem PostGIS)
    |   +-- telefone.py             # Link de WhatsApp a partir do telefone da RFB
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
    |   +-- tests/                  # 340 testes
    |   |   +-- test_admin_log.py
    |   |   +-- test_embedding_service.py
    |   |   +-- test_geoip.py
    |   |   +-- test_geoip_painel.py
    |   |   +-- test_integracao.py
    |   |   +-- test_manutencao.py
    |   |   +-- test_models.py
    |   |   +-- test_mongo_models.py
    |   |   +-- test_resposta_natural.py
    |   |   +-- test_signals.py
    |   |   +-- test_sql_executor.py
    |   |   +-- test_sql_generator.py
    |   |   +-- test_sql_validator.py
    |   +-- management/commands/
    |   |   +-- limpar_conversas_antigas.py   # Retencao do historico (padrao 30 dias)
    |   |   +-- relatorio_acesso.py           # Logins, chat e empresas (chat_logacesso)
    |   |   +-- localizar_acessos.py          # Cidade/UF/pais dos IPs do log (GeoLite2)
    |   |   +-- limpar_acessos.py             # Expurgo do log (lotes de 5000, minimo 7 dias)
    |   +-- models.py               # Conversa + Mensagem (Postgres) + LogAcesso + LocalizacaoIP
    |   +-- mongo_models.py         # Conversa + Mensagem (MongoDB)
    |   +-- repo.py                 # Dispatcher (Mongo | Postgres)
    |   +-- repo_postgres.py        # Implementacao ORM
    |   +-- repo_mongo.py           # Implementacao pymongo
    |   +-- geoip.py                # Localizacao dos IPs (maxmind | ip-api | ipapi)
    |   +-- manutencao.py           # Contagem e expurgo do LogAcesso
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

- Bootstrap **5.3.8** via CDN (`core/templates/core/base.html`), com tema claro/escuro
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
4. Carregar a `c_ceps` (coordenada/nome por CEP) pelo `ProjetoCargaDados`
   (`manage.py carregar_ceps`) — é o que habilita o link do Google Maps.
   Sem ela a página funciona, mas o mapa cai para o endereço em texto.
5. Carregar a `c_municipios_geo` (TOM ↔ IBGE ↔ UF + centroide do município)
   pelo `ProjetoCargaDados` (`manage.py carregar_municipios`). É o que habilita
   o filtro de **Cidade** e a busca por **proximidade**. Sem ela, o select de
   cidade fica vazio e `/proximidade/` responde com erro de base indisponível.
6. Carregar a `c_ceps_estab` (estabelecimentos por CEP) pelo `ProjetoCargaDados`
   (`manage.py contar_estab_por_cep`). É o que dá o **total** e o "Página X de Y"
   da `/proximidade/`. Sem ela a busca por proximidade funciona, mas o rodapé
   mostra só "Página X".
7. Carregar a `c_ceps_estab_sit` (estabelecimentos por CEP **e** situação) pelo
   `ProjetoCargaDados` (`manage.py contar_estab_por_cep_situacao`). É o que
   mantém o total e o "Página X de Y" quando o filtro de situação está ligado.
   Sem ela, `/proximidade/?situacao=...` funciona, mas mostra só "Página X"
   (sem o total de empresas e sem o "de Y").

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
| Cidade | Município da UF escolhida (código TOM) | `Joinville` |
| Situação | Nula, Ativa, Suspensa, Inapta, Baixada | `ATIVA` |
| Simples | S ou N | `S` |
| MEI | S ou N | `S` |

O select de **Cidade** só carrega os municípios da UF selecionada (no máximo
645, em MG) e fica desabilitado enquanto não houver UF. Ele lê a tabela
`c_municipios_geo` (TOM ↔ IBGE ↔ UF), criada pelo `ProjetoCargaDados`
(`manage.py carregar_municipios`) — ver "Requisitos de dados".

A lista mostra por empresa: CNPJ, razão social, nome fantasia, **cidade**,
**quantidade de filiais** (estabelecimentos do mesmo `cnpj_basico` que não
são a matriz), UF, situação, Simples, MEI e data de início.

### Ordenação

A lista pode ser ordenada por:

- **CNPJ** (padrão) - mais rápido
- **Razão social** - usa INNER JOIN com `c_empresas`

A ordenação por **nome fantasia** foi removida: o keyset por `nm_fantasia`
não era estável entre páginas (NULLS FIRST na ida e DESC NULLS LAST na volta)
e o fallback de 10s acabava sobrescrevendo o resultado. O **filtro** por nome
fantasia continua funcionando (usa os índices GIN trigram).

### Paginação

Usa **keyset pagination** (não OFFSET). Isso significa que a página 5000
carrega tão rápido quanto a página 1, mesmo com 38 milhões de linhas.

O botão "Próxima" passa o último CNPJ/razão da página atual como ponto de
partida. O botão "Anterior" faz o mesmo, ao contrário. A contagem total
usa a tabela de metadados `c_estatisticas_totais` quando não há filtros,
e cache de 15min com timeout de 800ms quando há filtros combinados.

**Ir para a página** (`?page=N`, campo numérico no rodapé): keyset não pula N
páginas, então este modo — e só ele — usa `OFFSET` (`(N-1)*20`). Para o salto
não virar bitmap scan + Sort (medido em 2026-11: `uf=PR` na página 5.000 passava
de 25 s), a view liga `SET LOCAL enable_bitmapscan/enable_seqscan/enable_hashjoin/
enable_mergejoin = off` antes da consulta e `RESET` depois. Com index scan a
mesma página de `uf=PR` fica em 4-7 s (`uf=SP` página 500 em ~0,8 s; sem filtro
de UF o salto também é barato). Como isso depende de a ordenação sair do índice,
há `statement_timeout = 8 s` — e ele não deixa a tela vazia: estourou, a view
faz `rollback` (o que também devolve o plano padrão, já que os `SET LOCAL` caem
com a transação) e **repete a consulta sem o OFFSET**, mostrando a primeira
página com o aviso "A página N fica longe demais para esses filtros...". Sem
filtros de UF/município/situação o salto é barato em qualquer página; com eles,
páginas muito altas (ex.: `uf=PR` perto da 100.000) caem nesse aviso. Número
maior que o total de páginas é ajustado para a última página.

## O que aparece no detalhe da empresa

A página `/empresa/<cnpj>/` mostra **tudo** que a Receita pública sobre
aquela empresa. Dividido em seções:

### Link do Google Maps

Cada estabelecimento (e o card da matriz) tem um atalho para o Google Maps. A
URL `maps/search/?api=1&query=` é montada por `core/maps.py:montar_maps_url`:
não precisa de chave de API e não faz chamada externa no request — usa só o que
já veio do banco. A ordem é:

1. **rua + número → endereço em texto** (`logradouro, número, bairro, município,
   UF`). O pino de `c_ceps` é do **CEP**, não do número: é a média do trecho que
   o CEP cobre, e numa rua longa isso erra por quilômetros (Rua Blumenau, 3011,
   Joinville/SC: o CEP 89204-251 aponta para a América, o número fica em Santo
   Antônio, ~1,2 km fora);
2. **sem número (ou sem rua) → coordenada `lat,lon`** com 6 casas da `c_ceps`
   (texto sem número não posiciona nada, o pino do CEP é o melhor que há);
3. **sem coordenada → endereço em texto**.

O CEP não entra no texto quando há rua: com o CEP na busca o Google não acha a
rua, e sem hífen ele ainda lê `89204251` como número de porta. Ele só aparece
(formatado, `74740-300`) quando não há logradouro nenhum. Medido na base: 90,1%
dos 38,5 milhões de estabelecimentos têm rua + número e passam pelo texto; 9,9%
(3,8 milhões, número `S/N`, `SN`, `0`) ficam no pino do CEP, que é o que tem
coordenada.

**CEP genérico** (`c_ceps.generico = true`): em município pequeno o Correios
usa um único CEP para a cidade inteira (o `xxxxx-000`, ex.: `89669-000`
Ipira/SC serve 809 estabelecimentos em 208 logradouros, a maioria rural). A
coordenada desse CEP é o centroide da área do município, então o pino cai no
mato. Nesse caso o texto perde também o **bairro** (na Receita vem "CENTRO"
para tudo) e fica `logradouro, número, município, UF` — testado com o Maps:
`RUA QUINZE DE AGOSTO, 123, CENTRO, IPIRA, SC, 89669000, Brasil` **não**
resolve; `RUA QUINZE DE AGOSTO, 123, IPIRA, SC` resolve. São 10.218 CEPs
assim no país, o que alcança 24% dos estabelecimentos da base (9,3 milhões, em
5.356 municípios).

### Buscar pelo nome (segundo link)

Ao lado do link por endereço existe um segundo botão, **Buscar pelo nome**,
montado por `core/maps.py:montar_maps_nome_url` (`nm_fantasia`, senão a razão
social, + município + UF). Ele existe porque o endereço da Receita é **fiscal**
e às vezes não corresponde ao cadastro do Google — e aí não há texto de
endereço que posicione certo.

Caso medido (Condor Auto Posto Atuba, `01.184.650/0001-05`, Colombo/PR):

| O que | Valor |
|---|---|
| Receita | `RODOVIA BR-116, 850, ATUBA, COLOMBO/PR, CEP 83413-000` |
| Link por endereço | Google devolve `BR-116, 850 - **Mauá**, Colombo - PR, 83413-000` — o CEP 83413-000 é do bairro Mauá, **1,76 km** do Atuba |
| POI no Google | `Condor Auto Posto Atuba — BR-116, 87 - Atuba, **Curitiba**/PR, 83411-380` (outro município, outro número) |
| Link pelo nome | `CONDOR AUTO POSTO ATUBA LTDA, COLOMBO, PR, Brasil` → **0 m** do POI |

Foram testados 12 formatos de endereço para esse CNPJ (`RODOVIA`/`BR-116`,
com/sem CEP, com/sem Brasil, traço antes do bairro, "Regis Bittencourt"): todos
caem em Mauá ou numa lista. Só o nome acha o ponto.

O segundo link é **aditivo** — o link por endereço não mudou. Medido em 8
casos: fundir nome+endereço numa query única piorou 3 (Laguna Náutica 10 m →
653 m, KM Log → 11,5 km, Tijucas fantasia → 884 m), e o nome sozinho cai em
"Resultados" na maioria (Posto Prudente/Joinville a 2 km). Por isso ele é um
botão separado, e só aparece quando há nome e município.

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

Quando um dos telefones é **celular** (8 ou 9 dígitos começando com 9), aparece o
botão **WhatsApp** ao lado do número: ele abre `https://wa.me/55<DDD><numero>`.
Telefone fixo não gera link — a regra (e a razão de existir) está em
`core/telefone.py`, e é aplicada também na coluna Telefone da tabela de
estabelecimentos.

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

## Empresas por proximidade (`/proximidade/`)

Responde "quais empresas ficam perto daqui?". A origem pode ser:

| Origem | Exemplo |
|---|---|
| CNPJ (14 dígitos) | `02.831.172/0001-90` — usa o CEP da matriz |
| CEP | `89201-000` |
| Cidade (e UF) | `Joinville/SC` — usa o centroide do município |
| Endereço | `Rua do Príncipe, 500, Joinville/SC` — média dos CEPs do logradouro |
| Coordenada | `-26.30,-48.84` |

Acentos não importam: `Picarras`, `Piçarras` e `Balneário Piçarras` chegam ao
mesmo município. O banco não tem a extensão `unaccent` (só `pg_trgm` e
`vector`), então a comparação usa `upper(translate(coluna, acentos, sem_acento))`
no SQL contra o texto normalizado em Python (`sem_acento`, `core/proximidade.py`).

O modo **endereço** aceita `logradouro[, número][, bairro], Cidade/UF` ou
`logradouro, 89201-000`. O número é ignorado (a base não guarda numeração de
imóvel): a coordenada é a **média dos CEPs do logradouro**, ou seja, o meio da
rua. Quando o texto tem CEP, o CEP manda. A busca do logradouro recorta primeiro
a caixa de 60 km em volta do centro do município (varredura de `c_ceps`,
~250 ms), restringe ao `id_municipio` (IBGE) informado e só então aplica
`translate()` nas linhas que sobraram — sem esse recorte o `LIKE` normalizado
varreria 1,26 M de linhas (~3,8 s). Se o logradouro não existir naquele
município, a resposta é um erro claro ("Tente só a cidade ou um CEP"): **não**
cai para a UF inteira, que trazia homônimo de outra cidade (`Rua das Palmeiras,
Joinville/SC` virava Blumenau/SC). O tipo do logradouro só é descartado quando
sobra mais de uma palavra (`Rua Sete de Setembro` → procura também
`SETE DE SETEMBRO`); `Avenida Brasil` **não** vira `%BRASIL%`, que casaria Rua
Brasil, Assis Brasil e Brasília juntas.

O raio vai de 1 a 100 km e há a opção **somente matrizes** (marcada por padrão).
O resultado sai ordenado por **distância e, no empate, por CNPJ**
(`ORDER BY c.km, e.cnpj_basico, e.cnpj_ordem, e.cnpj_dv`), com a distância em km,
CNPJ, razão social, endereço, município/UF, **situação cadastral**, tipo
(matriz/filial) e os links de detalhe e Google Maps. O botão de detalhe abre em
**outra aba** (`target="_blank"`), para não perder a lista; o mesmo vale para o
item **Proximidade** do menu.

**Como funciona sem PostGIS** (`core/proximidade.py`): o servidor não tem
PostGIS (conferido no PG 18 local e no PG 16 da VPS; `cube`/`earthdistance`
também não estão instalados). A consulta então é SQL puro:

1. caixa geográfica (bbox) calculada em Python a partir do raio;
2. `c_ceps` filtrada pela bbox e **ordenada por distância** numa CTE
   `MATERIALIZED` (1,26 M de linhas, ~200 ms);
3. join por CEP com `c_estabelecimentos` (índice `idx_estabelecimentos_cep`) —
   como a CTE já vem ordenada, o `LIMIT` para cedo;
4. haversine exata e `ORDER BY (km, cnpj_basico, cnpj_ordem, cnpj_dv)`;
5. paginação **keyset** por essa tupla, com **"Página X de Y"** igual à lista de
   empresas: avançar manda `depois_km` + `depois_cnpj` (cursor da última linha) e
   `hist` (pilha de cursores das páginas anteriores) permite voltar sem OFFSET.

**Total de páginas** vem de `c_ceps_estab` (`cep, n_matrizes, n_total`), a
contagem de estabelecimentos por CEP criada pelo `ProjetoCargaDados`
(`manage.py contar_estab_por_cep`). Um `COUNT` no raio custaria 5-15 s (bitmap
scan em 38,5 M de linhas); somar o agregado no raio custa ~30-60 ms. Sem essa
tabela a tela continua funcionando e mostra só "Página X" (o `to_regclass` fica
em cache de 5 min: `TTL_TABELA_TOTAL`).

Tempos medidos localmente (Joinville, 38,5 M de estabelecimentos): página 1 em
~250-350 ms para raios de 1 a 20 km (~500 ms em 100 km, já com o total); página 2
em ~250 ms. Um `CREATE INDEX` em `c_ceps (latitude, longitude)` foi testado e
**descartado**: o planner trocava o early stop por bitmap scan e a página 1 ia
para ~1,5 s.

**Filtro de situação** (`situacao=ATIVA|NULA|SUSPENSA|INAPTA|BAIXADA`, o mesmo
select e os mesmos códigos da lista de empresas — `SITUACOES` em `core/views.py`)
vira `AND e.cd_sit_cadastro = %(situacao)s` no `WHERE` e não mexe no keyset. O
total com filtro vem de `c_ceps_estab_sit` (`cep, cd_sit_cadastro, n_matrizes,
n_total`, uma linha por CEP **e** situação, ~2,1 M linhas/183 MB, criada por
`ProjetoCargaDados: manage.py contar_estab_por_cep_situacao`), então `Página X
de Y` e o contador de empresas funcionam com o filtro ligado. Medido em
2026-11: Joinville raio 10 km → 151.386 matrizes (7.570 páginas), 118.170 ativas
(5.909) e 32.225 inaptas (1.612), com a página 1 em ~450-550 ms (mesmo custo do
caso sem filtro). A soma das situações bate com o total sem filtro. Sem
`c_ceps_estab_sit` a tela cai para "Página X" sem total e avisa em letra miúda.
Valor desconhecido em `situacao` é ignorado (cai em "Todas").

**Cursor do keyset** vai na URL com o km **sem arredondar** (`repr(float)`,
`km_para_texto`). O km é o do CEP e um CEP concentra milhares de empresas: com
`f'{km:.6f}'` (código até 2026-11) o arredondamento para cima pulava o resto do
grupo (perdia linhas) e para baixo deixava o cursor antes do grupo, repetindo a
mesma página para sempre com o "Página N" subindo (bug visto em Piçarras/SC,
página 9). A view também não oferece "próxima" quando o cursor não andou.

**Ir para a página** (`?page=N`, campo numérico no rodapé): como a listagem é
keyset, o cursor da linha pedida vem de `cursor_para_pagina`
(`core/proximidade.py`) em duas consultas baratas, sem `OFFSET` no raio (que
custava 4-23 s em Joinville/10 km):

1. `SQL_CEP_DO_INDICE` — soma corrida (`sum(...) OVER (ORDER BY km, cep)`) das
   contagens de `c_ceps_estab` (ou `c_ceps_estab_sit` com filtro de situação)
   dentro do raio; devolve o CEP onde cai a linha `(N-1)*20 - 1` (~0,4 s);
2. `SQL_CNPJ_NA_POSICAO` — `OFFSET` pequeno dentro daquele CEP
   (`idx_estabelecimentos_cep`) devolve o CNPJ da posição (~0,4 s).

O resultado é o cursor `(km, cnpj)` da linha anterior à primeira da página, que
é exatamente o que `buscar_proximos(depois=...)` espera — daí em diante o
usuário continua com as setas. Números de página inválidos/fora do raio viram
`PAGINA_ALEM_DO_FIM` (aviso + volta para a primeira página) e, sem a tabela
agregada, `PAGINA_SEM_TABELA` (não há como calcular posição sem ela). O rodapé
mantém "Página X de Y" correto depois do salto: o `pbase` da URL guarda quantas
páginas ficaram para trás.

Dois detalhes de custo (medidos em 2026-11, Joinville/10 km, 151.386 matrizes):

- **Poda da CTE pelo km do cursor**. Toda linha que passa no keyset tem
  `km >= depois_km`, então `SQL_PROXIMOS` filtra `km >= %(depois_km)s` na CTE
  `ceps` quando há cursor — mesma lista, sem percorrer os CEPs que ficaram
  para trás. Sem a poda o custo crescia com a página (0,4 s na 2 → 9 s na
  última, e o `statement_timeout` de 15 s derrubava a tela).
- **O total continua vindo de `ceps_todos`** (todos os CEPs do raio): a CTE
  podada serve só à busca, senão o "de Y" passaria a contar a partir da página
  pedida.

Com a poda, `?page=N` na proximidade custa ~0,5-0,6 s em qualquer página
(medido: 0,56 s na 1.000, 0,64 s na 3.000, 0,63 s na 5.000, 0,54 s na última
— 7.570 do exemplo) e o conteúdo é idêntico ao obtido navegando com o keyset.

**Como o km é calculado** — distância em **linha reta geodésica** (great-circle,
fórmula haversine com raio da Terra de 6.371 km), não distância de rua nem rota:
`6371 * acos(cos(lat1)·cos(lat2)·cos(lon2-lon1) + sin(lat1)·sin(lat2))`, com
`least(1.0, ...)` para o `acos` não estourar por arredondamento. Não há consulta
ao Google Maps: o link do Maps é só visualização. A caixa do passo 1 é uma
aproximação grosseira (graus → km com 111,32 km por grau de latitude e
`/cos(lat)` na longitude; em Joinville 1° de longitude = 99,68 km) e serve apenas
para descartar linhas; quem decide se o estabelecimento está no raio é o
haversine no `WHERE c.km <= raio`, então nada fora do círculo entra na lista.
Precisão: esfera contra elipsoide (WGS84) erra 0,07% a 30 km (~20 m) e 0,36% a
5 km (~18 m) — menor que o erro do próprio dado. E o dado é o ponto fraco: a
coordenada é a do **CEP**, não a do número do imóvel (a Receita não traz
geocodificação). Um CEP cobre um trecho de rua — medido aqui: `Rua Blumenau`
(Joinville) tem 9 CEPs, o centroide do logradouro fica a até 1,4 km de um deles;
`Rua do Príncipe` tem 6 CEPs numa extensão de 5,0 km (até 2,8 km do centroide);
no CEP genérico o "ponto" é o centro do município. Ou seja: diferenças de 1-3 km
para o imóvel real são esperadas, e as distâncias são exibidas com 2 casas
(10 m) só para o desempate ficar visível.

**Limitação conhecida**: em município pequeno quase todos os estabelecimentos
caem no **CEP genérico** (que cobre a cidade inteira e tem a coordenada do
centroide), então a lista enche de linhas com a mesma distância (ex.:
`Balneário Piçarras/SC`, 2 km → 8.811 estabelecimentos em 4 CEPs, todos a
0,89 km). Não é erro do cálculo: é a precisão do CEP. O total e a paginação
continuam corretos.

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
- **Keyset pagination** em vez de OFFSET (exceto o campo "Ir para", que usa
  OFFSET com os `enable_*scan` desligados — ver a seção de paginação)
- **Tabela de metadados** `c_estatisticas_totais` pra contagens instantâneas
- **ILIKE** em vez de `UPPER(col) LIKE` (aproveita índices GIN trigram)
- **Fast-count** com timeout de 800ms e cache de 15min em filtros combinados
- **Filiais por página**: uma única query com `cnpj_basico = ANY(...)` para as 20
  empresas da página (~2 ms), em vez de um `LATERAL` por linha
- **Busca por proximidade**: CTE `MATERIALIZED` de CEPs ordenados por distância
  (ver a seção `/proximidade/`)

O tempo de resposta típico é:

- Primeira página sem filtro: ~5ms (lê da tabela de metadados)
- Páginas seguintes (keyset): ~200ms
- Filtro por cidade (UF + município): ~140ms
- Ordenação por razão social: ~500ms
- Proximidade (raio de 1 a 100 km): ~450-550ms com o total (mesmo custo com e
  sem o filtro de situação)
- "Ir para" a página N: lista 0,3-7 s (keyset segue em ~200 ms; com filtro de
  UF a página 5.000 é o pior caso medido); proximidade ~0,5-0,6 s em qualquer
  página (soma corrida por CEP + consulta podada pelo km do cursor; 0,56 s na
  1.000, 0,63 s na 5.000 e 0,54 s na última de Joinville/10 km)
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
| GET | `/proximidade/` | Empresas num raio de 1 a 100 km de um CNPJ/CEP/endereço/cidade/coordenada |
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

## Localização dos IPs (GeoLite2)

O painel do log mostra **cidade/UF/país** de cada IP, mas nada é consultado
durante o request: a localização vive em `chat.LocalizacaoIP` (uma linha por IP,
tabela separada do `LogAcesso`) e é preenchida fora da web, pelo comando:

    python manage.py localizar_acessos --dry-run      # só lista os pendentes
    python manage.py localizar_acessos --dias 30      # padrão: últimos 30 dias
    python manage.py localizar_acessos --dias 0       # todo o histórico

| Fonte (`GEOIP_FONTE`) | Como funciona | Observação |
|---|---|---|
| `maxmind` (**padrão**) | `GeoLite2-City.mmdb` local | nenhum IP sai do servidor, sem cota, sem rede |
| `ip-api` | `http://ip-api.com/batch`, 100 IPs por POST | **HTTP** (sem TLS) |
| `ipapi` | `https://ipapi.co/{ip}/json/` | 1 IP por requisição |

- O `.mmdb` **não é versionado** (`geoip/` está no `.gitignore`) e o `deploy.sh`
  **não o envia** (`rsync --exclude='geoip'`): a base é instalada no servidor
  uma vez (`scp`, `scripts/atualizar_geoip.sh` ou upload pelo painel) e renovada
  por lá.
- Renovar: `bash scripts/atualizar_geoip.sh` (conta gratuita em maxmind.com,
  `MAXMIND_ACCOUNT_ID` + `MAXMIND_LICENSE_KEY` no `.env`) — ou pelo painel, em
  `/admin/chat/logacesso/`: **checar base nova** (só `HEAD` no permalink, não
  baixa), **baixar da MaxMind** e **instalar arquivo enviado** (`.mmdb` ou
  `.tar.gz`, validado antes de substituir; o `.zip` "-CSV" não serve). Cada troca
  fica no `LogEntry` do admin.
- Cache por IP: localização mais nova que `--revalidar-dias` não é reconsultada.
  Erro de rede/limite **não levanta** — o IP fica para a próxima execução.
- **PATH do systemd (10/11/2026)**: o `cnpj.service` declara
  `Environment="PATH=…/venv/bin"` e o systemd **substitui** (não acrescenta) o
  PATH. Dentro do gunicorn **não existem `bash`, `curl` nem `tar`**, então o
  "Checar/Baixar" do painel devolvia **500** (`FileNotFoundError`) só em
  produção — no terminal o script rodava. `chat/geoip.py` agora monta o ambiente
  com `ambiente_script()` (venv/bin + `/usr/local/bin:/usr/bin:/bin` + herdado,
  sem repetir), acha o `bash` por `shutil.which`, confere `curl`/`tar` e converte
  `OSError` em `GeoIPIndisponivel` (mensagem na tela, nunca 500). O
  `TIMEOUT_SCRIPT` é **240 s**, abaixo do `--timeout 300` do gunicorn.
- Licença: o GeoLite2 exige o crédito de atribuição (rodapé do site/admin).
- Base ausente ou erro de rede não afeta a aplicação: só o comando reclama
  (`GeoIPIndisponivel`).

## Expurgo do log

`chat/manutencao.py` é a única implementação de exclusão, usada pelo painel
(`POST /admin/chat/logacesso/expurgar/`) e pelo comando:

    python manage.py limpar_acessos --dias 90 --dry-run
    python manage.py limpar_acessos --dias 90

- **Idade mínima de 7 dias** (`MIN_DIAS`): um `--dias 1` digitado por engano não
  zera a auditoria (abaixo disso o comando recusa).
- `DELETE` em **lotes de 5000 ids** (`TAMANHO_LOTE`): um `DELETE` único em
  centenas de milhares de linhas seguraria lock e transação longa no Postgres.

# Testes

    python manage.py test chat       # 340 testes do chat
    python manage.py test core       # 152 testes do core

Roda **340 testes** em `chat/tests/` (+ 152 em `core/tests.py`) = 492 no total:

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
- `test_geoip.py` - fontes de localização, cache por IP, erro de rede e `verificar_fonte`
- `test_geoip_painel.py` - administração da base pelo painel (checar/baixar/enviar), só superusuário
- `test_manutencao.py` - idade mínima, lotes de exclusão e `limpar_acessos`
- `core/tests.py` - template do detalhe (município/UF), links do Maps (endereço e busca pelo nome), log de acesso das páginas de empresas e o "Chat IA" do navbar em nova aba

Os testes usam mock do DeepSeek e do `psycopg2.connect`. **`test_mongo_models`
e `test_integracao` (no modo Mongo) usam o MongoDB real e limpam a collection
`chat_conversas`** - não rodar com dados reais. Os testes de GeoIP usam cache
local e respostas/artefatos falsos: não tocam a base `.mmdb` nem a rede.

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
- `GEOIP_FONTE` - fonte da localização dos IPs: `maxmind` (padrão, base local),
  `ip-api` (`http://ip-api.com/batch`) ou `ipapi` (`https://ipapi.co`)
- `GEOIP_CIDADE_MMDB` - caminho do `GeoLite2-City.mmdb` (vazio = `geoip/GeoLite2-City.mmdb`)
- `MAXMIND_ACCOUNT_ID` / `MAXMIND_LICENSE_KEY` - conta gratuita da MaxMind, usada
  **só** pelo `scripts/atualizar_geoip.sh` para baixar a base (não vai junto com os IPs)

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
