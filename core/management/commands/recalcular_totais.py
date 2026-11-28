# /home/neto/cnpj_interface/core/management/commands/recalcular_totais.py
"""
Recalcula c_estatisticas_totais fora do request.

A stored procedure troca as tabelas por RENAME (a pagina nunca fica sem
dados), mas o calculo leva ~24 min na base de 74,4 M de estabelecimentos -
acima do timeout de 300 s do gunicorn/nginx. A view /estatisticas/ dispara
este comando em background e acompanha o status em c_estatisticas_recalculo.

    python manage.py recalcular_totais
    python manage.py recalcular_totais --status
"""
import time

from django.core.management.base import BaseCommand, CommandError

from core.estatisticas import LOCK_KEY, obter_conexao, status_recalculo

RODANDO_SQL = """
    UPDATE c_estatisticas_recalculo
       SET status = 'rodando',
           iniciado_em = now(),
           terminado_em = NULL,
           duracao_s = NULL,
           linhas = NULL,
           pid = pg_backend_pid(),
           mensagem = NULL
     WHERE id = 1
"""

OK_SQL = """
    UPDATE c_estatisticas_recalculo
       SET status = 'ok',
           terminado_em = now(),
           duracao_s = %s,
           linhas = %s,
           pid = NULL,
           mensagem = NULL
     WHERE id = 1
"""

ERRO_SQL = """
    UPDATE c_estatisticas_recalculo
       SET status = 'erro',
           terminado_em = now(),
           pid = NULL,
           mensagem = %s
     WHERE id = 1
"""


class Command(BaseCommand):
    help = ('Recalcula c_estatisticas_totais com troca de tabela '
            '(~24 min na base de 74,4 M; rodar fora do request).')

    def add_arguments(self, parser):
        parser.add_argument(
            '--status',
            action='store_true',
            help='apenas mostra o status do ultimo recalculo',
        )

    def handle(self, *args, **options):
        if options['status']:
            self._mostrar_status()
            return

        try:
            conn = obter_conexao()
        except Exception as exc:
            raise CommandError(f'Nao foi possivel conectar ao bancocnpj: {exc}')

        try:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_try_advisory_lock(hashtext(%s))', (LOCK_KEY,))
                if not cur.fetchone()[0]:
                    raise CommandError(
                        'Ja existe um recalculo em andamento (advisory lock ocupado).'
                    )
                # A SP leva ~24 min: sem statement_timeout. O swap pega
                # ACCESS EXCLUSIVE por milissegundos - se algum leitor segurar
                # a tabela, aborta em 10 s e a versao anterior continua valendo.
                cur.execute('SET statement_timeout = 0')
                cur.execute("SET lock_timeout = '10s'")
                cur.execute(RODANDO_SQL)
            conn.commit()

            inicio = time.monotonic()
            with conn.cursor() as cur:
                cur.execute('CALL sp_recalcular_estatisticas_totais()')
            conn.commit()
            duracao = round(time.monotonic() - inicio, 2)

            with conn.cursor() as cur:
                cur.execute('SELECT count(*) FROM c_estatisticas_totais')
                linhas = cur.fetchone()[0]
                cur.execute(OK_SQL, (duracao, linhas))
            conn.commit()

            self.stdout.write(self.style.SUCCESS(
                f'Totais recalculados em {duracao:.1f}s '
                f'({linhas} linhas em c_estatisticas_totais).'
            ))
        except CommandError:
            conn.rollback()
            raise
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            self._registrar_erro(exc)
            raise CommandError(f'Falha no recalculo dos totais: {exc}')
        finally:
            conn.close()

    def _registrar_erro(self, exc):
        """Grava status='erro' numa conexao nova (a anterior pode estar morta)."""
        try:
            conn = obter_conexao()
            try:
                with conn.cursor() as cur:
                    cur.execute(ERRO_SQL, (str(exc)[:500],))
                conn.commit()
            finally:
                conn.close()
        except Exception as erro_secundario:
            self.stderr.write(
                f'Nao foi possivel gravar o status de erro: {erro_secundario}'
            )

    def _mostrar_status(self):
        status = status_recalculo()
        if not status:
            self.stdout.write(
                'Sem status: a tabela c_estatisticas_recalculo nao existe '
                '(rode sql/estatisticas_totais.sql) ou a consulta falhou.'
            )
            return

        self.stdout.write(f"status: {status['status']}")
        if status['iniciado_em']:
            self.stdout.write(f"iniciado: {status['iniciado_em']:%d/%m/%Y %H:%M:%S}")
        if status['terminado_em']:
            self.stdout.write(f"terminado: {status['terminado_em']:%d/%m/%Y %H:%M:%S}")
        if status['duracao_s'] is not None:
            self.stdout.write(f"duracao: {status['duracao_s']} s")
        if status['linhas'] is not None:
            self.stdout.write(f"linhas: {status['linhas']}")
        if status['ativo']:
            self.stdout.write('existe recalculo em andamento')
        if status['mensagem']:
            self.stdout.write(f"mensagem: {status['mensagem']}")
