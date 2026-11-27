# Migração Braza 384d → BrazEmbed-PT-BR 768d

**Criado em:** 27/11/2026 · **Status:** plano; nenhuma DDL/DML executada

## Decisão

**BrazEmbed-PT-BR é o modelo escolhido** para a próxima fase (melhor resultado
do benchmark inicial: top-1 53,5%, top-3 73,3%). Ele substitui o Braza 384d como
**gerador de candidatos**, não como decisor final: KB + híbrido + reranking

continuam necessários.

## Antes de migrar: corrigir a medição

O benchmark codificou `nome (cnae)`; o banco atual tem somente `nm_cnae` no
embedding. São textos diferentes. Antes da DDL:

1. decidir o formato do documento (`nm_cnae` ou `nm_cnae (cnae)`);
2. rebenchmarkar os dois formatos;
3. só então escolher e reembedar.

## Banco (local primeiro, depois produção)

Tabela `bancocnpj.c_cnaes`: 1.359 linhas, `halfvec(384)`, índice HNSW.
pgvector 0.8.x suporta `halfvec(768)`.

**Opção recomendada (A): nova coluna + troca atômica**, com rollback:

```sql
ALTER TABLE public.c_cnaes ADD COLUMN embedding_768 halfvec(768);
-- reembedding das 1.359 descrições
CREATE INDEX CONCURRENTLY c_cnaes_embedding_768_idx
  ON public.c_cnaes USING hnsw (embedding_768 halfvec_cosine_ops);
-- sanidade: sem NULL, vector_dims = 768
BEGIN;
  ALTER INDEX c_cnaes_embedding_idx RENAME TO c_cnaes_embedding_braza384_idx;
  ALTER TABLE c_cnaes RENAME COLUMN embedding TO embedding_braza_384;
  ALTER TABLE c_cnaes RENAME COLUMN embedding_768 TO embedding;
  ALTER INDEX c_cnaes_embedding_768_idx RENAME TO c_cnaes_embedding_idx;
COMMIT;
```

- O código antigo continua lendo `embedding` 384 até o deploy; por isso a troca
  e o deploy precisam ocorrer na mesma janela.
- Manter `embedding_braza_384` por 30 dias para rollback; depois `DROP`.

## Código

- `chat/services/embedding_service.py`: modelo, dimensão e cast.
- `chat/services/schema_context.py`: exemplos/DDL/prosa 384 → 768.
- `chat/services/sql_generator.py`: exemplos e regra de fallback.
- `core/cnae_atividade.py`: cast `halfvec(768)` e recalibrar `SCORE_MIN_VETOR`.
- `chat/views.py`: regex que mostra `[VETOR]` precisa aceitar 768.
- `scripts/reembedding_cnae.py`: script novo, com `--dry-run`, `--verify`.
- Testes: atualizar asserts e adicionar teste de cast antigo normalizado.
- Docs: README, docs do chat, menu_interface (diz Braza/384).

**Bug a corrigir antes:** SQL histórico guarda `%s::halfvec(384)`. O
`injetar_vetor` não pode simplesmente inserir 768 no cast antigo; normalizar o
cast para a dimensão atual.

## Operação/ETL

- `ProjetoCargaDados` recarrega `c_cnaes` com **TRUNCATE** e não popula
  embedding. Após qualquer recarga, rodar o reembedding.
- O publish do lote leva `c_cnaes` inteira; a VPS precisa ter as mesmas colunas
  antes do próximo lote.
- `deploy.sh` não toca `bancocnpj`; DDL é manual e exige autorização R9/R10.

## Memória/latência

- BrazEmbed 110M, 768d, ~416 MB fp32 (vs Braza ~186 MB).
- Medido localmente: ~0,88 GB RSS num processo com modelo; encode ~120–180 ms
  após carga.
- `cnpj.service` hoje: 4 workers, `MemoryHigh=3G`, `MemoryMax=6G`. O modelo por
  worker pode exigir reduzir workers/threads ou revisar o teto.

## Ordem local

1. dump de segurança de `c_cnaes`;
2. decidir formato e rebenchmarkar;
3. nova coluna + reembedding + índice;
4. troca atômica em dev;
5. atualizar código/testes;
6. smoke: "energia solar" (vetor), "padarias de lages" (ILIKE), export de
   conversa antiga (regressão de cast), health.

## Produção (somente após dev verde e autorização)

1. conferir pgvector/colunas na VPS;
2. garantir modelo no cache (rsync não leva cache HF);
3. medir RSS/latência;
4. aplicar DDL na janela + deploy do código;
5. smoke e monitorar memória;
6. manter coluna antiga 30 dias.
