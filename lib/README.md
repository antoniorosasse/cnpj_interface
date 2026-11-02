# lib/

Esta pasta guarda o driver JDBC do IBM i (AS/400).

## jt400.jar

O arquivo `jt400.jar` **não está versionado** neste repositório
(ele tem ~6 MB e é propriedade da IBM).

Para baixar:

1. Acesse https://jt400.sourceforge.net/
2. Baixe o arquivo `jt400.jar` (versão mais recente)
3. Coloque exatamente nesta pasta: `lib/jt400.jar`

O caminho absoluto precisa estar no `.env`:

    AS400_JDBC_JAR=/caminho/absoluto/para/lib/jt400.jar
