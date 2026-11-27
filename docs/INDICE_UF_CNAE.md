# Índice UF + CNAE para listagens com capital social

**Criado em:** 27/11/2026 · **Status:** índices criados no banco local; produção pendente

## Motivo

A consulta "transportadoras com capital > R$ 500 mil no PR" fazia
`Seq Scan` em `c_estabelecimentos` (2,3M linhas no PR) porque não havia índice
que combinasse UF e CNAE. Medido antes: ~24 s; depois: ~1,1 s no `EXPLAIN`.

## DDL local aplicado

```sql
CREATE INDEX idx_estab_uf_cnae_matriz
  ON c_estabelecimentos (uf, cnae_principal)
  INCLUDE (cnpj_basico, cnpj_ordem, cnpj_dv)
  WHERE cd_matriz_filial = 1;
```

- Tamanho/tempo local: ~169 s de criação.
- O índice é parcial (`cd_matriz_filial = 1`), cobre o filtro de UF+CNAE e
  inclui as colunas do CNPJ para evitar heap na maior parte do caminho.
- O filtro de `capital_social` continua em `c_empresas` (join por
  `cnpj_basico`), por isso a consulta ainda faz um `Index Scan` em
  `c_empresas`; o ganho vem de não varrer 2,3M de estabelecimentos.

## Índices complementares (idade + município)

Para "abertas há mais de 10 anos em Curitiba/PR" o COUNT ainda varria muito.
Foram criados no local:

```sql
CREATE INDEX idx_estab_mun_dt_matriz
  ON c_estabelecimentos (municipio, dt_ini)
  WHERE cd_matriz_filial = 1;                    -- ~71 s

CREATE INDEX idx_estab_mun_uf_dt_matriz
  ON c_estabelecimentos (municipio, uf, dt_ini)
  WHERE cd_matriz_filial = 1;                    -- ~121 s
```

- COUNT de Curitiba (>10 anos): **43,9 s → 0,53 s**;
- o chat passou a exibir `100 de 476.017`;
- a consulta de listagem fica em ~6 s.

## Produção

Não aplicar ainda. Quando for aprovado:

```sql
CREATE INDEX CONCURRENTLY idx_estab_uf_cnae_matriz
  ON c_estabelecimentos (uf, cnae_principal)
  INCLUDE (cnpj_basico, cnpj_ordem, cnpj_dv)
  WHERE cd_matriz_filial = 1;
```

- `CONCURRENTLY` para não bloquear escrita durante a criação.
- É DDL em tabela gigante; exige autorização R9/R10 e janela/monitoramento.
- Medir tempo, tamanho e `EXPLAIN (ANALYZE, BUFFERS)` antes/depois.
