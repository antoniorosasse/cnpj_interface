from django.urls import path

from . import views

app_name = 'exportacao'

urlpatterns = [
    path('exportar/', views.personalizar, name='personalizar'),
    path('exportar/lista/', views.exportar_lista, name='exportar_lista'),
    path('exportar/proximidade/', views.exportar_proximidade, name='exportar_proximidade'),
    path('exportar/chat/', views.exportar_chat, name='exportar_chat'),
]
