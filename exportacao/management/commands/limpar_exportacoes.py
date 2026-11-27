"""
Expurgo de exportacoes e contadores antigos.

Uso:
    python manage.py limpar_exportacoes --dias 180 --dry-run
    python manage.py limpar_exportacoes --dias 180

- Exportacao 'pendente'/'falhou' mais velha que o prazo e' removida;
- 'baixada' mais velha que o prazo tambem (o arquivo nao e' guardado, so' o registro);
- PesquisaAnonima com dia anterior ao corte e' removida (contador diario).
"""

from datetime import timedelta
from pathlib import Path

from django.conf import settings

from django.core.management.base import BaseCommand
from django.utils import timezone

from exportacao.models import Exportacao, PesquisaAnonima


class Command(BaseCommand):
    help = 'Remove exportacoes e contadores de pesquisa antigos.'

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=180)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opcoes):
        dias = opcoes['dias']
        corte = timezone.now() - timedelta(days=dias)
        dia_corte = timezone.localdate() - timedelta(days=dias)

        exportacoes = Exportacao.objects.filter(criado_em__lt=corte)
        pesquisas = PesquisaAnonima.objects.filter(dia__lt=dia_corte)

        if opcoes['dry_run']:
            self.stdout.write(f'[dry-run] exportacoes: {exportacoes.count()}')
            self.stdout.write(f'[dry-run] pesquisas: {pesquisas.count()}')
            return

        # Remove tambem os arquivos temporarios das exportacoes completas.
        pasta = Path(getattr(settings, 'EXPORTACAO_COMPLETA_DIR',
                             Path(settings.BASE_DIR) / 'exportacoes'))
        for job in exportacoes.filter(completo=True).exclude(arquivo_nome=''):
            try:
                (pasta / job.arquivo_nome).unlink(missing_ok=True)
            except OSError:
                pass
        removidas = exportacoes.delete()[0]
        removidas_p = pesquisas.delete()[0]
        self.stdout.write(self.style.SUCCESS(
            f'exportacoes removidas: {removidas} | pesquisas removidas: {removidas_p}'))
