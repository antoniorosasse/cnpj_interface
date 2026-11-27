# Índice UF + CNAE para listagens com capital social

**Criado em:** 27/11/2026 · **Status:** criado no banco local; produção pendente

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
