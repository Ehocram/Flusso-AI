"""Forza il cambio password quando l'admin lo richiede (dopo un reset)."""

from django.shortcuts import redirect
from django.urls import reverse


class ForzaCambioPasswordMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and getattr(user, "deve_cambiare_password", False):
            consentiti = {reverse("cambia_password"), reverse("logout")}
            if request.path not in consentiti and not request.path.startswith("/static/"):
                return redirect("cambia_password")
        return self.get_response(request)
