# core/routers.py

class CNPJRouter:
    """
    Router que direciona as consultas do Django:
    - Modelos do app 'core' → PostgreSQL ('bancocnpj', dados da RFB)
    - Outros apps (admin, auth, sessions, chat) → 'default' (django_internal)
    """

    def db_for_read(self, model, **hints):
        """
        Define qual banco usar para LEITURA (SELECT)
        """
        if model._meta.app_label == 'core':
            return 'postgres'
        return 'default'  # Postgres django_internal para admin, auth, chat, etc.

    def db_for_write(self, model, **hints):
        """
        Define qual banco usar para ESCRITA (INSERT, UPDATE, DELETE)
        """
        if model._meta.app_label == 'core':
            return 'postgres'
        return 'default'  # Postgres django_internal para admin, auth, chat, etc.

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
            # Migrações do app 'core' vão para o PostgreSQL (bancocnpj)
            return db == 'postgres'
        # Migrações de outros apps (admin, auth) vão para o default (django_internal)
        return db == 'default'
