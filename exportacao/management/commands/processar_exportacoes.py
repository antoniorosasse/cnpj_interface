# exportacao/management/commands/processar_exportacoes.py
"""Processa exportacoes completas pendentes (fallback de producao)."""
import time

from django.core.management.base import BaseCommand

from exportacao.async_export import processar_job
from exportacao.models import Exportacao


class Command(BaseCommand):
    help = 'Processa exportacoes completas pendentes.'

    def add_arguments(self, parser):
        parser.add_argument('--uma', action='store_true',
                            help='processa no maximo uma exportacao')
        parser.add_argument('--esperar', type=int, default=0,
                            help='intervalo (s) para polling continuo')

    def handle(self, *args, **options):
        while True:
            job = (Exportacao.objects
                   .filter(completo=True, status='pendente')
                   .order_by('criado_em').first())
            if job is None:
                if options['esperar']:
                    time.sleep(options['esperar'])
                    continue
                self.stdout.write('Nenhuma exportacao pendente.')
                return
            self.stdout.write(f'Processando {job.pk}...')
            processar_job(job.pk)
            if options['uma']:
                return
