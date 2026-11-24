"""
Formatos compactos de data/hora (pt-BR).

O Django procura primeiro os modulos de FORMAT_MODULE_PATH e so' depois o
locale padrao; por isso definir DATE_FORMAT em settings nao bastava (o admin
continuava em "24 de Novembro de 2026").
"""

DATE_FORMAT = 'd/m/Y'
SHORT_DATE_FORMAT = 'd/m/Y'
DATETIME_FORMAT = 'd/m/Y H:i'
SHORT_DATETIME_FORMAT = 'd/m/Y H:i'
TIME_FORMAT = 'H:i'
MONTH_DAY_FORMAT = 'd/m'
YEAR_MONTH_FORMAT = 'm/Y'

DATE_INPUT_FORMATS = ['%d/%m/%Y', '%Y-%m-%d']
DATETIME_INPUT_FORMATS = ['%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
