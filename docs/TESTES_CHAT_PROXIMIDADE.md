# Checklist de testes — integração Chat IA ↔ Proximidade

**Criado em:** 26/11/2026 · **Escopo:** `cnpj_interface` (apps `chat`, `core`, `exportacao`)
**Ambiente principal:** dev (`http://localhost:8002`) · **Produção:** só depois do OK
**Objetivo:** fechar a integração antes de começar a divulgação. Cada item tem
passo, resultado esperado e onde olhar quando falhar.

> Rodar o checklist **na ordem**. A seção A é automatizável; B–D são manuais
> (navegador). Registre data/hora, ambiente, usuário e evidência (print/tempo)
> nos itens que falharem.

---

## A. Automático (sem navegador)

Pré-requisitos: `venv` do projeto, banco local carregado (`bancocnpj`) e
migrations aplicadas (`python manage.py migrate chat`).

### A.1 Script rápido (30–60 s)

```bash
cd /home/neto/ProjetosPython/cnpj_interface
scripts/verificar_chat_proximidade.sh
```

Esperado: termina com `OK — verificacao rapida` e código de saída `0`.
O script roda: `manage.py check`, `makemigrations --check`, e as suítes
dirigidas de planner, serviço de proximidade, API, gerador SQL, motor filtrado,
filtros da página e export de proximidade.

### A.2 Suíte completa (3–5 min)

```bash
cd /home/neto/ProjetosPython/cnpj_interface
scripts/verificar_chat_proximidade.sh --completo
```

Esperado: `manage.py test chat core exportacao` sem falhas
(referência de 26/11: chat **393**, core+exportacao **391**).

### A.3 Smoke HTTP (com o `runserver` no ar)

```bash
cd /home/neto/ProjetosPython/cnpj_interface
venv/bin/python manage.py runserver 8002   # em outro terminal
scripts/verificar_chat_proximidade.sh --smoke http://localhost:8002
```

Esperado para cada URL: `/proximidade/` 200; `/proximidade/?origem=Joinville/SC&raio=1` 200;
`/chat/` 200 (logado) ou 302 (deslogado, vai para login); `/health/deep/` 200
(quando existir no host).

---

## B. Smoke manual de telas

| ID | Passo | Esperado |
|---|---|---|
| B1 | Abrir `/proximidade/` sem parâmetros | Formulário em 3 linhas (origem/raio/situação/matrizes; atividade/UF/idade/capital; botões), sem erro |
| B2 | Buscar `Joinville/SC` raio 5 | Lista, total, mapa e paginação; sem *timeout* |
| B3 | Abrir `/chat/` logado | Conversa atual carregada; sidebar “Conversas”; composer visível |
| B4 | Recarregar (F5) o chat com resposta antiga que tinha tabela | Tabela continua renderizada (PostgreSQL agora guarda `colunas/rows`) |
| B5 | Fazer a mesma pergunta em conversa nova | Mesma resposta; histórico listado; dados antigos não vazam |
| B6 | Alternar tema claro/escuro nas duas páginas | Componentes legíveis; sem “flash” de tema |

---

## C. Funcional — Chat + Proximidade

### C1. Chat SQL (não geográfico) com categoria de atividade (CNAE)
1. Pergunte: `Indústrias metalúrgicas em Santa Catarina abertas há mais de 3 anos`.
2. Esperado: SQL com `e.cnae_principal BETWEEN 2400000 AND 2599999` e
   `e.dt_ini < CURRENT_DATE - INTERVAL '3 years'`; sem `%s::halfvec(768)`;
   a lista **inclui a Data Abertura** (`e.dt_ini`) em `dd/mm/aaaa`.
   Regra geral: todo campo citado na pergunta (idade, capital, UF, situação,
   atividade) deve aparecer na lista, mesmo já estando no filtro.
3. Repita com `Transportadoras com capital social acima de R$ 500 mil no Paraná`.
   Esperado: `cnae_principal IN (4930201..4930204)` e `capital_social > 500000`;
   sem `ORDER BY razao_social` (lista parcial rápida).

### C2. Chat de proximidade — origem
1. Pergunte: `Encontre 248 indústrias ativas em um raio de 10 km a partir de Piçarras`.
   Esperado: resposta com `tipo=proximidade`, total, “mostrando os 248 mais próximos”,
   `dt_ini` em `dd/mm/aaaa`, link “Abrir na página de proximidade”.
2. Teste as 5 origens: CNPJ completo, CEP, `Joinville/SC`, endereço
   (`Rua do Príncipe, 500, Joinville/SC`) e coordenada (`-26.30,-48.84`).
   Esperado: todas resolvem; endereço mostra “média dos endereços do logradouro”
   quando o CNEFE cobre a rua.
3. Sem origem (“...a partir de uma empresa”) → o chat **pede** CNPJ/endereço.
4. `Av. Paulista` sem cidade/UF → o chat **pede desambiguação** (cidade/UF ou CEP).
5. “deste endereço” depois de uma consulta geo → reusa a origem anterior; sem
   contexto, pede a origem.

### C3. Filtros na página vindos do chat
1. Abra o link do item C2.1.
2. Esperado:
   - total da página **igual** ao total informado no chat;
   - painel **“Consulta feita no chat”** com pergunta e resposta;
   - `atividade`, `uf`, `idade`, `capital` e `situacao` preenchidos;
   - “Somente matrizes” desmarcado quando o chat inclui filiais.
3. Avance uma página e alterne distância real × CEP: o painel e os filtros
   continuam na URL.

### C4. Modalidade de distância
1. O botão de alternar “distância do CEP” foi **removido** em 26/11: a página
   usa automaticamente o endereço (CNEFE) e cai para o centro do CEP quando
   não há endereço. Não deve aparecer botão de troca na tela.
2. Esperado: a lista continua com km; a página não quebra quando
   `c_estab_geo`/`c_ceps_folha` não existem (cai para CEP).

### C5. Exportação
1. Na página filtrada, clique **CSV** e **JSON**.
2. Esperado: arquivo respeita os filtros; cabeçalho legível; linhas ≤ teto do
   plano/amostra; registro em `Exportacao` com `origem=proximidade`; créditos
   debitados/estornados conforme o caso.
3. No chat, após consulta geo, use “Baixar última lista (CSV/JSON)”.
   Esperado: reexecuta o plano guardado, gera arquivo com os mesmos filtros;
   CSV com cabeçalho `CNPJ;Razão social;...`.
4. F5 e repetir: a última resposta continua sendo a exportável (não volta para
   um SQL antigo).

### C6. Casos de borda
| Caso | Esperado |
|---|---|
| Atividade fora das categorias conhecidas (`naves espaciais`) | esclarecimento com as categorias de atividade aceitas |
| Código CNAE digitado como atividade (`8630504`) | aceita e filtra |
| `indústrias de transformação` em raio de 100 km de São Paulo/SP | mensagem pedindo raio menor (~10 km) ou UF/cidade: a área tem mais de 50 mil CEPs para o filtro |
| Raio grande com filtro (acima do limite de CEPs) | mensagem pedindo raio menor |
| `capital` + atividade comum em capital | pode demorar; timeout vira mensagem amigável, não 500 |
| Consulta sem resultado | texto “Nenhum resultado…” e tabela vazia, sem erro |
| Duas atividades (“supermercados e padarias”) | página e chat com o mesmo conjunto |

---

## D. Antes e depois do deploy (produção)

1. Backup (`backup_bancos_servidor.sh`) e conferência do dump.
2. `migrate chat` (migrations `0006_mensagem_plano` e `0007_…_colunas_rows`).
3. `deploy.sh cnpj`; health checks 200.
4. Repetir B1–B6 e C2–C5 **em produção**, com um usuário de teste.
5. Medir tempos das 5 perguntas de referência + página filtrada (cache quente)
   e registrar em `README.md`/docs.
6. Só então liberar a divulgação.

---

## Registro de execução

| Data | Ambiente | Seção | Resultado | Evidência/observação |
|---|---|---|---|---|
| | | | | |
