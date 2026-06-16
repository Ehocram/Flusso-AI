"""Notifiche Teams (opzionali) sui cambi di stato delle richieste.

Tutto è opt-in e a prova di guasto:
- se le notifiche Teams sono disattivate o l'URL non è impostato, le funzioni
  non fanno nulla;
- l'invio HTTP avviene in un thread separato con timeout, e ogni eccezione è
  catturata e solo registrata a log: una notifica non riuscita (rete assente,
  URL errato, endpoint non raggiungibile) NON può mai interrompere o far
  fallire una transizione di workflow.

Il payload usa il formato MessageCard, accettato dai webhook dei flussi
Power Automate («Workflows») che sostituiscono i vecchi Office 365 Connectors.
"""

import json
import logging
import threading
import urllib.request

from .models import ConfigurazioneAI
from .workflow import Stato

log = logging.getLogger("flusso.notifiche")

# Cambi di stato considerati "importanti" (milestone del processo).
AZIONI_IMPORTANTI = {
    "invia", "approva", "respingi", "respingi_qualifica", "avvia_progetto", "completa",
}

_VERDE, _ROSSO, _BLU = "1F8A4C", "E2001A", "1D6FB8"
_COLORE_PER_STATO = {
    Stato.APPROVATA: _VERDE, Stato.ATTIVO: _VERDE, Stato.COMPLETATO: _VERDE,
    Stato.RESPINTA: _ROSSO,
}


def _spedisci(url, payload):
    """POST del payload (eseguito in un thread; non deve mai propagare errori)."""
    try:
        dati = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=dati, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
    except Exception as e:  # noqa: BLE001
        log.warning("Notifica Teams non inviata: %s", e)


def notifica_transizione(request, richiesta, transizione):
    """Invia in background una scheda Teams per il cambio di stato, se configurato.

    No-op se le notifiche sono disattivate, se manca l'URL, o se l'evento non
    rientra nella selezione corrente. Non solleva mai eccezioni.
    """
    try:
        cfg = ConfigurazioneAI.load()
        if not cfg.teams_abilitato or not (cfg.teams_webhook_url or "").strip():
            return
        if cfg.teams_eventi == "importanti" and transizione.azione not in AZIONI_IMPORTANTI:
            return

        try:
            url_dettaglio = request.build_absolute_uri(richiesta.get_absolute_url())
        except Exception:  # noqa: BLE001
            url_dettaglio = ""

        attore = ""
        if transizione.attore_id:
            attore = transizione.attore.get_full_name() or transizione.attore.username

        facts = [
            {"name": "Codice", "value": richiesta.codice},
            {"name": "Funzione", "value": richiesta.get_funzione_display()},
            {"name": "Stato", "value": f"{transizione.stato_da_label} → {transizione.stato_a_label}"},
        ]
        if attore:
            facts.append({"name": "A cura di", "value": attore})
        if transizione.nota:
            facts.append({"name": "Nota", "value": transizione.nota})

        sezioni = [{
            "activityTitle": f"**{richiesta.titolo}**",
            "activitySubtitle": transizione.etichetta,
            "facts": facts,
            "markdown": True,
        }]
        if url_dettaglio:
            sezioni.append({"text": f"[Apri la richiesta]({url_dettaglio})", "markdown": True})

        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": _COLORE_PER_STATO.get(transizione.stato_a, _BLU),
            "summary": f"{richiesta.codice} · {transizione.etichetta}",
            "sections": sezioni,
        }

        threading.Thread(
            target=_spedisci, args=((cfg.teams_webhook_url or "").strip(), payload),
            daemon=True,
        ).start()
    except Exception as e:  # noqa: BLE001 — la notifica non deve mai rompere il flusso
        log.warning("Errore nella preparazione della notifica Teams: %s", e)
