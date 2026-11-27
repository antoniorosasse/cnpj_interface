from django.urls import path

from . import views

app_name = 'exportacao'

urlpatterns = [
    path('exportar/', views.personalizar, name='personalizar'),
    path('exportar/desafio/', views.desafio_export, name='desafio'),
    path('exportar/lista/', views.exportar_lista, name='exportar_lista'),
    path('exportar/proximidade/', views.exportar_proximidade, name='exportar_proximidade'),
    path('exportar/chat/', views.exportar_chat, name='exportar_chat'),
    path('exportar/chat/completo/', views.exportar_chat_completo,
         name='exportar_chat_completo'),
    path('exportar/completo/<str:token>/', views.exportacao_completa_status,
         name='completa_status'),
    path('exportar/completo/<str:token>/baixar/',
         views.exportacao_completa_baixar, name='completa_baixar'),
]
