"""Allinea ai fogli di budget i progetti la cui compliance è già stata validata.

Serve dopo il primo import dei workbook (o dopo un periodo in cui i fogli non
erano presenti): la copia automatica scatta al momento della terza validazione
del CISO, quindi i progetti validati PRIMA non hanno una riga.

Uso:
    python manage.py sincronizza_budget            # anteprima, non scrive nulla
    python manage.py sincronizza_budget --applica  # crea/aggiorna le righe

Regole invariate: Budget -> esercizio successivo, Extra Budget -> esercizio in
corso; ogni progetto ha UNA riga (se esiste viene aggiornata, mai duplicata).
"""

from django.core.management.base import BaseCommand

from flusso.models import FoglioBudget, Richiesta
from flusso.servizi import copia_in_budget
from flusso.workflow import Stato

STATI_VALIDATI = [Stato.PRONTA_APPROVAZIONE, Stato.IN_APPROVAZIONE, Stato.APPROVATA,
                  Stato.ATTIVO, Stato.MONITORAGGIO, Stato.COMPLETATO]


class Command(BaseCommand):
    help = "Copia nei fogli di budget i progetti con compliance già validata dal CISO."

    def add_arguments(self, parser):
        parser.add_argument("--applica", action="store_true",
                            help="Scrive le righe (senza questa opzione mostra solo l'anteprima).")

    def handle(self, *args, **opts):
        if not FoglioBudget.objects.exists():
            self.stdout.write(self.style.ERROR(
                "Nessun foglio di budget presente: esegui prima «importa_budget»."))
            return

        candidati = [r for r in Richiesta.objects.exclude(stato=Stato.RESPINTA)
                     .prefetch_related("classificazioni", "righe_budget")
                     if r.stato in STATI_VALIDATI or r.rischi_tutti_validati]
        if not candidati:
            self.stdout.write(self.style.WARNING("Nessun progetto con compliance validata."))
            return

        applica = opts["applica"]
        nuovi = aggiornati = falliti = 0
        for r in candidati:
            if not applica:
                extra = r.budget_it == "EXTRA_BUDGET" or r.esito_budget == "EXTRA_BUDGET"
                dove = "Extra Budget" if extra else "Budget"
                stato = "già presente" if r.righe_budget.exists() else "da creare"
                self.stdout.write(f"  [{stato}] {r.codice} — {r.titolo} → {dove}")
                continue
            try:
                riga, creata = copia_in_budget(r)
            except Exception as exc:  # fail loudly, senza fermare gli altri
                falliti += 1
                self.stdout.write(self.style.ERROR(f"  [FAIL] {r.codice} — {exc}"))
                continue
            if riga is None:
                falliti += 1
                self.stdout.write(self.style.ERROR(
                    f"  [FAIL] {r.codice} — nessun foglio di destinazione disponibile."))
                continue
            nuovi += 1 if creata else 0
            aggiornati += 0 if creata else 1
            verbo = "creata" if creata else "aggiornata"
            self.stdout.write(self.style.SUCCESS(
                f"  [OK] {r.codice} — riga {verbo} in {riga.foglio.nome} {riga.foglio.anno}"))

        if not applica:
            self.stdout.write(self.style.WARNING(
                f"\nAnteprima: {len(candidati)} progetti. "
                "Rilancia con --applica per scrivere le righe."))
            return
        stile = self.style.SUCCESS if falliti == 0 else self.style.WARNING
        self.stdout.write(stile(
            f"\nCompletato: {nuovi} righe create, {aggiornati} aggiornate, {falliti} fallite."))
