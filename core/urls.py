# /home/neto/cnpj_interface/core/urls.py
from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Lista de empresas (com busca)
    path('', views.lista_empresas, name='lista'),
    
    # Busca rápida via AJAX
    path('busca-rapida/', views.buscar_empresas_rapido, name='busca_rapida'),
    
    # Detalhe da empresa
    path('empresa/<str:cnpj>/', views.detalhe_empresa, name='detalhe'),
    
    # Estatísticas
    path('estatisticas/', views.estatisticas, name='estatisticas'),
]