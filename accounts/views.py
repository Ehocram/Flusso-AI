"""View di account: cambio password e gestione utenti (riservata alla Funzione AI)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from .forms import CreazioneUtenteForm
from .models import Utente
from .utils import genera_password


class CambioPasswordView(PasswordChangeView):
    """Cambio password dell'utente. Azzera l'eventuale obbligo impostato dall'admin."""

    template_name = "registration/cambia_password.html"
    success_url = reverse_lazy("flusso:dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)  # mantiene la sessione attiva
        utente = self.request.user
        if utente.deve_cambiare_password:
            utente.deve_cambiare_password = False
            utente.save(update_fields=["deve_cambiare_password"])
        messages.success(self.request, "Password aggiornata.")
        return response


# --- Gestione utenti (solo Funzione AI) -------------------------------------

def _solo_funzione_ai(request):
    """Restituisce None se autorizzato, altrimenti una risposta 403."""
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Sezione riservata alla Funzione AI.")
    return None


@login_required
def utenti(request):
    nego = _solo_funzione_ai(request)
    if nego:
        return nego
    # La credenziale generata viene mostrata una sola volta, poi rimossa.
    cred = request.session.pop("credenziale_generata", None)
    elenco = Utente.objects.order_by("last_name", "first_name", "username")
    return render(request, "accounts/utenti.html", {"utenti": elenco, "cred": cred})


@login_required
def nuovo_utente(request):
    nego = _solo_funzione_ai(request)
    if nego:
        return nego
    if request.method == "POST":
        form = CreazioneUtenteForm(request.POST)
        if form.is_valid():
            user = form.save()
            request.session["credenziale_generata"] = {
                "username": user.username,
                "password": user._password_generata,
                "azione": "creata",
            }
            return redirect("accounts:utenti")
    else:
        form = CreazioneUtenteForm()
    return render(request, "accounts/nuovo_utente.html", {"form": form})


@login_required
@require_POST
def reset_password_utente(request, pk):
    nego = _solo_funzione_ai(request)
    if nego:
        return nego
    u = get_object_or_404(Utente, pk=pk)
    pwd = genera_password()
    u.set_password(pwd)
    u.deve_cambiare_password = True
    u.save(update_fields=["password", "deve_cambiare_password"])
    request.session["credenziale_generata"] = {
        "username": u.username, "password": pwd, "azione": "reimpostata",
    }
    return redirect("accounts:utenti")
