# cnpj_interface/chat/management/commands/limpar_conversas_antigas.py
"""
Remove conversas mais antigas que N dias.

Funciona com os dois backends (Postgres e MongoDB), dependendo
da flag CHAT_USE_MONGO no .env.

Uso:
    python manage.py limpar_conversas_antigas          # padrao: 30 dias
    python manage.py limpar_conversas_antigas --dias 90
    python manage.py limpar_conversas_antigas --dry-run

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.core.management.base import BaseCommand

from chat import repo


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

        self.stdout.write(f'Backend: {repo.backend_ativo()}')

        alvo = repo.listar_conversas_antigas(dias=dias)
        total = len(alvo)

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
                self.stdout.write(
                    f'  - #{c["id"]} {c["titulo"][:60]} '
                    f'(usuario {c["usuario_id"]})'
                )
            if total > 10:
                self.stdout.write(f'  ... e mais {total - 10}.')
            return

        # Deleta uma por uma (o repo.deletar_conversa exige user)
        removidas = 0
        for c in alvo:
            # Precisamos do objeto user pra deletar; usamos usuario_id
            # direto no Mongo. No Postgres, tambem da pra deletar por pk.
            if repo.deletar_conversa_por_id(c['id']):
                removidas += 1

        self.stdout.write(self.style.SUCCESS(
            f'Removidas {removidas} conversa(s) mais antigas que {dias} dias.'
        ))