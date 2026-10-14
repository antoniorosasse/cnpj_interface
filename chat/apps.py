# cnpj_interface/chat/apps.py
from django.apps import AppConfig


class ChatConfig(AppConfig):
    name = 'chat'

    def ready(self):
        # Importa os signals para registra-los no boot do Django.
        # Nao use o retorno; e' so pra garantir o "side effect" do import.
        from . import signals  # noqa: F401
        