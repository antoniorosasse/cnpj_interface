# cnpj_interface/chat/management/commands/localizar_acessos.py
"""
Localiza (cidade/UF/pais) os IPs que aparecem no log de acesso.

Uso:
    python manage.py localizar_acessos --dry-run
    python manage.py localizar_acessos --dias 30 --limite 500

Grava em chat.LocalizacaoIP. A fonte vem de settings.GEOIP_FONTE
(env GEOIP_FONTE): 'maxmind' (padrao: GeoLite2-City.mmdb local, sem rede),
'ip-api' (lote, HTTP) ou 'ipapi' (HTTPS, 1 IP por requisicao).
Com 'maxmind' nenhum IP sai do servidor; as outras duas fazem chamadas
externas (rode de proposito, nao em loop de cron apertado).

Sem acentos no codigo. Comentarios em PT-BR sem acento.
"""

from django.core.management.base import BaseCommand, CommandError

from chat.geoip import (
    LIMITE_PADRAO,
    REVALIDAR_PADRAO,
    GeoIPIndisponivel,
    localizar_pendentes,
    verificar_fonte,
)


class Command(BaseCommand):
    help = 'Localiza os IPs do log de acesso (cidade/UF/pais)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias',
            type=int,
            default=30,
            help='So IPs com acesso nos ultimos N dias (padrao: 30; 0 = todos)',
        )
        parser.add_argument(
            '--limite',
            type=int,
            default=LIMITE_PADRAO,
            help=f'Maximo de IPs por execucao (padrao: {LIMITE_PADRAO})',
        )
        parser.add_argument(
            '--revalidar-dias',
            type=int,
            default=REVALIDAR_PADRAO,
            help=(
                'Reconsulta localizacao mais velha que N dias '
                f'(padrao: {REVALIDAR_PADRAO}; 0 = nunca)'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='So lista os IPs pendentes, sem consultar a fonte externa',
        )

    def handle(self, *args, **options):
        try:
            fonte = verificar_fonte()
        except GeoIPIndisponivel as erro:
            raise CommandError(str(erro)) from erro

        resumo = localizar_pendentes(
            dias=options['dias'] or None,
            limite=options['limite'],
            revalidar_dias=options['revalidar_dias'],
            dry_run=options['dry_run'],
            fonte=fonte,
        )

        self.stdout.write(
            f'Fonte: {resumo["fonte"]} | IPs pendentes: {resumo["pendentes"]}'
        )
        if options['dry_run']:
            for ip in resumo['ips'][:20]:
                self.stdout.write(f'  - {ip}')
            if resumo['pendentes'] > 20:
                self.stdout.write(f'  ... e mais {resumo["pendentes"] - 20}.')
            self.stdout.write(self.style.WARNING(
                '[DRY-RUN] Nenhuma consulta externa foi feita.'
            ))
            return

        if not resumo['pendentes']:
            self.stdout.write(self.style.SUCCESS('Nada a localizar.'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'Localizados {resumo["gravados"]} IP(s); '
            f'sem dados em {resumo["sem_dados"]}.'
        ))