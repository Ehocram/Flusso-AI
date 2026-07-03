"""Filtri di presentazione per i template."""

from django import template

register = template.Library()

# Mappa funzione -> (icona Tabler, classe colore della pill)
_FUNZIONE_META = {
    "RND": ("ti-flask", "fx-rnd"),
    "IT": ("ti-server-cog", "fx-it"),
    "SALES": ("ti-presentation", "fx-sales"),
    "SC": ("ti-truck-delivery", "fx-sc"),
    "FINANCE": ("ti-coin-euro", "fx-finance"),
    "HR": ("ti-users", "fx-hr"),
    "OPS": ("ti-settings-2", "fx-ops"),
}

_STATO_STILE = {
    "BOZZA": "st-neutro",
    "INVIATA": "st-info",
    "IN_QUALIFICA": "st-info",
    "IN_APPROVAZIONE": "st-attesa",
    "APPROVATA": "st-ok",
    "ATTIVO": "st-ok",
    "MONITORAGGIO": "st-ok",
    "COMPLETATO": "st-chiuso",
    "RESPINTA": "st-ko",
}


@register.filter
def icona_funzione(codice):
    return _FUNZIONE_META.get(codice, ("ti-bulb", "fx-rnd"))[0]


@register.filter
def classe_funzione(codice):
    return _FUNZIONE_META.get(codice, ("ti-bulb", "fx-rnd"))[1]


@register.filter
def classe_stato(codice):
    return _STATO_STILE.get(codice, "st-neutro")


from flusso.workflow import FASI, Stato  # noqa: E402

_FASI_STEPPER = [
    ("in_coda", "In coda"),
    ("in_analisi", "Analisi"),
    ("pronte", "Pronta"),
    ("in_approvazione", "Approvazione"),
    ("approvati", "Attivo"),
    ("chiusi", "Chiuso"),
]


@register.simple_tag
def stepper(stato):
    """Dati per il mini-stepper di fase nel dettaglio richiesta."""
    valori = {k: [s.value for s in FASI[k]] for k, _ in _FASI_STEPPER}
    idx = next((i for i, (k, _) in enumerate(_FASI_STEPPER) if stato in valori[k]), None)
    is_ko = stato == Stato.RESPINTA.value
    passi = []
    for i, (k, label) in enumerate(_FASI_STEPPER):
        if idx is None:
            css = "todo"
        elif i < idx:
            css = "done"
        elif i == idx:
            css = "ko" if is_ko else "active"
        else:
            css = "todo"
        passi.append({"label": label, "css": css, "n": i + 1})
    return passi
