# testar_simple.py
import os
import sys
from pathlib import Path

# Configura o Django
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cnpj_project.settings')

import django
django.setup()

from core.conexoes import testar_todas_conexoes

if __name__ == "__main__":
    print("\n" + "="*50)
    print("🧪 TESTANDO CONEXÕES")
    print("="*50 + "\n")
    
    resultados = testar_todas_conexoes()
    
    for banco, info in resultados.items():
        status = "✅ OK" if info['ok'] else "❌ FALHA"
        print(f"{banco}: {status}")
        print(f"   {info['msg']}")
        print()