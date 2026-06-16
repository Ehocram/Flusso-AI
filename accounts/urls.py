"""Rotte dell'app account (gestione utenti, riservata alla Funzione AI)."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("utenti/", views.utenti, name="utenti"),
    path("utenti/nuovo/", views.nuovo_utente, name="nuovo_utente"),
    path("utenti/<int:pk>/reset-password/", views.reset_password_utente, name="reset_password_utente"),
]
