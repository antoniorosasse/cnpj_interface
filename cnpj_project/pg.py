# cnpj_project/pg.py
"""Conexao direta com o bancocnpj para os scripts de manutencao.

Os tres `atualizar_dt_ini_*.py` tinham cada um a sua copia de `DB_CONFIG` e
de `conectar_postgresql()`. Agora a configuracao vem de um lugar so, lendo as
mesmas variaveis PG_* do .env usadas pelo settings.DATABASES.

Nao e usado pelo app em runtime: as views falam com o banco pelo ORM.
"""
import os

import psycopg2

DB_CONFIG = {
    'dbname': os.getenv('PG_DB', 'bancocnpj'),
    'user': os.getenv('PG_USER', 'arosasse'),
    'password': os.getenv('PG_PASSWORD'),
    'host': os.getenv('PG_HOST', 'localhost'),
    'port': os.getenv('PG_PORT', '5432'),
}


def conectar():
    """Abre uma conexao nova com o bancocnpj; feche com conn.close()."""
    return psycopg2.connect(**DB_CONFIG)
