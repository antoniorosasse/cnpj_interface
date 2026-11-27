# Configuração de APIs de IA pelo admin

**Criado em:** 27/11/2026

## Para que serve

Permite cadastrar/rotacionar as credenciais dos provedores de IA **sem editar o
`.env`**, separando o consumo por finalidade:

| Finalidade | Uso |
|---|---|
| **Chat dos clientes** (`chat_cliente`) | Perguntas que os clientes fazem no site/chat. |
| **Aprendizado interno da KB** (`kb_interna`) | Juiz/geração de aliases CNAE, propostas de KB e rotinas internas. |
| **Testes/diagnóstico** (`teste`) | Validação de chave/modelo. |

Cada finalidade tem sua própria chave, modelo, base URL e timeout. O cliente
resolve primeiro a configuração **ativa e padrão** da finalidade no banco; se não
houver, usa o fallback `DEEPSEEK_*` do `.env` (compatibilidade).

## Onde fica

`Admin → Configurações de IA` (`/admin/chat/configuracaoia/`).

- A chave é digitada uma vez e **nunca volta** para a tela; o admin mostra só
  `sk-abc...1234`.
- A chave fica criptografada no banco (`api_key_encrypted`).
- A ação **“Testar conexão das configurações selecionadas”** faz uma chamada
  curta de verdade.
- `Admin → Usos de IA` mostra tokens, modelo, finalidade, duração e sucesso —
  base para custo e auditoria.

## Criptografia

- A chave-mestra vem de `IA_CREDENTIALS_KEY` ou, se ausente, é derivada da
  `SECRET_KEY` do Django.
- **Não troque a `SECRET_KEY` sem reconfigurar as chaves** se estiver usando a
  derivação padrão; a chave antiga deixa de ser descriptografável.
- Para produção, recomenda-se definir uma `IA_CREDENTIALS_KEY` própria, separada
  da `SECRET_KEY`, e guardá-la fora do repositório.
- A API key do provedor **não** é exibida em log, admin, migration ou resposta.

## Ordem de resolução

```text
ConfiguracaoIA ativa/padrão da finalidade
  → se tiver chave válida: usa ela
  → senão: DEEPSEEK_API_KEY / CHAT_DEEPSEEK_MODEL / CHAT_DEEPSEEK_BASE_URL do .env
  → senão: erro de configuração amigável
```

## Rotação

1. Cadastre a nova configuração com a chave nova.
2. Marque como **padrão**.
3. Teste a conexão.
4. Desative a antiga.
5. Confirme em `Usos de IA` que a nova configuração está sendo usada.

## Controle financeiro

- `UsoIA` registra tokens e duração por finalidade.
- O controle de **cobrança de clientes** é separado: `PesquisaAnonima`/limites do
  site + ledger de créditos da exportação.
- A arquitetura de API externa de produto (API key, ledger, outbox) está em
  [`ARQUITETURA_API_CHAT_E_FINANCEIRO.md`](ARQUITETURA_API_CHAT_E_FINANCEIRO.md).
  Ela **não** é a credencial do DeepSeek; são coisas diferentes.

## Segurança

- Apenas superusuários devem gerenciar `ConfiguracaoIA`.
- Nunca copie a chave para documento, issue ou chat.
- A chave fica em `api_key_encrypted`; não a coloque em `UsoIA`, logs ou
  mensagens de erro.
