"""
Re-importa l'elenco progetti da un file Excel, senza toccare gli utenti.
Utile dopo un aggiornamento del file: crea le richieste mancanti (per ID).

Uso:
  python manage.py importa_progetti                 # file Excel incluso
  python manage.py importa_progetti --file X.xlsx   # altro file
  python manage.py importa_progetti --reset         # azzera prima di reimportare
"""

from accounts.models import Funzione, Ruolo
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from flusso import importer
from flusso.models import Richiesta

Utente = get_user_model()


class Command(BaseCommand):
    help = "Importa i progetti dall'elenco Excel (richiede gli utenti gia' creati)."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(importer.DEFAULT_XLSX), help="Percorso del file Excel.")
        parser.add_argument("--reset", action="store_true", help="Elimina le richieste prima di reimportare.")

    @transaction.atomic
    def handle(self, *args, **opt):
        ai = Utente.objects.filter(ruolo=Ruolo.AI_OFFICER).first()
        appr = Utente.objects.filter(ruolo=Ruolo.APPROVATORE).first()
        if not (ai and appr):
            raise CommandError("Mancano utenti Funzione AI/Approvatore. Lancia prima 'python manage.py seed_demo --solo-utenti'.")

        owner_per_funzione = {}
        for codice, _ in Funzione.choices:
            u = Utente.objects.filter(ruolo=Ruolo.OWNER, funzione=codice).first()
            if u:
                owner_per_funzione[codice] = u

        if opt["reset"]:
            n = Richiesta.objects.count()
            Richiesta.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Eliminate {n} richieste."))

        self.stdout.write("Importo i progetti dall'Excel:")
        creati = importer.importa(owner_per_funzione, {"ai": ai, "appr": appr},
                                  path=opt["file"], log=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(f"\nCreate {creati} richieste."))
