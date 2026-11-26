# /home/neto/cnpj_interface/core/urls.py
from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'core'

urlpatterns = [
    # Pagina inicial do produto (landing comercial) na raiz do dominio.
    # Em producao e' busquecnpj.com.br/; no dev, http://<host>:8002/.
    path('', views.landing_comercial, name='landing'),

    # Lista de empresas (com busca). Fica em /empresas/ desde a virada de
    # 23/11/2026, quando a raiz passou a ser a pagina do produto.
    path('empresas/', views.lista_empresas, name='lista'),

    # Endereco antigo da landing: 301 para a raiz, para nao perder links e
    # o que ja foi indexado.
    path('site/', RedirectView.as_view(pattern_name='core:landing', permanent=True)),

    # Busca por proximidade (raio em km a partir de CNPJ/CEP/cidade/coordenada)
    path('proximidade/', views.proximidade, name='proximidade'),

    # Densidade do raio em JSON (camada de areas do mapa da proximidade)
    path('proximidade/mapa/', views.proximidade_mapa, name='proximidade_mapa'),

    # Pontos individuais (zoom de quadra: troca a area pelos pontos)
    path('proximidade/mapa/pontos/', views.proximidade_mapa_pontos,
         name='proximidade_mapa_pontos'),

    # Todos os pontos do recorte filtrado (botao "mostrar todos no mapa")
    path('proximidade/mapa/filtrado/', views.proximidade_mapa_filtrado,
         name='proximidade_mapa_filtrado'),

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
