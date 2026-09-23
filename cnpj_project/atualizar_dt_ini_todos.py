# /home/neto/cnpj_interface/atualizar_dt_ini_todos.py
"""
Script para atualizar a coluna dt_ini da tabela c_estabelecimentos
a partir de MÚLTIPLOS arquivos Parquet (Estabelecimentos0.parquet, ...)

Uso:
    cd /home/neto/cnpj_interface
    source venv/bin/activate
    python atualizar_dt_ini_todos.py
"""

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import time
import os
import glob
from datetime import datetime, timedelta
import pyarrow.parquet as pq
from typing import Dict, Any

# ============================================================
# CONFIGURAÇÕES
# ============================================================
PARQUET_DIR = "/home/neto/DataSetsTeste/base_cnpj"  # ← AJUSTE AQUI!
PARQUET_PATTERN = "Estabelecimentos*.parquet"

CHUNK_SIZE = 100000
BATCH_SIZE = 10000

DB_CONFIG: Dict[str, Any] = {
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
    """Formata segundos em HH:MM:SS"""
    return str(timedelta(seconds=int(segundos)))


def normalizar_colunas(df):
    """
    Normaliza os nomes das colunas:
    - Converte para minúsculas
    - Remove espaços
    - Mapeia nomes alternativos
    """
    # Converter para minúsculas e remover espaços
    df.columns = [str(c).lower().strip() for c in df.columns]
    
    # Mapear nomes alternativos (se necessário)
    mapeamento = {
        'cnpj': 'cnpj_basico',
        'basico': 'cnpj_basico',
        'ordem': 'cnpj_ordem',
        'dv': 'cnpj_dv',
        'digito_verificador': 'cnpj_dv',
        'data_inicio': 'dt_ini',
        'data_inicio_atividade': 'dt_ini',
        'data_abertura': 'dt_ini',
        'situacao_cadastral': 'cd_sit_cadastro',
        'situacao': 'cd_sit_cadastro',
        'matriz_filial': 'cd_matriz_filial',
    }
    
    df = df.rename(columns=mapeamento)
    
    return df


def listar_arquivos():
    """Lista todos os arquivos Parquet"""
    padrao = os.path.join(PARQUET_DIR, PARQUET_PATTERN)
    arquivos = sorted(glob.glob(padrao))
    return arquivos


def conectar_postgresql():
    """Conecta ao PostgreSQL"""
    return psycopg2.connect(
        dbname=DB_CONFIG['dbname'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        host=DB_CONFIG['host'],
        port=DB_CONFIG['port']
    )


def processar_arquivo(arquivo, cur, conn, stats):
    """Processa um único arquivo Parquet"""
    print(f"\n{'=' * 70}")
    print(f"📄 PROCESSANDO: {os.path.basename(arquivo)}")
    print(f"{'=' * 70}")
    
    try:
        parquet_file = pq.ParquetFile(arquivo)
        total_registros = parquet_file.metadata.num_rows
        print(f"📊 Total de registros no arquivo: {total_registros:,}")
        
        # Mostrar colunas na primeira vez
        print(f"📋 Colunas originais: {parquet_file.schema.names[:10]}...")
        
        inicio_arquivo = time.time()
        processado_arquivo = 0
        atualizado_arquivo = 0
        chunk_count = 0
        
        for batch in parquet_file.iter_batches(batch_size=CHUNK_SIZE):
            chunk_count += 1
            df = batch.to_pandas()
            
            # ✅ NORMALIZAR COLUNAS (MAIÚSCULAS → minúsculas)
            df = normalizar_colunas(df)
            
            # Verificar colunas necessárias
            colunas_necessarias = ['cnpj_basico', 'cnpj_ordem', 'cnpj_dv', 'dt_ini']
            colunas_faltando = [c for c in colunas_necessarias if c not in df.columns]
            
            if colunas_faltando:
                if chunk_count == 1:
                    print(f"   ⚠️  Colunas faltando: {colunas_faltando}")
                    print(f"   📋 Colunas disponíveis: {list(df.columns)}")
                continue
            
            # ✅ FILTRAR APENAS NÃO BAIXADAS
            if 'cd_sit_cadastro' in df.columns:
                df = df[df['cd_sit_cadastro'] != 8]
            
            # ✅ FILTRAR APENAS COM dt_ini PREENCHIDA
            df = df[df['dt_ini'].notna()]
            
            if len(df) == 0:
                continue
            
            # Preparar dados para UPDATE
            dados = []
            for _, row in df.iterrows():
                try:
                    dt_ini = pd.to_datetime(row['dt_ini']).date()
                    dados.append((
                        dt_ini,
                        str(row['cnpj_basico']).strip().zfill(8),
                        str(row['cnpj_ordem']).strip().zfill(4),
                        str(row['cnpj_dv']).strip().zfill(2)
                    ))
                except Exception as e:
                    continue
            
            # UPDATE em massa
            if dados:
                try:
                    execute_values(
                        cur,
                        """
                        UPDATE c_estabelecimentos 
                        SET dt_ini = data.dt_ini
                        FROM (VALUES %s) AS data(dt_ini, cnpj_basico, cnpj_ordem, cnpj_dv)
                        WHERE c_estabelecimentos.cnpj_basico = data.cnpj_basico
                        AND c_estabelecimentos.cnpj_ordem = data.cnpj_ordem
                        AND c_estabelecimentos.cnpj_dv = data.cnpj_dv
                        """,
                        dados,
                        page_size=BATCH_SIZE
                    )
                    conn.commit()
                    atualizado_arquivo += len(dados)
                except Exception as e:
                    conn.rollback()
                    print(f"   ❌ Erro no chunk {chunk_count}: {e}")
                    continue
            
            processado_arquivo += len(df)
            
            # Progresso
            tempo_decorrido = time.time() - inicio_arquivo
            velocidade = processado_arquivo / tempo_decorrido if tempo_decorrido > 0 else 0
            
            pct = (processado_arquivo / total_registros) * 100
            print(f"   📊 {processado_arquivo:,}/{total_registros:,} ({pct:.1f}%) | "
                  f"⏱️  {formatar_tempo(tempo_decorrido)} | "
                  f"🚀 {velocidade:,.0f} reg/s | "
                  f"✅ {atualizado_arquivo:,} atualizados")
        
        tempo_arquivo = time.time() - inicio_arquivo
        print(f"\n   ✅ ARQUIVO CONCLUÍDO!")
        print(f"   📊 Processados: {processado_arquivo:,}")
        print(f"   📊 Atualizados: {atualizado_arquivo:,}")
        print(f"   ⏱️  Tempo: {formatar_tempo(tempo_arquivo)}")
        
        stats['total_processado'] += processado_arquivo
        stats['total_atualizado'] += atualizado_arquivo
        stats['arquivos_processados'] += 1
        
    except Exception as e:
        print(f"   ❌ ERRO no arquivo {os.path.basename(arquivo)}: {e}")
        import traceback
        traceback.print_exc()
        stats['erros'] += 1


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print("🚀 ATUALIZANDO dt_ini - MÚLTIPLOS ARQUIVOS PARQUET")
    print("   (IGNORANDO EMPRESAS BAIXADAS - cd_sit_cadastro = 8)")
    print("=" * 70)
    print(f"📁 Pasta: {PARQUET_DIR}")
    print(f"📄 Padrão: {PARQUET_PATTERN}")
    print(f"⏰ Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print()

    arquivos = listar_arquivos()
    
    if not arquivos:
        print(f"❌ Nenhum arquivo encontrado em: {PARQUET_DIR}")
        return
    
    print(f"📄 Encontrados {len(arquivos)} arquivos:")
    for i, arq in enumerate(arquivos, 1):
        tamanho_mb = os.path.getsize(arq) / (1024 * 1024)
        print(f"   {i}. {os.path.basename(arq)} ({tamanho_mb:.1f} MB)")
    print()

    try:
        conn = conectar_postgresql()
        cur = conn.cursor()
        print("✅ Conectado ao PostgreSQL")
    except Exception as e:
        print(f"❌ Erro ao conectar: {e}")
        return

    # Estado inicial
    print("\n📊 ESTADO INICIAL:")
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
    print(f"   Total (não baixadas): {inicial[0]:,}")
    print(f"   Com dt_ini: {inicial[1]:,}")
    print(f"   Sem dt_ini: {inicial[2]:,}")

    stats = {
        'total_processado': 0,
        'total_atualizado': 0,
        'arquivos_processados': 0,
        'erros': 0,
    }
    
    inicio_total = time.time()
    
    for i, arquivo in enumerate(arquivos, 1):
        print(f"\n{'#' * 70}")
        print(f"# ARQUIVO {i}/{len(arquivos)}")
        print(f"{'#' * 70}")
        
        processar_arquivo(arquivo, cur, conn, stats)
        
        tempo_decorrido = time.time() - inicio_total
        print(f"\n📊 PROGRESSO GLOBAL:")
        print(f"   Arquivos: {stats['arquivos_processados']}/{len(arquivos)}")
        print(f"   Processados: {stats['total_processado']:,}")
        print(f"   Atualizados: {stats['total_atualizado']:,}")
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
    print(f"   Total (não baixadas): {final[0]:,}")
    print(f"   Com dt_ini: {final[1]:,}")
    print(f"   Sem dt_ini: {final[2]:,}")
    print(f"   Diferença: {final[1] - inicial[1]:,} registros atualizados")

    cur.close()
    conn.close()
    
    tempo_total = time.time() - inicio_total
    print("\n" + "=" * 70)
    print(f"✅ CONCLUÍDO!")
    print(f"⏱️  Tempo total: {formatar_tempo(tempo_total)}")
    print(f"📄 Arquivos processados: {stats['arquivos_processados']}/{len(arquivos)}")
    print(f"📊 Total processado: {stats['total_processado']:,}")
    print(f"📊 Total atualizado: {stats['total_atualizado']:,}")
    print(f"❌ Erros: {stats['erros']}")
    print("=" * 70)


if __name__ == "__main__":
    main()