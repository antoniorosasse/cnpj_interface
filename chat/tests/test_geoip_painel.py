# cnpj_interface/chat/tests/test_geoip_painel.py
"""
Testes do bloco "Base de localizacao (GeoIP)" da tela de logs:
estado da base, instalacao do arquivo enviado, script de download e as tres
views (checar / baixar / enviar).

Nenhum teste baixa da MaxMind e nenhum depende de geoip/GeoLite2-City.mmdb:
o script (`rodar_script`), o instalador e a leitura de metadados sao mockados
quando o teste nao quer testar o proprio arquivo.

Roda com:
    python manage.py test chat.tests.test_geoip_painel
"""

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from chat.geoip import (
    MMDB_MINIMO,
    GeoIPIndisponivel,
    _mmdb_do_pacote,
    caminho_marca,
    caminho_mmdb,
    estado_base,
    instalar_arquivo,
    metadados_mmdb,
    rodar_script,
)

User = get_user_model()

NOME_PACOTE = 'GeoLite2-City_20261106.tar.gz'
MEMBRO = 'GeoLite2-City_20261106/GeoLite2-City.mmdb'
BUILD = 1762473600   # 2026-11-06 00:00 UTC


def tar_gz(nome_interno, conteudo):
    """Pacote .tar.gz em memoria, no formato que a MaxMind entrega."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as pacote:
        info = tarfile.TarInfo(nome_interno)
        info.size = len(conteudo)
        pacote.addfile(info, io.BytesIO(conteudo))
    return buffer.getvalue()


class PastaTemporariaMixin:
    """Aponta GEOIP_CIDADE_MMDB para uma pasta temporaria (nada e' tocado)."""

    def setUp(self):
        super().setUp()
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.arquivo = Path(self.pasta.name) / 'GeoLite2-City.mmdb'
        override = override_settings(GEOIP_CIDADE_MMDB=str(self.arquivo))
        override.enable()
        self.addCleanup(override.disable)
        self.tmp = Path(tempfile.mkdtemp(prefix='geoip-teste-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)


class EstadoBaseTestCase(PastaTemporariaMixin, SimpleTestCase):
    """O painel precisa dizer o que esta em uso sem quebrar a tela."""

    def test_sem_arquivo_explica_o_que_fazer(self):
        estado = estado_base()
        self.assertFalse(estado['existe'])
        self.assertTrue(estado['gravavel'])
        self.assertIn('não encontrada', estado['erro'])

    def test_arquivo_invalido_nao_promete_build(self):
        self.arquivo.write_bytes(b'x' * (MMDB_MINIMO + 1))
        estado = estado_base()
        self.assertTrue(estado['existe'])
        self.assertIsNone(estado['build'])
        self.assertIn('não abre como MMDB', estado['erro'])

    def test_le_a_marca_do_download(self):
        self.arquivo.write_bytes(b'x')
        caminho_marca().write_text('Mon, 09 Nov 2026 23:56:46 GMT\n', 'utf-8')
        self.assertEqual(
            estado_base()['marca'], 'Mon, 09 Nov 2026 23:56:46 GMT',
        )

    def test_metadados_de_arquivo_inexistente(self):
        self.assertIsNone(metadados_mmdb(self.arquivo))


class PacoteEnviadoTestCase(PastaTemporariaMixin, SimpleTestCase):
    """Qual arquivo o painel aceita e qual recusa."""

    def test_zip_csv_e_recusado_com_explicacao(self):
        with self.assertRaisesMessage(ValueError, 'CSV'):
            _mmdb_do_pacote(
                'GeoLite2-City-CSV_20261106.zip', b'PK\x03\x04', self.tmp,
            )

    def test_sufixo_desconhecido(self):
        with self.assertRaisesMessage(ValueError, 'Envie o .mmdb'):
            _mmdb_do_pacote('base.rar', b'x', self.tmp)

    def test_pacote_sem_mmdb(self):
        with self.assertRaisesMessage(ValueError, 'Nenhum .mmdb'):
            _mmdb_do_pacote(
                NOME_PACOTE,
                tar_gz('GeoLite2-City_20261106/README.txt', b'oi'),
                self.tmp,
            )

    def test_pacote_com_mmdb_extrai_so_o_arquivo(self):
        conteudo = b'abc' * 1000
        alvo = _mmdb_do_pacote(NOME_PACOTE, tar_gz(MEMBRO, conteudo), self.tmp)
        self.assertEqual(alvo.name, 'GeoLite2-City.mmdb')
        self.assertEqual(alvo.read_bytes(), conteudo)

    def test_mmdb_puro(self):
        alvo = _mmdb_do_pacote('GeoLite2-City.mmdb', b'conteudo', self.tmp)
        self.assertEqual(alvo.read_bytes(), b'conteudo')


class InstalarArquivoTestCase(PastaTemporariaMixin, SimpleTestCase):
    """Instalacao pelo painel: valida antes de trocar a base em uso."""

    def test_arquivo_vazio(self):
        with self.assertRaisesMessage(ValueError, 'vazio'):
            instalar_arquivo(NOME_PACOTE, b'')

    @patch('chat.geoip.UPLOAD_MAXIMO', 10)
    def test_arquivo_grande_demais(self):
        with self.assertRaisesMessage(ValueError, 'grande demais'):
            instalar_arquivo(NOME_PACOTE, b'x' * 20)

    def test_mmdb_truncado_e_recusado(self):
        pacote = tar_gz(MEMBRO, b'x' * 1000)
        with self.assertRaisesMessage(ValueError, 'truncado'):
            instalar_arquivo(NOME_PACOTE, pacote)
        self.assertFalse(self.arquivo.exists())

    def test_arquivo_que_nao_e_mmdb_e_recusado(self):
        pacote = tar_gz(MEMBRO, b'x' * (MMDB_MINIMO + 10))
        with self.assertRaisesMessage(ValueError, 'não é um MMDB válido'):
            instalar_arquivo(NOME_PACOTE, pacote)
        self.assertFalse(self.arquivo.exists())

    @patch('chat.geoip.metadados_mmdb', return_value=('GeoLite2-City', BUILD))
    def test_instala_pacote_valido_e_apaga_a_marca(self, meta):
        caminho_marca().write_text('Mon, 09 Nov 2026 23:56:46 GMT', 'utf-8')
        conteudo = b'0' * (MMDB_MINIMO + 10)
        estado = instalar_arquivo(NOME_PACOTE, tar_gz(MEMBRO, conteudo))
        self.assertTrue(self.arquivo.exists())
        self.assertEqual(self.arquivo.stat().st_size, len(conteudo))
        self.assertFalse(caminho_marca().exists())
        self.assertEqual(estado['nome'], 'GeoLite2-City.mmdb')
        self.assertIsNotNone(estado['build'])
        self.assertIsNone(estado['erro'])

    @patch('chat.geoip.metadados_mmdb', return_value=('GeoLite2-City', BUILD))
    def test_cria_a_pasta_quando_falta(self, meta):
        novo = Path(self.pasta.name) / 'geoip' / 'GeoLite2-City.mmdb'
        with override_settings(GEOIP_CIDADE_MMDB=str(novo)):
            instalar_arquivo(
                'GeoLite2-City.mmdb', b'0' * (MMDB_MINIMO + 10),
            )
            self.assertEqual(caminho_mmdb(), novo)
        self.assertTrue(novo.exists())


class RodarScriptTestCase(SimpleTestCase):
    """O painel executa o script oficial; erro do script vira mensagem."""

    @override_settings(BASE_DIR='/tmp/nao-existe-cnpj-geoip')
    def test_script_ausente(self):
        with self.assertRaisesMessage(GeoIPIndisponivel, 'atualizar_geoip.sh'):
            rodar_script()

    @patch('chat.geoip.subprocess.run')
    def test_sucesso_devolve_a_saida(self, run):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout='situacao: base ja atualizada\n',
            stderr='',
        )
        ok, saida = rodar_script('--checar')
        self.assertTrue(ok)
        self.assertEqual(saida, 'situacao: base ja atualizada')
        self.assertEqual(run.call_args.args[0][-1], '--checar')

    @patch('chat.geoip.subprocess.run')
    def test_erro_do_script_nao_levanta(self, run):
        run.return_value = SimpleNamespace(
            returncode=1, stdout='', stderr='HTTP 401 nao autorizado',
        )
        ok, saida = rodar_script()
        self.assertFalse(ok)
        self.assertIn('401', saida)

    @patch(
        'chat.geoip.subprocess.run',
        side_effect=subprocess.TimeoutExpired('bash', 5),
    )
    def test_timeout_vira_erro_claro(self, run):
        with self.assertRaisesMessage(GeoIPIndisponivel, 'interrompida'):
            rodar_script(timeout=5)

    @patch('chat.geoip.subprocess.run')
    def test_bash_do_sistema_entra_no_path_do_script(self, run):
        """
        Os .service de producao definem `Environment=PATH=` so com o venv/bin
        e o systemd substitui o PATH: sem sanear, `bash`, `curl` e `tar` nao
        existem dentro do gunicorn e o painel devolvia 500 no servidor.
        """
        run.return_value = SimpleNamespace(
            returncode=0, stdout='remoto: x', stderr='',
        )
        rodar_script('--checar')
        ambiente = run.call_args.kwargs['env']
        caminhos = ambiente['PATH'].split(os.pathsep)
        self.assertEqual(
            caminhos[0], str(Path(settings.BASE_DIR) / 'venv' / 'bin'),
        )
        for pasta in ('/usr/bin', '/bin'):
            self.assertIn(pasta, caminhos)
        self.assertTrue(run.call_args.args[0][0].endswith('bash'))

    @patch('chat.geoip.subprocess.run')
    def test_path_herdado_entra_sem_duplicar(self, run):
        """O PATH do processo continua valendo, sem repetir o venv/bin."""
        run.return_value = SimpleNamespace(
            returncode=0, stdout='remoto: x', stderr='',
        )
        with patch.dict(os.environ, {'PATH': '/opt/extra/bin'}, clear=False):
            rodar_script('--checar')
        caminhos = run.call_args.kwargs['env']['PATH'].split(os.pathsep)
        self.assertIn('/opt/extra/bin', caminhos)
        self.assertEqual(
            caminhos.count(str(Path(settings.BASE_DIR) / 'venv' / 'bin')), 1,
        )

    @patch('chat.geoip.shutil.which', return_value=None)
    def test_sem_bash_vira_erro_claro(self, which):
        """Sem `bash` no PATH, mensagem na tela - nunca erro 500."""
        with self.assertRaisesMessage(GeoIPIndisponivel, 'bash'):
            rodar_script('--checar')

    @patch('chat.geoip.subprocess.run', side_effect=FileNotFoundError('bash'))
    def test_execucao_impossivel_vira_erro_claro(self, run):
        """FileNotFoundError do `subprocess.run` nao pode virar 500."""
        with self.assertRaisesMessage(GeoIPIndisponivel, 'executar'):
            rodar_script('--checar')


class PainelGeoIPTestCase(TestCase):
    """
    As tres acoes do painel: quem pode, o que fazem e o que aparece na tela.

    `rodar_script` e `instalar_arquivo` sao mockados: nenhum teste baixa da
    MaxMind nem escreve em geoip/.
    """

    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'admin_geo', 'admin_geo@example.com', 'senha-teste',
        )
        self.staff = User.objects.create_user(
            'staff_geo', 'staff_geo@example.com', 'senha-teste', is_staff=True,
        )
        self.url_checar = reverse('admin:chat_logacesso_geoip_checar')
        self.url_baixar = reverse('admin:chat_logacesso_geoip_baixar')
        self.url_enviar = reverse('admin:chat_logacesso_geoip_enviar')

    def test_anonimo_vai_para_o_login(self):
        resposta = self.client.post(self.url_checar)
        self.assertEqual(resposta.status_code, 302)
        self.assertIn('/admin/login/', resposta['Location'])

    def test_staff_sem_superusuario_recebe_403(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url_baixar).status_code, 403)

    def test_get_avisa_que_a_acao_e_post(self):
        self.client.force_login(self.superusuario)
        resposta = self.client.get(self.url_checar, follow=True)
        self.assertContains(resposta, 'só roda pelo botão')

    @patch(
        'chat.admin.rodar_script', return_value=(True, 'Base ja atualizada'),
    )
    def test_checar_mostra_a_saida_do_script(self, rodar):
        self.client.force_login(self.superusuario)
        resposta = self.client.post(self.url_checar, follow=True)
        # O admin aplica |capfirst na mensagem: comparar sem depender disso
        self.assertContains(resposta, 'ja atualizada')
        rodar.assert_called_once_with('--checar')

    @patch('chat.admin.rodar_script', return_value=(True, 'base trocada'))
    def test_baixar_sem_forcar(self, rodar):
        self.client.force_login(self.superusuario)
        self.client.post(self.url_baixar, follow=True)
        rodar.assert_called_once_with()

    @patch('chat.admin.rodar_script', return_value=(True, 'base trocada'))
    def test_baixar_com_forcar(self, rodar):
        self.client.force_login(self.superusuario)
        self.client.post(self.url_baixar, {'forcar': '1'}, follow=True)
        rodar.assert_called_once_with('--forcar')

    @patch('chat.admin.rodar_script', return_value=(True, 'base trocada'))
    def test_baixar_registra_no_log_do_admin(self, rodar):
        self.client.force_login(self.superusuario)
        self.client.post(self.url_baixar, follow=True)
        self.assertTrue(
            LogEntry.objects.filter(
                object_repr='Base de localizacao (GeoIP)',
            ).exists()
        )

    @patch(
        'chat.admin.rodar_script',
        side_effect=GeoIPIndisponivel('Script nao encontrado'),
    )
    def test_falha_do_script_aparece_na_tela(self, rodar):
        self.client.force_login(self.superusuario)
        resposta = self.client.post(self.url_baixar, follow=True)
        self.assertContains(resposta, 'Script nao encontrado')

    def test_enviar_sem_arquivo_avisa(self):
        self.client.force_login(self.superusuario)
        resposta = self.client.post(self.url_enviar, follow=True)
        self.assertContains(resposta, 'Escolha o arquivo')

    @patch('chat.admin.instalar_arquivo')
    def test_enviar_instala_o_pacote(self, instalar):
        instalar.return_value = {
            'nome': 'GeoLite2-City.mmdb', 'tamanho_mb': 48.0,
            'build': '06/11/2026', 'fonte': 'maxmind', 'erro': None,
        }
        self.client.force_login(self.superusuario)
        pacote = SimpleUploadedFile(NOME_PACOTE, b'pacote', 'application/gzip')
        resposta = self.client.post(
            self.url_enviar, {'arquivo': pacote}, follow=True,
        )
        self.assertContains(resposta, 'Base instalada')
        instalar.assert_called_once()
        self.assertEqual(instalar.call_args.args[0], NOME_PACOTE)

    @patch(
        'chat.admin.instalar_arquivo',
        side_effect=ValueError('Pacote .zip (CSV) não serve'),
    )
    def test_enviar_arquivo_recusado_explica(self, instalar):
        self.client.force_login(self.superusuario)
        zipado = SimpleUploadedFile(
            'GeoLite2-City-CSV_20261106.zip', b'PK', 'application/zip',
        )
        resposta = self.client.post(
            self.url_enviar, {'arquivo': zipado}, follow=True,
        )
        self.assertContains(resposta, 'Não instalei o arquivo')

    def test_painel_mostra_o_bloco_e_os_botoes(self):
        self.client.force_login(self.superusuario)
        resposta = self.client.get(
            reverse('admin:chat_logacesso_changelist'),
        )
        self.assertContains(resposta, 'Base de localização (GeoIP)')
        self.assertContains(resposta, 'Checar atualização')
        self.assertContains(resposta, 'Baixar da MaxMind e atualizar')
        self.assertContains(resposta, 'Instalar arquivo enviado')
