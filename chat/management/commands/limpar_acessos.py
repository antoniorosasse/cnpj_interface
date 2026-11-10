# cnpj_interface/chat/management/commands/limpar_acessos.py
"""
Apaga logs de acesso (chat.LogAcesso) mais antigos que N dias.

Uso:
    python manage.py limpar_acessos --dias 90 --dry-run
    python manage.py limpar_acessos --dias 90 --tipo login --sim

Sem `--sim` o comando nao apaga nada: mostra a contagem e para. E' a trava
contra um `--dias` errado. Idade minima aceita: chat.manutencao.MIN_DIAS.

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.core.management.base import BaseCommand, CommandError

from chat.manutencao import MIN_DIAS, contar_acessos, limpar_acessos
from chat.models import LogAcesso


class Command(BaseCommand):
    help = f'Apaga logs de acesso mais antigos que N dias (minimo {MIN_DIAS})'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias',
            type=int,
            default=90,
            help='Apaga logs com mais de N dias (padrao: 90)',
        )
        parser.add_argument(
            '--tipo',
            default=None,
            choices=[valor for valor, _ in LogAcesso.TIPO_CHOICES],
            help='Limita o expurgo a um tipo de evento',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas conta e mostra por tipo, sem apagar',
        )
        parser.add_argument(
            '--sim',
            action='store_true',
            help='Confirma a exclusao (sem esta flag, nada e apagado)',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        tipo = options['tipo']

        try:
            resumo = contar_acessos(dias=dias, tipo=tipo)
        except ValueError as erro:
            raise CommandError(str(erro))

        self.stdout.write(
            f'Logs mais antigos que {dias} dias '
            f'(antes de {resumo["antes_de"]:%d/%m/%Y %H:%M}): {resumo["total"]}'
        )
        for linha in resumo['por_tipo']:
            self.stdout.write(f'  - {linha["tipo"]}: {linha["total"]}')

        if not resumo['total']:
            self.stdout.write(self.style.SUCCESS('Nada a apagar.'))
            return

        if options['dry_run'] or not options['sim']:
            motivo = '--dry-run' if options['dry_run'] else 'sem --sim'
            self.stdout.write(self.style.WARNING(
                f'[NADA FOI APAGADO] {motivo}: rode com --sim para apagar.'
            ))
            return

        resultado = limpar_acessos(dias=dias, tipo=tipo)
        self.stdout.write(self.style.SUCCESS(
            f'Apagados {resultado["removidos"]} log(s) de acesso.'
        ))
