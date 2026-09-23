# cnpj_interface/chat/management/commands/limpar_conversas_antigas.py
"""
Remove conversas mais antigas que N dias.

Uso:
    python manage.py limpar_conversas_antigas          # padrao: 30 dias
    python manage.py limpar_conversas_antigas --dias 90
    python manage.py limpar_conversas_antigas --dry-run

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import Conversa


class Command(BaseCommand):
    help = 'Remove conversas mais antigas que N dias (padrao: 30)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias',
            type=int,
            default=30,
            help='Idade minima em dias para remover (padrao: 30)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas mostra o que seria removido, sem apagar',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        dry_run = options['dry_run']

        limite = timezone.now() - timedelta(days=dias)

        alvo = Conversa.objects.filter(atualizado_em__lt=limite)
        total = alvo.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                f'Nenhuma conversa mais antiga que {dias} dias.'
            ))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'[DRY-RUN] {total} conversa(s) seriam removidas '
                f'(mais antigas que {dias} dias).'
            ))
            for c in alvo[:10]:
                self.stdout.write(f'  - #{c.pk} {c.titulo[:60]} ({c.atualizado_em:%Y-%m-%d})')
            if total > 10:
                self.stdout.write(f'  ... e mais {total - 10}.')
            return

        removidas, _ = alvo.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Removidas {removidas} conversa(s) mais antigas que {dias} dias.'
        ))