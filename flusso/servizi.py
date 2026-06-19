"""Orchestrazione AI: classificazione delle tre dimensioni di rischio e stima incrementi.

Tutte le funzioni sono fail-safe (non sollevano eccezioni verso il chiamante) e
usano la configurazione AI esistente (stessa chiave e stesso modello della
lettura KPI). Sono richiamate sia dalle view sia dall'import (seed_demo).
"""

import logging

from .ai_client import classifica_rischio, stima_incrementi
from .models import ConfigurazioneAI, TipoRischio

log = logging.getLogger("flusso.audit")


def _config_pronta():
    cfg = ConfigurazioneAI.load()
    if not cfg.abilitato or not cfg.configurata:
        return None
    return cfg


def classifica_tutti_i_rischi(richiesta, attore=None) -> dict:
    """Classifica (o riclassifica) le tre dimensioni di rischio di una richiesta.

    Ritorna {'ok': [tipi riusciti], 'errori': {tipo: messaggio}}.
    Non sovrascrive le dimensioni già validate dal presidio competente.
    """
    esito = {"ok": [], "errori": {}}
    cfg = _config_pronta()
    if cfg is None:
        esito["errori"]["_"] = "Analisi AI non disponibile (abilitazione/API key)."
        return esito
    richiesta.assicura_classificazioni()
    classi = {c.tipo: c for c in richiesta.classificazioni.all()}
    for tipo, _ in TipoRischio.choices:
        c = classi.get(tipo)
        if c is None:
            continue
        try:
            dati, errore = classifica_rischio(richiesta, cfg, tipo)
            if errore:
                esito["errori"][tipo] = errore
            else:
                c.applica_ai(dati["categoria"], motivazione=dati["motivazione"],
                             riferimenti=dati["riferimenti"], obblighi=dati["obblighi"],
                             modello=dati["modello"], attore=attore)
                esito["ok"].append(tipo)
        except Exception as e:  # noqa: BLE001
            log.warning("classifica richiesta=%s tipo=%s eccezione=%s", richiesta.codice, tipo, e)
            esito["errori"][tipo] = f"errore imprevisto: {e}"
    return esito


def stima_incrementi_se_serve(richiesta, attore=None) -> bool:
    """Stima gli incrementi mancanti, UNA sola volta. True se ha valorizzato qualcosa."""
    if richiesta.incrementi_ai_stimati:
        return False
    eff_manca = richiesta.incremento_efficienza is None
    qual_manca = richiesta.incremento_qualitativo is None
    if not eff_manca and not qual_manca:
        richiesta.incrementi_ai_stimati = True
        richiesta.save(update_fields=["incrementi_ai_stimati"])
        return False
    cfg = _config_pronta()
    if cfg is None:
        return False  # riprova al prossimo salvataggio quando l'AI è disponibile
    try:
        dati, errore = stima_incrementi(richiesta, cfg)
        if errore or not dati:
            return False
        valorizzati = richiesta.applica_stima_incrementi(
            efficienza=dati.get("efficienza"), qualita=dati.get("qualita"),
            modello=dati.get("modello", ""), attore=attore)
        return bool(valorizzati)
    except Exception as e:  # noqa: BLE001
        log.warning("stima incrementi richiesta=%s eccezione=%s", richiesta.codice, e)
        return False


def genera_tutto(richiesta, attore=None) -> dict:
    """All'import: stima gli incrementi mancanti + classifica le tre dimensioni di rischio."""
    incr = stima_incrementi_se_serve(richiesta, attore=attore)
    risk = classifica_tutti_i_rischi(richiesta, attore=attore)
    return {"incrementi": incr, "rischi_ok": len(risk["ok"]), "rischi_errori": risk["errori"]}
