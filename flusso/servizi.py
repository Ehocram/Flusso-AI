"""Orchestrazione AI: classificazione delle tre dimensioni di rischio e stima incrementi.

Tutte le funzioni sono fail-safe (non sollevano eccezioni verso il chiamante) e
usano la configurazione AI esistente (stessa chiave e stesso modello della
lettura KPI). Sono richiamate sia dalle view sia dall'import (seed_demo).
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from .ai_client import (classifica_rischio, genera_analisi_completa, stima_costo_token,
                        stima_incrementi, stima_ripartizione_effort)
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
    tipi = [t for t, _ in TipoRischio.choices if classi.get(t) is not None]

    # Le chiamate AI (sola rete, nessun accesso al DB) girano in PARALLELO: tre chiamate
    # sequenziali da ~20-25s l'una sommavano oltre il timeout della richiesta web
    # (gunicorn/proxy) -> 500. In parallelo il tempo totale e' ~quello della singola
    # chiamata. I risultati vengono poi applicati al DB in modo SEQUENZIALE.
    risultati = {}
    with ThreadPoolExecutor(max_workers=len(tipi) or 1) as ex:
        futuri = {ex.submit(classifica_rischio, richiesta, cfg, t): t for t in tipi}
        for fut in futuri:
            tipo = futuri[fut]
            try:
                risultati[tipo] = fut.result()
            except Exception as e:  # noqa: BLE001
                log.warning("classifica richiesta=%s tipo=%s eccezione=%s", richiesta.codice, tipo, e)
                risultati[tipo] = (None, f"errore imprevisto: {e}")

    for tipo in tipi:
        dati, errore = risultati.get(tipo, (None, "nessun risultato"))
        if errore:
            esito["errori"][tipo] = errore
            continue
        try:
            classi[tipo].applica_ai(
                dati["categoria"], motivazione=dati["motivazione"],
                riferimenti=dati["riferimenti"], obblighi=dati["obblighi"],
                modello=dati["modello"], attore=attore)
            esito["ok"].append(tipo)
        except Exception as e:  # noqa: BLE001
            log.warning("applica_ai richiesta=%s tipo=%s eccezione=%s", richiesta.codice, tipo, e)
            esito["errori"][tipo] = f"errore imprevisto: {e}"
    return esito


def stima_incrementi_se_serve(richiesta, attore=None) -> bool:
    """Stima beneficio e incrementi mancanti, UNA sola volta. True se ha valorizzato qualcosa."""
    from .models import TipoProgetto
    if richiesta.tipo != TipoProgetto.AI:
        return False  # incrementi qualitativo/efficienza: solo sui progetti AI
    if richiesta.incrementi_ai_stimati:
        return False
    eff_manca = richiesta.incremento_efficienza is None
    qual_manca = richiesta.incremento_qualitativo is None
    ben_manca = richiesta.saving_economico is None
    if not eff_manca and not qual_manca and not ben_manca:
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
            beneficio=dati.get("beneficio"), beneficio_nota=dati.get("beneficio_nota", ""),
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


def stima_costo_token_se_serve(richiesta, attore=None) -> bool:
    """Se l'importo token manca e c'e' abbastanza contesto, lo stima con l'AI (una volta).

    Richiede periodicita' e ambito impostati (la struttura del costo) per una stima
    sensata. L'importo resta sempre modificabile a mano. True se ha valorizzato.
    """
    if richiesta.costo_token_ai is not None:
        return False
    if not (richiesta.costo_token_periodicita and richiesta.costo_token_ambito):
        return False
    cfg = _config_pronta()
    if cfg is None:
        return False
    try:
        from decimal import Decimal
        dati, errore = stima_costo_token(richiesta, cfg)
        if errore or not dati:
            return False
        richiesta.costo_token_ai = Decimal(str(dati["costo"]))
        richiesta.costo_token_ai_stimato = True
        richiesta.save(update_fields=["costo_token_ai", "costo_token_ai_stimato"])
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("stima costo token richiesta=%s eccezione=%s", richiesta.codice, e)
        return False


def compila_analisi_con_ai(richiesta, attore=None):
    """Precompila l'intera analisi (fattibilita + parametri) con l'AI.

    Ritorna (True, None) se i campi sono stati proposti, (False, errore) altrimenti.
    I valori restano modificabili dall'AI Officer.
    """
    cfg = _config_pronta()
    if cfg is None:
        return False, "Analisi AI non disponibile (abilitazione o API key)."
    dati, errore = genera_analisi_completa(richiesta, cfg)
    if errore:
        return False, errore
    campi = richiesta.applica_analisi_ai(dati)
    if not campi:
        return False, "L'AI non ha restituito valori utilizzabili."
    return True, None


def _prossimo_giorno_lavorativo(d):
    from datetime import timedelta
    while d.weekday() >= 5:  # 5=sabato, 6=domenica
        d += timedelta(days=1)
    return d


def _aggiungi_giorni_lavorativi(d, giorni):
    from datetime import timedelta
    avanti = 0
    while avanti < giorni:
        d += timedelta(days=1)
        if d.weekday() < 5:
            avanti += 1
    return d


def pianifica_su_approvazione(richiesta, forza=False):
    """All'approvazione propone data inizio e consegna prevista (utili ai KPI).

    Calcolo deterministico a partire dall'effort stimato dall'AI: inizio = primo
    giorno lavorativo da oggi; durata in giorni lavorativi ~ effort/6 (ore produttive
    al giorno), minimo una settimana. Non sovrascrive date gia' impostate a mano,
    salvo forza=True. Tutto resta modificabile dalla pagina di schedulazione.
    Ritorna True se ha impostato le date.
    """
    from datetime import date
    if not forza and (richiesta.data_inizio or richiesta.data_consegna_prevista):
        return False
    inizio = _prossimo_giorno_lavorativo(date.today())
    ore = richiesta.effort_ore or 0
    giorni_lav = max(5, -(-ore // 6)) if ore > 0 else 10  # ceil(ore/6), min 5 gg; default 2 settimane
    fine = _aggiungi_giorni_lavorativi(inizio, giorni_lav)
    richiesta.data_inizio = inizio
    richiesta.data_consegna_prevista = fine
    richiesta.save(update_fields=["data_inizio", "data_consegna_prevista"])
    return True


def ripartisci_effort_con_ai(richiesta):
    """Fa proporre all'AI la ripartizione dell'effort e la applica (totale invariato).

    Ritorna (True, None) oppure (False, errore). Funziona in QUALSIASI stato,
    inclusi in approvazione e approvati: non modifica l'effort totale, solo la
    sua suddivisione esplicativa.
    """
    if not richiesta.effort_ore:
        return False, "La richiesta non ha un effort in ore da ripartire."
    cfg = _config_pronta()
    if cfg is None:
        return False, "Ripartizione AI non disponibile (abilitazione o API key)."
    voci, errore = stima_ripartizione_effort(richiesta, cfg)
    if errore:
        return False, errore
    create = richiesta.applica_ripartizione_effort(voci)
    if not create:
        return False, "L'AI non ha restituito una ripartizione utilizzabile."
    return True, None


def crea_griglia_effort(richiesta) -> bool:
    """Griglia manuale: tutte le attività a 0 ore con figure di default.

    Per compilare a mano quando l'AI non è disponibile o non convince. Non crea
    nulla se esistono già voci. Le ore a 0 NON quadrano finché non distribuite:
    la pagina lo segnala (fail loudly, nessun numero inventato).
    """
    from .models import (AttivitaEffort, FIGURA_DEFAULT_PER_ATTIVITA, VoceEffort)
    if richiesta.voci_effort.exists():
        return False
    VoceEffort.objects.bulk_create([
        VoceEffort(richiesta=richiesta, attivita=a, figura=FIGURA_DEFAULT_PER_ATTIVITA[a],
                   ore=0, stimata_ai=False)
        for a in AttivitaEffort
    ])
    return True


def clona_per_funzioni(richiesta, attore=None) -> list:
    """Crea le schede dedicate per Application e/o IT Operation, se i dettagli sono compilati.

    La scheda AI di origine resta invariata; le copie sono progetti autonomi,
    gestiti dalla Funzione Applicativa / IT Operations, con ID «ID xx Application»
    (o «ID xx IT Operation») e lo stesso titolo. Vengono create UNA sola volta:
    se esistono già, non si duplicano né si sovrascrivono (sono ormai progetti
    indipendenti). Ritorna l'elenco delle schede create.
    """
    from .models import Richiesta, TipoProgetto
    from .workflow import Stato

    if richiesta.is_clone:
        return []  # non si clona un clone
    creati = []
    classi = {
        TipoProgetto.APPLICATION: (richiesta.app_capex, richiesta.app_opex, richiesta.app_ifrs),
        TipoProgetto.IT_OPERATION: (richiesta.ops_capex, richiesta.ops_opex, richiesta.ops_ifrs),
    }
    for tipo, testo in ((TipoProgetto.APPLICATION, richiesta.dettaglio_application),
                        (TipoProgetto.IT_OPERATION, richiesta.dettaglio_it_operation)):
        testo = (testo or "").strip()
        if not testo or richiesta.cloni.filter(tipo=tipo).exists():
            continue
        clone = Richiesta.objects.create(
            clone_di=richiesta,
            tipo=tipo,
            titolo=richiesta.titolo,
            funzione=richiesta.funzione,
            proponente=richiesta.proponente,
            referente_area=richiesta.referente_area,
            tipo_soluzione=richiesta.tipo_soluzione,
            priorita=richiesta.priorita,
            entity=richiesta.entity,
            descrizione=testo,
            # Classificazione economica propria della componente (modificabile poi
            # dalla funzione competente nella sua analisi).
            is_capex=classi[tipo][0],
            is_opex=classi[tipo][1],
            is_ifrs=classi[tipo][2],
        )
        # Entra subito nella coda della funzione competente, con traccia in audit.
        clone.applica("invia", attore=attore,
                      nota=f"Scheda generata dalla componente «{clone.get_tipo_display()}» "
                           f"di {richiesta.codice} — {richiesta.titolo}.")
        creati.append(clone)
    return creati


def _riga_da_richiesta(foglio, richiesta):
    """Costruisce la riga di budget mappando i campi del progetto sulle colonne del foglio.

    La mappatura e' per NOME di colonna: se una colonna non esiste nel workbook
    importato, semplicemente non viene valorizzata (nessun dato inventato).
    """
    dati = [""] * len(foglio.intestazioni)

    def scrivi(valore, *nomi):
        i = foglio.indice_colonna(*nomi)
        if i is not None and valore not in (None, ""):
            dati[i] = valore

    etichetta = f"{richiesta.codice} {richiesta.titolo}"
    scrivi(etichetta, "#ID", "ITEM ID", "ID")
    scrivi(etichetta, "Descrizione", "DESCRIPTION")
    scrivi(richiesta.titolo, "Fase+Desc", "BUDGET ITEM")
    scrivi(richiesta.get_priorita_display(), "Priorità", "PRIORITY")
    scrivi(richiesta.get_tipo_display(), "Tipologia", "ITEM TYPE")
    scrivi(richiesta.get_funzione_display(), "Area")
    nome_owner = (richiesta.proponente.get_full_name() or richiesta.proponente.username)
    scrivi(nome_owner, "Owner", "REFERENCE PERSON")
    scrivi(nome_owner, "BUDGET RESPONSIBLE")
    scrivi(richiesta.get_entity_display(), "Compete soc.", "Pagato da soc.")
    if richiesta.is_capex and not richiesta.is_opex:
        scrivi("CPX", "CPX/OPX")
    elif richiesta.is_opex and not richiesta.is_capex:
        scrivi("OPX", "CPX/OPX")
    if richiesta.is_ifrs:
        scrivi("IFRS", "IFRS")
    costo = richiesta.costo_progetto_stimato
    if costo is not None:
        importo = float(costo)
        scrivi(importo, "ESTIMATED AMOUNT")
        scrivi(importo, "CPX Inv." if richiesta.is_capex else "OPX Imp. tot.")
    if richiesta.effort_ore:
        scrivi(round(richiesta.effort_ore / 8, 1), "EFFORT IT (GG)")
    if richiesta.data_inizio:
        scrivi(richiesta.data_inizio.isoformat(), "Due date")
    scrivi(richiesta.funzione_competente_label, "Rich.")
    scrivi("Progetto Digital Transformation — compliance validata dal CISO", "Note", "NOTE")
    return dati


def copia_in_budget(richiesta, attore=None, anno=None):
    """Copia il progetto nelle righe del foglio di budget corretto.

    Va sul foglio Extra Budget se la copertura e' extra budget, altrimenti sul
    foglio Budget. Mantiene «ID xx + titolo» come identificativo di riga. Se il
    progetto e' gia' presente la riga viene AGGIORNATA, non duplicata.
    Ritorna (riga, creata) oppure (None, False) se il foglio non e' stato importato.
    """
    from django.db.models import Max

    from .models import RigaBudget, TipoFoglio

    esistente = RigaBudget.objects.filter(richiesta=richiesta).select_related("foglio").first()
    extra = (richiesta.budget_it == "EXTRA_BUDGET"
             or richiesta.esito_budget == "EXTRA_BUDGET")
    tipo = TipoFoglio.EXTRA if extra else TipoFoglio.BUDGET
    # Budget -> esercizio successivo; Extra budget -> esercizio in corso.
    anno = anno or anno_destinazione(extra)
    foglio = foglio_budget(tipo, anno, crea=True)
    if foglio is None:
        return None, False
    dati = _riga_da_richiesta(foglio, richiesta)
    if esistente:
        if esistente.foglio_id != foglio.id:  # la copertura è cambiata: riga spostata
            esistente.foglio = foglio
        esistente.dati = dati
        esistente.save(update_fields=["foglio", "dati", "aggiornata_il"])
        return esistente, False
    ultimo = foglio.righe.aggregate(m=Max("ordine"))["m"] or 0
    riga = RigaBudget.objects.create(foglio=foglio, ordine=ultimo + 10, dati=dati,
                                     richiesta=richiesta, da_progetto=True)
    return riga, True


def foglio_budget(tipo, anno, crea=False):
    """Foglio di un certo tipo (BUDGET/EXTRA) per un dato anno.

    Se manca e crea=True lo genera vuoto, ereditando le intestazioni dal foglio
    piu' recente dello stesso tipo: cosi' a inizio anno le voci trovano gia' il
    foglio dell'anno successivo, con la struttura del workbook in uso.
    """
    import re
    import unicodedata

    from .models import FoglioBudget

    foglio = FoglioBudget.objects.filter(tipo=tipo, anno=anno).first()
    if foglio or not crea:
        return foglio
    modello = FoglioBudget.objects.filter(tipo=tipo).order_by("-anno").first()
    if modello is None:
        return None  # nessun workbook importato: fail loudly a monte
    base = unicodedata.normalize("NFKD", modello.nome).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "foglio"
    prefisso = "xb-" if tipo == "EXTRA" else ""
    return FoglioBudget.objects.create(
        chiave=f"{prefisso}{base}-{anno}"[:60], nome=modello.nome, tipo=tipo, anno=anno,
        intestazioni=list(modello.intestazioni), ordine=modello.ordine,
        note=f"Creato automaticamente da {modello.nome} {modello.anno}.",
    )


def anno_destinazione(extra: bool, oggi=None) -> int:
    """Anno di destinazione di una voce approvata.

    Regola: quello che si mette a BUDGET finanzia l'esercizio successivo (il
    budget dell'anno prossimo, si compila durante tutto l'anno in corso); quello
    che va in EXTRA BUDGET incide sull'anno in corso.
    """
    from datetime import date

    oggi = oggi or date.today()
    return oggi.year if extra else oggi.year + 1


def crea_progetto_da_riga(foglio, dati_riga, attore, titolo, funzione, tipo_progetto,
                          priorita="MEDIA", importo=None, note=""):
    """Riga inserita a mano nel budget: crea il progetto corrispondente e li collega.

    La voce di budget e il progetto sono due facce della stessa cosa: la riga
    nasce nel foglio, il progetto entra nel flusso (in coda alla funzione
    competente) con la copertura gia' coerente col foglio di origine.
    Ritorna (riga, richiesta).
    """
    from .models import BudgetIT, Richiesta, RigaBudget, TipoFoglio
    from .workflow import Stato

    extra = foglio.tipo == TipoFoglio.EXTRA
    richiesta = Richiesta.objects.create(
        titolo=titolo,
        descrizione=note or f"Voce inserita nel foglio {foglio.nome} {foglio.anno}.",
        funzione=funzione,
        tipo=tipo_progetto,
        priorita=priorita,
        proponente=attore,
        altri_costi=importo,
        budget_it=BudgetIT.EXTRA_BUDGET if extra else BudgetIT.BUDGET,
    )
    richiesta.applica(
        "invia", attore=attore,
        nota=f"Progetto generato dalla riga inserita nel foglio {foglio.nome} {foglio.anno}.")
    dati = list(dati_riga)
    i_id = foglio.indice_colonna("#ID", "ITEM ID", "ID")
    i_desc = foglio.indice_colonna("Descrizione", "DESCRIPTION")
    etichetta = f"{richiesta.codice} {richiesta.titolo}"
    if i_id is not None:
        dati[i_id] = etichetta
    if i_desc is not None and not str(dati[i_desc]).strip():
        dati[i_desc] = etichetta
    from django.db.models import Max
    ultimo = foglio.righe.aggregate(m=Max("ordine"))["m"] or 0
    riga = RigaBudget.objects.create(foglio=foglio, ordine=ultimo + 10, dati=dati,
                                     richiesta=richiesta, da_progetto=True)
    return riga, richiesta
