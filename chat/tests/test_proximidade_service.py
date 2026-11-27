# cnpj_interface/chat/tests/test_proximidade_service.py
"""
Testes da ferramenta de proximidade do chat (deterministica, sem LLM).

A conexao e' injetada (`conectar=`); `resolver_origem` e o motor de busca sao
mockados para testar o contrato: esclarecimentos, filtros, resumo e link.
"""
from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase

from chat.services import proximidade_service as svc


class FakeConn:
    """Conexao minima: o servico so usa cursor/rollback/close."""

    def cursor(self, **kwargs):
        return object()

    def rollback(self):
        pass

    def close(self):
        pass


def _conectar():
    return FakeConn()


LINHA_MOTOR = {
    'cnpj_completo': '84709955000102',
    'razao_social': 'CIA INDUSTRIAL H. CARLOS SCHNEIDER',
    'nm_fantasia': 'SCHNEIDER',
    'municipio_nome': 'Joinville',
    'uf': 'SC',
    'atividade_principal': 'Metalurgia de outros metais não-ferrosos',
    'situacao_descricao': 'ATIVA',
    'km': '0.45',
    'telefone_1': '4733334444',
    'email': 'contato@exemplo.com',
    'dt_ini': date(1969, 1, 2),
}


class DataHaAnosTests(SimpleTestCase):
    def test_ano_normal_e_bissexto(self):
        self.assertEqual(svc.data_ha_anos(3).year, date.today().year - 3)
        # 29/02 -> 28/02 (nao levanta ValueError)
        with patch.object(svc, 'date') as mock_date:
            mock_date.today.return_value = date(2028, 2, 29)
            mock_date.side_effect = lambda *a, **k: date(*a, **k)
            self.assertEqual(svc.data_ha_anos(3), date(2025, 2, 28))


class ResolverAtividadesTests(SimpleTestCase):
    def setUp(self):
        self._alias_patcher = patch(
            "core.cnae_atividade._alias_publicado", return_value=None)
        self._alias_patcher.start()
        self.addCleanup(self._alias_patcher.stop)
    def test_metalurgicas(self):
        filtro = svc._resolver_atividades(['indústrias metalúrgicas'])
        self.assertEqual(filtro['categorias'], ['metalurgica'])
        self.assertEqual(filtro['faixas'], [(2400000, 2599999)])
        self.assertEqual(filtro['nao_reconhecidas'], [])

    def test_supermercados_e_padarias(self):
        filtro = svc._resolver_atividades(['supermercados e padarias'])
        self.assertEqual(set(filtro['categorias']), {'supermercados', 'padarias'})
        self.assertEqual(set(filtro['cnaes']),
                         {4711302, 1091102, 4721101, 4721102})

    def test_atividade_desconhecida(self):
        filtro = svc._resolver_atividades(['naves espaciais'])
        self.assertEqual(filtro['categorias'], [])
        self.assertEqual(filtro['nao_reconhecidas'], ['naves espaciais'])


class ResumoTests(SimpleTestCase):
    def test_resumo_com_total_e_filtros(self):
        texto = svc._resumo(
            1228, 248, True, 'indústrias metalúrgicas', 'Joinville/SC', 10,
            uf='SC', idade=3, capital=None, situacao=2)
        self.assertIn('1.228', texto)
        self.assertIn('Joinville/SC', texto)
        self.assertIn('10 km', texto)
        self.assertIn('metalúrgicas', texto)
        self.assertIn('mais próximos', texto)
        self.assertIn('linha reta', texto)

    def test_resumo_sem_resultado(self):
        texto = svc._resumo(0, 0, False, 'padarias', 'Curitiba/PR', 5)
        self.assertIn('Nenhum resultado', texto)

    def test_resumo_total_indisponivel(self):
        texto = svc._resumo(10, 10, False, 'padarias', 'Curitiba/PR', 5,
                            total_indisponivel=True)
        self.assertIn('total exato não está disponível', texto)


class LinkProximidadeTests(SimpleTestCase):
    def test_link_usa_texto_da_origem(self):
        link = svc._link_proximidade(
            'Joinville/SC',
            {'latitude': -26.3, 'longitude': -48.8}, 10, 2)
        self.assertIn('/proximidade/', link)
        self.assertIn('origem=Joinville%2FSC', link)
        self.assertIn('raio=10', link)
        self.assertIn('situacao=ATIVA', link)

    def test_link_sem_texto_usa_coordenada(self):
        link = svc._link_proximidade(
            None, {'latitude': -23.561, 'longitude': -46.655}, 5, None)
        self.assertIn('origem=-23.561000%2C-46.655000', link)
        self.assertNotIn('situacao=', link)


class LinkComFiltrosTests(SimpleTestCase):
    def test_link_leva_filtros_e_contexto_do_chat(self):
        link = svc._link_proximidade(
            'Joinville/SC',
            {'latitude': -26.3, 'longitude': -48.8}, 10, 2,
            atividade='indústrias metalúrgicas', uf='SC', idade=3,
            capital=500000, pergunta='Metalúrgicas num raio de 10 km?',
            resposta='Encontrei 1.202 empresas.',
        )
        self.assertIn('atividade=ind%C3%BAstrias+metal%C3%BArgicas', link)
        self.assertIn('uf=SC', link)
        self.assertIn('idade=3', link)
        self.assertIn('capital=500000', link)
        self.assertIn('pergunta=Metal%C3%BArgicas', link)
        self.assertIn('resposta=Encontrei+1.202', link)


class AtividadePorCodigoTests(SimpleTestCase):
    def test_codigo_cnae_vira_filtro(self):
        filtro = svc._resolver_atividades(['8630504'])
        self.assertEqual(filtro['cnaes'], [8630504])
        self.assertIn('CNAE 8630504', filtro['rotulo'])


class ConsultarProximidadeTests(SimpleTestCase):
    """Contrato de consultar_proximidade (motor e origem mockados)."""

    def setUp(self):
        # `tem_tabela_total` tem cache de modulo; outro teste do suite pode
        # te-lo deixado True (banco real) e o fake cursor nao suporta o
        # caminho por endereco. Isola o cache por teste.
        from core import proximidade
        self._cache_original = proximidade._tabela_total_cache
        proximidade._tabela_total_cache = {}
        self.addCleanup(
            setattr, proximidade, '_tabela_total_cache', self._cache_original)
        # Nao gravar AtividadeLog em SimpleTestCase (sem banco).
        self._log_patcher = patch(
            "chat.services.proximidade_service.registrar_atividade")
        self._log_patcher.start()
        self.addCleanup(self._log_patcher.stop)
        self._alias_patcher = patch(
            "core.cnae_atividade._alias_publicado", return_value=None)
        self._alias_patcher.start()
        self.addCleanup(self._alias_patcher.stop)

    def test_sem_origem_pede_esclarecimento(self):
        resultado = svc.consultar_proximidade(
            {'origem': None, 'usar_contexto': False, 'raio_km': 10,
             'atividades': ['indústrias']},
            conectar=_conectar)
        self.assertFalse(resultado['ok'])
        self.assertEqual(resultado['etapa'], 'esclarecimento')
        self.assertIn('origem', resultado['erro'])

    def test_contexto_ausente_pede_esclarecimento(self):
        resultado = svc.consultar_proximidade(
            {'origem': None, 'usar_contexto': True, 'raio_km': 5,
             'atividades': ['padarias']},
            contexto_geo=None, conectar=_conectar)
        self.assertFalse(resultado['ok'])
        self.assertEqual(resultado['etapa'], 'esclarecimento')
        self.assertIn('deste endereço', resultado['erro'])

    def test_atividade_desconhecida(self):
        resultado = svc.consultar_proximidade(
            {'origem': 'Joinville/SC', 'usar_contexto': False, 'raio_km': 10,
             'atividades': ['naves espaciais']},
            conectar=_conectar)
        self.assertFalse(resultado['ok'])
        self.assertEqual(resultado['etapa'], 'atividade')
        self.assertIn('Não reconheci', resultado['erro'])

    @patch('chat.services.proximidade_service.tem_tabela_total',
           return_value=True)
    @patch('chat.services.proximidade_service.buscar_proximos_filtrado')
    @patch('chat.services.proximidade_service.resolver_origem')
    def test_usa_endereco_quando_ceps_geo_existem(
            self, mock_origem, mock_buscar, mock_tabela):
        mock_origem.return_value = (
            {'latitude': -26.3, 'longitude': -48.8,
             'rotulo': 'Joinville/SC'}, None)
        mock_buscar.return_value = ([LINHA_MOTOR], False, 1)
        resultado = svc.consultar_proximidade(
            {'origem': 'Joinville/SC', 'usar_contexto': False, 'raio_km': 10,
             'atividades': ['indústrias metalúrgicas'], 'situacao': 'ativa',
             'limite': 10}, conectar=_conectar)
        self.assertTrue(resultado['ok'])
        self.assertTrue(mock_buscar.call_args.kwargs['usar_endereco'])
        self.assertIn('coordenada do endereço', resultado['resumo'])
        self.assertTrue(resultado['plano']['usar_endereco'])

    @patch('chat.services.proximidade_service.buscar_proximos_filtrado')
    @patch('chat.services.proximidade_service.resolver_origem')
    def test_executa_com_filtro_cnae(self, mock_origem, mock_buscar):
        mock_origem.return_value = (
            {'latitude': -26.3, 'longitude': -48.8,
             'rotulo': 'Joinville/SC'}, None)
        mock_buscar.return_value = ([LINHA_MOTOR], True, 1228)

        resultado = svc.consultar_proximidade(
            {'origem': 'Joinville/SC', 'usar_contexto': False, 'raio_km': 10,
             'atividades': ['indústrias metalúrgicas'], 'situacao': 'ativa',
             'uf': 'SC', 'idade_min_anos': 3, 'capital_min': None,
             'limite': 248},
            conectar=_conectar)

        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['total'], 1228)
        self.assertEqual(resultado['exibidas'], 1)
        self.assertTrue(resultado['tem_mais'])
        self.assertEqual(resultado['rows'][0]['km'], 0.45)
        self.assertEqual(resultado['rows'][0]['dt_ini'], '02/01/1969')
        self.assertIn('/proximidade/', resultado['link'])
        self.assertEqual(resultado['plano']['origem']['rotulo'], 'Joinville/SC')

        kwargs = mock_buscar.call_args.kwargs
        self.assertEqual(kwargs['faixas_cnae'], [(2400000, 2599999)])
        self.assertEqual(kwargs['situacao'], 2)
        self.assertEqual(kwargs['uf'], 'SC')
        self.assertEqual(kwargs['limite'], 248)
        self.assertEqual(kwargs['dt_ini_antes_de'].year, date.today().year - 3)

    @patch('chat.services.proximidade_service.buscar_proximos')
    @patch('chat.services.proximidade_service.resolver_origem')
    def test_sem_filtro_usa_o_motor_da_pagina(self, mock_origem, mock_buscar):
        mock_origem.return_value = (
            {'latitude': -26.3, 'longitude': -48.8,
             'rotulo': 'Joinville/SC'}, None)
        mock_buscar.return_value = ([], False, 0)

        resultado = svc.consultar_proximidade(
            {'origem': 'Joinville/SC', 'usar_contexto': False, 'raio_km': 5,
             'atividades': [], 'situacao': 'todas', 'limite': 100},
            conectar=_conectar)

        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['total'], 0)
        mock_buscar.assert_called_once()
        self.assertEqual(mock_buscar.call_args.kwargs['somente_matrizes'], False)

    @patch('chat.services.proximidade_service.buscar_proximos_filtrado')
    @patch('chat.services.proximidade_service.resolver_origem')
    def test_origem_ambigua_pede_esclarecimento(self, mock_origem, mock_buscar):
        mock_origem.return_value = (
            None, '"Av. Paulista" é ambíguo: informe a UF.')
        resultado = svc.consultar_proximidade(
            {'origem': 'Av. Paulista', 'usar_contexto': False, 'raio_km': 5,
             'atividades': ['clínicas médicas e odontológicas']},
            conectar=_conectar)
        self.assertFalse(resultado['ok'])
        self.assertEqual(resultado['etapa'], 'esclarecimento')
        self.assertIn('ambíguo', resultado['erro'])
        mock_buscar.assert_not_called()

    @patch('chat.services.proximidade_service.buscar_proximos_filtrado')
    def test_deste_endereco_reusa_contexto_sem_resolver(self, mock_buscar):
        mock_buscar.return_value = ([LINHA_MOTOR], False, 1)
        with patch('chat.services.proximidade_service.resolver_origem') as mock_origem:
            resultado = svc.consultar_proximidade(
                {'origem': None, 'usar_contexto': True, 'raio_km': 5,
                 'atividades': ['supermercados e padarias'], 'limite': 10},
                contexto_geo={'latitude': -25.4, 'longitude': -49.2,
                              'rotulo': 'Curitiba/PR', 'texto': 'Curitiba/PR'},
                conectar=_conectar)
        self.assertTrue(resultado['ok'])
        mock_origem.assert_not_called()
        self.assertEqual(resultado['origem']['rotulo'], 'Curitiba/PR')
