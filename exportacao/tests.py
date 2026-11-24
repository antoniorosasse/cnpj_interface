"""Testes da exportacao, creditos e limite diario. Sem rede e sem bancocnpj."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from exportacao.models import (Cliente, ConfiguracaoUso, Exportacao, LimiteUsuario,
                                LinhaExportada, MovimentoCredito)
from exportacao.servicos import registrar_pesquisa

User = get_user_model()


def linha(i):
    return {
        'cnpj_completo': f'{i:014d}',
        'razao_social': f'EMPRESA {i}',
        'nm_fantasia': f'FANTASIA {i}',
        'situacao_descricao': 'ATIVA',
        'data_inicio_atividade': '2020-01-01',
        'logradouro': 'RUA A',
        'numero': '10',
        'bairro': 'CENTRO',
        'cep': '89201000',
        'uf': 'SC',
        'municipio_nome': 'JOINVILLE',
        'telefone_1': '47999990000',
        'email': 'a@b.com',
        'cnae_principal': '6201000',
        'atividade_principal': 'DESENVOLVIMENTO',
        'op_simples': 'N',
        'op_mei': 'N',
    }


def corpo(response):
    return b''.join(response.streaming_content).decode('utf-8')


def _cookie_visitante(client, valor='abc123'):
    from django.core.signing import dumps
    from exportacao.servicos import SAL
    client.cookies['visitante_id'] = dumps(valor, salt=SAL)


@override_settings(EXPORTACAO_AMOSTRA_LINHAS=20, EXPORTACAO_MAX_LINHAS=50000)
class ExportacaoAnonimaTests(TestCase):

    def setUp(self):
        _cookie_visitante(self.client)
        def fake_iterar(filtros, limite, lote=1000):
            return iter([linha(i) for i in range(min(30, limite))])

        patcher = patch('exportacao.views.iterar_linhas', side_effect=fake_iterar)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_amostra_csv_tem_bom_e_20_linhas(self):
        resp = self.client.get('/exportar/lista/')
        self.assertEqual(resp.status_code, 200)
        texto = corpo(resp)
        self.assertTrue(texto.startswith('\ufeffCNPJ;'))
        linhas = texto.lstrip('\ufeff').strip().splitlines()
        self.assertEqual(len(linhas), 21)  # cabecalho + 20
        self.assertIn('00.000.000/0000-00', linhas[1])  # CNPJ formatado

    def test_amostra_json_valida(self):
        resp = self.client.get('/exportar/lista/', {'formato': 'json'})
        dados = json.loads(corpo(resp))
        self.assertEqual(len(dados), 20)
        self.assertEqual(dados[0]['cnpj_completo'], '00.000.000/0000-00')

    def test_registra_exportacao_como_baixada(self):
        resp = self.client.get('/exportar/lista/')
        corpo(resp)
        exp = Exportacao.objects.get()
        self.assertTrue(exp.amostra)
        self.assertEqual(exp.n_linhas, 20)
        self.assertEqual(exp.status, 'baixada')
        self.assertIsNotNone(exp.baixada_em)
        self.assertEqual(exp.creditos, 0)

    def test_sem_cookie_bloqueia_export(self):
        """Bot que descarta cookie nao exporta (senao cada request e' nova identidade)."""
        self.client.cookies.pop('visitante_id', None)
        resp = self.client.get('/exportar/lista/')
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, 'Ative os cookies', status_code=403)

    def test_cookie_e_gravado_na_navegacao(self):
        from django.http import HttpResponse
        from django.test import RequestFactory
        from exportacao.servicos import aplicar_cookie_visitante, visitante_id
        req = RequestFactory().get('/')
        req.COOKIES = {}
        req.META = {'REMOTE_ADDR': '10.2.2.2'}
        visitante_id(req)
        resposta = aplicar_cookie_visitante(req, HttpResponse('ok'))
        self.assertIn('visitante_id', resposta.cookies)


class ExportacaoClienteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cliente1', password='x')
        self.cliente = Cliente.objects.create(user=self.user, nome='Cliente 1',
                                              saldo_creditos=100)
        def fake_iterar(filtros, limite, lote=1000):
            return iter([linha(i) for i in range(min(30, limite))])

        patcher = patch('exportacao.views.iterar_linhas', side_effect=fake_iterar)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.user)

    def test_exporta_tudo_e_debita_creditos(self):
        resp = self.client.get('/exportar/lista/')
        self.assertEqual(resp.status_code, 200)
        linhas = corpo(resp).lstrip('\ufeff').strip().splitlines()
        self.assertEqual(len(linhas), 31)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.saldo_creditos, 70)
        self.assertTrue(MovimentoCredito.objects.filter(tipo='consumo', quantidade=-30).exists())
        exp = Exportacao.objects.latest('pk')
        self.assertEqual(exp.n_linhas, 30)
        self.assertEqual(exp.creditos, 30)

    def test_reexportar_nao_cobra_de_novo(self):
        corpo(self.client.get('/exportar/lista/'))
        corpo(self.client.get('/exportar/lista/'))
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.saldo_creditos, 70)

    def test_sem_saldo_bloqueia(self):
        Cliente.objects.filter(pk=self.cliente.pk).update(saldo_creditos=0)
        resp = self.client.get('/exportar/lista/')
        self.assertEqual(resp.status_code, 402)
        self.assertEqual(Exportacao.objects.count(), 0)


class LimiteDiarioTests(TestCase):
    @override_settings(PESQUISAS_DIA_IP=100)
    def test_anonimo_estoura_no_terceiro(self):
        configuracao = ConfiguracaoUso(id=1)
        configuracao.lista_dia_anonimo = 2
        configuracao.save()
        class R:
            user = type('U', (), {'is_authenticated': False})()
            COOKIES = {}
            META = {'REMOTE_ADDR': '10.0.0.1'}

        r = R()
        with patch('exportacao.servicos.visitante_id', return_value='abcd1234'):
            estados = [registrar_pesquisa(r)['permitido'] for _ in range(3)]
        self.assertEqual(estados, [True, True, False])


class CamposTests(TestCase):

    def setUp(self):
        _cookie_visitante(self.client)
        patcher = patch('exportacao.views.iterar_linhas',
                        side_effect=lambda filtros, limite, lote=1000: iter([linha(1)]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_campos_escolhidos_limitam_o_cabecalho(self):
        resp = self.client.get('/exportar/lista/', {
            'campos': 'cnpj_completo,razao_social', 'formato': 'csv'})
        cabecalho = corpo(resp).lstrip('\ufeff').splitlines()[0]
        self.assertEqual(cabecalho, 'CNPJ;RAZAO_SOCIAL')

    def test_pagina_de_campos_responde(self):
        resp = self.client.get('/exportar/', {'uf': 'SC'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="campos"')
        self.assertContains(resp, 'value="SC"')


class CnaeFiltroTests(TestCase):
    def test_codigo_usa_prefixo(self):
        from core.consultas_lista import montar_filtros
        sql, params = montar_filtros(cnae='6201')
        self.assertIn('e.cnae_principal BETWEEN %s AND %s', sql)
        self.assertEqual(params, [6201000, 6201999])

    def test_descricao_usa_ilike(self):
        from core.consultas_lista import montar_filtros
        sql, params = montar_filtros(cnae='desenvolvimento')
        self.assertIn('c.nm_cnae ILIKE %s', sql)
        self.assertEqual(params, ['%desenvolvimento%'])


class ProximidadeExportTests(TestCase):

    def setUp(self):
        _cookie_visitante(self.client)
        patcher = patch('exportacao.views.resolver_origem',
                        return_value=({'latitude': -26.3, 'longitude': -48.8,
                                       'rotulo': 'Joinville'}, None))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher2 = patch('exportacao.views.iterar_proximidade',
                         side_effect=lambda *a, **k: iter([
                             {'cnpj_completo': '00000000000191', 'nm_fantasia': 'LOJA',
                              'logradouro': 'RUA A', 'numero': '1', 'bairro': 'CENTRO',
                              'uf': 'SC', 'municipio_nome': 'JOINVILLE',
                              'telefone_1': '47999990000', 'situacao_descricao': 'ATIVA',
                              'km': 1.23}]))
        patcher2.start()
        self.addCleanup(patcher2.stop)

    def test_exporta_csv_da_proximidade(self):
        resp = self.client.get('/exportar/proximidade/', {
            'origem': 'Joinville/SC', 'raio': 5, 'formato': 'csv'})
        self.assertEqual(resp.status_code, 200)
        texto = corpo(resp)
        self.assertIn('CNPJ;RAZAO_SOCIAL;NOME_FANTASIA', texto)
        self.assertIn('00.000.000/0001-91', texto)
        exp = Exportacao.objects.get()
        self.assertEqual(exp.origem, 'proximidade')

    def test_sem_origem_da_400(self):
        resp = self.client.get('/exportar/proximidade/', {'formato': 'csv'})
        self.assertEqual(resp.status_code, 400)


class ProximidadeConsultaTests(TestCase):
    def test_junta_razao_social_por_cnpj_basico(self):
        from unittest.mock import MagicMock
        from exportacao.consulta import iterar_proximidade

        cur = MagicMock()
        cur.fetchall.return_value = [{'cnpj_basico': '12345678', 'razao_social': 'ACME LTDA'}]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        linhas_prox = [{'cnpj_basico': '12345678', 'cnpj_completo': '12345678000191', 'km': 1.0}]

        with patch('exportacao.consulta.get_postgres_connection', return_value=conn), \
             patch('core.proximidade.buscar_proximos', return_value=(linhas_prox, False, 1)):
            linhas = list(iterar_proximidade(-26.3, -48.8, 5, True, None, 10))

        self.assertEqual(linhas[0]['razao_social'], 'ACME LTDA')


class StaffExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin1', 'a@b.com', 'x')
        self.client.force_login(self.user)
        patcher = patch('exportacao.views.iterar_linhas',
                        side_effect=lambda filtros, limite, lote=1000: iter(
                            [linha(i) for i in range(min(30, limite))]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_staff_exporta_tudo_sem_cliente_e_sem_credito(self):
        resp = self.client.get('/exportar/lista/', {'formato': 'csv'})
        self.assertEqual(resp.status_code, 200)
        linhas = corpo(resp).lstrip('\ufeff').strip().splitlines()
        self.assertEqual(len(linhas), 31)  # cabecalho + 30
        exp = Exportacao.objects.latest('pk')
        self.assertFalse(exp.amostra)
        self.assertEqual(exp.creditos, 0)
        self.assertIn('attachment', resp['Content-Disposition'])
        self.assertNotIn('amostra', resp['Content-Disposition'])


class CamposProximidadeTests(TestCase):

    def setUp(self):
        _cookie_visitante(self.client)
        patcher = patch('exportacao.views.resolver_origem',
                        return_value=({'latitude': -26.7, 'longitude': -48.6}, None))
        patcher.start(); self.addCleanup(patcher.stop)
        patcher2 = patch('exportacao.views.iterar_proximidade',
                         side_effect=lambda *a, **k: iter([
                             {'cnpj_completo': '00000000000191', 'razao_social': 'ACME',
                              'nm_fantasia': 'LOJA', 'logradouro': 'RUA A', 'numero': '1',
                              'bairro': 'CENTRO', 'uf': 'SC', 'municipio_nome': 'PICARRAS',
                              'telefone_1': '47999990000', 'situacao_descricao': 'ATIVA',
                              'km': 1.0}]))
        patcher2.start(); self.addCleanup(patcher2.stop)

    def test_campos_da_proximidade_limitam_o_arquivo(self):
        resp = self.client.get('/exportar/proximidade/', {
            'origem': 'Balneário Piçarras/SC', 'raio': 5, 'formato': 'csv',
            'campos': 'cnpj_completo,razao_social,km'})
        cabecalho = corpo(resp).lstrip('\ufeff').splitlines()[0]
        self.assertEqual(cabecalho, 'CNPJ;RAZAO_SOCIAL;DISTANCIA_KM')

    def test_pagina_de_campos_no_modo_proximidade(self):
        resp = self.client.get('/exportar/', {'origem': 'Balneário Piçarras/SC', 'raio': 5})
        self.assertContains(resp, 'name="origem"')
        self.assertContains(resp, 'action="/exportar/proximidade/"')
        self.assertContains(resp, 'value="km"')
        self.assertContains(resp, 'value="email"')


class ChatExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('chat1', password='x')
        self.cliente = Cliente.objects.create(user=self.user, nome='Chat 1', saldo_creditos=100)
        self.client.force_login(self.user)
        sessao = self.client.session
        sessao['chat_conversa_id'] = '1'
        sessao.save()

    def _patches(self, mensagens=None, execucao=None):
        mensagens = mensagens or [{'papel': 'assistant', 'sql': 'SELECT 1'}]
        execucao = execucao or {'ok': True, 'colunas': ['a', 'b'],
                                'rows': [{'a': 1, 'b': 'x'}, {'a': 2, 'b': 'y'}]}
        return [
            patch('chat.repo.buscar_conversa', return_value={'pk': 1}),
            patch('chat.repo.mensagens_da_conversa', return_value=mensagens),
            patch('chat.services.sql_validator.validar_sql',
                   return_value=SimpleNamespace(sql='SELECT 1', original_sql='SELECT 1',
                                                teve_limit=False, limit_valor=0)),
            patch('chat.services.sql_executor.executar_sql', return_value=execucao),
        ]

    def test_baixa_csv_e_debita_um_credito_por_linha(self):
        p1, p2, p3, p4 = self._patches()
        with p1, p2, p3, p4:
            resp = self.client.get('/exportar/chat/', {'formato': 'csv'})
        self.assertEqual(resp.status_code, 200)
        texto = resp.content.decode('utf-8')
        self.assertTrue(texto.startswith('\ufeffa;b'))
        self.assertIn('1;x', texto)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.saldo_creditos, 98)
        exp = Exportacao.objects.latest('pk')
        self.assertEqual(exp.origem, 'chat')
        self.assertEqual(exp.n_linhas, 2)

    def test_sem_conversa_da_404(self):
        with patch('chat.repo.buscar_conversa', return_value=None):
            resp = self.client.get('/exportar/chat/')
        self.assertEqual(resp.status_code, 404)

    def test_sem_sql_na_ultima_resposta_da_404(self):
        p1, p2, p3, p4 = self._patches(mensagens=[{'papel': 'assistant', 'sql': ''}])
        with p1, p2, p3, p4:
            resp = self.client.get('/exportar/chat/')
        self.assertEqual(resp.status_code, 404)

    def test_sem_saldo_suficiente_bloqueia(self):
        Cliente.objects.filter(pk=self.cliente.pk).update(saldo_creditos=1)
        p1, p2, p3, p4 = self._patches()
        with p1, p2, p3, p4:
            resp = self.client.get('/exportar/chat/')
        self.assertEqual(resp.status_code, 402)
        self.assertEqual(Exportacao.objects.count(), 0)

    def test_staff_baixa_sem_debitar(self):
        admin = User.objects.create_superuser('admin2', 'a@b.com', 'x')
        self.client.force_login(admin)
        sessao = self.client.session
        sessao['chat_conversa_id'] = '1'
        sessao.save()
        p1, p2, p3, p4 = self._patches()
        with p1, p2, p3, p4:
            resp = self.client.get('/exportar/chat/', {'formato': 'json'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('application/json', resp['Content-Type'])


class ProtecoesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('prot1', password='x')
        self.cliente = Cliente.objects.create(user=self.user, nome='Prot', saldo_creditos=100)
        self.client.force_login(self.user)

    def test_abortar_download_nao_sai_de_graca(self):
        """O debito acontece antes do streaming: ler 1 chunk e fechar ja' cobrou."""
        patcher = patch('exportacao.views.iterar_linhas',
                        side_effect=lambda f, l, lote=1000: iter(
                            [linha(i) for i in range(min(30, l))]))
        patcher.start(); self.addCleanup(patcher.stop)
        resp = self.client.get('/exportar/lista/', {'formato': 'csv'})
        self.assertEqual(resp.status_code, 200)
        next(iter(resp.streaming_content))  # consome so' o primeiro pedaco
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.saldo_creditos, 70)

    def test_saldo_nunca_fica_negativo(self):
        from exportacao.servicos import SaldoInsuficiente, debitar
        with self.assertRaises(SaldoInsuficiente):
            debitar(self.cliente, 500, 'teste')

    def test_conta_logada_sem_cliente_tem_limite(self):
        from django.test import RequestFactory
        from exportacao.servicos import registrar_uso
        user = User.objects.create_user('semcliente', password='x')
        req = RequestFactory().get('/')
        req.user = user
        req.COOKIES = {}
        req.META = {'REMOTE_ADDR': '10.1.1.1'}
        cfg = ConfiguracaoUso(id=1)
        cfg.lista_dia_conta = 2
        cfg.save()
        with override_settings(PESQUISAS_DIA_IP=100):
            estados = [registrar_uso(req, 'pesquisa')['permitido'] for _ in range(3)]
        self.assertEqual(estados, [True, True, False])

    def test_paginacao_tem_teto_proprio(self):
        from django.test import RequestFactory
        from exportacao.servicos import registrar_uso
        req = RequestFactory().get('/')
        req.user = type('U', (), {'is_authenticated': False})()
        req.COOKIES = {}
        req.META = {'REMOTE_ADDR': '10.1.1.2'}
        cfg = ConfiguracaoUso(id=1)
        cfg.lista_dia_anonimo = 2
        cfg.save()
        with override_settings(PAGINAS_DIA_IP=100):
            with patch('exportacao.servicos.visitante_id', return_value='pag1'):
                estados = [registrar_uso(req, 'pagina')['permitido'] for _ in range(3)]
        self.assertEqual(estados, [True, True, False])

    def test_registra_usuario_na_exportacao(self):
        patcher = patch('exportacao.views.iterar_linhas',
                        side_effect=lambda f, l, lote=1000: iter([linha(1)]))
        patcher.start(); self.addCleanup(patcher.stop)
        corpo(self.client.get('/exportar/lista/', {'formato': 'csv'}))
        self.assertEqual(Exportacao.objects.latest('pk').usuario, self.user)


class CamposMultiplosTests(TestCase):
    def setUp(self):
        _cookie_visitante(self.client)
        patcher = patch('exportacao.views.iterar_linhas',
                        side_effect=lambda f, l, lote=1000: iter([linha(1)]))
        patcher.start(); self.addCleanup(patcher.stop)

    def test_checkboxes_multiplos(self):
        resp = self.client.get('/exportar/lista/',
                               {'campos': ['cnpj_completo', 'razao_social'],
                                'formato': 'csv'})
        cabecalho = corpo(resp).lstrip('\ufeff').splitlines()[0]
        self.assertEqual(cabecalho, 'CNPJ;RAZAO_SOCIAL')

    def test_municipio_id_do_fluxo_de_campos_e_preservado(self):
        capturado = {}

        def falso(filtros, limite, lote=1000):
            capturado.update(filtros)
            return iter([linha(1)])

        with patch('exportacao.views.iterar_linhas', side_effect=falso):
            corpo(self.client.get('/exportar/lista/',
                                  {'municipio_id': '4209102', 'formato': 'csv'}))
        self.assertEqual(capturado.get('municipio_id'), 4209102)


class IpTests(TestCase):
    def test_x_real_ip_tem_prioridade(self):
        from django.test import RequestFactory
        from exportacao.servicos import ip_do_request
        req = RequestFactory().get('/', HTTP_X_REAL_IP='187.15.85.94',
                                   HTTP_X_FORWARDED_FOR='1.2.3.4')
        req.META['REMOTE_ADDR'] = '127.0.0.1'
        self.assertEqual(ip_do_request(req), '187.15.85.94')

    def test_x_forwarded_for_e_ignorado(self):
        from django.test import RequestFactory
        from exportacao.servicos import ip_do_request
        req = RequestFactory().get('/', HTTP_X_FORWARDED_FOR='1.2.3.4')
        req.META['REMOTE_ADDR'] = '10.9.9.9'
        self.assertEqual(ip_do_request(req), '10.9.9.9')

    def test_ip_invalido_cai_para_remote_addr(self):
        from django.test import RequestFactory
        from exportacao.servicos import ip_do_request
        req = RequestFactory().get('/', HTTP_X_REAL_IP='nao-e-ip')
        req.META['REMOTE_ADDR'] = '10.8.8.8'
        self.assertEqual(ip_do_request(req), '10.8.8.8')


class ChatExportVetorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('chatv', password='x')
        Cliente.objects.create(user=self.user, nome='ChatV', saldo_creditos=50)
        self.client.force_login(self.user)
        sessao = self.client.session
        sessao['chat_conversa_id'] = '1'
        sessao.save()

    def _mensagens(self):
        return [{'papel': 'user', 'conteudo': 'liste as padarias de picarras'},
                {'papel': 'assistant', 'sql': "SELECT 1 FROM x WHERE nome ILIKE %s"}]

    def test_injeta_vetor_antes_de_executar(self):
        with patch('chat.repo.buscar_conversa', return_value={'pk': 1}), \
             patch('chat.repo.mensagens_da_conversa', return_value=self._mensagens()), \
             patch('chat.services.sql_validator.validar_sql',
                   return_value=SimpleNamespace(sql='SELECT 1', original_sql='SELECT 1',
                                                teve_limit=False, limit_valor=0)), \
             patch('chat.services.embedding_service.injetar_vetor',
                   return_value=("SELECT 1 FROM x WHERE nome ILIKE '[0.1,0.2]'", None)) as inj, \
             patch('chat.services.sql_executor.executar_sql',
                   return_value={'ok': True, 'colunas': ['a'], 'rows': [{'a': 1}]}):
            resp = self.client.get('/exportar/chat/', {'formato': 'csv'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('liste as padarias', inj.call_args[0][1])

    def test_sem_vetor_da_400_com_mensagem(self):
        with patch('chat.repo.buscar_conversa', return_value={'pk': 1}), \
             patch('chat.repo.mensagens_da_conversa', return_value=self._mensagens()), \
             patch('chat.services.sql_validator.validar_sql',
                   return_value=SimpleNamespace(sql='SELECT 1', original_sql='SELECT 1',
                                                teve_limit=False, limit_valor=0)), \
             patch('chat.services.embedding_service.injetar_vetor',
                   return_value=('SELECT 1', 'Falha ao gerar o vetor da pergunta (modelo indisponivel).')):
            resp = self.client.get('/exportar/chat/')
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, 'Não foi possível montar a lista', status_code=400)


class AumentarLimitTests(TestCase):
    def test_eleva_limit_do_gerador(self):
        from exportacao.views import _aumentar_limit
        self.assertEqual(_aumentar_limit('SELECT 1 LIMIT 100', 5000),
                         'SELECT 1 LIMIT 5000')

    def test_nao_reduz_limit_maior(self):
        from exportacao.views import _aumentar_limit
        self.assertEqual(_aumentar_limit('SELECT 1 LIMIT 9000', 5000),
                         'SELECT 1 LIMIT 9000')

    def test_mexe_so_no_limit_externo(self):
        from exportacao.views import _aumentar_limit
        sql = 'SELECT * FROM (SELECT 1 LIMIT 5) t LIMIT 100'
        self.assertEqual(_aumentar_limit(sql, 5000),
                         'SELECT * FROM (SELECT 1 LIMIT 5) t LIMIT 5000')


class CnaeEnriquecimentoTests(TestCase):
    def test_acrescenta_codigo_quando_so_tem_nome(self):
        from unittest.mock import MagicMock
        from exportacao.views import _enriquecer_cnae
        cur = MagicMock()
        cur.fetchall.return_value = [(4721102, 'Padaria e confeitaria')]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        colunas = ['cnae_nome', 'total']
        linhas = [{'cnae_nome': 'Padaria e confeitaria', 'total': 183}]
        with patch('exportacao.views.get_postgres_connection', return_value=conn):
            novas, novas_linhas = _enriquecer_cnae(colunas, linhas)
        self.assertEqual(novas, ['cnae_nome', 'cnae_codigo', 'total'])
        self.assertEqual(novas_linhas[0]['cnae_codigo'], 4721102)

    def test_nao_duplica_quando_codigo_ja_existe(self):
        from exportacao.views import _enriquecer_cnae
        colunas = ['cnae_codigo', 'cnae_nome']
        linhas = [{'cnae_codigo': 1, 'cnae_nome': 'x'}]
        novas, novas_linhas = _enriquecer_cnae(colunas, linhas)
        self.assertEqual(novas, colunas)
        self.assertEqual(novas_linhas, linhas)


class ConfiguracaoContadoresTests(TestCase):
    def _request(self, user=None, ip='10.5.0.1'):
        from django.test import RequestFactory
        req = RequestFactory().get('/')
        req.user = user or type('U', (), {'is_authenticated': False})()
        req.COOKIES = {}
        req.META = {'REMOTE_ADDR': ip}
        return req

    def test_contador_desligado_nao_conta_nem_bloqueia(self):
        from exportacao.servicos import registrar_uso
        cfg = ConfiguracaoUso(id=1)
        cfg.ativo_geral = False
        cfg.save()
        req = self._request()
        with patch('exportacao.servicos.visitante_id', return_value='v1'):
            r = registrar_uso(req, 'lista')
        self.assertTrue(r['permitido'])
        self.assertFalse(r['contando'])

    def test_tipos_sao_independentes(self):
        from exportacao.servicos import registrar_uso
        cfg = ConfiguracaoUso(id=1)
        cfg.chat_ativa = False
        cfg.save()
        req = self._request()
        with patch('exportacao.servicos.visitante_id', return_value='v2'):
            chat = registrar_uso(req, 'chat')
            lista = registrar_uso(req, 'lista')
        self.assertFalse(chat['contando'])
        self.assertTrue(lista['contando'])

    def test_override_por_usuario(self):
        from exportacao.servicos import registrar_uso
        user = User.objects.create_user('lim1', password='x')
        LimiteUsuario.objects.create(user=user, chat_dia=2)
        req = self._request(user)
        with patch('exportacao.servicos.visitante_id', return_value='x'):
            estados = [registrar_uso(req, 'chat')['permitido'] for _ in range(3)]
        self.assertEqual(estados, [True, True, False])

    def test_staff_conta_mas_nunca_bloqueia(self):
        from exportacao.servicos import registrar_uso
        admin = User.objects.create_superuser('adm-lim', 'a@b.com', 'x')
        cfg = ConfiguracaoUso(id=1)
        cfg.lista_dia_conta = 1
        cfg.save()
        req = self._request(admin)
        with patch('exportacao.servicos.visitante_id', return_value='y'):
            primeiro = registrar_uso(req, 'lista')
            segundo = registrar_uso(req, 'lista')
        self.assertTrue(primeiro['permitido'] and segundo['permitido'])
        self.assertTrue(segundo['ilimitado'])
        self.assertEqual(segundo['usados'], 2)


class AdminContadoresTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('adm-cont', 'a@b.com', 'x')
        self.client.force_login(self.admin)

    def test_changelist_redireciona_para_edicao(self):
        resp = self.client.get('/admin/exportacao/configuracaouso/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/admin/exportacao/configuracaouso/1/', resp['Location'])

    def test_painel_de_contadores_responde(self):
        resp = self.client.get('/admin/exportacao/configuracaouso/contadores/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Contadores de uso')

    def test_limite_por_usuario_no_admin(self):
        resp = self.client.get('/admin/exportacao/limiteusuario/add/')
        self.assertEqual(resp.status_code, 200)


class ChatQuotaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('chatq', password='x')
        self.client.force_login(self.user)

    def test_chat_bloqueado_devolve_429(self):
        with patch('exportacao.servicos.registrar_uso',
                   return_value={'permitido': False, 'usados': 5, 'limite': 5,
                                 'contando': True, 'tipo': 'chat'}):
            resp = self.client.post('/chat/api/', data='{"pergunta": "teste"}',
                                    content_type='application/json')
        self.assertEqual(resp.status_code, 429)
        self.assertIn('consultas de chat hoje', resp.json()['erro'])


class TetoPorIpTests(TestCase):
    def test_teto_por_ip_bloqueia_mesmo_com_identidades_diferentes(self):
        from django.test import RequestFactory
        from exportacao.servicos import registrar_uso
        cfg = ConfiguracaoUso(id=1)
        cfg.ip_dia_exportacao = 2
        cfg.save()
        req = RequestFactory().get('/')
        req.user = type('U', (), {'is_authenticated': False})()
        req.COOKIES = {}
        req.META = {'REMOTE_ADDR': '57.141.18.9', 'HTTP_USER_AGENT': 'bot/1.0'}
        with patch('exportacao.servicos.visitante_id', side_effect=['a1', 'a2', 'a3']):
            estados = [registrar_uso(req, 'exportacao')['permitido'] for _ in range(3)]
        self.assertEqual(estados, [True, True, False])
