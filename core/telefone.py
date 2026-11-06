# /home/neto/cnpj_interface/core/telefone.py
"""
Link de WhatsApp a partir do telefone da Receita Federal.

A base da RFB guarda o telefone com 8 digitos (sem o nono digito) no campo
tel_1/tel_2 e o DDD em campo separado. Celular comeca com 9; fixo comeca com
2, 3, 4 ou 5 e nao tem WhatsApp.

Regra adotada: so gera link quando o numero parece celular (8 ou 9 digitos
comecando com 9) e o DDD e valido (11 a 99). Nos outros casos devolve string
vazia e o template nao mostra o botao — melhor nao mostrar do que abrir uma
conversa com o numero errado.

Exemplos:
    montar_whatsapp_url('41', '99998888')  -> 'https://wa.me/554199998888'
    montar_whatsapp_url('41', '33334444')  -> ''  (fixo)
"""
from urllib.parse import quote

DDD_MIN, DDD_MAX = 11, 99
PREFIXO_CELULAR = '9'
CODIGO_BRASIL = '55'


def _digitos(valor) -> str:
    """Mantem so os digitos de um campo (que pode vir None, int ou str)."""
    return ''.join(c for c in str(valor or '') if c.isdigit())


def montar_whatsapp_url(ddd, telefone, mensagem: str = '') -> str:
    """
    Link wa.me do celular, ou '' quando o telefone nao serve.

    mensagem vira o texto pre-preenchido (?text=), opcional.
    """
    ddd = _digitos(ddd)
    telefone = _digitos(telefone)

    if len(ddd) != 2 or not DDD_MIN <= int(ddd) <= DDD_MAX:
        return ''
    if len(telefone) not in (8, 9) or not telefone.startswith(PREFIXO_CELULAR):
        return ''

    url = f'https://wa.me/{CODIGO_BRASIL}{ddd}{telefone}'
    if mensagem:
        url += '?text=' + quote(mensagem)
    return url


def montar_whatsapp(ddd_1, tel_1, ddd_2='', tel_2='', mensagem: str = '') -> str:
    """Primeiro telefone que for celular (tel_1, depois tel_2). '' se nenhum."""
    for ddd, telefone in ((ddd_1, tel_1), (ddd_2, tel_2)):
        url = montar_whatsapp_url(ddd, telefone, mensagem)
        if url:
            return url
    return ''
