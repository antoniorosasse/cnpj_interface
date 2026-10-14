# cnpj_interface/chat/management/commands/relatorio_acesso.py
"""
Imprime um relatorio dos logs de acesso (chat.LogAcesso).

Uso:
    python manage.py relatorio_acesso                 # ultimos 7 dias
    python manage.py relatorio_acesso --dias 30
    python manage.py relatorio_acesso --tipo login
    python manage.py relatorio_acesso --tipo chat_pergunta
    python manage.py relatorio_acesso --dias 1        # so hoje

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from chat.models import LogAcesso


class Command(BaseCommand):
    help = 'Relatorio de logs de acesso (logins + perguntas no chat)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias',
            type=int,
            default=7,
            help='Periodo do relatorio, em dias (padrao: 7)',
        )
        parser.add_argument(
            '--tipo',
            choices=[LogAcesso.TIPO_LOGIN, LogAcesso.TIPO_CHAT_PERGUNTA],
            default=None,
            help='Filtra por tipo (padrao: todos)',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        tipo_filtro = options['tipo']

        desde = timezone.now() - timedelta(days=dias)

        qs = LogAcesso.objects.filter(criado_em__gte=desde)
        if tipo_filtro:
            qs = qs.filter(tipo=tipo_filtro)

        # ----- Cabecalho -----
        self.stdout.write('=' * 60)
        self.stdout.write(f'Relatorio de acesso - ultimos {dias} dia(s)')
        self.stdout.write('=' * 60)
        if tipo_filtro:
            self.stdout.write(f'Filtro de tipo: {tipo_filtro}')
        self.stdout.write('')

        # ----- Totais por tipo -----
        self.stdout.write('--- Totais por tipo ---')
        por_tipo = (
            qs.values('tipo')
            .annotate(total=Count('id'))
            .order_by('-total')
        )
        if por_tipo:
            for row in por_tipo:
                self.stdout.write(f'  {row["tipo"]:20s} {row["total"]:>6}')
        else:
            self.stdout.write('  (sem registros no periodo)')
        self.stdout.write('')

        # ----- Por dia -----
        self.stdout.write('--- Por dia ---')
        por_dia = (
            qs.annotate(dia=TruncDate('criado_em'))
            .values('dia', 'tipo')
            .annotate(total=Count('id'))
            .order_by('dia', 'tipo')
        )
        dias_dict: dict = {}
        for row in por_dia:
            dia = row['dia']
            dias_dict.setdefault(dia, {})
            dias_dict[dia][row['tipo']] = row['total']

        if dias_dict:
            self.stdout.write(f'  {"data":12s} {"login":>8s} {"pergunta":>10s}')
            for dia in sorted(dias_dict.keys()):
                login = dias_dict[dia].get(LogAcesso.TIPO_LOGIN, 0)
                pergunta = dias_dict[dia].get(LogAcesso.TIPO_CHAT_PERGUNTA, 0)
                self.stdout.write(f'  {dia.strftime("%Y-%m-%d"):12s} {login:>8d} {pergunta:>10d}')
        else:
            self.stdout.write('  (sem registros no periodo)')
        self.stdout.write('')

        # ----- Usuarios distintos -----
        self.stdout.write('--- Usuarios distintos ---')
        usuarios_login = (
            LogAcesso.objects
            .filter(tipo=LogAcesso.TIPO_LOGIN, criado_em__gte=desde)
            .values('usuario')
            .distinct()
            .count()
        )
        usuarios_pergunta = (
            LogAcesso.objects
            .filter(tipo=LogAcesso.TIPO_CHAT_PERGUNTA, criado_em__gte=desde)
            .values('usuario')
            .distinct()
            .count()
        )
        self.stdout.write(f'  Usuarios que logaram:       {usuarios_login}')
        self.stdout.write(f'  Usuarios que perguntaram:   {usuarios_pergunta}')
        self.stdout.write('')

        # ----- Top IPs (pergunta) -----
        self.stdout.write('--- Top 10 IPs (perguntas no chat) ---')
        top_ips = (
            LogAcesso.objects
            .filter(tipo=LogAcesso.TIPO_CHAT_PERGUNTA, criado_em__gte=desde)
            .exclude(ip__isnull=True)
            .values('ip')
            .annotate(total=Count('id'))
            .order_by('-total')[:10]
        )
        if top_ips:
            for row in top_ips:
                self.stdout.write(f'  {row["ip"]:20s} {row["total"]:>6}')
        else:
            self.stdout.write('  (sem IPs registrados)')
        self.stdout.write('')

        self.stdout.write('=' * 60)
