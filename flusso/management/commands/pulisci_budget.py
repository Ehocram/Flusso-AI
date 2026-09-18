"""Toglie dai fogli di budget le righe dei progetti che non proseguono.

La regola — riportato in bozza, respinto o archiviato dall'owner ⇒ niente riga —
agisce sulle transizioni, quindi vale da quando è stata introdotta. Questo comando
serve una volta sola, per ripulire ciò che era stato scritto prima.

Le righe storiche importate dai workbook non vengono toccate: si cancellano solo
quelle collegate a un progetto, e solo se quel progetto è in uno degli stati che
non prosegue.

Uso:
    python manage.py pulisci_budget            # anteprima, non cancella nulla
    python manage.py pulisci_budget --applica  # cancella le righe elencate
"""

from django.core.management.base import BaseCommand

from flusso.models import RigaBudget
from flusso.workflow import Stato

STATI_FUORI = [Stato.BOZZA, Stato.RESPINTA, Stato.ARCHIVIATA]


class Command(BaseCommand):
    help = "Rimuove dai fogli di budget le righe dei progetti in bozza, respinti o archiviati."

    def add_arguments(self, parser):
        parser.add_argument("--applica", action="store_true",
                            help="Cancella le righe (senza questa opzione mostra solo l'anteprima).")

    def handle(self, *args, **opts):
        righe = list(RigaBudget.objects.filter(richiesta__stato__in=STATI_FUORI)
                     .select_related("richiesta", "foglio").order_by("foglio__nome", "ordine"))
        if not righe:
            self.stdout.write(self.style.SUCCESS(
                "Nessuna riga da togliere: i fogli sono già allineati."))
            return

        for riga in righe:
            r = riga.richiesta
            self.stdout.write(
                f"  [da togliere] {r.codice} — {r.titolo} "
                f"({r.get_stato_display()}) → {riga.foglio.nome} {riga.foglio.anno}")

        if not opts["applica"]:
            self.stdout.write(self.style.WARNING(
                f"\nAnteprima: {len(righe)} righe da togliere. "
                "Rilancia con --applica per cancellarle."))
            return

        quante, _ = RigaBudget.objects.filter(pk__in=[r.pk for r in righe]).delete()
        self.stdout.write(self.style.SUCCESS(
            f"\nCompletato: {len(righe)} righe tolte dai fogli di budget."))
