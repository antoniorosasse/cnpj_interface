# /home/neto/cnpj_interface/core/estatisticas.py
"""
Apoio ao recalculo de c_estatisticas_totais.

A procedure sp_recalcular_estatisticas_totais() calcula tudo em
c_estatisticas_calc e troca as tabelas por RENAME (lock exclusivo de
milissegundos, a pagina continua servindo o resultado anterior), mas o
calculo ainda leva ~11 minutos na base de 74,4 M de estabelecimentos - acima
do timeout de 300 s do gunicorn e do proxy_read_timeout 300s do nginx.

Por isso o recalculo roda em outro processo (manage.py recalcular_totais) e
a pagina /estatisticas/ so acompanha o status gravado em
c_estatisticas_recalculo.
"""
import logging
import subprocess
import sys
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Chave do advisory lock: impede dois recalculos ao mesmo tempo (o segundo
# ficaria parado no LOCK TABLE do swap).
LOCK_KEY = 'c_estatisticas_totais'

# Um status 'rodando' mais velho que isso esta travado (processo morto,
# kill -9, reboot no meio). A SP leva ~11 min; 30 min e folga.
LIMITE_RODANDO = '30 minutes'

STATUS_SQL = f"""
    SELECT status,
           iniciado_em,
           terminado_em,
           duracao_s,
           linhas,
           mensagem,
           pid,
           (status = 'rodando'
            AND iniciado_em > now() - interval '{LIMITE_RODANDO}') AS ativo
      FROM c_estatisticas_recalculo
     WHERE id = 1
"""


def obter_conexao():
    """Conexao com o bancocnpj (import tardio: evita ciclo com core.views)."""
    from core.views import get_postgres_connection
    return get_postgres_connection()


def status_recalculo(conn=None):
    """
    Le o status do ultimo recalculo.

    Devolve dict, ou None quando a tabela c_estatisticas_recalculo ainda nao
    existe (ex.: producao antes de aplicar sql/estatisticas_totais.sql) ou a
    consulta falhou - a pagina de estatisticas nao pode quebrar por isso.
    """
    propria = conn is None
    if propria:
        conn = obter_conexao()
    try:
        from psycopg2.extras import RealDictCursor

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(STATUS_SQL)
            linha = cur.fetchone()
        return dict(linha) if linha else None
    except Exception as exc:
        logger.warning('Status do recalculo indisponivel: %s', exc)
        if propria:
            try:
                conn.rollback()
            except Exception:
                pass
        return None
    finally:
        if propria:
            conn.close()


def iniciar_recalculo_background():
    """
    Dispara `manage.py recalcular_totais` em outro processo; devolve o pid.

    start_new_session=True desliga o processo do gunicorn, entao o recalculo
    continua mesmo se o worker for reciclado. Usa `sys.executable` (caminho
    absoluto do venv) e o `env=ambiente_script()` do projeto, porque o PATH do
    systemd aponta so para o venv/bin.
    """
    from chat.geoip import ambiente_script  # import tardio: evita ciclo

    base = Path(settings.BASE_DIR)
    log = base / 'logs' / 'recalculo_totais.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    comando = [sys.executable, str(base / 'manage.py'), 'recalcular_totais']
    with open(log, 'ab') as saida:
        proc = subprocess.Popen(
            comando,
            cwd=str(base),
            env=ambiente_script(),
            stdin=subprocess.DEVNULL,
            stdout=saida,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    logger.info('Recalculo disparado em background: pid=%s log=%s', proc.pid, log)
    return proc.pid
