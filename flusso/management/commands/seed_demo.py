"""
Popola l'ambiente con gli utenti del processo e i progetti REALI letti
dall'elenco Excel incluso nel progetto (flusso/data/Elenco_Progetti_AI.xlsx).

Crea/aggiorna gli utenti (anche se gia' esistenti), assegna alla Funzione AI
i permessi admin per gestire le utenze, e importa le richieste dall'Excel
portandole allo stato di workflow corretto.

Uso:
  python manage.py seed_demo                 # utenti + progetti dall'Excel incluso
  python manage.py seed_demo --file X.xlsx   # usa un altro file Excel
  python manage.py seed_demo --reset         # azzera le richieste prima di ricreare
  python manage.py seed_demo --solo-utenti   # crea/aggiorna solo gli utenti
"""

from accounts.models import Funzione, Ruolo
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from flusso import importer
from flusso.models import Richiesta

Utente = get_user_model()

# username, nome, cognome, email, ruolo, funzione, dipartimento, is_staff, is_superuser
UTENTI = [
    ("marco.bonometti", "Marco", "Bonometti", "marco.bonometti@iseo.com",
     Ruolo.AI_OFFICER, "", "Information Security / AI", True, True),
    ("approvatore", "Direzione", "Generale", "approvatore@iseo.com",
     Ruolo.APPROVATORE, "", "Direzione Generale", False, False),
    ("paolo.laini", "Paolo", "Laini", "paolo.laini@iseo.com",
     Ruolo.AUDITOR, "", "MS & ESG", False, False),
    ("legale", "Funzione", "Legale", "legale@iseo.com",
     Ruolo.LEGALE, "", "Affari Legali / Compliance", False, False),
    ("ciso", "Funzione", "Sicurezza", "ciso@iseo.com",
     Ruolo.CISO, "", "Information Security (NIS2)", False, False),
    ("dpo", "Funzione", "Privacy", "dpo@iseo.com",
     Ruolo.DPO, "", "Data Protection (GDPR)", False, False),
    ("owner.it", "Owner", "IT", "owner.it@iseo.com",
     Ruolo.OWNER, Funzione.IT, "Information Technology", False, False),
    ("owner.rnd", "Owner", "R&D", "owner.rnd@iseo.com",
     Ruolo.OWNER, Funzione.RND, "Ricerca & Sviluppo", False, False),
    ("owner.sales", "Owner", "Sales", "owner.sales@iseo.com",
     Ruolo.OWNER, Funzione.SALES, "Vendite", False, False),
    ("owner.sc", "Owner", "Supply Chain", "owner.sc@iseo.com",
     Ruolo.OWNER, Funzione.SUPPLY_CHAIN, "Supply Chain", False, False),
    ("owner.hr", "Owner", "HR", "owner.hr@iseo.com",
     Ruolo.OWNER, Funzione.HR, "Risorse Umane", False, False),
    ("owner.ops", "Owner", "Operations", "owner.ops@iseo.com",
     Ruolo.OWNER, Funzione.OPERATIONS, "Operations", False, False),
    ("owner.finance", "Owner", "Finance", "owner.finance@iseo.com",
     Ruolo.OWNER, Funzione.FINANCE, "Finance & Legal", False, False),
]



class Command(BaseCommand):
    help = "Crea/aggiorna utenti e importa i progetti reali dall'elenco Excel."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="iseo2026", help="Password comune agli utenti (solo alla creazione).")
        parser.add_argument("--file", default=str(importer.DEFAULT_XLSX), help="Percorso del file Excel.")
        parser.add_argument("--reset", action="store_true", help="Elimina le richieste prima di ricreare.")
        parser.add_argument("--solo-utenti", action="store_true", help="Crea/aggiorna solo gli utenti.")
        parser.add_argument("--senza-ai", action="store_true",
                            help="Non generare con l'AI rischi e stime (importa solo i dati).")

    def handle(self, *args, **opt):
        with transaction.atomic():
            if opt["reset"]:
                n = Richiesta.objects.count()
                Richiesta.objects.all().delete()
                self.stdout.write(self.style.WARNING(f"Eliminate {n} richieste esistenti."))

            attori = self._crea_utenti(opt["password"])
            self.stdout.write(self.style.SUCCESS(f"Utenti pronti: {len(attori)} (password «{opt['password']}» alla creazione)."))
            self.stdout.write("  · marco.bonometti è superuser (accesso completo all'admin).")

            if opt["solo_utenti"]:
                return

            owner_per_funzione = {
                Funzione.IT: attori["owner.it"], Funzione.RND: attori["owner.rnd"],
                Funzione.SALES: attori["owner.sales"], Funzione.SUPPLY_CHAIN: attori["owner.sc"],
                Funzione.HR: attori["owner.hr"], Funzione.OPERATIONS: attori["owner.ops"],
                Funzione.FINANCE: attori["owner.finance"],
            }
            chiavi = {"ai": attori["marco.bonometti"], "appr": attori["approvatore"]}

            self.stdout.write("\nImporto i progetti dall'Excel:")
            creati = importer.importa(owner_per_funzione, chiavi, path=opt["file"], log=self.stdout.write)
            self.stdout.write(self.style.SUCCESS(f"\nCreate {creati} richieste dall'elenco."))

        # Generazione AI fuori dalla transazione dati: stime incrementi + 3 classificazioni.
        if not opt["senza_ai"]:
            self._genera_ai(attori["marco.bonometti"])

        self.stdout.write(self.style.WARNING(
            "Dati reali, credenziali DIMOSTRATIVE: cambia le password prima dell'uso reale."
        ))

    def _genera_ai(self, attore):
        """All'import: per ogni richiesta stima gli incrementi mancanti + classifica i 3 rischi."""
        from flusso import servizi
        from flusso.models import ConfigurazioneAI

        cfg = ConfigurazioneAI.load()
        if not cfg.abilitato or not cfg.configurata:
            self.stdout.write(self.style.WARNING(
                "\nAnalisi AI non configurata: rischi e stime NON generati. "
                "Configura chiave/abilitazione (o riesegui seed_demo) per generarli; "
                "in alternativa usa le schede («Analizza le tre dimensioni con l'AI»)."))
            return
        richieste = list(Richiesta.objects.order_by("numero"))
        self.stdout.write(f"\nGenero stime e classificazioni di rischio con l'AI per {len(richieste)} progetti…")
        for r in richieste:
            esito = servizi.genera_tutto(r, attore=attore)
            self.stdout.write(
                f"  · {r.codice}: incrementi={'sì' if esito['incrementi'] else 'no'}, "
                f"rischi ok={esito['rischi_ok']}/3"
                + (f", errori={list(esito['rischi_errori'])}" if esito['rischi_errori'] else ""))
        self.stdout.write(self.style.SUCCESS("Generazione AI completata."))

    def _crea_utenti(self, pwd):
        """Crea gli utenti mancanti e allinea i campi di quelli esistenti."""
        attori = {}
        for username, nome, cognome, email, ruolo, funzione, dip, staff, is_super in UTENTI:
            u, nuovo = Utente.objects.get_or_create(username=username)
            if nuovo:
                u.set_password(pwd)
            u.first_name, u.last_name, u.email = nome, cognome, email
            u.ruolo, u.funzione, u.dipartimento = ruolo, funzione, dip
            u.is_staff, u.is_superuser = staff, is_super
            u.save()
            attori[username] = u
        return attori
