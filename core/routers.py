# core/routers.py

class CNPJRouter:
    """
    Router que direciona as consultas do Django:
    - Modelos do app 'core' → PostgreSQL (onde está a VIEW)
    - Outros apps (admin, auth, sessions) → SQLite
    """
    
    def db_for_read(self, model, **hints):
        """
        Define qual banco usar para LEITURA (SELECT)
        """
        if model._meta.app_label == 'core':
            return 'postgres'
        return 'default'  # SQLite para admin, auth, etc.

    def db_for_write(self, model, **hints):
        """
        Define qual banco usar para ESCRITA (INSERT, UPDATE, DELETE)
        """
        if model._meta.app_label == 'core':
            return 'postgres'
        return 'default'  # SQLite para admin, auth, etc.

    def allow_relation(self, obj1, obj2, **hints):
        """
        Permite relações entre objetos de bancos diferentes
        """
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Define onde as migrações de cada app devem ser aplicadas
        """
        if app_label == 'core':
            # Migrações do app 'core' vão para o PostgreSQL
            return db == 'postgres'
        # Migrações de outros apps (admin, auth) vão para o SQLite
        return db == 'default'