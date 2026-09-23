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

  INDICES (destaques):
    cnpj_basico, cd_matriz_filial, cd_sit_cadastro, uf, municipio,
    cnae_principal, dt_ini, cep
    Vários indices PARCIAIS com WHERE cd_matriz_filial = 1
    (matriz_sit, matriz_uf, matriz_cnae, matriz_ano, matriz_mun,
     matriz_count, keyset_parcial)

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

  INDICES: cnpj_basico, cnpj_cpf, faixa_etaria, nome_ou_razao_social (trgm)

  ATENCAO: a coluna de qualificacao chama-se qualif_socio (NAO
  qualificacao). Nao invente nomes.

=====================================================================
TABELAS DE LOOKUP (pequenas, JOIN barato)
=====================================================================

c_cnaes
  cnae      integer (PK)     -- codigo do CNAE
  nm_cnae   varchar(255)     -- descricao

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
"""



def get_schema_context() -> str:
    """Retorna o contexto de schema para ser usado no prompt do LLM."""
    return SCHEMA_CONTEXT.strip()
    