"""
Macchina a stati del processo AI.

Catena di approvazione (separazione dei compiti, applicata lato server):
  Owner        invia la richiesta della propria funzione
  Funzione tecnica  qualifica/analizza e presenta per l'approvazione
  Approvatore  approva o respinge
  Funzione tecnica  avvia il progetto e ne segue il SAL fino alla chiusura
L'Auditor vede tutto in sola lettura (nessuna azione di workflow).

Ogni transizione dichiara stati di partenza, stato di arrivo e ruoli abilitati.
L'autorizzazione è per ruolo: nessuna azione è eseguibile aggirando questi vincoli.
"""

from dataclasses import dataclass, replace

from accounts.models import RUOLI_FUNZIONE, Ruolo
from django.db import models


class Stato(models.TextChoices):
    BOZZA = "BOZZA", "Bozza"
    INVIATA = "INVIATA", "Inviata alla Funzione tecnica"
    IN_QUALIFICA = "IN_QUALIFICA", "In qualifica e analisi"
    ATTESA_BUDGET = "ATTESA_BUDGET", "Attesa decisione budget (owner)"
    PRONTA_APPROVAZIONE = "PRONTA_APPROVAZIONE", "Pronta per approvazione"
    IN_APPROVAZIONE = "IN_APPROVAZIONE", "In approvazione"
    APPROVATA = "APPROVATA", "Approvata"
    RESPINTA = "RESPINTA", "Respinta"
    ARCHIVIATA = "ARCHIVIATA", "Archiviata dall'owner"
    ATTIVO = "ATTIVO", "Progetto attivo"
    MONITORAGGIO = "MONITORAGGIO", "In monitoraggio (SAL)"
    COMPLETATO = "COMPLETATO", "Completato"


# Stati finali: nessuna ulteriore transizione di workflow.
STATI_TERMINALI = {Stato.RESPINTA, Stato.ARCHIVIATA, Stato.COMPLETATO}

# Stati in cui il progetto è operativo e il SAL è aggiornabile.
STATI_OPERATIVI = {Stato.ATTIVO, Stato.MONITORAGGIO}

# Da qualsiasi stato (tranne bozza) si può riportare la richiesta in bozza.
_TUTTI_TRANNE_BOZZA = tuple(s for s in Stato if s != Stato.BOZZA)

# Stati in cui la scheda è congelata: in approvazione alla Direzione e oltre.
# Nessuna modifica consentita, a nessun ruolo.
STATI_MODIFICA_BLOCCATA = {Stato.IN_APPROVAZIONE, Stato.APPROVATA, Stato.ATTIVO,
                           Stato.MONITORAGGIO, Stato.COMPLETATO}

# Raggruppamento per la board (kanban) e i conteggi di dashboard.
FASI = {
    "in_coda": [Stato.BOZZA, Stato.INVIATA],
    "in_analisi": [Stato.IN_QUALIFICA, Stato.ATTESA_BUDGET],
    "pronte": [Stato.PRONTA_APPROVAZIONE],
    "in_approvazione": [Stato.IN_APPROVAZIONE],
    "approvati": [Stato.APPROVATA, Stato.ATTIVO, Stato.MONITORAGGIO],
    "chiusi": [Stato.COMPLETATO, Stato.RESPINTA, Stato.ARCHIVIATA],
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
    solo_ai: bool = False
    descrizione: str = ""

    def per(self, richiesta):
        """Copia con etichetta e descrizione risolte sulla funzione competente della richiesta
        (AI / Applicativa / IT Operations), al posto del placeholder {funzione}."""
        nome = getattr(richiesta, "funzione_competente_label", "Funzione tecnica")
        return replace(self, label=self.label.replace("{funzione}", nome),
                       descrizione=self.descrizione.replace("{funzione}", nome))


TRANSIZIONI: tuple[Transizione, ...] = (
    Transizione(
        azione="invia",
        label="Invia alla {funzione}",
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
        ruoli=RUOLI_FUNZIONE,
        descrizione="Verifica completezza + qualifica, fattibilità, complessità.",
    ),
    Transizione(
        azione="richiedi_integrazione",
        label="Richiedi integrazione",
        da=(Stato.INVIATA, Stato.IN_QUALIFICA),
        a=Stato.BOZZA,
        ruoli=RUOLI_FUNZIONE,
        stile="neutro",
        richiede_nota=True,
        descrizione="Dati di input incompleti: torna all'owner per integrazione.",
    ),
    Transizione(
        azione="respingi_qualifica",
        label="Respingi — non attinente/fattibile",
        da=(Stato.IN_QUALIFICA,),
        a=Stato.RESPINTA,
        ruoli=RUOLI_FUNZIONE,
        stile="pericolo",
        richiede_nota=True,
        descrizione="Esito negativo del filtro della {funzione}.",
    ),
    Transizione(
        azione="invia_a_budget",
        label="Invia all'owner per la decisione di budget",
        da=(Stato.IN_QUALIFICA,),
        a=Stato.ATTESA_BUDGET,
        ruoli=RUOLI_FUNZIONE,
        solo_ai=True,
        descrizione="Conclusa l'analisi dei costi, l'owner deve indicare se l'importo è a budget o extra budget.",
    ),
    Transizione(
        azione="conferma_budget",
        label="Conferma budget e prosegui",
        da=(Stato.ATTESA_BUDGET,),
        a=Stato.IN_QUALIFICA,
        ruoli=(Ruolo.OWNER,),
        solo_proponente=True,
        stile="positivo",
        descrizione="L'owner conferma se l'importo è a budget o extra budget: l'AI genera i rischi e il flusso prosegue.",
    ),
    Transizione(
        azione="rifiuta_progetto",
        label="Rifiuta e archivia",
        da=(Stato.ATTESA_BUDGET,),
        a=Stato.ARCHIVIATA,
        ruoli=(Ruolo.OWNER,),
        solo_proponente=True,
        stile="pericolo",
        richiede_nota=True,
        descrizione="L'owner non procede con il progetto: archiviazione con motivazione.",
    ),
    Transizione(
        azione="presenta_approvazione",
        label="Segna pronta per l'approvazione",
        da=(Stato.IN_QUALIFICA,),
        a=Stato.PRONTA_APPROVAZIONE,
        ruoli=RUOLI_FUNZIONE,
        descrizione="Rischi validati e budget deciso: la pratica è pronta. La {funzione} deciderà quando inviarla alla Direzione.",
    ),
    Transizione(
        azione="invia_in_approvazione",
        label="Invia in approvazione alla Direzione",
        da=(Stato.PRONTA_APPROVAZIONE,),
        a=Stato.IN_APPROVAZIONE,
        ruoli=RUOLI_FUNZIONE,
        stile="positivo",
        descrizione="Solo la {funzione} sottopone la pratica all'Approvatore.",
    ),
    Transizione(
        azione="riporta_in_bozza",
        label="Riporta in bozza",
        da=_TUTTI_TRANNE_BOZZA,
        a=Stato.BOZZA,
        ruoli=RUOLI_FUNZIONE,
        stile="pericolo",
        richiede_nota=True,
        descrizione="La {funzione} riporta la richiesta in bozza da qualsiasi stato: decisione budget, date e validazioni dei rischi vengono azzerate (le categorie proposte restano).",
    ),
    Transizione(
        azione="riporta_in_bozza_owner",
        label="Riporta in bozza",
        da=_TUTTI_TRANNE_BOZZA,
        a=Stato.BOZZA,
        ruoli=(Ruolo.OWNER,),
        stile="pericolo",
        richiede_nota=True,
        solo_proponente=True,
        descrizione="L'owner riporta in bozza una propria richiesta da qualsiasi stato; budget, date e validazioni vengono azzerati.",
    ),
    Transizione(
        azione="ritira_da_approvazione",
        label="Riporta a «Pronta per approvazione»",
        da=(Stato.IN_APPROVAZIONE,),
        a=Stato.PRONTA_APPROVAZIONE,
        ruoli=RUOLI_FUNZIONE,
        stile="neutro",
        descrizione="La {funzione} ritira la pratica dalla coda della Direzione: torna modificabile e potrà essere reinviata.",
    ),
    Transizione(
        azione="approva",
        label="Approva e avvia",
        da=(Stato.PRONTA_APPROVAZIONE, Stato.IN_APPROVAZIONE),
        a=Stato.APPROVATA,
        ruoli=RUOLI_FUNZIONE,
        stile="positivo",
        descrizione="Approvazione operativa della {funzione}. L'approvazione formale della Direzione arriverà con il flusso budget/extra budget IT.",
    ),
    Transizione(
        azione="respingi",
        label="Non approvare",
        da=(Stato.PRONTA_APPROVAZIONE, Stato.IN_APPROVAZIONE),
        a=Stato.RESPINTA,
        ruoli=RUOLI_FUNZIONE,
        stile="pericolo",
        richiede_nota=True,
        descrizione="Decisione dell'Approvatore: non approvazione.",
    ),
    Transizione(
        azione="avvia_progetto",
        label="Avvia il progetto",
        da=(Stato.APPROVATA,),
        a=Stato.ATTIVO,
        ruoli=RUOLI_FUNZIONE,
        stile="positivo",
        descrizione="Con l'approvazione, la {funzione} avvia il progetto con il referente di area.",
    ),
    Transizione(
        azione="avvia_monitoraggio",
        label="Passa a monitoraggio (SAL)",
        da=(Stato.ATTIVO,),
        a=Stato.MONITORAGGIO,
        ruoli=RUOLI_FUNZIONE,
        descrizione="Monitoraggio periodico (SAL) e risultati ottenuti.",
    ),
    Transizione(
        azione="completa",
        label="Completa",
        da=(Stato.ATTIVO, Stato.MONITORAGGIO),
        a=Stato.COMPLETATO,
        ruoli=RUOLI_FUNZIONE,
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
    # Azioni riservate al perimetro AI (es. decisione di budget dell'owner sui
    # costi token): non si propongono su Application / IT Operation.
    if t.solo_ai and richiesta.tipo != "AI":
        return False
    return True


def azioni_disponibili(richiesta, utente) -> list[Transizione]:
    """Elenco delle transizioni eseguibili adesso da questo utente."""
    return [t.per(richiesta) for t in TRANSIZIONI if puo_eseguire(richiesta, utente, t.azione)]
