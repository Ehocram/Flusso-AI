"""
Macchina a stati del processo AI.

Catena di approvazione (separazione dei compiti, applicata lato server):
  Owner        invia la richiesta della propria funzione
  Funzione AI  qualifica/analizza e presenta per l'approvazione
  Approvatore  approva o respinge
  Funzione AI  avvia il progetto e ne segue il SAL fino alla chiusura
L'Auditor vede tutto in sola lettura (nessuna azione di workflow).

Ogni transizione dichiara stati di partenza, stato di arrivo e ruoli abilitati.
L'autorizzazione è per ruolo: nessuna azione è eseguibile aggirando questi vincoli.
"""

from dataclasses import dataclass

from accounts.models import Ruolo
from django.db import models


class Stato(models.TextChoices):
    BOZZA = "BOZZA", "Bozza"
    INVIATA = "INVIATA", "Inviata alla Funzione AI"
    IN_QUALIFICA = "IN_QUALIFICA", "In qualifica e analisi"
    IN_APPROVAZIONE = "IN_APPROVAZIONE", "In approvazione"
    APPROVATA = "APPROVATA", "Approvata"
    RESPINTA = "RESPINTA", "Respinta"
    ATTIVO = "ATTIVO", "Progetto attivo"
    MONITORAGGIO = "MONITORAGGIO", "In monitoraggio (SAL)"
    COMPLETATO = "COMPLETATO", "Completato"


# Stati finali: nessuna ulteriore transizione di workflow.
STATI_TERMINALI = {Stato.RESPINTA, Stato.COMPLETATO}

# Stati in cui il progetto è operativo e il SAL è aggiornabile.
STATI_OPERATIVI = {Stato.ATTIVO, Stato.MONITORAGGIO}

# Raggruppamento per la board (kanban) e i conteggi di dashboard.
FASI = {
    "in_coda": [Stato.BOZZA, Stato.INVIATA],
    "in_analisi": [Stato.IN_QUALIFICA],
    "in_approvazione": [Stato.IN_APPROVAZIONE],
    "approvati": [Stato.APPROVATA, Stato.ATTIVO, Stato.MONITORAGGIO],
    "chiusi": [Stato.COMPLETATO, Stato.RESPINTA],
}


@dataclass(frozen=True)
class Transizione:
    azione: str
    label: str
    da: tuple
    a: str
    ruoli: tuple
    stile: str = "primario"          # primario | positivo | pericolo | neutro
    richiede_nota: bool = False
    solo_proponente: bool = False
    descrizione: str = ""


TRANSIZIONI: tuple[Transizione, ...] = (
    Transizione(
        azione="invia",
        label="Invia alla Funzione AI",
        da=(Stato.BOZZA,),
        a=Stato.INVIATA,
        ruoli=(Ruolo.OWNER,),
        solo_proponente=True,
        descrizione="L'owner invia l'esigenza/opportunità della propria funzione.",
    ),
    Transizione(
        azione="prendi_in_carico",
        label="Prendi in carico — qualifica",
        da=(Stato.INVIATA,),
        a=Stato.IN_QUALIFICA,
        ruoli=(Ruolo.AI_OFFICER,),
        descrizione="Verifica completezza + qualifica, fattibilità, complessità.",
    ),
    Transizione(
        azione="richiedi_integrazione",
        label="Richiedi integrazione",
        da=(Stato.INVIATA, Stato.IN_QUALIFICA),
        a=Stato.BOZZA,
        ruoli=(Ruolo.AI_OFFICER,),
        stile="neutro",
        richiede_nota=True,
        descrizione="Dati di input incompleti: torna all'owner per integrazione.",
    ),
    Transizione(
        azione="respingi_qualifica",
        label="Respingi — non attinente/fattibile",
        da=(Stato.IN_QUALIFICA,),
        a=Stato.RESPINTA,
        ruoli=(Ruolo.AI_OFFICER,),
        stile="pericolo",
        richiede_nota=True,
        descrizione="Esito negativo del filtro della Funzione AI.",
    ),
    Transizione(
        azione="presenta_approvazione",
        label="Presenta per l'approvazione",
        da=(Stato.IN_QUALIFICA,),
        a=Stato.IN_APPROVAZIONE,
        ruoli=(Ruolo.AI_OFFICER,),
        descrizione="La Funzione AI sottopone la proposta all'Approvatore.",
    ),
    Transizione(
        azione="approva",
        label="Approva",
        da=(Stato.IN_APPROVAZIONE,),
        a=Stato.APPROVATA,
        ruoli=(Ruolo.APPROVATORE,),
        stile="positivo",
        descrizione="Decisione dell'Approvatore: approvazione.",
    ),
    Transizione(
        azione="respingi",
        label="Non approvare",
        da=(Stato.IN_APPROVAZIONE,),
        a=Stato.RESPINTA,
        ruoli=(Ruolo.APPROVATORE,),
        stile="pericolo",
        richiede_nota=True,
        descrizione="Decisione dell'Approvatore: non approvazione.",
    ),
    Transizione(
        azione="avvia_progetto",
        label="Avvia il progetto",
        da=(Stato.APPROVATA,),
        a=Stato.ATTIVO,
        ruoli=(Ruolo.AI_OFFICER,),
        stile="positivo",
        descrizione="Con l'approvazione, la Funzione AI avvia il progetto con il referente di area.",
    ),
    Transizione(
        azione="avvia_monitoraggio",
        label="Passa a monitoraggio (SAL)",
        da=(Stato.ATTIVO,),
        a=Stato.MONITORAGGIO,
        ruoli=(Ruolo.AI_OFFICER,),
        descrizione="Monitoraggio periodico (SAL) e risultati ottenuti.",
    ),
    Transizione(
        azione="completa",
        label="Completa",
        da=(Stato.ATTIVO, Stato.MONITORAGGIO),
        a=Stato.COMPLETATO,
        ruoli=(Ruolo.AI_OFFICER,),
        stile="neutro",
        descrizione="Chiusura del progetto al raggiungimento degli obiettivi.",
    ),
)

_PER_AZIONE = {t.azione: t for t in TRANSIZIONI}


def transizione(azione: str) -> Transizione | None:
    return _PER_AZIONE.get(azione)


def puo_eseguire(richiesta, utente, azione: str) -> bool:
    """True se 'utente' può eseguire 'azione' sulla 'richiesta' nello stato attuale.

    L'autorizzazione è per ruolo (separazione dei compiti): nemmeno un superuser
    bypassa la catena di approvazione dall'interfaccia. L'override resta possibile
    solo dall'admin Django, dove l'azione è comunque tracciata.
    """
    t = transizione(azione)
    if t is None:
        return False
    if richiesta.stato not in t.da:
        return False
    if utente.ruolo not in t.ruoli:
        return False
    if t.solo_proponente and richiesta.proponente_id != utente.id:
        return False
    return True


def azioni_disponibili(richiesta, utente) -> list[Transizione]:
    """Elenco delle transizioni eseguibili adesso da questo utente."""
    return [t for t in TRANSIZIONI if puo_eseguire(richiesta, utente, t.azione)]
