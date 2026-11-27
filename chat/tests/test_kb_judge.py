# cnpj_interface/chat/tests/test_kb_judge.py
"""Testes da Fase 2: juiz de IA, publicacao versionada e alias no resolvedor."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from chat.models import (CnaeAlias, CnaeAliasCandidato, CnaeAliasHistorico)
from chat.services import kb_judge


def _candidato(**kwargs):
    base = dict(
        termo='pizzarias',
        termo_normalizado='pizzarias',
        cnaes=[5611201, 5620104],
        categorias=[],
        fontes={'iliKe': 5},
        ocorrencias=5,
        evidencia={'termos': {'pizzarias': 5}},
    )
    base.update(kwargs)
    return CnaeAliasCandidato.objects.create(**base)


class JuizTests(TestCase):
    @patch('chat.services.kb_judge._descricoes', return_value={})
    @patch('chat.services.llm_client.gerar_resposta',
           return_value='{"decisao":"adicionar","cnaes":[5620104],'
                        '"confianca":0.95,"justificativa":"aderente"}')
    def test_avalia_json_e_filtra_candidato_invalido(self, mock_gerar, mock_desc):
        cand = _candidato()
        resultado = kb_judge.avaliar_candidato(cand)
        self.assertEqual(resultado['decisao'], 'adicionar')
        self.assertEqual(resultado['cnaes'], [5620104])
        self.assertAlmostEqual(resultado['confianca'], 0.95)
        # O CNAE 999999 nao esta na lista fechada e precisa ser descartado.
        mock_gerar.return_value = ('{"decisao":"adicionar","cnaes":[999999],'
                                   '"confianca":0.9}')
        resultado = kb_judge.avaliar_candidato(cand)
        self.assertEqual(resultado['decisao'], 'revisar')
        self.assertEqual(resultado['cnaes'], [])

    @patch('chat.services.kb_judge._descricoes', return_value={})
    @patch('chat.services.llm_client.gerar_resposta',
           return_value='texto qualquer sem json')
    def test_resposta_invalida_vira_revisao(self, mock_gerar, mock_desc):
        resultado = kb_judge.avaliar_candidato(_candidato())
        self.assertEqual(resultado['decisao'], 'revisar')
        self.assertEqual(resultado['confianca'], 0.0)

    def test_autoaprovacao_exige_evidencia_e_recorrencia(self):
        cand = _candidato(decisao_ia='adicionar', confianca_ia=0.95,
                          cnaes_ia=[5620104])
        self.assertTrue(kb_judge.pode_autoaprovar(cand))
        cand.fontes = {'vetor': 5}
        cand.save(update_fields=['fontes'])
        self.assertFalse(kb_judge.pode_autoaprovar(cand))
        cand.fontes = {'iliKe': 5}
        cand.ocorrencias = 1
        cand.save(update_fields=['fontes', 'ocorrencias'])
        self.assertFalse(kb_judge.pode_autoaprovar(cand))


class PublicacaoAliasTests(TestCase):
    def test_publica_cria_alias_historico_e_aprova_candidato(self):
        cand = _candidato()
        alias = kb_judge.publicar_alias(cand)
        self.assertEqual(alias.versao, 1)
        self.assertEqual(alias.cnaes, [5611201, 5620104])
        self.assertTrue(alias.ativo)
        cand.refresh_from_db()
        self.assertEqual(cand.status, CnaeAliasCandidato.STATUS_APROVADO)
        self.assertEqual(CnaeAliasHistorico.objects.count(), 1)
        self.assertEqual(CnaeAliasHistorico.objects.get().acao, 'publicado')

    def test_publica_usa_cnaes_da_ia_quando_existirem(self):
        cand = _candidato(cnaes=[5611201, 5620104],
                          cnaes_ia=[5620104])
        alias = kb_judge.publicar_alias(cand)
        self.assertEqual(alias.cnaes, [5620104])

    def test_republica_incrementa_versao(self):
        cand = _candidato()
        kb_judge.publicar_alias(cand)
        cand.cnaes = [5611201]
        cand.save(update_fields=['cnaes'])
        alias = kb_judge.publicar_alias(cand)
        self.assertEqual(alias.versao, 2)
        self.assertEqual(CnaeAliasHistorico.objects.count(), 2)

    def test_desativar_grava_historico(self):
        cand = _candidato()
        alias = kb_judge.publicar_alias(cand)
        kb_judge.desativar_alias(alias)
        alias.refresh_from_db()
        self.assertFalse(alias.ativo)
        self.assertEqual(CnaeAliasHistorico.objects.filter(
            acao='desativado').count(), 1)

    @override_settings(ATIVIDADE_RESOLVER='v2')
    def test_resolvedor_usa_alias_publicado(self):
        from core.cnae_atividade import resolver_atividade
        _candidato()
        kb_judge.publicar_alias(CnaeAliasCandidato.objects.get())
        filtro = resolver_atividade('pizzarias')
        self.assertEqual(filtro['fonte'], 'alias')
        self.assertEqual(set(filtro['cnaes']), {5611201, 5620104})
        self.assertEqual(filtro['score'], 1.0)
