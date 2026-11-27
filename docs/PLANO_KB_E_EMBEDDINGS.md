# Plano de KB autoaprendizável e melhoria do vetor

**Criado em:** 27/11/2026 · **Escopo:** `cnpj_interface` (chat + `/proximidade/`)
**Objetivo:** aumentar a precisão da resolução de atividade → CNAE sem perder
previsibilidade, usando IA na triagem e um benchmark de domínio para decidir o
modelo de embedding.

## Princípios

1. **Precisão antes de cobertura**: é melhor pedir esclarecimento do que devolver
   um lead errado.
2. **A IA escolhe entre candidatos recuperados**, nunca inventa códigos CNAE.
3. **KB é versionada, auditável e reversível**: toda promoção tem origem,
   evidência e rollback.
4. **Nenhuma autoaprovação usa apenas vetor**: exige evidência lexical/recorrência
   e limite de confiança.
5. **Produção só muda depois do benchmark offline** e de canário.

---

## Fase 0 — Baseline e gold set (concluída nesta rodada)

**Entregas**
- `docs/gold_atividade_cnae.json`: 86 atividades + 3 negativos, com CNAEs aceitos,
  grupo, dificuldade e observação.
- `scripts/avaliar_atividade_cnae.py`: benchmark lexical e modelos
  SentenceTransformers; métricas top-1/top-3/MRR por grupo.
- Resultado inicial: lexical 26,7% top-1, Braza atual **23,3%**, BrazEmbed-PT-BR
  **53,5%**. O Braza atual é fraco para descrições curtas de CNAE; BrazEmbed
  melhora, mas ainda não é precisão de produto — ver `AVALIACAO_ATIVIDADE_CNAE.md`.

**Gate:** escolher a base candidata (BrazEmbed/híbrido/reranking) antes de mexer
em produção.

---

## Fase 1 — Telemetria e fila de candidatos (sem escrever na KB) ✅ implementada localmente

**Status 27/11:** modelos `AtividadeLog`/`CnaeAliasCandidato` (migrações
`chat.0008` e `0010`), log em chat/página com **score**, admin e comando
`propor_aliases_cnae`; testes cobrindo score/fila. Suíte: 823 testes OK. Falta deploy.

**Objetivo:** descobrir o que os usuários realmente perguntam e medir a qualidade
do resolvedor.

**Entregas**
- Registrar em tabela `atividade_log`:
  - termo normalizado, texto original, origem (chat/página), grupo;
  - `fonte` (`kb`, `codigo`, `iliKe`, `vetor`, `nenhuma`);
  - CNAEs escolhidos, score/distância, quantidade de resultados;
  - hash do usuário (não o e-mail), timestamp, conversa.
- Comando `propor_aliases_cnae`:
  - agrupa por termo;
  - conta usos, fontes e CNAEs;
  - gera candidatos com evidência;
  - não altera a KB.
- Tela admin somente leitura de candidatos.

**Critérios de aceite**
- Nenhuma mudança no comportamento do chat.
- Consultas com `fonte=nenhuma` e `fonte=vetor` aparecem no relatório.
- Custo: apenas inserts; sem chamada extra de LLM.

**Riscos:** volume de log; definir retenção e anonimização.

---

## Fase 2 — Juiz IA + revisão humana ✅ implementada localmente

**Status 27/11:** `chat/services/kb_judge.py` (juiz com lista fechada),
`CnaeAlias`/`CnaeAliasHistorico` versionados, ações no admin para avaliar,
autoaprovar com critérios e publicar/desativar; migrações `chat.0011`/`0012`.
Ainda não deployado.

**Objetivo:** usar IA para triar candidatos, deixando ao humano apenas os casos
ambíguos.

**Fluxo**
1. Candidatos recuperados por KB/ILIKE/vetor (lista fechada, com descrição).
2. IA recebe termo + candidatos e devolve JSON:
   ```json
   {"decisao":"adicionar|revisar|rejeitar","cnaes":[...],
    "confianca":0.0,"justificativa":"..."}
   ```
3. Regras:
   - `adicionar` só se confiança ≥ 0.90 **e** houver evidência lexical/recorrência;
   - `revisar` para conflito, score médio, termo genérico ou vetor-only;
   - `rejeitar` para termos geográficos/genéricos/sem candidato aderente.
4. Admin aprova/rejeita; KB versionada em banco (`cnae_alias`), com cache.
5. Toda alteração registra autor, data, evidência e versão anterior.

**Critérios de aceite**
- Fila de revisão com os casos “mais discrepantes”.
- Amostra de 100 aprovações revisada: **0 falsos positivos graves**.
- Reversão de uma versão da KB em <5 min.
- Testes de regressão por alias aprovado.

**Riscos:** viés de confirmação e contaminação; por isso aprovação humana inicial.

**Segurança aplicada:** admin de `ConfiguracaoIA` restrito a superusuário;
`base_url` allowlist `https://api.deepseek.com`; chave obrigatória na criação e
re-digitação ao trocar endpoint; prompt do juiz delimita dados do usuário e
ignora instruções embutidas.

---

## Fase 3 — Autoaprovação controlada (base pronta, flag desligada)

**Status 27/11:** flag `KB_AUTO_APPROVE` (padrão `0`), ação de admin com
critérios estritos (confiança ≥ 90%, recorrência ≥ 3, evidência lexical, termo
não genérico) e registro em histórico. Falta monitoramento semanal e amostra
humana para ligar a flag em produção.

**Objetivo:** reduzir trabalho manual só nos casos de altíssima confiança.

**Regras de autoaprovação**
- termo recorrente (≥ N consultas independentes);
- mesmo conjunto de CNAEs em ≥ 90% das ocorrências;
- evidência ILIKE exata **ou** aprovação anterior equivalente;
- termo não genérico/geográfico;
- nenhum conflito com categoria existente;
- monitoramento e kill switch por flag.

**Critérios de aceite**
- 30 dias sem regressão de precisão em amostra semanal;
- rollback automático se taxa de correção do usuário subir;
- log de cada promoção automática.

---

## Fase 4 — Melhoria da recuperação (modelo/híbrido)

**Objetivo:** melhorar a qualidade dos candidatos que alimentam a KB e o chat.

**Ordem de experimentos**
1. **BrazEmbed-PT-BR** (768d, 110M, MIT): escolhido; com `nm_cnae` puro deu top-1 54,7% e top-3 74,4%.
2. **Híbrido lexical (`pg_trgm`) + denso + fusão de ranks** — BGE-M3 ficou em 52,3%/65,1%; não justifica o custo sozinho.
3. **Reranking com IA** sobre top 20–30 CNAEs.
4. Só depois: BGE-M3/Qwen3-0.6B em **serviço de embedding dedicado** (uma cópia,
   não uma por worker do gunicorn).
5. Qwen3-8B: descartado sem GPU.

**Restrições reais da VPS**
- 31 GiB, 8 vCPU, sem GPU; Postgres com 8 GB de shared buffers;
- `cnpj.service`: 4 workers × 2 threads, `MemoryHigh=3GB`, `MemoryMax=6GB`;
- modelos grandes por worker estouram o teto: usar processo único/ONNX/int8.

**Critérios de aceite**
- Benchmark no gold set com **top-1 ≥ 60%** e top-3 ≥ 85% (meta inicial);
- latência p95 de resolução ≤ 1 s após o modelo carregado (CPU, cache);
- RSS por worker dentro do teto;
- nenhuma migração irreversível; reembedding de `c_cnaes` reproduzível.

---

## Fase 5 — Canário e produção (flag criada)

**Status 27/11:** `ATIVIDADE_RESOLVER` existe com padrão `v1` em produção
(`v2` em DEBUG). O v2 consulta `CnaeAlias`; ainda não há rollout percentual nem
canário real.

1. Feature flag `ATIVIDADE_RESOLVER=v2` por percentual de usuários.
2. Comparar:
   - top-1/top-3 contra gold set;
   - taxa de esclarecimento;
   - consultas refinadas/abandonadas;
   - leads exportados;
   - feedback de “atividade errada”.
3. Promover para 100% só se falsos positivos não subirem.
4. Manter fallback para o resolvedor antigo por 30 dias.

**Critérios de go/no-go**
- precisão humana da atividade ≥ 95% na amostra;
- erro grave (CNAE de outra categoria) < 2%;
- latência p95 aceitável;
- rollback testado.

---

## Entregáveis técnicos por fase

| Fase | Arquivos principais |
|---|---|
| 0 | `docs/gold_atividade_cnae.json`, `scripts/avaliar_atividade_cnae.py` |
| 1 | modelos `AtividadeLog`, `CnaeAliasCandidato`; comando `propor_aliases_cnae`; admin |
| 2 | `core/cnae_atividade.py` + juiz IA + `CnaeAlias` versionado; cache |
| 3 | regras de autoaprovação + feature flag + monitor |
| 4 | migração `halfvec(768)`, reembedding, benchmark híbrido |
| 5 | canário, métricas, rollback |

## O que NÃO fazer

- Inserir alias direto no código sem teste.
- Deixar o vetor decidir sozinho.
- Autoaprovar termo genérico (“clínica”, “indústria”, “serviços”).
- Usar `Qwen3-8B` ou modelo grande em cada worker do gunicorn.
- Trocar o modelo sem medir no gold set e sem rollback.
