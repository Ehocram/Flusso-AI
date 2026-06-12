"""View di account: cambio password (la creazione utenti avviene da admin)."""

from django.contrib import messages
from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse_lazy


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
