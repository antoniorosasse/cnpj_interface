# Matriz de perguntas para testar o chat (com e sem proximidade)

**Criado em:** 27/11/2026 · **Escopo:** `chat` + página `/proximidade/`
**Como usar:** cada linha é um caso. Registre **PASS/FAIL**, o tempo, e em caso
de falha copie a resposta/SQL/print. O esperado é sempre: HTTP 200, resposta em
português coerente, sem erro 500, e resultado/tabela no painel.

> Regressão já encontrada: **postos de gasolina** não era categoria da KB.
> Corrigido em 27/11 (`postos` → CNAE 4731800). A pergunta do caso G1 deve
> retornar postos de combustíveis.

---

## A. SQL — listagens por atividade

| ID | Pergunta | Esperado |
|---|---|---|
| A1 | Indústrias metalúrgicas em Santa Catarina abertas há mais de 3 anos | SQL com divisões 24+25, `dt_ini < hoje-3a`, lista com **Data Abertura** em dd/mm/aaaa |
| A2 | Padarias ativas em Joinville/SC | Lista de padarias, município Joinville, tabela no painel |
| A3 | Supermercados em Blumenau/SC | Lista de supermercados (4711302) |
| A4 | Clínicas médicas e odontológicas em São Paulo/SP | CNAEs 8630501..05 + 8640202 |
| A5 | Transportadoras com capital social acima de R$ 500 mil no Paraná | CNAEs 4930201..04, `capital_social` na lista |
| A6 | Postos de gasolina em Joinville/SC | Categoria postos (4731800); lista com atividade |
| A7 | Indústrias de transformação em Santa Catarina | Divisões 10..33; lista |
| A8 | Academias em Curitiba/PR | Resultado coerente (CNAE de academia via ILIKE/vetor) |
| A9 | Farmácias em Florianópolis/SC | Resultado coerente |
| A10 | Restaurantes em Balneário Camboriú/SC | Resultado coerente |
| A11 | Oficinas mecânicas em Joinville/SC | Resultado coerente |
| A12 | Escolas de idiomas em São Paulo/SP | Resultado coerente |
| A13 | CNAE 8630504 em Santa Catarina | Aceita o código de 7 dígitos e filtra |
| A14 | Supermercados e padarias em Joinville/SC | As duas categorias na mesma consulta |
| A15 | Naves espaciais em Marte | Não inventa categoria; pede esclarecimento |
| A16 | Liste os postos de gasolina em Joinville/SC | Lista de postos (4731800) sem erro de categoria |

## B. SQL — contagens e agregações

| ID | Pergunta | Esperado |
|---|---|---|
| B1 | Quantas empresas ativas existem por UF? | Agregação por UF, sem lista detalhada |
| B2 | Quantas empresas ativas existem no Brasil? | Um total |
| B3 | Quantas empresas são do Simples/MEI? | Contagem correta, sem misturar situação |
| B4 | Quais os 10 CNAEs com mais empresas ativas? | Ranking com LIMIT, sem SQL inválido |
| B5 | Quantas empresas por porte? | Agrupamento por porte |
| B6 | Quantas empresas por município em Santa Catarina? | Agrupamento por município |
| B7 | Empresas ativas por ano de abertura em SC | Série por ano |
| B8 | Quantas empresas foram abertas em 2025? | Filtro por ano, contagem |

## C. SQL — filtros e ordenação

| ID | Pergunta | Esperado |
|---|---|---|
| C1 | Empresas com capital social acima de R$ 1 milhão em SC | `capital_social` na lista |
| C2 | Empresas abertas há mais de 10 anos em Curitiba/PR | `dt_ini` na lista, corte correto |
| C3 | Empresas ativas em São Paulo/SP | Situação ATIVA aplicada |
| C4 | Empresas baixadas em Joinville/SC | Situação BAIXADA aplicada |
| C5 | Padarias ativas em Joinville abertas há mais de 2 anos | Atividade + situação + idade juntos |
| C6 | Transportadoras no PR com capital > 500 mil e ativas | Capital + UF + situação |
| C7 | Liste 50 metalúrgicas em SC | LIMIT 50 respeitado |
| C8 | Ordene as metalúrgicas de SC por capital social | Ordenação correta, sem timeout |
| C9 | Empresas com nome “KOCH” em SC | Busca por nome sem estourar o tempo |
| C10 | MEIs ativos em Joinville/SC | Filtro MEI + situação |

## D. Proximidade — origens

| ID | Pergunta | Esperado |
|---|---|---|
| D1 | Encontre 248 indústrias ativas em um raio de 10 km a partir de Piçarras | 1.682 encontradas; 248 mais próximas; link para a página |
| D2 | Padarias ativas a 1 km da Rua do Príncipe, 500, Joinville/SC | Origem no endereço (CNEFE), lista com km |
| D3 | Clínicas a 5 km da Av. Paulista, 1000, São Paulo/SP | Origem resolvida; lista e mapa |
| D4 | Supermercados a 3 km do CEP 89201-000 | Origem pelo CEP |
| D5 | Academias a 2 km de Joinville/SC | Origem cidade; distância em km |
| D6 | Restaurantes a 1 km de -26.30,-48.84 | Origem coordenada |
| D7 | Farmácias a 5 km de um CNPJ específico | Origem por CNPJ completo |
| D8 | Metalúrgicas perto da Av. Paulista | Pede cidade/UF ou CEP (endereço ambíguo) |
| D9 | Encontre padarias a 5 km deste endereço | Sem contexto anterior, pede a origem |
| D10 | (após D2) Agora clínicas a 5 km deste endereço | Reusa a origem anterior |
| D11 | Postos de gasolina a 1 km de Piçarras | Categoria postos + raio |

## E. Proximidade — filtros e limites

| ID | Pergunta | Esperado |
|---|---|---|
| E1 | Padarias ativas a 5 km de Joinville/SC | Situação ativa |
| E2 | Supermercados e padarias a 5 km de Joinville/SC | Duas atividades |
| E3 | Clínicas em 10 km da Av. Paulista abertas há mais de 2 anos | Idade no link da página |
| E4 | Transportadoras no PR a 10 km de Curitiba com capital > 500 mil | UF + capital no link |
| E5 | Metalúrgicas a 100 km de São Paulo/SP | Explica que a área é grande e pede raio menor/UF |
| E6 | 10 padarias mais próximas de Joinville/SC | Limite 10 respeitado |
| E7 | Empresas a 1 km de um ponto no oceano | Nenhum resultado, sem erro |
| E8 | Indústrias a 100 km de Joinville/SC | Sem filtro seletivo, deve aceitar o raio grande |
| E9 | Padarias a 1 km de Joinville/SC somente matrizes | Só matrizes |
| E10 | Padarias a 1 km de Joinville/SC todas as situações | Sem filtro de situação |
| E11 | Clínicas a 5 km de Joinville/SC somente ativas | Ativas + categoria |

## F. Conversa, histórico e follow-up

| ID | Turno | Esperado |
|---|---|---|
| F1 | “e dessas, quais têm capital acima de 500 mil?” após A1 | Mantém o contexto da pergunta anterior |
| F2 | “agora só ativas” após A1 | Refiltra a consulta anterior |
| F3 | “e num raio de 5 km?” após D2 | Reusa a origem e recomputa |
| F4 | F5 após resposta com tabela | Conversa/tabela continuam no painel |
| F5 | Abrir outra conversa na gaveta | Painel mostra todas as tabelas da conversa escolhida |
| F6 | Nova conversa e fazer a mesma pergunta | Não herda dados da conversa antiga |
| F7 | Pergunta com mais de 500 caracteres | HTTP 400/ mensagem amigável, sem processar |

## G. Interface do resultado

| ID | Ação | Esperado |
|---|---|---|
| G1 | **Postos de gasolina a 1 km de Piçarras** | Painel com tabela, sem erro de categoria |
| G2 | Rolar a tabela | Cabeçalho fixo; barra horizontal quando houver colunas |
| G3 | Topo / Final | Vai ao início/fim da tabela |
| G4 | Abas Resultado / SQL / Gráfico | Alternam; SQL oculto em proximidade |
| G5 | CSV / JSON da tabela | Baixa a tabela exibida; não consome crédito |
| G6 | Exportar CSV/JSON no rodapé do painel | Baixa a última lista da conversa (caminho cobrado) |
| G7 | Expandir / Recolher / Reabrir painel | Largura alterna; botão Resultados reaparece |
| G8 | Várias perguntas na mesma conversa | Painel mantém todas as tabelas, não só a última |
| G9 | Mobile (390 px) | Painel empilhado, sem overflow horizontal da página |
| G10 | Tema escuro | Textos, botões, abas e tabela legíveis |

## H. Limites e erros

| ID | Pergunta/ação | Esperado |
|---|---|---|
| H1 | Sem login | Chat redireciona para login |
| H2 | Cota/limite de chat | Mensagem de limite, sem 500 |
| H3 | Pergunta vazia | Não envia |
| H4 | Atividade desconhecida | Pede esclarecimento com categorias aceitas |
| H5 | Origem inexistente | Pede cidade/UF/CEP |
| H6 | Consulta que estoura timeout | Mensagem amigável, sem 500 |
| H7 | Duas abas/reload durante consulta | Não duplica pergunta no histórico |

## I. Perguntas de referência do produto (as 5 do material)

1. Encontre 248 indústrias ativas em um raio de 10 km a partir de uma empresa/endereço/coordenadas.
2. Indústrias metalúrgicas em Santa Catarina abertas há mais de 3 anos.
3. Clínicas médicas e odontológicas em um raio de 5 km da Av. Paulista.
4. Transportadoras com capital social acima de R$ 500 mil no Paraná.
5. Supermercados e padarias a até 5 km deste endereço com mais de 2 anos de abertura.

**Regressão desta rodada:** `Postos de gasolina no raio de 1 km de picarras` — se
voltar “atividade não reconhecida”, a KB/categoria `postos` quebrou.
