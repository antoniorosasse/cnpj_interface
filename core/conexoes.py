# core/conexoes.py
"""Conexao com o MongoDB usada pelo chat (chat/mongo_models.py).

As classes ConexaoAS400 e ConexaoPostgreSQL sairam daqui: nenhuma tela
usa o AS/400 e o Postgres e acessado pelo ORM (settings.DATABASES) ou por
core/consultas_empresa.py. Com elas saiu o JPype1 do requirements.txt.
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# ==================== IMPORTAÇÕES CONDICIONAIS ====================


# PyMongo - para MongoDB
try:
    from pymongo import MongoClient
    PYMONGO_DISPONIVEL = True
except ImportError:
    PYMONGO_DISPONIVEL = False
    print("⚠️  PyMongo não instalado. Conexão com MongoDB indisponível.")
    print("   Execute: pip install pymongo")



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
