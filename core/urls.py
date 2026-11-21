# /home/neto/cnpj_interface/core/urls.py
from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Landing comercial (pagina inicial do produto). Fica ANTES da raiz para
    # nao ser capturada pela lista de empresas. E' o destino de /inicio/ e do
    # item "Inicio" do shell (core/context_processors.py).
    path('site/', views.landing_comercial, name='landing'),

    # Lista de empresas (com busca)
    path('', views.lista_empresas, name='lista'),

    # Busca por proximidade (raio em km a partir de CNPJ/CEP/cidade/coordenada)
    path('proximidade/', views.proximidade, name='proximidade'),
    
    # Busca rápida via AJAX
    path('busca-rapida/', views.buscar_empresas_rapido, name='busca_rapida'),
    
    # Detalhe da empresa
    path('empresa/<str:cnpj>/', views.detalhe_empresa, name='detalhe'),

    # Dossie da empresa em PDF (gerado na hora, a partir da mesma base)
    path('empresa/<str:cnpj>/dossie.pdf', views.dossie_empresa, name='dossie'),
    
    # Estatísticas
    path('estatisticas/', views.estatisticas, name='estatisticas'),
    path('estatisticas/recalcular/', views.recalcular_totais, name='recalcular_totais'),
]