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

# Istruzione di formato comune alle chiamate che attendono JSON. I tag <json></json>
# isolano la risposta finale anche quando il modello antepone del ragionamento
# (alcuni modelli non rispettano "solo JSON" e non supportano il prefill assistant).
_ISTRUZIONE_JSON = (
    "Se ti serve, ragiona pure brevemente prima. La risposta FINALE deve essere un "
    "oggetto JSON valido racchiuso ESATTAMENTE tra i tag <json> e </json>, e quel "
    "blocco deve contenere SOLO il JSON (niente backtick, niente commenti, niente testo)."
)


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


# =============================================================================
# Classificazione del rischio (AI Act, NIS2, GDPR) e stima incrementi — via Claude
# =============================================================================

RISCHIO_TIMEOUT = 45


def _estrai_json(testo: str):
    """Estrae un oggetto JSON dalla risposta del modello.

    Ordine: prima il contenuto tra i tag <json>...</json> (formato richiesto nei
    prompt, robusto a un eventuale ragionamento iniziale del modello); poi i
    fallback su backtick, parsing diretto e prima graffa.
    """
    import re
    t = (testo or "").strip()
    m = re.search(r"<json>\s*(.*?)\s*</json>", t, re.S | re.I)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            t = m.group(1).strip()  # tag presente ma con rumore: continua coi fallback sul contenuto
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _descrizione_progetto(richiesta) -> str:
    righe = [
        f"Funzione aziendale richiedente: {richiesta.get_funzione_display()}",
        f"Titolo: {richiesta.titolo}",
    ]
    if richiesta.tipo_soluzione:
        righe.append(f"Tipo di soluzione: {richiesta.tipo_soluzione}")
    if richiesta.ai_autonomia:
        righe.append(f"Grado di autonomia dell'AI: {richiesta.get_ai_autonomia_display()}")
    if richiesta.ai_deployment:
        righe.append(f"Infrastruttura prevista per i modelli: {richiesta.get_ai_deployment_display()}")
    righe.append(f"Descrizione: {richiesta.descrizione}")
    return "\n".join(righe)


def _chiama_modello(config, system, contenuto, max_tokens, timeout):
    """Chiamata POST a Claude. Ritorna (data_dict, None) o (None, errore)."""
    chiave = config.chiave_effettiva()
    if not chiave:
        return None, "API key non configurata."
    if not config.modello:
        return None, "Nessun modello selezionato."
    payload = {
        "model": config.modello,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": contenuto}],
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-api-key": chiave,
                 "anthropic-version": ANTHROPIC_VERSION},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
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


def _testo_risposta(data) -> str:
    parti = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(p for p in parti if p).strip()


def classifica_rischio(richiesta, config, tipo) -> tuple[dict | None, str | None]:
    """Classificazione PRELIMINARE di UNA dimensione di rischio (AI Act, NIS2, GDPR).

    Usa la stessa configurazione (chiave + modello) della lettura KPI e il prompt
    della dimensione richiesta. Ritorna ({'categoria','motivazione','riferimenti',
    'obblighi','modello'}, None) in caso di successo, oppure (None, errore).
    """
    from .models import categorie_valide  # evita import circolari a livello di modulo

    valide = categorie_valide(tipo)
    if not valide:
        return None, f"Tipo di rischio sconosciuto: {tipo}."
    system = config.prompt_rischio_effettivo(tipo)
    contenuto = ("Classifica il rischio del seguente progetto.\n\n"
                 + _descrizione_progetto(richiesta)
                 + "\n\n" + _ISTRUZIONE_JSON)
    data, errore = _chiama_modello(config, system, contenuto, 1500, RISCHIO_TIMEOUT)
    if errore:
        return None, errore
    obj = _estrai_json(_testo_risposta(data))
    if not isinstance(obj, dict):
        return None, "Risposta del modello non interpretabile come JSON."
    categoria = str(obj.get("categoria", "")).strip().upper()
    if categoria not in valide:
        return None, f"Categoria non valida per {tipo}: «{obj.get('categoria')}»."
    audit.info("rischio_ai richiesta=%s tipo=%s categoria=%s modello=%s token_out=%s",
               getattr(richiesta, "codice", "?"), tipo, categoria, config.modello,
               data.get("usage", {}).get("output_tokens", "?"))
    obblighi_raw = obj.get("obblighi", "")
    if isinstance(obblighi_raw, (list, tuple)):
        obblighi = "\n".join(str(x).strip() for x in obblighi_raw if str(x).strip())
    else:
        obblighi = str(obblighi_raw).strip()
    return {
        "categoria": categoria,
        "motivazione": str(obj.get("motivazione", "")).strip(),
        "riferimenti": str(obj.get("riferimenti", "")).strip()[:300],
        "obblighi": obblighi,
        "modello": config.modello,
    }, None


PROMPT_INCREMENTI = (
    "Sei un analista che stima i ritorni ATTESI di un progetto di AI per ISEO Group "
    "(produttore di sistemi di chiusura e controllo accessi). In base alla descrizione, "
    "stima in modo realistico e PRUDENTE: l'incremento di EFFICIENZA e di QUALITA' che il "
    "progetto puo' portare al processo (percentuali intere 0-100; 0 se un incremento non e' "
    "plausibile) e il BENEFICIO economico annuo atteso in EURO (0 se il valore e' prevalentemente "
    "qualitativo). Non essere ottimista: sono stime preliminari, modificabili dall'owner.\n"
    'Il JSON deve avere ESATTAMENTE queste chiavi: {"efficienza": <numero 0-100>, '
    '"qualita": <numero 0-100>, "beneficio": <euro/anno>, "beneficio_nota": "<breve nota, max 1 frase>"}'
)


def stima_incrementi(richiesta, config) -> tuple[dict | None, str | None]:
    """Stima preliminare di incremento efficienza/qualita' (%). Via Claude.

    Ritorna ({'efficienza': float|None, 'qualita': float|None, 'modello': str}, None)
    oppure (None, errore).
    """
    contenuto = ("Stima gli incrementi del seguente progetto.\n\n"
                 + _descrizione_progetto(richiesta)
                 + "\n\n" + _ISTRUZIONE_JSON)
    data, errore = _chiama_modello(config, PROMPT_INCREMENTI, contenuto, 1500, RISCHIO_TIMEOUT)
    if errore:
        return None, errore
    obj = _estrai_json(_testo_risposta(data))
    if not isinstance(obj, dict):
        return None, "Risposta del modello non interpretabile come JSON."

    def _pct(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(100.0, round(x, 1)))

    def _eur(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return max(0.0, round(x, 2))

    eff, qual = _pct(obj.get("efficienza")), _pct(obj.get("qualita"))
    ben = _eur(obj.get("beneficio"))
    if eff is None and qual is None and ben is None:
        return None, "Nessun valore numerico valido nella risposta."
    audit.info("incrementi_ai richiesta=%s eff=%s qual=%s beneficio=%s modello=%s",
               getattr(richiesta, "codice", "?"), eff, qual, ben, config.modello)
    return {"efficienza": eff, "qualita": qual, "beneficio": ben,
            "beneficio_nota": str(obj.get("beneficio_nota") or "")[:200],
            "modello": config.modello}, None


PROMPT_COSTO_TOKEN = (
    "Sei un analista che stima il COSTO DI CONSUMO TOKEN (API di modelli LLM) di un progetto di AI "
    "per ISEO Group (produttore di sistemi di chiusura e controllo accessi). Stima un importo in EURO, "
    "realistico e PRUDENTE, per il solo consumo di token dei modelli. Regole tassative:\n"
    "- Se l'infrastruttura prevista e' 'LLM locale (on-premise)', il costo dei token API e' nullo "
    "(i costi sono di infrastruttura, non di token): restituisci 0.\n"
    "- Se e' 'API (cloud)', stima il costo in base all'uso atteso desumibile dalla descrizione.\n"
    "- Se e' 'Ibrido', stima solo la quota a consumo via API.\n"
    "- Riferisci l'importo alla PERIODICITA' e all'AMBITO indicati: se l'ambito e' 'per utente' o "
    "'per team', stima il costo per UN SINGOLO utente/team; se 'complessivo' o assente, il costo totale.\n"
    "- E' una stima PRELIMINARE, sara' rivista da una persona: non essere ottimista.\n"
    'Il JSON deve avere ESATTAMENTE questa chiave: {"costo": <numero in euro, maggiore o uguale a 0>}'
)


def stima_costo_token(richiesta, config) -> tuple[dict | None, str | None]:
    """Stima preliminare del costo token (EUR) per la periodicita'/ambito scelti. Via Claude.

    Ritorna ({'costo': float, 'modello': str}, None) oppure (None, errore).
    """
    contesto = [_descrizione_progetto(richiesta)]
    if richiesta.costo_token_periodicita:
        contesto.append(f"Periodicita' per la stima: {richiesta.get_costo_token_periodicita_display()}")
    if richiesta.costo_token_ambito:
        contesto.append(f"Ambito per la stima: {richiesta.get_costo_token_ambito_display()}")
    contenuto = ("Stima il costo di consumo token del seguente progetto.\n\n"
                 + "\n".join(contesto)
                 + "\n\n" + _ISTRUZIONE_JSON)
    data, errore = _chiama_modello(config, PROMPT_COSTO_TOKEN, contenuto, 1500, RISCHIO_TIMEOUT)
    if errore:
        return None, errore
    obj = _estrai_json(_testo_risposta(data))
    if not isinstance(obj, dict):
        return None, "Risposta del modello non interpretabile come JSON."
    try:
        costo = float(obj.get("costo"))
    except (TypeError, ValueError):
        return None, "Nessun importo valido nella risposta."
    costo = max(0.0, round(costo, 2))
    audit.info("costo_token_ai richiesta=%s costo=%s modello=%s",
               getattr(richiesta, "codice", "?"), costo, config.modello)
    return {"costo": costo, "modello": config.modello}, None


PROMPT_ANALISI_COMPLETA = (
    "Sei la Funzione AI di ISEO. Ricevi una richiesta di caso d'uso AI (funzione aziendale, "
    "titolo, descrizione, eventuale numero utenti) e produci un'ANALISI DI FATTIBILITA' completa, "
    "scritta come la scriverebbe l'AI Officer, piu' la stima dei parametri di progetto.\n"
    "Scrivi in ITALIANO, professionale e sintetico. Sii prudente e realistico: e' una stima "
    "preliminare che sara' rivista da una persona, non essere ottimista.\n"
    "Il JSON deve avere ESATTAMENTE queste chiavi:\n"
    '  "fattibilita": stringa di 3-6 frasi (valutazione di fattibilita, approccio tecnico, rischi e dipendenze principali),\n'
    '  "autonomia": uno tra "NON_AGENTICA", "AGENTICA_SUPPORTO", "AGENTICA_AUTONOMA",\n'
    '  "deployment": uno tra "API" (cloud), "LOCALE" (on-premise), "IBRIDO",\n'
    '  "effort_ore": intero (ore-uomo di sviluppo stimate),\n'
    '  "costo_token_mensile_per_utente": numero in euro (consumo token al mese per utente; 0 se non applicabile).\n'
    "NON stimare il beneficio economico ne' le percentuali di efficienza/qualita: "
    "non fanno parte di questa analisi (sono di competenza dell'owner)."
)


def genera_analisi_completa(richiesta, config):
    """Genera l'intera analisi (fattibilita + parametri) in una sola chiamata.

    Ritorna (dati, None) con un dict pronto per Richiesta.applica_analisi_ai, oppure
    (None, errore). I valori restano una proposta: l'AI Officer li verifica e modifica.
    """
    contesto = [_descrizione_progetto(richiesta)]
    if richiesta.numero_utenti:
        contesto.append(f"Numero utenti previsti: {richiesta.numero_utenti}")
    contenuto = ("Analizza la seguente richiesta e compila l'analisi completa.\n\n"
                 + "\n".join(contesto) + "\n\n" + _ISTRUZIONE_JSON)
    data, errore = _chiama_modello(config, PROMPT_ANALISI_COMPLETA, contenuto, 2000, RISCHIO_TIMEOUT)
    if errore:
        return None, errore
    obj = _estrai_json(_testo_risposta(data))
    if not isinstance(obj, dict):
        return None, "Risposta del modello non interpretabile come JSON."
    audit.info("analisi_ai richiesta=%s modello=%s", getattr(richiesta, "codice", "?"), config.modello)
    return obj, None


PROMPT_RIPARTIZIONE = (
    "Sei la Funzione AI di ISEO. Ti viene dato un progetto AI interno (titolo, descrizione, "
    "analisi di fattibilita', tipo di AI, infrastruttura) e il suo effort TOTALE gia' deciso, in ore.\n"
    "NON devi ristimare il totale: devi solo RIPARTIRLO in percentuale sulle attivita' del ciclo "
    "di vita del progetto. Tieni conto che il codice viene scritto con forte supporto dell'AI, "
    "quindi lo SVILUPPO pesa tipicamente meno che in un progetto tradizionale, mentre analisi, "
    "test con gli utenti, compliance e adozione pesano in proporzione di piu'. Sii realistico e "
    "specifico per QUESTO progetto (es. un progetto vendor/attivazione ha sviluppo quasi nullo; "
    "un progetto con dati personali ha compliance alta).\n"
    "Attivita' ammesse (usa ESATTAMENTE questi codici):\n"
    "  ANALISI (analisi e progettazione), SVILUPPO (implementazione), TEST (test e validazione), "
    "COMPLIANCE (rischio, GDPR/AI Act, documentazione), INFRASTRUTTURA (deploy e integrazioni IT), "
    "ADOZIONE (formazione e avvio all'uso).\n"
    "Figura prevalente per ogni attivita' (usa ESATTAMENTE questi codici, sono RUOLI, mai persone):\n"
    "  FUNZIONE_AI, OWNER (owner del progetto o suoi delegati), INFRA (IT/infrastruttura), "
    "PRESIDI (CISO/DPO/Legale), UTENTI (utenti chiave).\n"
    'Il JSON deve avere ESATTAMENTE questa forma: {"voci": [{"attivita": "SVILUPPO", '
    '"figura": "FUNZIONE_AI", "percento": 25}, ...]}. Le percentuali sono interi che sommano 100; '
    "ometti le attivita' non pertinenti."
)


def stima_ripartizione_effort(richiesta, config):
    """Propone la ripartizione percentuale dell'effort sulle attività.

    Ritorna (lista_voci, None) — ogni voce {attivita, figura, percento} — oppure
    (None, errore). Il totale ore NON viene mai modificato: la conversione in ore
    e la quadratura sono deterministiche (Richiesta.applica_ripartizione_effort).
    """
    contesto = [_descrizione_progetto(richiesta)]
    if richiesta.analisi_fattibilita:
        contesto.append(f"Analisi di fattibilita': {richiesta.analisi_fattibilita}")
    if richiesta.ai_autonomia:
        contesto.append(f"Tipo AI: {richiesta.get_ai_autonomia_display()}")
    if richiesta.ai_deployment:
        contesto.append(f"Infrastruttura: {richiesta.get_ai_deployment_display()}")
    contesto.append(f"EFFORT TOTALE da ripartire: {richiesta.effort_ore} ore.")
    contenuto = ("Ripartisci l'effort del seguente progetto.\n\n"
                 + "\n".join(contesto) + "\n\n" + _ISTRUZIONE_JSON)
    data, errore = _chiama_modello(config, PROMPT_RIPARTIZIONE, contenuto, 1500, RISCHIO_TIMEOUT)
    if errore:
        return None, errore
    obj = _estrai_json(_testo_risposta(data))
    if not isinstance(obj, dict) or not isinstance(obj.get("voci"), list):
        return None, "Risposta del modello non interpretabile come JSON con «voci»."
    audit.info("ripartizione_ai richiesta=%s voci=%s modello=%s",
               getattr(richiesta, "codice", "?"), len(obj["voci"]), config.modello)
    return obj["voci"], None
