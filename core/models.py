# /home/neto/cnpj_interface/core/models.py
from django.db import models
from django.core.cache import cache

class EmpresaCompleta(models.Model):
    """Modelo baseado na view vw_empresa_completa (READ-ONLY)"""
    
    # CNPJ
    cnpj_completo = models.CharField(max_length=14, primary_key=True)
    cnpj_basico = models.CharField(max_length=8)
    cnpj_ordem = models.CharField(max_length=4)
    cnpj_dv = models.CharField(max_length=2)
    
    # Dados principais
    razao_social = models.CharField(max_length=255)
    nm_fantasia = models.TextField(blank=True, null=True)
    nat_juridica = models.IntegerField(blank=True, null=True)
    natureza_juridica = models.CharField(max_length=255, blank=True, null=True)
    capital_social = models.FloatField(blank=True, null=True)
    porte_empresa = models.FloatField(blank=True, null=True)
    
    # Situação
    cd_sit_cadastro = models.SmallIntegerField(blank=True, null=True)
    dt_sit_cadastro = models.DateField(blank=True, null=True)
    motivo_sit_cadastro = models.CharField(max_length=255, blank=True, null=True)
    data_abertura = models.DateField(blank=True, null=True)
    
    # Endereço
    tip_logradouro = models.CharField(max_length=50, blank=True, null=True)
    logradouro = models.TextField(blank=True, null=True)
    numero = models.CharField(max_length=20, blank=True, null=True)
    complemento = models.TextField(blank=True, null=True)
    bairro = models.TextField(blank=True, null=True)
    cep = models.CharField(max_length=10, blank=True, null=True)
    uf = models.CharField(max_length=10, blank=True, null=True)
    municipio = models.IntegerField(blank=True, null=True)
    nome_municipio = models.CharField(max_length=100, blank=True, null=True)
    cd_pais = models.IntegerField(blank=True, null=True)
    nome_pais = models.CharField(max_length=100, blank=True, null=True)
    cd_matriz_filial = models.SmallIntegerField(blank=True, null=True)
    tipo_estabelecimento = models.CharField(max_length=10)
    
    # Contato
    telefone_1 = models.CharField(max_length=25, blank=True, null=True)
    telefone_2 = models.CharField(max_length=25, blank=True, null=True)
    email = models.TextField(blank=True, null=True)
    
    # Atividade
    cnae_principal = models.IntegerField(blank=True, null=True)
    atividade_principal = models.CharField(max_length=255, blank=True, null=True)
    cnae_secundario = models.TextField(blank=True, null=True)
    
    # Simples/MEI
    op_simples = models.CharField(max_length=1, blank=True, null=True)
    dt_op_simples = models.DateField(blank=True, null=True)
    dt_exc_simples = models.DateField(blank=True, null=True)
    op_mei = models.CharField(max_length=1, blank=True, null=True)
    dt_op_mei = models.DateField(blank=True, null=True)
    dt_exc_mei = models.DateField(blank=True, null=True)
    
    # Metadados
    situacao_descricao = models.CharField(max_length=20)
    
    # Sócios (JSON)
    socios = models.JSONField(blank=True, null=True, default=list)
    total_socios = models.IntegerField(default=0)
    socios_pf = models.IntegerField(default=0)
    socios_pj = models.IntegerField(default=0)
    socios_estrangeiros = models.IntegerField(default=0)
    
    class Meta:
        managed = False
        db_table = 'vw_empresa_completa'
        ordering = ['razao_social']
    
    def __str__(self):
        return f"{self.razao_social} - {self.cnpj_formatado}"
    
    @property
    def cnpj_formatado(self):
        cnpj = self.cnpj_completo.zfill(14)
        return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    
    @property
    def endereco_completo(self):
        partes = []
        if self.tip_logradouro:
            partes.append(self.tip_logradouro)
        if self.logradouro:
            partes.append(self.logradouro)
        if self.numero:
            partes.append(f", {self.numero}")
        if self.bairro:
            partes.append(f" - {self.bairro}")
        if self.nome_municipio:
            partes.append(f", {self.nome_municipio}")
        if self.uf:
            partes.append(f"/{self.uf}")
        if self.cep:
            partes.append(f" - CEP: {self.cep}")
        return ''.join(partes).strip()
    
    @classmethod
    def buscar_empresas(cls, termo='', uf='', situacao='', page=1, limit=20):
        """
        Busca otimizada usando conexão direta (MAIS RÁPIDO)
        """
        import psycopg2
        from psycopg2.extras import RealDictCursor
        from django.conf import settings
        
        conn = psycopg2.connect(
            dbname=settings.PG_DB,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD,
            host=settings.PG_HOST,
            port=settings.PG_PORT
        )
        
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                offset = (page - 1) * limit
                
                # ========== CONSULTA RÁPIDA ==========
                sql = """
                    SELECT 
                        e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj_completo,
                        emp.razao_social,
                        e.nm_fantasia,
                        e.logradouro,
                        e.numero,
                        e.bairro,
                        e.cep,
                        e.uf,
                        e.email,
                        e.ddd_1 || e.tel_1 AS telefone_1,
                        e.cnae_principal,
                        c.nm_cnae AS atividade_principal,
                        e.cd_sit_cadastro,
                        CASE 
                            WHEN e.cd_sit_cadastro = 1 THEN 'ATIVA'
                            WHEN e.cd_sit_cadastro = 2 THEN 'INAPTA'
                            WHEN e.cd_sit_cadastro = 3 THEN 'SUSPENSA'
                            WHEN e.cd_sit_cadastro = 4 THEN 'BAIXADA'
                            ELSE 'OUTRA'
                        END AS situacao_descricao,
                        (
                            SELECT COUNT(*)
                            FROM c_socios s
                            WHERE s.cnpj_basico = e.cnpj_basico
                        ) AS total_socios,
                        (
                            SELECT json_agg(
                                json_build_object(
                                    'id_socio', s.id_socio,
                                    'nome', s.nome_ou_razao_social,
                                    'cnpj_cpf', s.cnpj_cpf,
                                    'qualificacao', q.nm_quals
                                )
                            )
                            FROM c_socios s
                            LEFT JOIN c_qualificacoes q ON s.qualif_socio = q.cd_quals
                            WHERE s.cnpj_basico = e.cnpj_basico
                        ) AS socios
                    FROM c_estabelecimentos e
                    LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                    LEFT JOIN c_cnaes c ON e.cnae_principal = c.cnae
                    WHERE e.cd_matriz_filial = 1
                """
                params = []
                
                if termo:
                    sql += " AND (emp.razao_social ILIKE %s OR e.nm_fantasia ILIKE %s OR e.cnpj_basico ILIKE %s)"
                    params.extend([f'%{termo}%', f'%{termo}%', f'%{termo}%'])
                
                if uf:
                    sql += " AND e.uf = %s"
                    params.append(uf.upper())
                
                if situacao:
                    sql += " AND e.cd_sit_cadastro = %s"
                    params.append(1 if situacao == 'ATIVA' else 2 if situacao == 'INAPTA' else 3 if situacao == 'SUSPENSA' else 4)
                
                sql += " ORDER BY emp.razao_social LIMIT %s OFFSET %s"
                params.extend([limit, offset])
                
                cur.execute(sql, params)
                empresas = cur.fetchall()
                
                # ========== CONTAGEM ==========
                if termo or uf or situacao:
                    count_sql = """
                        SELECT COUNT(*) as total
                        FROM c_estabelecimentos e
                        LEFT JOIN c_empresas emp ON e.cnpj_basico = emp.cnpj_basico
                        WHERE e.cd_matriz_filial = 1
                    """
                    count_params = []
                    
                    if termo:
                        count_sql += " AND (emp.razao_social ILIKE %s OR e.nm_fantasia ILIKE %s OR e.cnpj_basico ILIKE %s)"
                        count_params.extend([f'%{termo}%', f'%{termo}%', f'%{termo}%'])
                    
                    if uf:
                        count_sql += " AND e.uf = %s"
                        count_params.append(uf.upper())
                    
                    if situacao:
                        count_sql += " AND e.cd_sit_cadastro = %s"
                        count_params.append(1 if situacao == 'ATIVA' else 2 if situacao == 'INAPTA' else 3 if situacao == 'SUSPENSA' else 4)
                    
                    cur.execute(count_sql, count_params)
                    resultado = cur.fetchone()
                    total = resultado['total'] if resultado else 0
                else:
                    total = 20
                
                return empresas, total
                
        finally:
            conn.close()