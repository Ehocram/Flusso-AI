"""Dati resi disponibili a tutti i template (badge di sintesi in testata)."""

from .models import Richiesta
from .workflow import STATI_TERMINALI


def statistiche_globali(request):
    if not request.user.is_authenticated:
        return {}
    qs = Richiesta.objects.all()
    if request.user.is_owner:
        qs = qs.filter(proponente=request.user)
    aperte = qs.exclude(stato__in=STATI_TERMINALI).count()
    return {"nav_richieste_aperte": aperte}
