# cnpj_interface/chat/management/commands/propor_aliases_cnae.py
"""Gera candidatos de alias CNAE a partir do AtividadeLog (Fase 1).

Nao altera o comportamento do chat nem aprova nada: apenas agrega os termos
observados e cria/atualiza linhas em CnaeAliasCandidato com status pendente.
"""
from collections import Counter, defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.atividade_log import normalizar_termo
from chat.models import AtividadeLog, CnaeAliasCandidato


class Command(BaseCommand):
    help = 'Agrupa AtividadeLog e gera candidatos de alias CNAE (nao aprova).'

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=30,
                            help='janela de analise (default: 30)')
        parser.add_argument('--min-ocorrencias', type=int, default=3,
                            help='minimo de consultas para virar candidato')
        parser.add_argument('--dry-run', action='store_true',
                            help='nao grava candidatos, so mostra o resumo')

    def handle(self, *args, **options):
        desde = timezone.now() - timedelta(days=max(1, options['dias']))
        logs = AtividadeLog.objects.filter(criado_em__gte=desde)
        grupos = defaultdict(list)
        for log in logs.iterator():
            chave = log.termo_normalizado or normalizar_termo(log.termo)
            if chave:
                grupos[chave].append(log)

        criados = atualizados = ignorados = 0
        for chave, itens in sorted(grupos.items()):
            if len(itens) < options['min_ocorrencias']:
                ignorados += 1
                continue
            with_cnae = [i for i in itens if i.cnaes]
            if not with_cnae:
                ignorados += 1
                continue

            contagem_termos = Counter(i.termo for i in itens)
            termo = contagem_termos.most_common(1)[0][0]
            contagem_cnaes = Counter(tuple(sorted(i.cnaes)) for i in with_cnae)
            cnaes = list(contagem_cnaes.most_common(1)[0][0])
            contagem_categorias = Counter(
                tuple(i.categorias) for i in itens if i.categorias)
            categorias = (list(contagem_categorias.most_common(1)[0][0])
                          if contagem_categorias else [])
            fontes = dict(Counter(i.fonte for i in itens))
            resultados = [i.resultados for i in itens
                          if i.resultados is not None]
            media = (sum(resultados) / len(resultados)) if resultados else None
            scores = [i.score for i in itens if i.score is not None]
            score_medio = (sum(scores) / len(scores)) if scores else None

            candidato = CnaeAliasCandidato.objects.filter(
                termo_normalizado=chave).first()
            if candidato and candidato.status != CnaeAliasCandidato.STATUS_PENDENTE:
                ignorados += 1
                continue

            dados = {
                'termo': termo[:200],
                'cnaes': cnaes,
                'categorias': categorias,
                'fontes': fontes,
                'ocorrencias': len(itens),
                'resultados_medios': media,
                'score_medio': score_medio,
                # Evidencia mudou: a avaliacao anterior da IA nao vale mais.
                'decisao_ia': '',
                'confianca_ia': None,
                'justificativa_ia': '',
                'cnaes_ia': [],
                'evidencia': {
                    'termos': dict(contagem_termos),
                    'cnaes': {','.join(map(str, k)): v
                              for k, v in contagem_cnaes.items()},
                    'janela_dias': options['dias'],
                },
            }
            if options['dry_run']:
                self.stdout.write(
                    f'[dry] {termo!r} -> {cnaes} ({len(itens)}x, {fontes})')
                criados += 1
                continue
            if candidato:
                for campo, valor in dados.items():
                    setattr(candidato, campo, valor)
                candidato.save()
                atualizados += 1
            else:
                CnaeAliasCandidato.objects.create(
                    termo_normalizado=chave, **dados)
                criados += 1

        self.stdout.write(self.style.SUCCESS(
            f'candidatos: {criados} criados/previstos, {atualizados} '
            f'atualizados, {ignorados} ignorados'))
