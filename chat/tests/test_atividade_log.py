# cnpj_interface/chat/tests/test_atividade_log.py
"""Testes da telemetria de atividade e da fila de candidatos (Fase 1)."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from chat.atividade_log import normalizar_termo, registrar_atividade
from chat.models import AtividadeLog, CnaeAliasCandidato


class NormalizarTermoTests(TestCase):
    def test_acentos_e_pontuacao(self):
        self.assertEqual(normalizar_termo('  Piçarras, SC! '), 'picarras sc')
        self.assertEqual(normalizar_termo(''), '')


class RegistrarAtividadeTests(TestCase):
    def test_grava_log_com_filtro(self):
        log = registrar_atividade(
            'postos de gasolina',
            {'fonte': 'iliKe', 'cnaes': [4731800], 'faixas': []},
            AtividadeLog.ORIGEM_CHAT, resultados=22,
        )
        self.assertIsNotNone(log)
        self.assertEqual(AtividadeLog.objects.count(), 1)
        self.assertEqual(log.termo_normalizado, 'postos de gasolina')
        self.assertEqual(log.cnaes, [4731800])
        self.assertEqual(log.fonte, 'iliKe')
        self.assertEqual(log.resultados, 22)

    def test_termo_vazio_nao_grava(self):
        self.assertIsNone(registrar_atividade('', {}, AtividadeLog.ORIGEM_CHAT))
        self.assertEqual(AtividadeLog.objects.count(), 0)

    def test_falha_de_banco_nao_quebra_chamador(self):
        # Simula uma falha no model sem depender do banco.
        with self.settings():  # apenas garante contexto
            from unittest.mock import patch
            with patch('chat.atividade_log.AtividadeLog.objects.create',
                       side_effect=RuntimeError('boom')):
                self.assertIsNone(registrar_atividade(
                    'x', {'fonte': 'kb'}, AtividadeLog.ORIGEM_CHAT))


class ProporAliasesCommandTests(TestCase):
    def _log(self, termo, cnaes, fonte='iliKe', resultados=10, dias=0):
        log = AtividadeLog.objects.create(
            termo=termo,
            termo_normalizado=normalizar_termo(termo),
            origem=AtividadeLog.ORIGEM_CHAT,
            fonte=fonte,
            cnaes=cnaes,
            resultados=resultados,
        )
        if dias:
            AtividadeLog.objects.filter(pk=log.pk).update(
                criado_em=timezone.now() - timedelta(days=dias))
        return log

    def test_gera_candidato_recorrente(self):
        for _ in range(3):
            self._log('pizzarias', [5620104, 5611201])
        call_command('propor_aliases_cnae', '--min-ocorrencias', '3',
                     stdout=StringIO())
        cand = CnaeAliasCandidato.objects.get()
        self.assertEqual(cand.termo_normalizado, 'pizzarias')
        self.assertEqual(cand.cnaes, [5611201, 5620104])
        self.assertEqual(cand.ocorrencias, 3)
        self.assertEqual(cand.status, CnaeAliasCandidato.STATUS_PENDENTE)

    def test_ignora_pouco_recorrente_e_sem_cnae(self):
        self._log('academias', [9313100])
        self._log('naves espaciais', [])
        self._log('naves espaciais', [])
        self._log('naves espaciais', [])
        call_command('propor_aliases_cnae', '--min-ocorrencias', '3',
                     stdout=StringIO())
        self.assertEqual(CnaeAliasCandidato.objects.count(), 0)

    def test_nao_sobrescreve_aprovado(self):
        for _ in range(3):
            self._log('pizzarias', [5620104])
        cand = CnaeAliasCandidato.objects.create(
            termo='pizzarias', termo_normalizado='pizzarias',
            cnaes=[5620104], ocorrencias=3,
            status=CnaeAliasCandidato.STATUS_APROVADO)
        call_command('propor_aliases_cnae', '--min-ocorrencias', '3',
                     stdout=StringIO())
        cand.refresh_from_db()
        self.assertEqual(cand.status, CnaeAliasCandidato.STATUS_APROVADO)

    def test_dry_run_nao_grava(self):
        for _ in range(3):
            self._log('pizzarias', [5620104])
        out = StringIO()
        call_command('propor_aliases_cnae', '--min-ocorrencias', '3',
                     '--dry-run', stdout=out)
        self.assertEqual(CnaeAliasCandidato.objects.count(), 0)
        self.assertIn('pizzarias', out.getvalue())
