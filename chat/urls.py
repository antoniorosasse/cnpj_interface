# cnpj_interface/chat/urls.py
"""
URLs do app chat.

- GET  /chat/                     -> pagina do chat (conversa atual)
- POST /chat/api/                 -> endpoint AJAX do pipeline
- GET  /chat/historico/           -> lista as conversas do usuario
- GET  /chat/conversa/<id>/       -> abre uma conversa
- POST /chat/nova/                -> comeca conversa nova
- POST /chat/limpar/              -> (legado) limpa sessao
"""

from django.urls import path

from . import views


app_name = 'chat'


urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('api/', views.chat_api_view, name='api'),
    path('historico/', views.chat_historico_view, name='historico'),
    path('conversa/<int:conversa_id>/', views.chat_conversa_view, name='conversa'),
    path('nova/', views.chat_nova_view, name='nova'),
    path('deletar/<int:conversa_id>/', views.chat_deletar_view, name='deletar'),
    path('limpar/', views.chat_limpar_view, name='limpar'),
]
