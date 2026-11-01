# cnpj_interface/chat/services/schema_context.py
"""
Contexto do schema do banco de CNPJ para o LLM.

Gerado a partir do DDL real (pg_dump) para garantir que nomes de
tabelas e colunas batam com o banco.

Contem:
  - Tamanho estimado de cada tabela
  - Colunas reais de cada tabela
  - Codigos conhecidos (porte, situacao, op_simples, etc)
  - Regras de performance (indexes disponiveis)

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

SCHEMA_CONTEXT = r"""
BANCO: PostgreSQL 15+
SCHEMA: public (unico schema relevante)

=====================================================================
TABELAS E TAMANHOS (use para decidir se vale JOIN)
=====================================================================

  c_estabelecimentos    ~38.500.000 linhas   GIGANTE
  c_empresas            ~36.900.000 linhas   GIGANTE
  c_simples             ~30.300.000 linhas   GIGANTE
  c_socios              ~18.900.000 linhas   GRANDE
  c_municipios          ~5.570 linhas        LOOKUP
  c_cnaes               ~1.360 linhas        LOOKUP
  c_paises              ~260 linhas          LOOKUP
  c_naturezas           ~90 linhas           LOOKUP
  c_qualificacoes       ~70 linhas           LOOKUP
  c_motivos             ~65 linhas           LOOKUP

=====================================================================
c_empresas  (~36.9M linhas) -- dados cadastrais da empresa
=====================================================================

  cnpj_basico     varchar(8) NOT NULL   (8 primeiros digitos do CNPJ)
  razao_social    varchar(255)
  nat_juridica    integer               (FK para c_naturezas.cd_nat_juridica)
  qual_resp       smallint              (codigo, ver c_qualificacoes.cd_quals)
  capital_social  double precision
  porte_empresa   double precision      (1=ME, 3=EPP, 5=Demais, 0/NULL=NAO INFO)
  ente_fed_resp   varchar(50)
  id_empresa      serial (PK)

  INDICES: cnpj_basico (unique), porte_empresa, nat_juridica, razao_social (trgm)
           idx_empresas_razao_cnpj (razao_social, cnpj_basico) <- use na
             ordenacao por razao_social (permite keyset rapido)

=====================================================================
c_estabelecimentos  (~38.5M linhas) -- enderecos, situacao, CNAE
=====================================================================

  cnpj_basico         varchar(8) NOT NULL
  cnpj_ordem          varchar(4) NOT NULL
  cnpj_dv             varchar(2) NOT NULL
  cd_matriz_filial    smallint          (1=MATRIZ, 2=FILIAL)
  nm_fantasia         text
  cd_sit_cadastro     smallint          (1=NULA, 2=ATIVA, 3=SUSPENSA,
                                         4=INAPTA, 8=BAIXADA)
  dt_sit_cadastro     date
  motivo_sit_cadastro smallint          (FK c_motivos.cd_motivo_sit_cadastro)
  nm_cidade_ext       text              (cidade no exterior, se aplicavel)
  cd_pais             integer
  dt_ini              date              (data de inicio de atividade)
  cnae_principal      integer           (FK c_cnaes.cnae)
  cnae_secundario     text              (varios codigos separados por virgula)
  tip_logradouro      varchar(50)
  logradouro          text
  numero              varchar(20)
  complemento         text
  bairro              text
  cep                 varchar(10)
  uf                  varchar(10)
  municipio           integer           (FK c_municipios.cd_municipio)
  ddd_1, tel_1, ddd_2, tel_2, ddd_fax, fax
  email               text
  sit_esp             text
  dt_sit_esp          date
  id_estabelecimento  serial (PK)

  CONTAGEM DE ESTABELECIMENTOS (matriz + filiais) de uma empresa:
    (SELECT COUNT(*) FROM c_estabelecimentos x
     WHERE x.cnpj_basico = e.cnpj_basico) AS qtd_estabelecimentos
  Barato (a PK comeca por cnpj_basico) e util em LISTAGENS: mostra o
  tamanho da rede (ex: KOCH HIPERMERCADO S/A = 115 estabelecimentos).

  INDICES (destaques):
    -- Simples
    cnpj_basico, cd_matriz_filial, cd_sit_cadastro, uf, municipio,
    cnae_principal, dt_ini, cep
    -- Compostos parciais (WHERE cd_matriz_filial = 1) - USE ESTES em filtros
    idx_estab_uf_matriz_mun (uf, municipio)
    idx_estab_uf_sit_fantasia_matriz (uf, cd_sit_cadastro, nm_fantasia,
                                       cnpj_basico, cnpj_ordem, cnpj_dv)
    idx_estabelecimentos_fantasia_cnpj (nm_fantasia, cnpj_basico,
                                         cnpj_ordem, cnpj_dv)
    idx_estabelecimentos_keyset_parcial (cnpj_basico, cnpj_ordem, cnpj_dv)
    -- Parciais de coluna unica (WHERE cd_matriz_filial = 1)
    matriz_sit, matriz_cnae, matriz_ano, matriz_mun
    -- Nao-parciais uteis
    idx_estabelecimentos_cnpj_completo (cnpj_basico, cnpj_ordem, cnpj_dv)
    idx_estabelecimentos_cidade_uf (municipio, uf)
    idx_estabelecimentos_uf_sit (uf, cd_sit_cadastro)
    idx_estab_nome_fantasia_gin_todos (nm_fantasia) GIN trigram, SEM filtro
      de matriz - use para buscar nome fantasia em matriz OU filial

=====================================================================
c_simples  (~30.3M linhas) -- optantes Simples/MEI
=====================================================================

  cnpj_basico     varchar(8) NOT NULL (PK)
  op_simples      varchar(1)           ('S'=SIM, 'N'=NAO, NULL=outros)
  dt_op_simples   date
  dt_exc_simples  date
  op_mei          varchar(1)           ('S'=SIM, 'N'=NAO, NULL=outros)
  dt_op_mei       date
  dt_exc_mei      date

  INDICES: cnpj_basico, op_simples (where 'S'), op_mei (where 'S'),
           dt_op_simples, dt_op_mei,
           (cnpj_basico, op_simples) where 'S',
           (cnpj_basico, op_mei) where 'S'

  ATENCAO: TODA linha de c_simples tem correspondente em c_empresas
  (verificado, 0 orfaos). Mas o inverso nao e verdadeiro: nem toda
  empresa esta em c_simples.

=====================================================================
c_socios  (~18.9M linhas) -- socios das empresas
=====================================================================

  cnpj_basico           varchar(8)
  id_tipo_socio         smallint        (1=PJ, 2=PF, 3=ESTRANGEIRO)
  nome_ou_razao_social  varchar(255)
  cnpj_cpf              varchar(14)     (CPF descaracterizado pela RFB)
  qualif_socio          smallint        (FK c_qualificacoes.cd_quals)
  dt_entrada            date
  cd_pais               integer
  repr_legal            varchar(50)
  nm_repr               varchar(255)
  cd_qualif_repr        smallint
  faixa_etaria          smallint        (ver CODIGOS abaixo)
  id_socio              serial (PK)

  INDICES: cnpj_basico, cnpj_cpf, faixa_etaria, nome_ou_razao_social (trgm),
           cnpj_cpf (trgm)

  ATENCAO: a coluna de qualificacao chama-se qualif_socio (NAO
  qualificacao). Nao invente nomes.

=====================================================================
TABELAS DE LOOKUP (pequenas, JOIN barato)
=====================================================================

c_cnaes
  cnae      integer (PK)     -- codigo do CNAE
  nm_cnae   varchar(255)     -- descricao
  embedding halfvec(384)     -- vetor da descricao (para busca semantica)

=====================================================================
BUSCA SEMANTICA (VETORIAL) - SOMENTE COMO FALLBACK DO ILIKE
=====================================================================

A tabela c_cnaes tem uma coluna `embedding` (halfvec(384)) com o vetor
da descricao do CNAE. Ela serve para descobrir o CNAE quando o termo
da pergunta NAO aparece no nm_cnae.

REGRA DE OURO: primeiro o ILIKE, o vetor SO como fallback.
Use o vetor apenas quando o ILIKE nao encontrar NENHUM CNAE. Motivo:
o vetor devolve CNAEs "vizinhos" e, misturados ao ILIKE, eles enchem
o LIMIT 100 da query externa. Exemplo real: "supermercados em
picarras" (ILIKE em 4711302) devolvia 4 empresas; "supermercados de
picarras" fazia o vetor trazer 4712100 (minimercados/mercearias) e o
resultado pulava para 100 linhas de mercearia.

1. Extraia a palavra-chave da pergunta (o radical, sem plural):
   "padarias de lages"      -> padaria
   "confeitaria"            -> confeitaria
   "oficinas mecanicas"     -> oficina mecanica
   "empresas de carpintaria"-> carpintaria
   "energia solar"          -> energia solar (nao esta no nm_cnae)

2. Monte a subquery SEMPRE neste formato (ILIKE + vetor protegido
   por NOT EXISTS):

   AND e.cnae_principal IN (
       SELECT cnae FROM c_cnaes
       WHERE nm_cnae ILIKE '%PALAVRA%'
       UNION
       SELECT cnae FROM (
           SELECT cnae FROM c_cnaes
           WHERE NOT EXISTS (
               SELECT 1 FROM c_cnaes
               WHERE nm_cnae ILIKE '%PALAVRA%'
           )
           ORDER BY embedding <=> %s::halfvec(384)
           LIMIT 2
       ) sub
   )

   O NOT EXISTS e' OBRIGATORIO: ele faz a parte vetorial devolver
   zero linhas quando o ILIKE encontra algum CNAE. Assim o resultado
   nunca mistura categorias diferentes.

   O ILIKE pega os CNAEs que tem a palavra no nome (ex: "padaria"
   esta em 1091102 e 4721102). O vetor so entra quando o ILIKE nao
   acha nada (ex: "energia solar" nao esta no nome de nenhum CNAE,
   e o vetor traz "3511500 Geracao de energia eletrica").

3. NUNCA use so o vetor (sem ILIKE). Motivo: sem o ILIKE nao existe
   fallback, e o vetor com LIMIT 2 escolhe so os 2 CNAEs mais
   proximos, jogando fora os outros validos. Ex: "confeitaria" com
   so vetor trouxe 1 empresa; com ILIKE trouxe 299.

4. NUNCA remova o NOT EXISTS. Sem ele o vetor entra junto com o
   ILIKE e o resultado volta a misturar categorias diferentes.

5. A subquery do vetor PRECISA estar dentro de "SELECT cnae FROM (...) sub".
   Se voce fizer "SELECT cnae FROM c_cnaes ORDER BY ... LIMIT 2" direto
   dentro do UNION, o Postgres da erro ("ORDER BY invalido em UNION").

EXEMPLO COMPLETO para "energia solar em lages" (o ILIKE nao acha
nada, entao o vetor entra):
  SELECT e.cnpj_basico, emp.razao_social, c.nm_cnae
  FROM c_estabelecimentos e
  JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
  JOIN c_cnaes c ON e.cnae_principal = c.cnae
  WHERE e.cd_matriz_filial = 1
    AND e.municipio IN (SELECT cd_municipio FROM c_municipios WHERE municipio ILIKE '%LAGES%')
    AND e.cnae_principal IN (
        SELECT cnae FROM c_cnaes
        WHERE nm_cnae ILIKE '%energia solar%'
        UNION
        SELECT cnae FROM (
            SELECT cnae FROM c_cnaes
            WHERE NOT EXISTS (
                SELECT 1 FROM c_cnaes
                WHERE nm_cnae ILIKE '%energia solar%'
            )
            ORDER BY embedding <=> %s::halfvec(384)
            LIMIT 2
        ) sub
    )
  LIMIT 100

O %s acima e um vetor que o sistema vai injetar automaticamente na
query. Voce NAO precisa saber o valor. Apenas use a sintaxe
`ORDER BY embedding <=> %s::halfvec(384)`.

REGRA CRITICA: no UNION, o vetor DEVE estar em subquery:
  CERTO: UNION SELECT cnae FROM (SELECT cnae FROM c_cnaes WHERE NOT EXISTS (...) ORDER BY embedding <=> %s::halfvec(384) LIMIT 2) sub
  ERRADO: UNION SELECT cnae FROM c_cnaes WHERE NOT EXISTS (...) ORDER BY embedding <=> %s::halfvec(384) LIMIT 2
  (o "ERRADO" nao tem a subquery "SELECT cnae FROM (...) sub" e da
  erro "ORDER BY invalido em UNION")

NUNCA use a coluna embedding para outra coisa. Ela serve apenas para
descobrir o CNAE mais similar a um termo em portugues.


=====================================================================
ATIVIDADE + NOME (MARCA) - OS DOIS FILTROS SAO OBRIGATORIOS
=====================================================================

Quando a pergunta cita um TIPO DE NEGOCIO e um NOME/MARCA
("liste os mercados KOCH", "supermercados Koch em SC", "farmacias
Sao Joao"), a query precisa dos DOIS filtros ligados por AND:

  1. filtro de CNAE (secao BUSCA SEMANTICA acima);
  2. filtro de nome fantasia (EXISTS, secao CODIGOS CONHECIDOS).

NUNCA descarte o filtro de CNAE. Sem ele a query vira "qualquer
empresa com o nome": no banco real sao centenas de matrizes com KOCH
no nome fantasia, o LIMIT 100 corta a lista no meio e a rede de
supermercados (KOCH HIPERMERCADO S/A, CNAE 4691500) nem aparece.

EXEMPLO COMPLETO para "liste os mercados KOCH":

  SELECT e.cnpj_basico, emp.razao_social, e.nm_fantasia,
         m.municipio, e.uf, c.nm_cnae
  FROM c_estabelecimentos e
  JOIN c_empresas emp ON emp.cnpj_basico = e.cnpj_basico
  JOIN c_cnaes c ON c.cnae = e.cnae_principal
  LEFT JOIN c_municipios m ON m.cd_municipio = e.municipio
  WHERE e.cd_matriz_filial = 1
    AND e.cnae_principal IN (
        SELECT cnae FROM c_cnaes
        WHERE nm_cnae ILIKE '%mercado%'
        UNION
        SELECT cnae FROM (
            SELECT cnae FROM c_cnaes
            WHERE NOT EXISTS (
                SELECT 1 FROM c_cnaes
                WHERE nm_cnae ILIKE '%mercado%'
            )
            ORDER BY embedding <=> %s::halfvec(384)
            LIMIT 2
        ) sub
    )
    AND EXISTS (
        SELECT 1 FROM c_estabelecimentos sub
        WHERE sub.cnpj_basico = e.cnpj_basico
          AND sub.nm_fantasia ILIKE '%KOCH%'
    )
  ORDER BY emp.razao_social
  LIMIT 100

"mercado" casa com "mercadorias" no nm_cnae, entao o ILIKE ja traz
4691500 (atacadista), 4711301 (hipermercados), 4711302
(supermercados) e 4712100 (minimercados). O NOT EXISTS zera o vetor.

Toda LISTAGEM com LIMIT deve ter ORDER BY (ex:
ORDER BY emp.razao_social). Sem ORDER BY o corte das 100 linhas e'
arbitrario e a empresa procurada pode ficar de fora.


c_municipios
  cd_municipio  integer (PK) -- codigo IBGE
  municipio     varchar(100) -- nome

c_naturezas
  cd_nat_juridica  integer (PK)
  nat_juridica     varchar(255)

c_paises
  cd_pais  integer (PK)
  pais     varchar(100)

c_qualificacoes
  cd_quals  smallint (PK)
  nm_quals  varchar(255)

c_motivos
  cd_motivo_sit_cadastro  smallint (PK)
  motivo_sit_cadastro     varchar(255)

=====================================================================
FILTRO POR CIDADE - A EMPRESA PRECISA TER ESTABELECIMENTO NA CIDADE
=====================================================================

A listagem continua sendo UMA LINHA POR EMPRESA (matriz), mas o filtro de
cidade NAO pode olhar so a sede. A rede pode ter a matriz em uma cidade e
as lojas em outra:

  KOCH HIPERMERCADO S/A (cnpj_basico 02831172)
    matriz em TIJUCAS (SC), CNAE 4691500 (atacadista)
    4 filiais em BALNEARIO DE PICARRAS (SC), CNAE 4711301 (varejista)

Com `e.municipio IN (...picarras...)` a empresa some de "atacadistas de
picarras". Com o semi-join abaixo ela aparece (as 4 filiais garantem a
presenca na cidade) e a linha listada e' a matriz, com o municipio real
dela (Tijucas).

FORMA CERTA (semi-join por cnpj_basico):

  AND e.cnpj_basico IN (
      SELECT f.cnpj_basico FROM c_estabelecimentos f
      WHERE f.municipio IN (
          SELECT cd_municipio FROM c_municipios
          WHERE municipio ILIKE '%PICARRAS%'
      )
  )

FORMA ERRADA (EXISTS correlacionado - 10x mais lento):

  AND EXISTS (SELECT 1 FROM c_estabelecimentos f
              WHERE f.cnpj_basico = e.cnpj_basico
                AND f.municipio IN (...))

  Motivo: com o EXISTS o planner faz um Nested Loop por matriz candidata;
  com o IN ele monta um hash dos cnpj_basico da cidade. Medido:
  "atacadistas de picarras" = 0,3 s (IN) x 3,3 s (EXISTS).

O EXISTS de nome fantasia (secao CODIGOS CONHECIDOS) NAO e' afetado:
aquele usa idx_estab_nome_fantasia_gin_todos e e' barato.

CUSTO (medido, cache quente) - o semi-join escala com o tamanho da cidade
e com a popularidade da atividade:

  atacadistas de picarras    0,3 s
  supermercados de picarras  0,5 s
  padarias de picarras       2 a 5 s
  padarias de lages          6 s
  padarias de curitiba      12 s
  (filtro direto na matriz: 0,03 a 0,3 s nos mesmos casos)

Por isso:
  - cidade pequena/media + atividade especifica -> use o semi-join;
  - capital ou metropole -> filtro direto na matriz (e.municipio IN ...);
  - atividade muito comum (padaria, mercado, restaurante, posto,
    farmacia) -> filtro direto na matriz;
  - CONTAGEM PURA (COUNT sem GROUP BY) -> filtro direto na matriz;
  - pergunta so com UF (e.uf = 'SC') -> filtro direto na matriz.

Quando o semi-join e' usado, o texto da resposta deve dizer "empresas com
estabelecimento em <cidade>", porque a coluna municipio exibe o municipio
da matriz.

INDICES ENVOLVIDOS:
  idx_estabelecimentos_municipio (municipio)          -- lado da cidade
  idx_estabelecimentos_cnpj_basico (cnpj_basico)      -- salto para a matriz
  idx_estab_matriz_mun_cnae (municipio, cnae_principal) WHERE matriz
                                                      -- filtro direto

=====================================================================
CODIGOS CONHECIDOS (use para interpretar a pergunta em PT-BR)
=====================================================================

porte_empresa (c_empresas):
  1        = Micro Empresa (ME)
  3        = Empresa de Pequeno Porte (EPP)
  5        = Demais
  0 ou NULL = Nao informado

cd_matriz_filial (c_estabelecimentos):
  1 = Matriz
  2 = Filial

cd_sit_cadastro (c_estabelecimentos):
  1 = Nula
  2 = Ativa
  3 = Suspensa
  4 = Inapta
  8 = Baixada

op_simples (c_simples):
  'S' = Optante pelo Simples Nacional
  'N' = Nao optante
  NULL/vazio = Outros

op_mei (c_simples):
  'S' = Optante pelo MEI
  'N' = Nao optante
  NULL/vazio = Outros

id_tipo_socio (c_socios):
  1 = Pessoa Juridica
  2 = Pessoa Fisica
  3 = Estrangeiro

faixa_etaria (c_socios):
  0 = Nao se aplica
  1 = 0 a 12 anos
  2 = 13 a 20 anos
  3 = 21 a 30 anos
  4 = 31 a 40 anos
  5 = 41 a 50 anos
  6 = 51 a 60 anos
  7 = 61 a 70 anos
  8 = 71 a 80 anos
  9 = Maiores de 80 anos

TRADUCAO DE LINGUAGEM NATURAL PARA CODIGO:
  "empresas de <cidade>"    -> WHERE e.municipio IN (
                                  SELECT cd_municipio FROM c_municipios
                                  WHERE municipio ILIKE '%TOKEN%'
                                )
                                (nome oficial, sem acento, pode ter "DE"
                                 no meio: Balneario Picarras esta gravado
                                 como BALNEARIO DE PICARRAS)
                                Em LISTAGEM de empresa com cidade pequena
                                 ou media, troque por e.cnpj_basico IN (SELECT
                                 ... ) para pegar quem tem estabelecimento na
                                 cidade. Ver secao FILTRO POR CIDADE.
  "micro empresa" / "ME"     -> WHERE porte_empresa = 1
  "pequeno porte" / "EPP"    -> WHERE porte_empresa = 3
  "demais" / "grande"        -> WHERE porte_empresa = 5
  "empresa ativa"            -> WHERE cd_sit_cadastro = 2
  "empresa baixada"          -> WHERE cd_sit_cadastro = 8
  "matriz" / "sede"          -> WHERE cd_matriz_filial = 1
  "filial"                   -> WHERE cd_matriz_filial = 2
  "MEI"                      -> WHERE op_mei = 'S'
  "Simples"                  -> WHERE op_simples = 'S'
  "socio pessoa fisica"      -> WHERE id_tipo_socio = 2
  "nome fantasia X"         -> WHERE EXISTS (
                                  SELECT 1 FROM c_estabelecimentos sub
                                  WHERE sub.cnpj_basico = e.cnpj_basico
                                    AND sub.nm_fantasia ILIKE '%X%'
                                )
                                (o nome fantasia pode estar SO na filial,
                                 nao na matriz. Por isso use EXISTS, nao
                                 e.nm_fantasia ILIKE direto)
  "mercados KOCH"           -> filtro de CNAE (BUSCA SEMANTICA, com
                               ILIKE '%mercado%') E EXISTS de fantasia
                               ILIKE '%KOCH%', os dois com AND.
                               Ver secao ATIVIDADE + NOME (MARCA).

=====================================================================
DICA CRITICA DE PERFORMANCE
=====================================================================

Agregar (COUNT, GROUP BY) sobre qualquer tabela GIGANTE leva ~3 a ~10
segundos. Isso e esperado. O que NAO pode e:

  - JOIN direto entre duas GIGANTES sem filtro forte em uma delas
    (ex: c_empresas JOIN c_estabelecimentos sem WHERE)
  - Self-join em gigante
  - ORDER BY em coluna sem indice quando a tabela e gigante

Prefira:
  - Filtro por cd_matriz_filial = 1 (usa indices parciais)
  - Filtro por cd_sit_cadastro = 2 (idem)
  - Filtro por op_simples = 'S' ou op_mei = 'S' (indices parciais)
  - Subconsulta com IN em vez de JOIN entre gigantes
  - Ordenar/contar por razao_social: use idx_empresas_razao_cnpj
    (e' composto: razao_social + cnpj_basico, permite keyset rapido)
  - Ordenar/contar por nome fantasia em uma UF: use
    idx_estab_uf_sit_fantasia_matriz (composto: uf + cd_sit_cadastro +
    nm_fantasia + cnpj_basico + cnpj_ordem + cnpj_dv)
  - Contar por UF: use idx_estab_uf_matriz_mun (uf + municipio, parcial)
  - Buscar por nome fantasia: use EXISTS com subquery em
    c_estabelecimentos (cobre matriz + filial via
    idx_estab_nome_fantasia_gin_todos). NAO use OR do tipo
    `e.nm_fantasia ILIKE %s OR EXISTS (...)`, porque o planner escolhe
    Merge Join e le 36M de linhas (~23s). Use EXISTS sozinho, que forca
    Nested Loop (~200ms).
  - Para listagens de empresas, use SEMPRE INNER JOIN c_empresas emp ON
    emp.cnpj_basico = e.cnpj_basico e selecione emp.razao_social junto com
    e.nm_fantasia. A razao_social e' o nome oficial e sempre existe,
    enquanto nm_fantasia pode ser null.  
"""



def get_schema_context() -> str:
    """Retorna o contexto de schema para ser usado no prompt do LLM."""
    return SCHEMA_CONTEXT.strip()
    