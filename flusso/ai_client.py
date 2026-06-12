"""
Client minimale per l'API Messages di Anthropic (solo libreria standard).

Genera la lettura esecutiva dei KPI. Nessuna dipendenza esterna: usa urllib.
A runtime l'app deve poter raggiungere api.anthropic.com (HTTPS in uscita).
"""

import json
import logging
import urllib.error
import urllib.request

from .kpi import riassunto_per_ai

audit = logging.getLogger("flusso.audit")

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = 60


def prova_connessione(chiave: str, modello: str) -> tuple[bool, str]:
    """Verifica chiave + modello + raggiungibilità con una chiamata minima.

    Ritorna (True, dettaglio) oppure (False, messaggio_errore).
    """
    import time

    if not chiave:
        return False, "Nessuna API key disponibile (inseriscila nel form o usa ANTHROPIC_API_KEY)."
    if not modello:
        return False, "Nessun modello selezionato."

    payload = {
        "model": modello,
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "ping"}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": chiave,
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    inizio = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        ms = round((time.perf_counter() - inizio) * 1000)
        return True, f"modello «{modello}» raggiungibile · {ms} ms"
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            msg = err.get("error", {}).get("message", f"HTTP {e.code}")
        except Exception:
            msg = f"HTTP {e.code}"
        if e.code in (401, 403):
            msg = "chiave API non valida o non autorizzata"
        elif e.code == 404:
            msg = f"modello «{modello}» non trovato"
        return False, f"errore {e.code}: {msg}"
    except urllib.error.URLError as e:
        return False, (f"connessione non riuscita ({e.reason}). "
                       "Verificare l'uscita HTTPS verso api.anthropic.com.")
    except Exception as e:  # pragma: no cover
        return False, f"errore imprevisto: {e}"


def genera_analisi(kpi: dict, config) -> tuple[str | None, str | None]:
    """Ritorna (testo, None) in caso di successo, (None, messaggio_errore) altrimenti."""
    chiave = config.chiave_effettiva()
    if not chiave:
        return None, "API key non configurata."

    riassunto = riassunto_per_ai(kpi, config.includi_titoli)
    contenuto_utente = (
        "Ecco i KPI correnti del portafoglio progetti AI:\n\n"
        + riassunto
        + "\n\nScrivi la lettura esecutiva per il Comitato AI."
    )
    payload = {
        "model": config.modello,
        "max_tokens": int(config.max_tokens or 1200),
        "system": config.prompt_effettivo,
        "messages": [{"role": "user", "content": contenuto_utente}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": chiave,
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parti = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        testo = "\n".join(p for p in parti if p).strip()
        if not testo:
            return None, "Risposta vuota dal modello."
        audit.info("kpi_ai_analisi modello=%s token_out=%s", config.modello,
                   data.get("usage", {}).get("output_tokens", "?"))
        return testo, None
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            msg = err.get("error", {}).get("message", f"HTTP {e.code}")
        except Exception:
            msg = f"HTTP {e.code}"
        return None, f"Errore API ({e.code}): {msg}"
    except urllib.error.URLError as e:
        return None, (f"Connessione non riuscita: {e.reason}. "
                      "Verificare l'uscita HTTPS verso api.anthropic.com.")
    except Exception as e:  # pragma: no cover
        return None, f"Errore imprevisto: {e}"
