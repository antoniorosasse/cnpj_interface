# cnpj_project/urls.py
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from core import views as core_views

urlpatterns = [
    path('admin/', admin.site.urls),

    # Autenticacao (login, logout, trocar senha)
    path('accounts/login/', auth_views.LoginView.as_view(
        template_name='registration/login.html'
    ), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('admin/password_change/', auth_views.PasswordChangeView.as_view(
        template_name='registration/password_change.html',
        success_url='/chat/'
    ), name='password_change'),

    path('chat/', include('chat.urls')),
    path('', include('core.urls')),
    path('health/', lambda r: HttpResponse('ok'), name='health'),
    path('health/deep/', core_views.health_deep, name='health_deep'),
]
