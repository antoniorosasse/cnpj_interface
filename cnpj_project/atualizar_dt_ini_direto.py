# /home/neto/cnpj_interface/atualizar_dt_ini_direto.py
"""
Script OTIMIZADO para atualizar dt_ini:
- Lê Parquet
- Filtra e processa
- Carrega DIRETO no PostgreSQL via COPY (sem CSV em disco)
- Atualiza tabela principal via JOIN

Uso:
    cd /home/neto/cnpj_interface
    source venv/bin/activate
    python atualizar_dt_ini_direto.py
"""

import pandas as pd
import psycopg2
import time
import os
import glob
from datetime import datetime, timedelta
import pyarrow.parquet as pq
import io
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIGURAÇÕES
# ============================================================
PARQUET_DIR = "/home/neto/DataSetsTeste/base_cnpj"  # ← JÁ AJUSTADO!
PARQUET_PATTERN = "Estabelecimentos*.parquet"
CHUNK_SIZE = 500000  # 500 mil registros

DB_CONFIG = {
    'dbname': os.getenv('PG_DB', 'bancocnpj'),
    'user': os.getenv('PG_USER', 'arosasse'),
    'password': os.getenv('PG_PASSWORD'),
    'host': os.getenv('PG_HOST', 'localhost'),
    'port': os.getenv('PG_PORT', '5432'),
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


def processar_arquivo(arquivo, cur, conn):
    """
    Processa um arquivo Parquet e atualiza direto no PostgreSQL
    """
    print(f"\n{'=' * 70}")
    print(f"📄 PROCESSANDO: {os.path.basename(arquivo)}")
    print(f"{'=' * 70}")
    
    parquet_file = pq.ParquetFile(arquivo)
    total_registros = parquet_file.metadata.num_rows
    print(f"📊 Total de registros no arquivo: {total_registros:,}")
    
    inicio = time.time()
    total_processado = 0
    total_atualizado = 0
    chunk_count = 0
    
    # Criar tabela temporária UMA VEZ
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS temp_dt_ini (
            cnpj_basico varchar(8),
            cnpj_ordem varchar(4),
            cnpj_dv varchar(2),
            dt_ini date
        ) ON COMMIT PRESERVE ROWS
    """)
    conn.commit()
    
    for batch in parquet_file.iter_batches(batch_size=CHUNK_SIZE):
        chunk_count += 1
        df = batch.to_pandas()
        
        # Normalizar colunas
        df.columns = [str(c).lower().strip() for c in df.columns]
        
        # Verificar colunas
        colunas_necessarias = ['cnpj_basico', 'cnpj_ordem', 'cnpj_dv', 'dt_ini']
        colunas_faltando = [c for c in colunas_necessarias if c not in df.columns]
        
        if colunas_faltando:
            if chunk_count == 1:
                print(f"   ⚠️  Colunas faltando: {colunas_faltando}")
            continue
        
        # Filtrar não baixadas
        if 'cd_sit_cadastro' in df.columns:
            df = df[df['cd_sit_cadastro'] != 8]
        
        # Filtrar com dt_ini preenchida
        df = df[df['dt_ini'].notna()]
        
        if len(df) == 0:
            continue
        
        # Selecionar apenas colunas necessárias
        df = df[['cnpj_basico', 'cnpj_ordem', 'cnpj_dv', 'dt_ini']].copy()
        
        # ✅ LIMPAR DADOS (garantir formato correto)
        df['cnpj_basico'] = df['cnpj_basico'].astype(str).str.strip().str.zfill(8)
        df['cnpj_ordem'] = df['cnpj_ordem'].astype(str).str.strip().str.zfill(4)
        df['cnpj_dv'] = df['cnpj_dv'].astype(str).str.strip().str.zfill(2)
        df['dt_ini'] = pd.to_datetime(df['dt_ini']).dt.strftime('%Y-%m-%d')
        
        # ✅ LIMPAR TABELA TEMPORÁRIA (mais rápido que DELETE)
        cur.execute("TRUNCATE temp_dt_ini")
        
        # ✅ CARREGAR DIRETO VIA COPY (SEM CSV EM DISCO!)
        output = io.StringIO()
        df.to_csv(
            output,
            sep=';',
            header=False,
            index=False,
            encoding='utf-8'
        )
        output.seek(0)
        
        cur.copy_from(
            output,
            'temp_dt_ini',
            sep=';',
            columns=('cnpj_basico', 'cnpj_ordem', 'cnpj_dv', 'dt_ini')
        )
        conn.commit()
        
        # ✅ ATUALIZAR TABELA PRINCIPAL (via JOIN)
        cur.execute("""
            UPDATE c_estabelecimentos e
            SET dt_ini = t.dt_ini
            FROM temp_dt_ini t
            WHERE e.cnpj_basico = t.cnpj_basico
            AND e.cnpj_ordem = t.cnpj_ordem
            AND e.cnpj_dv = t.cnpj_dv
            AND e.dt_ini IS NULL
        """)
        atualizados = cur.rowcount
        conn.commit()
        
        total_processado += len(df)
        total_atualizado += atualizados
        
        # Progresso
        tempo_decorrido = time.time() - inicio
        velocidade = total_processado / tempo_decorrido if tempo_decorrido > 0 else 0
        tempo_restante = (total_registros - total_processado) / velocidade if velocidade > 0 else 0
        
        pct = (total_processado / total_registros) * 100
        print(f"   📊 {total_processado:,}/{total_registros:,} ({pct:.1f}%) | "
              f"⏱️  {formatar_tempo(tempo_decorrido)} | "
              f"🚀 {velocidade:,.0f} reg/s | "
              f"✅ {total_atualizado:,} atualizados | "
              f"⏳ restante: {formatar_tempo(tempo_restante)}")
    
    # Dropar tabela temporária
    cur.execute("DROP TABLE IF EXISTS temp_dt_ini")
    conn.commit()
    
    tempo_arquivo = time.time() - inicio
    print(f"\n   ✅ ARQUIVO CONCLUÍDO!")
    print(f"   📊 Processados: {total_processado:,}")
    print(f"   📊 Atualizados: {total_atualizado:,}")
    print(f"   ⏱️  Tempo: {formatar_tempo(tempo_arquivo)}")
    
    return total_processado, total_atualizado


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print("🚀 ATUALIZANDO dt_ini - VERSÃO OTIMIZADA (SEM CSV)")
    print("=" * 70)
    print(f"📁 Pasta: {PARQUET_DIR}")
    print(f"⏰ Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print()
    
    padrao = os.path.join(PARQUET_DIR, PARQUET_PATTERN)
    arquivos = sorted(glob.glob(padrao))
    
    if not arquivos:
        print(f"❌ Nenhum arquivo encontrado em: {PARQUET_DIR}")
        return
    
    print(f"📄 Encontrados {len(arquivos)} arquivos:")
    for i, arq in enumerate(arquivos, 1):
        tamanho_mb = os.path.getsize(arq) / (1024 * 1024)
        print(f"   {i}. {os.path.basename(arq)} ({tamanho_mb:.1f} MB)")
    print()
    
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
    total_processado_geral = 0
    total_atualizado_geral = 0
    
    for i, arquivo in enumerate(arquivos, 1):
        print(f"\n{'#' * 70}")
        print(f"# ARQUIVO {i}/{len(arquivos)}")
        print(f"{'#' * 70}")
        
        processado, atualizado = processar_arquivo(arquivo, cur, conn)
        total_processado_geral += processado
        total_atualizado_geral += atualizado
        
        tempo_decorrido = time.time() - inicio_total
        print(f"\n📊 PROGRESSO GLOBAL:")
        print(f"   Arquivos: {i}/{len(arquivos)}")
        print(f"   Processados: {total_processado_geral:,}")
        print(f"   Atualizados: {total_atualizado_geral:,}")
        print(f"   Tempo: {formatar_tempo(tempo_decorrido)}")
    
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
    
    cur.close()
    conn.close()
    
    tempo_total = time.time() - inicio_total
    print("\n" + "=" * 70)
    print(f"✅ CONCLUÍDO!")
    print(f"⏱️  Tempo total: {formatar_tempo(tempo_total)}")
    print(f"📊 Total processado: {total_processado_geral:,}")
    print(f"📊 Total atualizado: {total_atualizado_geral:,}")
    print("=" * 70)


if __name__ == "__main__":
    main()