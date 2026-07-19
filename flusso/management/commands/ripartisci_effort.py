"""Backfill della ripartizione effort su tutti i progetti esistenti.

Uso (dal server, fuori dai timeout HTTP):
    python manage.py ripartisci_effort            # solo i progetti senza ripartizione
    python manage.py ripartisci_effort --tutti    # rigenera anche le esistenti
    python manage.py ripartisci_effort --workers 3

Per ogni progetto con effort in ore (QUALSIASI stato, inclusi in approvazione e
approvati) l'AI propone la suddivisione percentuale; le ore sono derivate in modo
deterministico e quadrano sempre con l'effort registrato. Errori per singolo
progetto sono riportati a voce alta e non fermano gli altri.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from flusso.ai_client import stima_ripartizione_effort
from flusso.models import Richiesta
from flusso.servizi import _config_pronta


class Command(BaseCommand):
    help = "Ripartisce con l'AI l'effort dei progetti sulle attività (totali invariati)."

    def add_arguments(self, parser):
        parser.add_argument("--tutti", action="store_true",
                            help="Rigenera anche le ripartizioni già esistenti.")
        parser.add_argument("--workers", type=int, default=3,
                            help="Chiamate AI in parallelo (default 3).")

    def handle(self, *args, **opts):
        cfg = _config_pronta()
        if cfg is None:
            raise CommandError("AI non disponibile: abilitazione o API key mancanti in Impostazioni.")

        qs = Richiesta.objects.filter(effort_ore__gt=0).annotate(n_voci=Count("voci_effort"))
        if not opts["tutti"]:
            qs = qs.filter(n_voci=0)
        target = list(qs.order_by("pk"))

        esclusi = Richiesta.objects.exclude(effort_ore__gt=0).count()
        if esclusi:
            self.stdout.write(self.style.WARNING(
                f"[ATTENZIONE] {esclusi} richieste senza effort in ore: escluse "
                "(l'effort si registra nell'analisi; nessun numero inventato)."))
        if not target:
            self.stdout.write(self.style.SUCCESS("Niente da fare: nessun progetto da ripartire."))
            return

        self.stdout.write(f"Progetti da ripartire: {len(target)} "
                          f"(workers={opts['workers']}, tutti={opts['tutti']})")
        ok, ko = 0, 0
        with ThreadPoolExecutor(max_workers=max(1, opts["workers"])) as pool:
            futures = {pool.submit(stima_ripartizione_effort, r, cfg): r for r in target}
            for fut in as_completed(futures):
                r = futures[fut]
                try:
                    voci, errore = fut.result()
                except Exception as exc:  # fail loudly, non fermare gli altri
                    voci, errore = None, f"eccezione: {exc}"
                if errore:
                    ko += 1
                    self.stdout.write(self.style.ERROR(f"[FAIL] {r.codice} — {errore}"))
                    continue
                create = r.applica_ripartizione_effort(voci)
                if not create:
                    ko += 1
                    self.stdout.write(self.style.ERROR(
                        f"[FAIL] {r.codice} — ripartizione non utilizzabile."))
                    continue
                ok += 1
                dett = ", ".join(f"{v.attivita} {v.ore}h" for v in create)
                self.stdout.write(self.style.SUCCESS(
                    f"[OK] {r.codice} ({r.stato}) — {r.effort_ore}h → {dett}"))

        riep = f"Completato: {ok} ripartiti, {ko} falliti su {len(target)}."
        stile = self.style.SUCCESS if ko == 0 else self.style.WARNING
        self.stdout.write(stile(riep))
        if ko:
            self.stdout.write("Rilancia il comando per i falliti (sono rimasti senza voci) "
                              "o usa il bottone AI dalla pagina Effort.")
