from django.db import migrations


TIPOS = {'pesquisa': 'lista', 'pagina': 'lista', 'exportacao': 'exportacao',
         'chat': 'chat', 'lista': 'lista'}


def normaliza(apps, schema_editor):
    """
    Converte as chaves antigas ('pesquisa:abc', 'pagina:abc', ...) em
    (visitante='abc', tipo='lista'|'chat'|'exportacao').

    Duas chaves antigas podem virar a mesma (pesquisa:abc e pagina:abc):
    nesse caso os totais sao somados para nao violar a unicidade nova.
    """
    Pesquisa = apps.get_model('exportacao', 'PesquisaAnonima')
    for registro in list(Pesquisa.objects.all().order_by('pk')):
        visitante = registro.visitante or ''
        prefixo, _, resto = visitante.partition(':')
        if resto and prefixo in TIPOS:
            tipo, identidade = TIPOS[prefixo], resto
        else:
            tipo, identidade = 'lista', visitante

        existente = Pesquisa.objects.filter(
            visitante=identidade, tipo=tipo, dia=registro.dia
        ).exclude(pk=registro.pk).first()

        if existente:
            existente.total += registro.total
            existente.save(update_fields=['total'])
            registro.delete()
        else:
            registro.visitante = identidade
            registro.tipo = tipo
            registro.save(update_fields=['visitante', 'tipo'])


class Migration(migrations.Migration):
    dependencies = [('exportacao', '0005_configuracaouso_limiteusuario_and_more')]
    operations = [migrations.RunPython(normaliza, migrations.RunPython.noop)]
