# /home/neto/ProjetosPython/cnpj_interface/core/tests_estatisticas_pdf.py
from datetime import datetime

from django.test import SimpleTestCase

from core.estatisticas_pdf import gerar_pdf_estatisticas


class PdfEstatisticasTests(SimpleTestCase):
    def _dados(self):
        return {
            'totais': {
                'total_matrizes': 100, 'total_ativas': 60,
                'total_inaptas': 10, 'total_baixadas': 20,
                'total_suspensas': 5, 'total_nulas': 5, 'total_ufs': 1,
            },
            'situacoes': [
                {'situacao': 'ATIVA', 'rotulo': 'Ativa', 'total': 60},
                {'situacao': 'BAIXADA', 'rotulo': 'Baixada', 'total': 20},
            ],
            'portes': [
                {'porte': 'MICRO EMPRESA', 'total': 50},
                {'porte': 'DEMAIS', 'total': 10},
            ],
            'top_cnaes': [
                {'cnae_principal': '4711302', 'nm_cnae': 'Supermercados',
                 'total': 30},
            ],
            'top_municipios': [
                {'nome_municipio': 'Joinville', 'uf': 'SC', 'total': 80},
            ],
            'por_ano': [{'ano': 2025, 'total': 40}],
        }

    def test_gera_pdf_com_filtro(self):
        pdf = gerar_pdf_estatisticas(
            self._dados(), resumo='UF: SC | Cidade: Joinville',
            agora=datetime(2026, 11, 28, 12, 0))
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1000)

    def test_gera_pdf_sem_filtro(self):
        pdf = gerar_pdf_estatisticas(self._dados())
        self.assertTrue(pdf.startswith(b'%PDF'))
