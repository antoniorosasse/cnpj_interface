# /home/neto/cnpj_interface/atualizar_dt_ini_csv.py
"""
Script para atualizar dt_ini usando CSV + COPY (MUITO mais rápido)

Estratégia:
1. Extrai do Parquet apenas cnpj_basico, cnpj_ordem, cnpj_dv, dt_ini
2. Gera um arquivo CSV
3. Carrega no PostgreSQL via COPY (rápido)
4. Atualiza a tabela principal via JOIN (rápido)
5. Dropa a tabela temporária

Uso:
    cd /home/neto/cnpj_interface
    source venv/bin/activate
    python atualizar_dt_ini_csv.py
"""

import pandas as pd
import psycopg2
import time
import os
import glob
from datetime import datetime, timedelta
import pyarrow.parquet as pq
import csv

# ============================================================
# CONFIGURAÇÕES
# ============================================================
PARQUET_DIR = "/home/neto/DataSetsTeste/base_cnpj"  # ← AJUSTE AQUI!
PARQUET_PATTERN = "Estabelecimentos*.parquet"
CSV_TEMP_DIR = "/home/neto/DataSetsTeste/base_cnpj"  # ← Pasta para CSVs temporários
CHUNK_SIZE = 500000  # 500 mil registros por CSV

DB_CONFIG = {
    'dbname': 'bancocnpj',
    'user': 'arosasse',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': '5432'
}


# ============================================================
# FUNÇÕES
# ============================================================
def formatar_tempo(segundos):
    return str(timedelta(seconds=int(segundos)))


def conectar_postgresql():
    return psycopg2.connect(
        dbname=DB_CONFIG['dbname'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        host=DB_CONFIG['host'],
        port=DB_CONFIG['port']
    )


def extrair_parquet_para_csv(arquivo, csv_path):
    """
    Extrai do Parquet apenas as colunas necessárias e gera um CSV
    """
    print(f"   📄 Lendo Parquet: {os.path.basename(arquivo)}")
    
    parquet_file = pq.ParquetFile(arquivo)
    total_registros = parquet_file.metadata.num_rows
    
    registros_escritos = 0
    inicio = time.time()
    
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')
        
        for batch in parquet_file.iter_batches(batch_size=CHUNK_SIZE):
            df = batch.to_pandas()
            
            # Normalizar colunas (MAIÚSCULAS → minúsculas)
            df.columns = [str(c).lower().strip() for c in df.columns]
            
            # Verificar colunas necessárias
            if 'dt_ini' not in df.columns:
                continue
            
            # Filtrar apenas não baixadas
            if 'cd_sit_cadastro' in df.columns:
                df = df[df['cd_sit_cadastro'] != 8]
            
            # Filtrar apenas com dt_ini preenchida
            df = df[df['dt_ini'].notna()]
            
            if len(df) == 0:
                continue
            
            # Escrever no CSV
            for _, row in df.iterrows():
                try:
                    dt_ini = pd.to_datetime(row['dt_ini']).strftime('%Y-%m-%d')
                    cnpj_basico = str(row['cnpj_basico']).strip().zfill(8)
                    cnpj_ordem = str(row['cnpj_ordem']).strip().zfill(4)
                    cnpj_dv = str(row['cnpj_dv']).strip().zfill(2)
                    
                    writer.writerow([cnpj_basico, cnpj_ordem, cnpj_dv, dt_ini])
                    registros_escritos += 1
                except:
                    continue
            
            # Progresso
            tempo_decorrido = time.time() - inicio
            velocidade = registros_escritos / tempo_decorrido if tempo_decorrido > 0 else 0
            print(f"      📊 {registros_escritos:,} registros | "
                  f"⏱️  {formatar_tempo(tempo_decorrido)} | "
                  f"🚀 {velocidade:,.0f} reg/s")
    
    print(f"   ✅ CSV gerado: {csv_path}")
    print(f"   📊 Total de registros: {registros_escritos:,}")
    print(f"   📁 Tamanho do CSV: {os.path.getsize(csv_path) / (1024*1024):.1f} MB")
    
    return registros_escritos


def carregar_csv_no_postgres(csv_path, cur, conn):
    """
    Carrega o CSV em uma tabela temporária via COPY (RÁPIDO)
    """
    print(f"   📤 Carregando CSV no PostgreSQL...")
    
    # Criar tabela temporária
    cur.execute("""
        CREATE TEMP TABLE temp_dt_ini (
            cnpj_basico varchar(8),
            cnpj_ordem varchar(4),
            cnpj_dv varchar(2),
            dt_ini date
        ) ON COMMIT DROP
    """)
    
    # Carregar via COPY (MUITO RÁPIDO)
    with open(csv_path, 'r', encoding='utf-8') as f:
        cur.copy_from(
            f,
            'temp_dt_ini',
            sep=';',
            columns=('cnpj_basico', 'cnpj_ordem', 'cnpj_dv', 'dt_ini')
        )
    conn.commit()
    
    # Criar índice na tabela temporária (para o JOIN ser rápido)
    cur.execute("""
        CREATE INDEX idx_temp_dt_ini 
        ON temp_dt_ini (cnpj_basico, cnpj_ordem, cnpj_dv)
    """)
    conn.commit()
    
    # Contar registros
    cur.execute("SELECT COUNT(*) FROM temp_dt_ini")
    total = cur.fetchone()
    print(f"   ✅ {total[0]:,} registros carregados na tabela temporária")
    
    return total[0] if total else 0


def atualizar_tabela_principal(cur, conn):
    """
    Atualiza a tabela principal via JOIN (RÁPIDO)
    """
    print(f"   🔄 Atualizando c_estabelecimentos...")
    
    inicio = time.time()
    
    cur.execute("""
        UPDATE c_estabelecimentos e
        SET dt_ini = t.dt_ini
        FROM temp_dt_ini t
        WHERE e.cnpj_basico = t.cnpj_basico
        AND e.cnpj_ordem = t.cnpj_ordem
        AND e.cnpj_dv = t.cnpj_dv
        AND e.dt_ini IS NULL  -- ← Só atualiza os que estão NULL
    """)
    
    atualizados = cur.rowcount
    conn.commit()
    
    tempo = time.time() - inicio
    print(f"   ✅ {atualizados:,} registros atualizados em {formatar_tempo(tempo)}")
    
    return atualizados


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print("🚀 ATUALIZANDO dt_ini VIA CSV + COPY (RÁPIDO)")
    print("=" * 70)
    print(f"📁 Pasta: {PARQUET_DIR}")
    print(f"⏰ Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print()
    
    # Criar pasta temporária
    os.makedirs(CSV_TEMP_DIR, exist_ok=True)
    
    # Listar arquivos
    padrao = os.path.join(PARQUET_DIR, PARQUET_PATTERN)
    arquivos = sorted(glob.glob(padrao))
    
    if not arquivos:
        print(f"❌ Nenhum arquivo encontrado em: {PARQUET_DIR}")
        return
    
    print(f"📄 Encontrados {len(arquivos)} arquivos")
    print()
    
    # Conectar ao PostgreSQL
    conn = conectar_postgresql()
    cur = conn.cursor()
    print("✅ Conectado ao PostgreSQL")
    
    # Estado inicial
    cur.execute("""
        SELECT 
            COUNT(*) AS total,
            COUNT(dt_ini) AS com_data,
            COUNT(*) - COUNT(dt_ini) AS sem_data
        FROM c_estabelecimentos
        WHERE cd_matriz_filial = 1
        AND cd_sit_cadastro <> 8
    """)
    inicial = cur.fetchone() or (0, 0, 0)
    print(f"\n📊 ESTADO INICIAL:")
    print(f"   Total: {inicial[0]:,}")
    print(f"   Com dt_ini: {inicial[1]:,}")
    print(f"   Sem dt_ini: {inicial[2]:,}")
    
    inicio_total = time.time()
    total_atualizado = 0
    
    # Processar cada arquivo
    for i, arquivo in enumerate(arquivos, 1):
        print(f"\n{'#' * 70}")
        print(f"# ARQUIVO {i}/{len(arquivos)}: {os.path.basename(arquivo)}")
        print(f"{'#' * 70}")
        
        # Gerar CSV
        csv_path = os.path.join(CSV_TEMP_DIR, f"dt_ini_{i}.csv")
        registros_csv = extrair_parquet_para_csv(arquivo, csv_path)
        
        if registros_csv == 0:
            print(f"   ⚠️  Nenhum registro para atualizar")
            os.remove(csv_path)
            continue
        
        # Carregar CSV no PostgreSQL
        carregar_csv_no_postgres(csv_path, cur, conn)
        
        # Atualizar tabela principal
        atualizados = atualizar_tabela_principal(cur, conn)
        total_atualizado += atualizados
        
        # Limpar CSV
        os.remove(csv_path)
        print(f"   🗑️  CSV removido: {csv_path}")
    
    # Estado final
    print("\n" + "=" * 70)
    print("📊 ESTADO FINAL:")
    cur.execute("""
        SELECT 
            COUNT(*) AS total,
            COUNT(dt_ini) AS com_data,
            COUNT(*) - COUNT(dt_ini) AS sem_data
        FROM c_estabelecimentos
        WHERE cd_matriz_filial = 1
        AND cd_sit_cadastro <> 8
    """)
    final = cur.fetchone() or (0, 0, 0)
    print(f"   Total: {final[0]:,}")
    print(f"   Com dt_ini: {final[1]:,}")
    print(f"   Sem dt_ini: {final[2]:,}")
    print(f"   Diferença: {final[1] - inicial[1]:,} registros atualizados")
    
    # Fechar
    cur.close()
    conn.close()
    
    tempo_total = time.time() - inicio_total
    print("\n" + "=" * 70)
    print(f"✅ CONCLUÍDO!")
    print(f"⏱️  Tempo total: {formatar_tempo(tempo_total)}")
    print(f"📊 Total atualizado: {total_atualizado:,}")
    print("=" * 70)


if __name__ == "__main__":
    main()