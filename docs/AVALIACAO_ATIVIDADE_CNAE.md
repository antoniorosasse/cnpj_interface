# Avaliação atividade → CNAE (baseline de 27/11/2026)

## Método

- **Gold set:** [`gold_atividade_cnae.json`](gold_atividade_cnae.json), com
  **86 atividades** e 3 negativos. Cada atividade tem CNAEs aceitos, grupo e
  dificuldade; termos com faixa de divisão usam `faixas`.
- **Catálogo:** 1.359 CNAEs de `c_cnaes`.
- **Métrica:** top-1 (CNAE aceito na 1ª posição), top-3 (aceito em qualquer das
  3 primeiras) e MRR (posição do primeiro aceito).
- **Execução:** `scripts/avaliar_atividade_cnae.py`.
- **Negativos** (`naves espaciais`, `xpto inexistente`, `serviços gerais`) ficam
  fora das métricas de ranking; eles serão usados na avaliação do juiz de KB.

## Resultados (gold atual, 86 atividades)

| Método | Top-1 | Top-3 | MRR |
|---|---:|---:|---:|
| Lexical (`SequenceMatcher` + bônus de frase) | **26,7%** | 38,4% | 0,348 |
| Braza atual (384d) | **23,3%** | 41,9% | 0,352 |
| **BrazEmbed-PT-BR (768d)** | **53,5%** | **73,3%** | **0,649** |
| BGE-M3 (1024d) | 52,3% | 65,1% | 0,622 |

### BrazEmbed por grupo

| Grupo | n | Top-1 | Top-3 |
|---|---:|---:|---:|
| alimentação | 15 | 66,7% | 66,7% |
| automotivo | 6 | 16,7% | 33,3% |
| beleza | 5 | 80,0% | 80,0% |
| combustível | 3 | 66,7% | 66,7% |
| comércio | 4 | 25,0% | 75,0% |
| construção | 5 | 60,0% | 60,0% |
| educação | 1 | 0,0% | 0,0% |
| esporte | 3 | 33,3% | 66,7% |
| financeiro | 4 | 75,0% | 75,0% |
| hospedagem | 2 | 50,0% | 100,0% |
| imobiliário | 2 | 50,0% | 100,0% |
| indústria | 5 | 40,0% | 80,0% |
| logística | 5 | 40,0% | 80,0% |
| saúde | 13 | 46,2% | 76,9% |
| serviços | 9 | 88,9% | 100,0% |
| tecnologia | 4 | 25,0% | 75,0% |

## Leitura

1. **O Braza atual não é bom para este domínio**: foi treinado em descrições de
   empresas B2B, não em rótulos curtos de CNAE. Top-1 de 23,3% é insuficiente
   para decidir CNAE sozinho.
2. **BrazEmbed melhora muito** (53,5% top-1, 73,3% top-3), mas ainda **não é
   precisão de produto**. Ele é um bom candidato de candidato/recall, não de
   decisão final.
3. **O lexical puro também não resolve**: descrições oficiais usam vocabulário
   diferente (“condicionamento físico” vs “academia”, “fornecimento de alimentos”
   vs “pizzaria”).
4. Portanto, a arquitetura de precisão deve ser:
   **KB curada + lexical + BrazEmbed + reranking IA + limiar + revisão humana**.

## Próximos benchmarks

- Qwen3-Embedding-0.6B (mesmo gold set).
- Reexecutar todos os modelos quando o gold mudar; números só são comparáveis na mesma versão do gold.
- Híbrido `pg_trgm` + BrazEmbed, com fusão de ranks.
- Reranking IA sobre top 20–30.
- Latência de consulta e RSS por worker na VPS real.

## Reprodução

```bash
cd /home/neto/ProjetosPython/cnpj_interface
venv/bin/python scripts/avaliar_atividade_cnae.py --model lexical,braza,brazembed
```

O cache de embeddings fica em `/tmp/avaliacao_cnae` (não versionado).

## Limitações

- **Formato do documento:** o benchmark usa `nome (cnae)`; o embedding atual do banco usa só `nm_cnae`. Antes de migrar, rebenchmarkar os dois formatos (ver `PLANO_MIGRACAO_BRAZEMBED.md`).

- Gold set inicial de 86 atividades, não 200–300; ainda precisa de ampliação com
  perguntas reais.
- CNAEs aceitos foram curados manualmente; alguns termos amplos aceitam mais de
  um código.
- A avaliação mede **ranking do catálogo**, não a decisão final da IA.
