"""Riallinea ai fogli di budget i progetti presi in carico.

La riga nasce alla presa in carico e viene riscritta ai passaggi successivi e al
salvataggio dell'analisi. Questo comando serve per il pregresso: progetti presi in
carico prima che la regola esistesse, o la cui riga e' rimasta indietro rispetto
all'analisi (tipicamente l'effort, che nel foglio e' «EFFORT IT (GG)» a 8 ore/giorno).

L'aggiornamento e' per colonna: quello che e' stato scritto a mano nel foglio —
spunta di approvazione compresa — non viene toccato.

Restano fuori le pratiche che nel foglio non devono stare (bozza, in coda,
respinte, archiviate): per togliere le loro righe c'e' «pulisci_budget».

Uso:
    python manage.py sincronizza_budget            # anteprima, non scrive nulla
    python manage.py sincronizza_budget --applica  # crea/aggiorna le righe
"""

from django.core.management.base import BaseCommand

from flusso.models import FoglioBudget, Richiesta
from flusso.servizi import anteprima_copia_in_budget, copia_in_budget
from flusso.workflow import Stato

# Una pratica sta nel foglio da quando e' in carico a una funzione tecnica.
STATI_FUORI = [Stato.BOZZA, Stato.INVIATA, Stato.RESPINTA, Stato.ARCHIVIATA]

ETICHETTE = {"crea": "da creare", "sposta": "da spostare", "aggiorna": "da aggiornare",
             "togli": "da togliere (scheda generata: l'iniziativa ha una riga sola)"}


def _euro(valore) -> str:
    """Importo all'italiana: 10.000,00."""
    return "€ " + f"{valore:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _righe(n: int) -> str:
    return "1 riga" if n == 1 else f"{n} righe"


class Command(BaseCommand):
    help = "Allinea ai fogli di budget le righe dei progetti presi in carico."

    def add_arguments(self, parser):
        parser.add_argument("--applica", action="store_true",
                            help="Scrive le righe (senza questa opzione mostra solo l'anteprima).")

    def handle(self, *args, **opts):
        if not FoglioBudget.objects.exists():
            self.stdout.write(self.style.ERROR(
                "Nessun foglio di budget presente: crealo dalla pagina Budget o importa i workbook."))
            return

        candidati = (Richiesta.objects.exclude(stato__in=STATI_FUORI)
                     .select_related("proponente").prefetch_related("righe_budget", "cloni")
                     .order_by("numero"))
        da_fare = []
        invariati = 0
        for r in candidati:
            azione, dove = anteprima_copia_in_budget(r)
            if azione == "invariata":
                invariati += 1
                continue
            da_fare.append((r, azione, dove))

        if not da_fare:
            self.stdout.write(self.style.SUCCESS(
                f"Niente da fare: {invariati} progetti già allineati ai fogli."))
            return

        for r, azione, dove in da_fare:
            effort = f"{r.effort_ore / 8:.1f} gg".replace(".", ",") if r.effort_ore else "effort n.d."
            costo = (_euro(r.costo_progetto_stimato)
                     if r.costo_progetto_stimato is not None else "costo n.d.")
            self.stdout.write(f"  [{ETICHETTE[azione]}] {r.codice} — {r.titolo} "
                              f"→ {dove} · {effort} · {costo}")

        if not opts["applica"]:
            self.stdout.write(self.style.WARNING(
                f"\nAnteprima: {_righe(len(da_fare))} da scrivere ({invariati} già allineate). "
                "Rilancia con --applica per applicare."))
            return

        creati = aggiornati = tolti = falliti = 0
        for r, azione, _dove in da_fare:
            try:
                riga, creata = copia_in_budget(r)
            except Exception as exc:  # un progetto rotto non ferma gli altri
                falliti += 1
                self.stdout.write(self.style.ERROR(f"  [FAIL] {r.codice} — {exc}"))
                continue
            if azione == "togli":
                tolti += 1  # scheda generata: copia_in_budget ha rimosso la riga
                continue
            if riga is None:
                falliti += 1
                self.stdout.write(self.style.ERROR(
                    f"  [FAIL] {r.codice} — nessun foglio di destinazione disponibile."))
                continue
            creati += 1 if creata else 0
            aggiornati += 0 if creata else 1

        stile = self.style.SUCCESS if falliti == 0 else self.style.WARNING
        self.stdout.write(stile(
            f"\nCompletato: {_righe(creati)} create, {aggiornati} aggiornate, "
            f"{tolti} tolte, {falliti} fallite ({invariati} erano già allineate)."))
