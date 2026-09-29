# core/conexoes.py
import logging
import os
import sys
from django.conf import settings

logger = logging.getLogger(__name__)

# ==================== IMPORTAÇÕES CONDICIONAIS ====================
# JPype - para conexão com AS/400
try:
    import jpype
    import jpype.imports
    JPYPE_DISPONIVEL = True
except ImportError:
    JPYPE_DISPONIVEL = False
    print("⚠️  JPype não instalado. Conexão com AS/400 indisponível.")
    print("   Execute: pip install JPype1")

# Psycopg2 - para PostgreSQL
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_DISPONIVEL = True
except ImportError:
    PSYCOPG2_DISPONIVEL = False
    print("⚠️  Psycopg2 não instalado. Conexão com PostgreSQL indisponível.")
    print("   Execute: pip install psycopg2-binary")

# PyMongo - para MongoDB
try:
    from pymongo import MongoClient
    PYMONGO_DISPONIVEL = True
except ImportError:
    PYMONGO_DISPONIVEL = False
    print("⚠️  PyMongo não instalado. Conexão com MongoDB indisponível.")
    print("   Execute: pip install pymongo")


# ==================== CLASSE AS/400 ====================
class ConexaoAS400:
    """Gerenciador de conexão com AS/400 (DB2) via JDBC"""
    
    _instance = None
    _jvm_iniciada = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not JPYPE_DISPONIVEL:
            raise Exception("JPype não está disponível. Instale com: pip install JPype1")
    
    def conectar(self):
        """Retorna uma conexão JDBC com o AS/400"""
        if not self._jvm_iniciada:
            self._iniciar_jvm()
        
        config = settings.AS400_CONFIG
        url = f"jdbc:as400://{config['HOST']};libraries={config['LIBRARY']};naming=system;errors=full"
        
        try:
            # Importa dinamicamente os módulos Java
            DriverManager = jpype.JPackage('java.sql').DriverManager  # type: ignore
            conn = DriverManager.getConnection(
                url,
                config['USER'],
                config['PASSWORD']
            )
            return conn
        except Exception as e:
            raise Exception(f"Erro ao conectar ao AS/400: {str(e)}")
    
    def _iniciar_jvm(self):
        """Inicia a JVM com o driver JDBC"""
        try:
            # Verifica se o arquivo JAR existe
            jar_path = settings.AS400_CONFIG['JDBC_JAR']
            if not os.path.exists(jar_path):
                raise Exception(f"Arquivo JAR não encontrado: {jar_path}")
            
            # Verifica se a JVM já está rodando
            if jpype.isJVMStarted():
                logger.debug("JVM ja esta rodando")
                self._jvm_iniciada = True
                return
            
            # Inicia a JVM
            jpype.startJVM(
                jpype.getDefaultJVMPath(),
                "-Djava.class.path=" + jar_path,
                "-Djava.security.manager=allow",
                convertStrings=True
            )
            self._jvm_iniciada = True
            logger.debug("JVM iniciada com sucesso")
        except Exception as e:
            raise Exception(f"Erro ao iniciar JVM: {str(e)}")
    
    def executar_consulta(self, sql, params=None, limite=500):
        """Executa uma consulta SQL no AS/400"""
        conn = None
        try:
            conn = self.conectar()
            
            # Adiciona limite se não tiver
            if 'limit' not in sql.lower() and limite:
                sql = f"{sql} FETCH FIRST {limite} ROWS ONLY"
            
            # Importa classes Java
            DriverManager = jpype.JPackage('java.sql').DriverManager  # type: ignore
            
            # Executa consulta
            if params:
                PreparedStatement = jpype.JClass('java.sql.PreparedStatement')  # type: ignore
                pstmt = conn.prepareStatement(sql)
                for i, param in enumerate(params, 1):
                    pstmt.setObject(i, param)
                rs = pstmt.executeQuery()
                stmt = pstmt
            else:
                stmt = conn.createStatement()
                rs = stmt.executeQuery(sql)
            
            # Converte resultados
            ResultSetMetaData = jpype.JClass('java.sql.ResultSetMetaData')  # type: ignore
            meta = rs.getMetaData()
            colunas = [meta.getColumnName(i + 1).lower() for i in range(meta.getColumnCount())]
            
            resultados = []
            while rs.next():
                linha = {}
                for coluna in colunas:
                    try:
                        valor = rs.getString(coluna.upper())
                        if valor:
                            linha[coluna] = valor.strip()
                        else:
                            linha[coluna] = None
                    except:
                        linha[coluna] = None
                resultados.append(linha)
            
            # Fecha recursos
            rs.close()
            stmt.close()
            conn.close()
            
            return resultados
            
        except Exception as e:
            if conn:
                try:
                    conn.close()
                except:
                    pass
            raise Exception(f"Erro na consulta AS/400: {str(e)}")
    
    def executar_sql(self, sql, params=None, limite=500):
        """Alias para executar_consulta (mantido para compatibilidade)"""
        return self.executar_consulta(sql, params, limite)
    
    def testar_conexao(self):
        """Testa a conexão com o AS/400"""
        try:
            conn = self.conectar()
            # Tenta executar uma consulta simples
            stmt = conn.createStatement()
            rs = stmt.executeQuery("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            rs.close()
            stmt.close()
            conn.close()
            return True, "Conexão com AS/400 OK!"
        except Exception as e:
            return False, f"Erro na conexão AS/400: {str(e)}"
    
    def fechar(self):
        """Fecha a conexão e a JVM"""
        try:
            if self._jvm_iniciada and jpype.isJVMStarted():
                jpype.shutdownJVM()
                self._jvm_iniciada = False
                logger.debug("JVM finalizada com sucesso")
        except:
            pass


# ==================== CLASSE POSTGRESQL ====================
class ConexaoPostgreSQL:
    """Gerenciador de conexão com PostgreSQL"""
    
    def __init__(self):
        if not PSYCOPG2_DISPONIVEL:
            raise Exception("Psycopg2 não está disponível. Instale com: pip install psycopg2-binary")
    
    def conectar(self):
        """Retorna uma conexão com PostgreSQL"""
        config = settings.PG_CONFIG
        try:
            return psycopg2.connect(
                host=config['HOST'],
                port=config['PORT'],
                dbname=config['DATABASE'],
                user=config['USER'],
                password=config['PASSWORD']
            )
        except Exception as e:
            raise Exception(f"Erro ao conectar ao PostgreSQL: {str(e)}")
    
    def executar_consulta(self, sql, params=None):
        """Executa uma consulta SQL no PostgreSQL"""
        conn = None
        try:
            conn = self.conectar()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params or [])
                return cur.fetchall()
        except Exception as e:
            raise Exception(f"Erro na consulta PostgreSQL: {str(e)}")
        finally:
            if conn:
                conn.close()
    
    def executar_many(self, sql, params_list):
        """Executa múltiplas inserções no PostgreSQL"""
        conn = None
        try:
            conn = self.conectar()
            with conn.cursor() as cur:
                cur.executemany(sql, params_list)
                conn.commit()
                return cur.rowcount
        except Exception as e:
            raise Exception(f"Erro na inserção PostgreSQL: {str(e)}")
        finally:
            if conn:
                conn.close()
    
    def executar_sql(self, sql, params=None):
        """Alias para executar_consulta (mantido para compatibilidade)"""
        return self.executar_consulta(sql, params)
    
    def testar_conexao(self):
        """Testa a conexão com o PostgreSQL"""
        try:
            conn = self.conectar()
            conn.close()
            return True, "Conexão com PostgreSQL OK!"
        except Exception as e:
            return False, f"Erro na conexão PostgreSQL: {str(e)}"


# ==================== CLASSE MONGODB ====================
# core/conexoes.py - Classe MongoDB corrigida

import urllib.parse  # NOVA IMPORTACAO

class ConexaoMongoDB:
    """Gerenciador de conexão com MongoDB"""
    
    _client = None
    
    def __init__(self):
        if not PYMONGO_DISPONIVEL:
            raise Exception("PyMongo não está disponível. Instale com: pip install pymongo")
    
    def conectar(self):
        """Retorna uma conexão com MongoDB"""
        if self._client is None:
            try:
                config = settings.MONGO_CONFIG
                
                # ESCAPAR USERNAME E PASSWORD (RFC 3986)
                if config.get('USER') and config.get('PASSWORD'):
                    username_escaped = urllib.parse.quote_plus(config['USER'])
                    password_escaped = urllib.parse.quote_plus(config['PASSWORD'])
                    uri = f"mongodb://{username_escaped}:{password_escaped}@{config['HOST']}:{config['PORT']}/"
                else:
                    uri = f"mongodb://{config['HOST']}:{config['PORT']}/"
                
                # Adiciona o database name
                uri = uri + config['DATABASE']
                
                logger.debug("Conectando ao MongoDB: %s:%s/%s", config['HOST'], config['PORT'], config['DATABASE'])
                
                self._client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
                # Testa a conexão
                self._client.admin.command('ping')
                logger.debug("Conexao com MongoDB estabelecida")
            except Exception as e:
                raise Exception(f"Erro ao conectar ao MongoDB: {str(e)}")
        return self._client
    
    def get_database(self):
        """Retorna o banco de dados MongoDB"""
        client = self.conectar()
        return client[settings.MONGO_CONFIG['DATABASE']]
    
    def executar_consulta(self, colecao, filtro=None, projecao=None, limite=500):
        """Executa uma consulta no MongoDB"""
        try:
            db = self.get_database()
            colecao_obj = db[colecao]
            filtro = filtro or {}
            projecao = projecao or {}
            return list(colecao_obj.find(filtro, projecao).limit(limite))
        except Exception as e:
            raise Exception(f"Erro na consulta MongoDB: {str(e)}")
    
    def inserir(self, colecao, dados):
        """Insere dados no MongoDB"""
        try:
            db = self.get_database()
            colecao_obj = db[colecao]
            if isinstance(dados, list):
                return colecao_obj.insert_many(dados)
            return colecao_obj.insert_one(dados)
        except Exception as e:
            raise Exception(f"Erro na inserção MongoDB: {str(e)}")
    
    def executar_sql(self, colecao, filtro=None, projecao=None, limite=500):
        """Alias para executar_consulta (mantido para compatibilidade)"""
        return self.executar_consulta(colecao, filtro, projecao, limite)
    
    def testar_conexao(self):
        """Testa a conexão com o MongoDB"""
        try:
            self.conectar()
            return True, "Conexão com MongoDB OK!"
        except Exception as e:
            return False, f"Erro na conexão MongoDB: {str(e)}"
    
    def fechar(self):
        """Fecha a conexão com MongoDB"""
        if self._client:
            self._client.close()
            self._client = None
            logger.debug("Conexao MongoDB fechada")


# ==================== FUNÇÕES AUXILIARES ====================
def testar_todas_conexoes():
    """Testa todas as conexões configuradas"""
    resultados = {}
    
    # Testa AS/400
    try:
        as400 = ConexaoAS400()
        ok, msg = as400.testar_conexao()
        resultados['AS/400'] = {'ok': ok, 'msg': msg}
    except Exception as e:
        resultados['AS/400'] = {'ok': False, 'msg': str(e)}
    
    # Testa PostgreSQL
    try:
        pg = ConexaoPostgreSQL()
        ok, msg = pg.testar_conexao()
        resultados['PostgreSQL'] = {'ok': ok, 'msg': msg}
    except Exception as e:
        resultados['PostgreSQL'] = {'ok': False, 'msg': str(e)}
    
    # Testa MongoDB
    try:
        mongo = ConexaoMongoDB()
        ok, msg = mongo.testar_conexao()
        resultados['MongoDB'] = {'ok': ok, 'msg': msg}
    except Exception as e:
        resultados['MongoDB'] = {'ok': False, 'msg': str(e)}
    
    return resultados