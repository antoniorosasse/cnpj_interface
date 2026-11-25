# Regras do projeto CNPJ Interface

Este arquivo e lido pelo DSH quando a sessao trabalha neste diretorio (cwd do
projeto ou arquivo tocado). Mantenha-o enxuto e versionado no git.

## Onde esta o que

| Assunto | Arquivo |
|---|---|
| Regras globais (valem nos 4 projetos: menu, as400, cnpj, carga) | `~/.dsh/AGENTS.md` |
| Estado atual, pendencias, decisoes e arquitetura | `/home/neto/ProjetosPython/Projetos/docs/PROJETOS.md` |
| Comandos (dev/prod, queries, git, djLint) | `/home/neto/ProjetosPython/Projetos/docs/COMANDOS.md` |
| Historico detalhado (com commits e erros) | `/home/neto/ProjetosPython/Projetos/docs/HISTORICO.md` |
| Briefing portatil (para colar em outra IA) | `/home/neto/ProjetosPython/Projetos/docs/TODOS_PROJETOS.md` |
| Este arquivo | regras especificas do `cnpj_interface` |

Nao repita aqui o que ja esta nas regras globais (R1-R16) nem duplique o estado
atual: se algo mudar, atualize o arquivo correspondente.

## Tarefa que atravessa projetos

Uma conversa pode comecar neste repositorio e mexer em outro (`.env` do vizinho,
`deploy.sh`, backups e docs em `~/ProjetosPython/Projetos`). A regra global vale
nos quatro, mas o spec de cada repo **nao** e carregado automaticamente: antes
de editar fora daqui, **leia** o `AGENTS.md` do projeto que vai ser tocado.

- `/home/neto/ProjetosPython/menu_interface/AGENTS.md`
- `/home/neto/ProjetosPython/as400_interface/AGENTS.md`
- `/home/neto/ProjetosPython/cnpj_interface/AGENTS.md`
- `/home/neto/ProjetosPython/ProjetoCargaDados/AGENTS.md`

O que muda de um para o outro e o spec do repo (stack, schema, comandos,
decisoes que nao se reabrem).

## Idioma e estilo

- Responda sempre em **portugues do Brasil (pt-BR)**.
- Codigo, comentarios e docstrings: **sem acentos** (ex: `situacao`, `nao`,
  `codigo`). Prosa em Markdown (.md) pode usar acentos normalmente.
- Comentarios e docstrings em PT-BR. Nao traduza nomes de tabelas/colunas da
  RFB (`c_estabelecimentos`, `cd_matriz_filial`, etc.).
- Siga o estilo existente dos arquivos vizinhos (4 espacos, aspas simples,
  sem aspas em SQL quando o arquivo ja usa triple-quote).

## Stack e arquitetura

- Django 6.1.1, Python 3.13, PostgreSQL 18 (**dois bancos**:
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
- **Localizacao dos IPs** (`chat/geoip.py` + `chat.LocalizacaoIP`, desde 09/11):
  uma linha por IP, preenchida **fora do request** por
  `manage.py localizar_acessos`. Fonte padrao `GEOIP_FONTE=maxmind`
  (`GeoLite2-City.mmdb` local em `geoip/`, no `.gitignore` e fora do
  `rsync --exclude='geoip'` do deploy): nenhum IP de visitante sai do servidor.
  A base e instalada/renovada por `scripts/atualizar_geoip.sh` ou pelo painel
  (checar/baixar/enviar `.mmdb`, so superusuario, com `LogEntry`). Nao consulte
  GeoIP dentro de view, signal ou template. **Precisao** (10/11): a base gratuita
  so da cidade para faixa residencial/movel; IP de datacenter/crawler fica no
  **pais** — decisao registrada, nao trocar por fonte HTTP/terceiro nem exigir
  cidade de bot.
- **PATH do systemd** (10/11): os `.service` declaram
  `Environment="PATH=.../venv/bin"` e o systemd **substitui** (nao acrescenta) o
  PATH: dentro do gunicorn nao existem `bash`, `curl` nem `tar`. Todo
  `subprocess` do projeto passa por `chat/geoip.py::ambiente_script()`
  (venv/bin + `/usr/local/bin:/usr/bin:/bin` + herdado, sem repetir) e converte
  `OSError`/`FileNotFoundError` em `GeoIPIndisponivel` - mensagem no painel,
  nunca 500. `subprocess` novo: use o mesmo `env=` e `shutil.which`, senao o bug
  so aparece em producao.
- **Expurgo do log** (`chat/manutencao.py` + `manage.py limpar_acessos`):
  idade minima de 7 dias (`MIN_DIAS`) e `DELETE` em lotes de 5000
  (`TAMANHO_LOTE`) - nunca um `DELETE` unico na tabela inteira. O painel e o
  comando usam a **mesma** funcao.
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
  reenviar com `~/ProjetosPython/Projetos/restaura_ceps.sh` (ver
  `docs/COMANDOS.md`). Nunca
  `pg_dump -Fc` entre as duas maquinas: o local e PG 18.6 e a VPS e PG 18.7
  (a VPS ficou no PG 16 ate a migracao de 17/11/2026, que e o que o texto
  historico abaixo descreve).
- **WhatsApp** (`core/telefone.py`): `montar_whatsapp_url` so gera link quando o
  telefone da RFB parece **celular** (8 ou 9 digitos **comecando com 9**, DDD
  11..99). A base guarda 8 digitos, sem o nono. Fixo (2,3,4,5) nao gera link —
  nao invente um numero so para ter o botao. `montar_whatsapp` tenta `tel_1` e
  depois `tel_2`. Aplicado no card do detalhe e na tabela de estabelecimentos.
- **Filtro de cidade e `/proximidade/`**: dependem da tabela
  **`c_municipios_geo`** (5.572 linhas: `cd_municipio` = TOM, `cd_ibge`, `nome`
  do IBGE com acento, `nome_tom` da RFB, `uf`, `latitude`/`longitude` do
  centroide e `n_ceps`). Ela **nao substitui** `c_municipios` (que continua sendo
  a lista da RFB). Carregada pelo ProjetoCargaDados
  (`manage.py carregar_municipios`); este repo so le. Em producao **ainda nao
  existe** (ver pendencia #28 em `docs/PROJETOS.md`): sem ela o select de Cidade
  fica vazio e `/proximidade/` responde com erro.
- **Busca por proximidade** (`core/proximidade.py`): nao ha PostGIS (nem
  `cube`/`earthdistance`) no local nem na VPS. A ordem do SQL e: bbox calculada
  em Python -> **CTE `MATERIALIZED` de CEPs ordenados por km** -> join por
  `cep` em `c_estabelecimentos` -> haversine -> keyset
  `(km, cnpj_basico, cnpj_ordem, cnpj_dv)`. **Nao** troque a CTE ordenada por
  um `JOIN` direto em `c_ceps` dentro da CTE: foi medido (Joinville, raio 5 km)
  250 ms com a CTE contra **1,2 s** na versao anterior, porque o planner perde
  o early stop na pagina 2. Tambem **nao** crie indice em
  `c_ceps(latitude, longitude)`: testado, o planner troca o early stop por
  bitmap scan e a pagina 1 vai para ~1,5 s (o indice foi removido).
- **Coordenada do endereco** (`c_estab_geo`, do `ProjetoCargaDados`: CNEFE 2022
  casado com a RFB por CEP + logradouro + numero): `core/views.py`
  `_aplicar_endereco_geo` troca lat/lon das 20 linhas da pagina quando existe
  linha (precisao `exato`/`rua`); sem a tabela, tudo cai no centroide do CEP.
  `ordem=endereco` na querystring usa `buscar_proximos_endereco`
  (`core/proximidade.py`), que ordena pelo km do endereco com janela por
  `c_ceps_folga`; nesse modo o km da lista e o do endereco e o campo
  "Ir para pagina" fica escondido (depende da contagem por CEP). O km do CEP
  continua sendo o que sustenta a paginacao no modo padrao.
- **Fundo do mapa** (25/11): dois niveis, nenhum tile de terceiro com chave.
  1. **Mapa de rua**: OpenFreeMap (`tiles.openfreemap.org/styles/liberty`,
     vetorial, sem chave, uso comercial permitido, sem limite) renderizado pelo
     **MapLibre** via plugin `maplibre-gl-leaflet` (vendorizados em
     `static/vendor/maplibre/`, ~1,1 MB). Roda no `tilePane`, abaixo das camadas
     vetoriais; a atribuicao (OpenFreeMap/OpenMapTiles/OSM) vem do estilo.
  2. **Fallback**: se o estilo nao carregar em 8 s (rede/WebGL), a camada de rua
     sai e entra o **BC250** (`scripts/gera_mapa_bc250.py`, leitura de WKB em
     Python puro, sem GDAL/shapely) em `static/mapa/{municipios,urbano,rodovias,cidades}_<UF>.geojson`
     (~15 MB simplificados, ~520 KB por tela), no pane `baseMapa`.
  `_ufs_do_mapa` (core/views.py) escolhe as UFs que encostam na caixa do raio e
  manda a lista no JSON. **Nao** volte para `tile.openstreetmap.org`: o servidor
  publico bloqueia rajadas com a imagem "403 Access blocked" em HTTP 200 (24 e
  25/11/2026).
- **Mapa interativo** (25/11): a area laranja da densidade e clicavel e abre um
  popup com a contagem e o link **"Abrir consulta aqui"** (`/proximidade/` com
  raio 1 km no centro da celula, mantendo filtros). Na tabela, o botao de alvo
  (`.prox-centrar`, com `data-lat`/`data-lon`) centraliza a empresa no mapa sem
  recarregar (nao conta consulta); na lista de empresas o botao de pino abre a
  proximidade com o CNPJ como origem (em nova aba, e conta uma consulta).
- **Ordenacao da lista**: so `cnpj` e `razao_social`. A ordenacao por nome
  fantasia foi **removida** (keyset instavel e fallback de 10s sobrescrevendo o
  resultado); o **filtro** por nome fantasia continua. Nao reintroduza a opcao
  no `<select>` de `lista.html` sem refazer o keyset.
- **Colunas da lista**: `Filiais` e `Cidade` nao vem das queries de keyset. As
  filiais sao uma unica query com `cnpj_basico = ANY(...)` para os 20 CNPJs da
  pagina (~2 ms); o nome da cidade vem de `LEFT JOIN c_municipios_geo mg ON
  mg.cd_municipio = e.municipio`. Nao troque por `LATERAL` por linha.
- **"Ir para" da lista** (`?page=N` sem `last_cnpj`): o unico lugar da lista com
  `OFFSET`, e so com `SET LOCAL enable_bitmapscan/enable_seqscan/
  enable_hashjoin/enable_mergejoin = off` + `statement_timeout = 8 s` (RESET no
  fim). Sem o tuning `uf=PR` pagina 5000 passava de 25 s (bitmap + Sort); com
  ele, 4-7 s. O timeout nao derruba a tela nem deixa a lista vazia:
  `consultar_com_salto` faz `rollback` (o que tambem devolve o plano padrao,
  porque `SET LOCAL` cai com a transacao) e **repete a consulta sem OFFSET**;
  a tela mostra a primeira pagina com aviso. A pagina e limitada a
  `total_paginas` quando conhecido, e o campo do rodape so aparece se
  `total_paginas`.
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

## Busca por proximidade (`/proximidade/`)

- `core/proximidade.py` resolve a origem, monta a bbox e roda a CTE
  `MATERIALIZED` de CEPs ordenados por km (não existe PostGIS no local nem na
  VPS; `cube`/`earthdistance` também não). **Nunca** troque a CTE ordenada por
  join direto (1,2 s na página 2) nem crie índice em
  `c_ceps(latitude, longitude)` (piora a página 1 para 1,5 s).
- **Acento**: o banco não tem `unaccent` (só `pg_trgm` e `vector`). Compare com
  `sql_sem_acento(coluna)` (`upper(translate(...))`) contra `sem_acento(texto)`.
  Nunca use `UPPER(nome) = %s` para nome de município/logradouro: `Picarras`
  deixaria de achar `Balneário Piçarras`.
- **Origem por endereço**: aceita `logradouro[, número][, bairro], Cidade/UF` e
  `logradouro, 89201-000`. O número é ignorado (a base não tem numeração) e a
  coordenada é a média dos CEPs do logradouro. A busca recorta antes a caixa de
  60 km do centro do município (`RAIO_BUSCA_LOGRADOURO_KM`): sem isso o `LIKE`
  normalizado varre 1,26M de CEPs em ~3,8 s.
- **Paginação**: "Página X de Y" com keyset — `depois_km`/`depois_cnpj` avançam
  e a pilha `hist` (tokens `km|cnpj`, `-` = início) volta. Nunca use OFFSET no
  raio: **a única exceção** é o campo "Ir para" (`?page=N`), que chama
  `cursor_para_pagina` — soma corrida em `c_ceps_estab(_sit)` acha o CEP da
  posição e um `OFFSET` pequeno dentro do CEP (`idx_estabelecimentos_cep`) acha
  o CNPJ. OFFSET direto no raio custa 4-23 s. `pbase` na URL guarda as páginas
  que ficaram para trás depois de um salto; sem a tabela agregada o salto é
  recusado (`PAGINA_SEM_TABELA`) e a tela avisa. Com cursor, `SQL_PROXIMOS`
  poda a CTE em `km >= depois_km` (mesma lista, sem varrer os CEPs anteriores);
  **o total sai de `ceps_todos`**, nunca da CTE podada. Custo medido em
  Joinville/10 km: ~0,5-0,6 s em qualquer página (0,56 s na 1.000, 0,63 s na
  5.000, 0,54 s na última) — sem a poda a última página levava 9 s.
- **Cursor**: o km vai na URL com `repr(float)` (`km_para_texto`, round-trip
  exato). **Nunca** formate com `f'{km:.6f}'`: o km é o do CEP e um CEP tem
  milhares de empresas com o mesmo km; arredondar para cima salta o resto do
  grupo (perde linhas) e para baixo repete a mesma página para sempre, com o
  contador de páginas subindo (bug de Piçarras/SC, página 9, 2026-11).
- **Total**: vem de `c_ceps_estab` (`cep`, `n_matrizes`, `n_total`), criada pelo
  `ProjetoCargaDados` (`contar_estab_por_cep`). `COUNT` no raio custa 5-15 s. Se
  a tabela não existir, degrade para "Página X" (`tem_tabela_total`, cache de
  5 min por `to_regclass`) — nunca quebre a página.
- **Filtro de situação** (`situacao=` na querystring → `AND e.cd_sit_cadastro`):
  usa o dict `SITUACOES` de `core/views.py` (1 nula, 2 ativa, 3 suspensa,
  4 inapta, 8 baixada), o mesmo da lista. Total e "Página X de Y" do filtro vêm
  de `c_ceps_estab_sit` (`cep`, `cd_sit_cadastro`, `n_matrizes`, `n_total`),
  criada por `contar_estab_por_cep_situacao` (2,06 M pares, 183 MB, ~154 s).
  Sem ela, degrade para "Página X" e avise na tela. **Não** troque isso por
  `count(*) OVER ()`: custa 13-16 s com o filtro (Joinville, raios 5 e 20 km).
- A ordem é `km` e, no empate, CNPJ (`cnpj_basico, cnpj_ordem, cnpj_dv`), que é
  o próprio cursor do keyset. Não ofereça "ordenar por CNPJ": sem o early stop
  na ordem de km a consulta varre o raio inteiro.

## Produto: exportacao, creditos e contadores

O app `exportacao/` e' a camada comercial, separada do `core/` de proposito:
ele grava no banco `django_internal` (o `core` roteia para o `bancocnpj`).

- Export CSV/JSON de lista e proximidade; amostra de 20 linhas para anonimo;
  1 credito por CNPJ novo (dedup de 30 dias). O debito acontece **antes** do
  streaming (abortar nao e' gratis); falha de entrega gera estorno e status
  `falhou`.
- `Cliente`/`MovimentoCredito` (livro-caixa), `Exportacao`, `LinhaExportada`,
  `PesquisaAnonima` (contador por identidade/dia/tipo).
- Contadores: `ConfiguracaoUso` (linha unica, global) + `LimiteUsuario`
  (override por usuario). Tipos: `lista` (empresas), `proximidade`, `chat`,
  `exportacao` (arquivos). O switch "aplicar limites" so' bloqueia; desligado,
  **continua contando**.
- Admin: `/admin/exportacao/configuracaouso/` (parametros),
  `/admin/exportacao/configuracaouso/contadores/` (painel do dia) e o inline de
  limites na pagina do usuario.
- Antiabuso: export anonimo exige cookie `visitante_id` (sem ele, 403); teto
  por IP/dia por tipo; desafio Cloudflare Turnstile em `/exportar/desafio/`
  com ticket assinado de 15 min (20 tickets/IP/hora). Sem
  `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` no `.env`, o desafio fica inativo.
- Chat: layout amplo e' o padrao (gaveta + atalho `c`); `?layout=padrao` e'
  backup manual. O export da ultima lista reinjeta o vetor do `%s` e eleva o
  `LIMIT` do gerador ate o teto do arquivo.
- Embedding: carrega do cache local do Hugging Face (`EMBEDDING_OFFLINE=auto`);
  sem cache, tenta o Hub.

    python manage.py limpar_exportacoes --dias 180 --dry-run   # expurgo
    python manage.py limpar_exportacoes --dias 180

## Comandos do projeto

    source venv/bin/activate
    python manage.py runserver 8002          # app em http://localhost:8002/
    python manage.py test chat               # 347 testes em chat/tests/
    python manage.py test exportacao         # 58 testes (creditos, cotas, desafio, admin)
    python manage.py test core               # 146 testes (lista, detalhe, Maps, WhatsApp, proximidade, log)
    python manage.py test chat.tests.test_sql_validator   # teste isolado
    CHAT_USE_MONGO=1 python manage.py test chat.tests.test_integracao  # backend Mongo

    # Recalculo de c_estatisticas_totais (~11 min / 669 s na base de 74,4 M).
    # Roda FORA do request; a procedure calcula em c_estatisticas_calc e troca
    # as tabelas por RENAME no fim (a /estatisticas/ nunca fica vazia).
    python manage.py recalcular_totais
    python manage.py recalcular_totais --status

- Use sempre o Python do `venv/` (`venv/bin/python`). Nunca instale pacotes
  globalmente e nao altere `requirements.txt` sem pedir.
- `CHAT_USE_MONGO=1` no teste de integracao **apaga** a collection
  `chat_conversas`: nunca rode isso contra dados reais.

## Regras de seguranca (nao negociaveis)

- Nunca commitar/alterar segredos: `.env`, `.env.*`, credenciais, `lib/jt400.jar`.
- Nunca rodar SQL de escrita/DDL no `bancocnpj` e nunca desativar as validacoes
  do `sql_validator` para "fazer funcionar". A unica excecao sao `c_ceps`,
  `c_municipios_geo` e `c_ceps_estab`, que sao criadas e carregadas fora daqui,
  pelo `ProjetoCargaDados` (e restauradas na VPS por
  `~/ProjetosPython/Projetos/restaura_*.sh`).
- Nao execute `git commit`, `git push`, `migrate` em producao, `collectstatic`
  ou `sp_recalcular_estatisticas_totais()` sem pedir confirmacao explicita.
  O recalculo das estatisticas e `python manage.py recalcular_totais` (fora do
  request, ~11 min); a DDL dos objetos e `sql/estatisticas_totais.sql`.
- Proibido criar arquivos de backup manuais (`*.bak`, `*.bak_*`) no repo.

## Convencoes de implementacao

- **Schema e fonte da verdade do LLM**: `chat/services/schema_context.py`.
  Se uma coluna, codigo, indice ou tabela mudar, atualize esse arquivo e o
  README na mesma tarefa.
- **Performance** (base com ~74,4M linhas em `c_estabelecimentos` desde o lote
  2026-11): mantenha
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
