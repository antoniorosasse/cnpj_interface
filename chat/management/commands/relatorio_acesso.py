# cnpj_interface/chat/management/commands/relatorio_acesso.py
"""
Imprime um relatorio dos logs de acesso (chat.LogAcesso).

Cobre eventos do chat e da consulta de empresas:
  login, chat_pergunta, empresas_lista, empresas_detalhe.

Os numeros vem de chat/relatorio.py (resumo_acesso), a mesma funcao que
alimenta o painel do admin (/admin/chat/logacesso/) - comando e tela mostram
sempre a mesma coisa.

Uso:
    python manage.py relatorio_acesso                 # ultimos 7 dias
    python manage.py relatorio_acesso --dias 30
    python manage.py relatorio_acesso --tipo login
    python manage.py relatorio_acesso --tipo chat_pergunta
    python manage.py relatorio_acesso --tipo empresas_lista
    python manage.py relatorio_acesso --dias 1        # so hoje

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.core.management.base import BaseCommand

from chat.models import LogAcesso
from chat.relatorio import resumo_acesso


class Command(BaseCommand):
    help = 'Relatorio de logs de acesso (login, chat e consulta de empresas)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias',
            type=int,
            default=7,
            help='Periodo do relatorio, em dias (padrao: 7)',
        )
        parser.add_argument(
            '--tipo',
            choices=[valor for valor, _ in LogAcesso.TIPO_CHOICES],
            default=None,
            help='Filtra por tipo (padrao: todos)',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        tipo_filtro = options['tipo']

        resumo = resumo_acesso(dias=dias, tipo=tipo_filtro)

        # ----- Cabecalho -----
        self.stdout.write('=' * 60)
        self.stdout.write(f'Relatorio de acesso - ultimos {dias} dia(s)')
        self.stdout.write('=' * 60)
        if tipo_filtro:
            self.stdout.write(f'Filtro de tipo: {tipo_filtro}')
        self.stdout.write('')

        # ----- Totais por tipo -----
        self.stdout.write('--- Totais por tipo ---')
        if resumo['total_por_tipo']:
            for row in resumo['total_por_tipo']:
                self.stdout.write(f'  {row["tipo"]:20s} {row["total"]:>6}')
        else:
            self.stdout.write('  (sem registros no periodo)')
        self.stdout.write('')

        # ----- Por dia -----
        self.stdout.write('--- Por dia ---')
        if resumo['por_dia']:
            # Colunas apenas para os tipos que apareceram no periodo
            tipos_presentes = resumo['tipos_presentes']
            self.stdout.write(
                '  ' + f'{"data":12s}' + ''.join(f'{tipo:>20s}' for tipo in tipos_presentes)
            )
            for dia in resumo['dias_ordenados']:
                linha = '  ' + f'{dia.strftime("%Y-%m-%d"):12s}'
                for tipo in tipos_presentes:
                    linha += f'{resumo["por_dia"][dia].get(tipo, 0):>20d}'
                self.stdout.write(linha)
        else:
            self.stdout.write('  (sem registros no periodo)')
        self.stdout.write('')

        # ----- Usuarios distintos -----
        self.stdout.write('--- Usuarios distintos ---')
        self.stdout.write(f'  Usuarios que logaram:       {resumo["usuarios"]["logaram"]}')
        self.stdout.write(f'  Usuarios que perguntaram:   {resumo["usuarios"]["perguntaram"]}')
        self.stdout.write(f'  Logados que viram empresas: {resumo["usuarios"]["viram_empresas"]}')
        self.stdout.write('')

        # ----- Top IPs (pergunta) -----
        self.stdout.write('--- Top 10 IPs (perguntas no chat) ---')
        if resumo['top_ips_chat']:
            for row in resumo['top_ips_chat']:
                self.stdout.write(f'  {row["ip"]:20s} {row["total"]:>6}')
        else:
            self.stdout.write('  (sem IPs registrados)')
        self.stdout.write('')

        # ----- Top IPs (empresas) -----
        self.stdout.write('--- Top 10 IPs (acessos a empresas) ---')
        if resumo['top_ips_empresas']:
            for row in resumo['top_ips_empresas']:
                self.stdout.write(f'  {row["ip"]:20s} {row["total"]:>6}')
        else:
            self.stdout.write('  (sem IPs registrados)')
        self.stdout.write('')

        self.stdout.write('=' * 60)
