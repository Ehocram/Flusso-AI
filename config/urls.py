"""Routing principale del progetto."""

from accounts import views as account_views
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/cambia-password/", account_views.CambioPasswordView.as_view(), name="cambia_password"),
    path("accounts/", include("accounts.urls")),
    path("", include("flusso.urls")),
]
