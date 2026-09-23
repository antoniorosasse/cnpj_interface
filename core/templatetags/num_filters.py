# /home/neto/cnpj_interface/core/templatetags/num_filters.py
from django import template

register = template.Library()

@register.filter
def format_number(value):
    """Formata números com separadores de milhar (ex: 3476166 → 3.476.166)"""
    if value is None:
        return '0'
    try:
        # Converte para inteiro e formata
        num = int(value)
        # Formata com separadores de milhar (ponto para milhar)
        return f"{num:,}".replace(',', '.')
    except (ValueError, TypeError):
        return str(value)

@register.filter
def format_number_br(value):
    """Formata números com separadores de milhar no padrão brasileiro"""
    if value is None:
        return '0'
    try:
        num = int(value)
        # Formata com separadores de milhar
        partes = f"{num:,}".split(',')
        return '.'.join(partes)
    except (ValueError, TypeError):
        return str(value)