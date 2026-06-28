"""
View del flusso.

Tutte le autorizzazioni sono verificate lato server: la visibilita' delle
richieste dipende dal ruolo, e ogni transizione passa da workflow.puo_eseguire
prima di essere applicata. I pulsanti nei template sono solo un riflesso di
questi controlli, non la loro fonte.
"""

import os
from collections import Counter

from accounts.models import Funzione
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import servizi
from .ai_client import genera_analisi, prova_connessione
from .forms import (AnalisiAIForm, AzioneTrattamentoFormSet, BeneficioForm, ImpostazioniAIForm,
                    PianificazioneForm, RichiestaForm, SalForm, TrattamentoRischioForm,
                    ValidazioneRischioForm)
from .kpi import calcola_kpi
from .models import (ClassificazioneRischio, ConfigurazioneAI, DIMENSIONE_PER_RUOLO,
                     EsitoBudget, PROMPT_RISCHIO_DEFAULT, PROMPT_SISTEMA_DEFAULT, Richiesta,
                     StatoRischio, TipoRischio)
from .notifiche import notifica_transizione
from .workflow import FASI, STATI_OPERATIVI, STATI_TERMINALI, Stato, azioni_disponibili, puo_eseguire, transizione

_NOMI_RISCHIO = dict(TipoRischio.choices)


def _richieste_visibili(utente):
    """Owner: solo le proprie. Gestori (AI/Comitato/presìdi/Auditor): tutte."""
    qs = Richiesta.objects.select_related("proponente")
    if utente.is_gestore:
        return qs
    return qs.filter(proponente=utente)


def _puo_validare(utente, tipo) -> bool:
    """True SOLO se l'utente è il presidio competente per quella dimensione.

    Separazione dei compiti: validare il rischio è un atto di governance legato
    al ruolo (Funzione Legale → AI Act, CISO → NIS2, DPO → GDPR), non un
    privilegio amministrativo. La Funzione AI (che è superuser per gestire
    l'applicazione) e ogni altro superuser NON possono quindi validare al posto
    del presidio: la validazione resta sempre dei tre presìdi competenti.
    """
    return {"AIACT": utente.is_legale, "NIS2": utente.is_ciso, "GDPR": utente.is_dpo}.get(tipo, False)


@login_required
def dashboard(request):
    qs = _richieste_visibili(request.user)
    per_stato = Counter()
    for stato in qs.values_list("stato", flat=True):
        per_stato[stato] += 1

    conteggio_fasi = {
        nome: sum(per_stato.get(s, 0) for s in stati) for nome, stati in FASI.items()
    }
    totale = qs.count()
    aperte = qs.exclude(stato__in=STATI_TERMINALI).count()
    attivi = qs.filter(stato__in=STATI_OPERATIVI).count()

    per_funzione = qs.values("funzione").annotate(n=Count("id")).order_by("-n")
    label_funzione = dict(Funzione.choices)
    distribuzione = [
        {"label": label_funzione.get(r["funzione"], r["funzione"]), "n": r["n"]}
        for r in per_funzione
    ]
    max_funzione = max((d["n"] for d in distribuzione), default=1)

    da_fare = [r for r in qs if azioni_disponibili(r, request.user)]

    # Presidio (Legale/CISO/DPO): rischi della propria dimensione in attesa di validazione.
    rischi_da_validare, dimensione_presidio = [], ""
    if request.user.is_validatore_rischio:
        dim = DIMENSIONE_PER_RUOLO.get(request.user.ruolo)
        if dim:
            dimensione_presidio = dict(TipoRischio.choices).get(dim, dim)
            rischi_da_validare = list(
                ClassificazioneRischio.objects
                .filter(tipo=dim, stato=StatoRischio.PROPOSTO_AI)
                .select_related("richiesta", "richiesta__proponente")
                .order_by("richiesta__numero")
            )

    return render(request, "flusso/dashboard.html", {
        "totale": totale, "aperte": aperte, "attivi": attivi,
        "conteggio_fasi": conteggio_fasi, "distribuzione": distribuzione,
        "max_funzione": max_funzione, "da_fare": da_fare[:8], "da_fare_totale": len(da_fare),
        "rischi_da_validare": rischi_da_validare, "dimensione_presidio": dimensione_presidio,
        "rischi_da_validare_n": len(rischi_da_validare),
    })


@login_required
def lista(request):
    qs = _richieste_visibili(request.user)
    stato = request.GET.get("stato", "")
    funzione = request.GET.get("funzione", "")
    cerca = request.GET.get("q", "").strip()
    if stato:
        qs = qs.filter(stato=stato)
    if funzione:
        qs = qs.filter(funzione=funzione)
    if cerca:
        qs = qs.filter(Q(titolo__icontains=cerca) | Q(descrizione__icontains=cerca))

    richieste = [{"obj": r, "azioni": azioni_disponibili(r, request.user)} for r in qs]
    return render(request, "flusso/lista.html", {
        "richieste": richieste, "stati": Stato.choices, "funzioni": Funzione.choices,
        "f_stato": stato, "f_funzione": funzione, "q": cerca,
    })


@login_required
def kanban(request):
    qs = _richieste_visibili(request.user)
    colonne = []
    etichette = {
        "in_coda": "In coda", "in_analisi": "In analisi (Funzione AI)",
        "in_approvazione": "In approvazione", "approvati": "Approvati / attivi", "chiusi": "Chiusi",
    }
    for chiave, stati in FASI.items():
        items = [r for r in qs if r.stato in stati]
        colonne.append({"chiave": chiave, "titolo": etichette[chiave], "items": items})
    return render(request, "flusso/kanban.html", {"colonne": colonne})


@login_required
def dettaglio(request, pk):
    richiesta = get_object_or_404(Richiesta.objects.select_related("proponente"), pk=pk)
    if not request.user.is_gestore and richiesta.proponente_id != request.user.id:
        return HttpResponseForbidden("Non hai i permessi per questa richiesta.")

    azioni = azioni_disponibili(richiesta, request.user)
    timeline = richiesta.transizioni.select_related("attore").all()
    bloccata = richiesta.modifica_bloccata
    # La Funzione AI modifica la scheda completa in tutti gli stati non bloccati;
    # l'owner solo prima della presa in carico (bozza/inviata).
    puo_modificare = (not bloccata) and (
        request.user.is_ai_officer
        or (richiesta.proponente_id == request.user.id and richiesta.stato in (Stato.BOZZA, Stato.INVIATA))
    )
    puo_eliminare = request.user.is_ai_officer or (
        request.user.is_owner and richiesta.proponente_id == request.user.id and richiesta.is_bozza
    )
    sal_form = SalForm(initial={"sal": richiesta.sal}) if (
        richiesta.is_operativa and request.user.is_ai_officer
    ) else None
    analisi_form = AnalisiAIForm(instance=richiesta) if (request.user.is_ai_officer and not bloccata) else None
    # Beneficio economico e incrementi: modificabili da owner e Funzione AI fino al blocco.
    puo_beneficio = (not bloccata) and (
        request.user.is_ai_officer or richiesta.proponente_id == request.user.id
    )
    beneficio_form = BeneficioForm(instance=richiesta) if puo_beneficio else None

    # Rischio & conformità: le tre dimensioni, con form di validazione SOLO per il presidio competente.
    richiesta.assicura_classificazioni()
    rischi = []
    for c in richiesta.lista_rischi():
        form = None
        tratta_form = None
        azioni_formset = None
        if _puo_validare(request.user, c.tipo):
            form = ValidazioneRischioForm(tipo=c.tipo, initial={
                "categoria": c.categoria or c.ai_categoria or None,
                "obblighi": "\n".join(c.obblighi_voci)})
            tratta_form = TrattamentoRischioForm(
                instance=c, tipo=c.tipo, prefix=f"tr_{c.tipo}",
                initial={"rischio_residuo": c.rischio_residuo or c.residuo_suggerito})
            azioni_formset = AzioneTrattamentoFormSet(instance=c, prefix=f"az_{c.tipo}")
        rischi.append({"c": c, "form": form, "tratta_form": tratta_form,
                       "azioni_formset": azioni_formset})
    blocco_approvazione = richiesta.stato == Stato.IN_QUALIFICA and not richiesta.rischi_tutti_validati
    if blocco_approvazione:
        azioni = [a for a in azioni if a.azione != "presenta_approvazione"]
    # La decisione di budget dell'owner ha un pannello dedicato: fuori dai bottoni generici.
    mostra_decisione_budget = (
        richiesta.stato == Stato.ATTESA_BUDGET
        and request.user.is_owner
        and richiesta.proponente_id == request.user.id
    )
    azioni = [a for a in azioni if a.azione not in ("conferma_budget", "rifiuta_progetto")]

    return render(request, "flusso/dettaglio.html", {
        "richiesta": richiesta, "azioni": azioni, "timeline": timeline,
        "puo_modificare": puo_modificare, "puo_eliminare": puo_eliminare,
        "sal_form": sal_form, "analisi_form": analisi_form, "beneficio_form": beneficio_form,
        "rischi": rischi, "puo_analizza_rischio": request.user.is_ai_officer,
        "blocco_approvazione": blocco_approvazione,
        "mostra_decisione_budget": mostra_decisione_budget,
        "rischi_mancanti": richiesta.rischi_mancanti_label,
    })


@login_required
def nuova(request):
    if not request.user.is_owner:
        return HttpResponseForbidden("Solo gli owner di funzione possono creare richieste.")

    if request.method == "POST":
        form = RichiestaForm(request.POST, funzione_owner=request.user.funzione or None)
        if form.is_valid():
            richiesta = form.save(commit=False)
            if request.user.funzione:
                richiesta.funzione = request.user.funzione
            richiesta.proponente = request.user
            richiesta.save()
            # Stima AI degli incrementi mancanti (una sola volta; sempre modificabili).
            estimato = servizi.stima_incrementi_se_serve(richiesta, attore=request.user)
            msg = f"Richiesta {richiesta.codice} creata in bozza."
            if estimato:
                msg += " Beneficio atteso e incrementi stimati dall'AI dove mancanti (modificabili)."
            messages.success(request, msg)
            return redirect(richiesta)
    else:
        form = RichiestaForm(funzione_owner=request.user.funzione or None)

    return render(request, "flusso/richiesta_form.html", {"form": form, "nuova": True})


@login_required
def modifica(request, pk):
    richiesta = get_object_or_404(Richiesta, pk=pk)
    is_owner = richiesta.proponente_id == request.user.id
    if richiesta.modifica_bloccata:
        messages.error(request, "La richiesta è in approvazione o approvata: non è più modificabile.")
        return redirect(richiesta)
    if not request.user.is_ai_officer and not (is_owner and richiesta.stato in (Stato.BOZZA, Stato.INVIATA)):
        return HttpResponseForbidden("Non puoi modificare questa richiesta in questo stato.")
    funz = (richiesta.proponente.funzione or None) if request.user.is_ai_officer else (request.user.funzione or None)

    if request.method == "POST":
        form = RichiestaForm(request.POST, instance=richiesta, funzione_owner=funz)
        if form.is_valid():
            era_inviata = richiesta.stato == Stato.INVIATA
            form.save()
            # Solo lato owner: prima volta utile, stima beneficio/incrementi ancora vuoti (best-effort).
            # La Funzione AI non genera mai questi valori (li corregge solo a mano).
            if is_owner and not request.user.is_ai_officer:
                servizi.stima_incrementi_se_serve(richiesta, attore=request.user)
            if is_owner and not request.user.is_ai_officer and era_inviata:
                richiesta.stato = Stato.BOZZA
                richiesta.save(update_fields=["stato", "aggiornata_il"])
                richiesta.transizioni.create(
                    azione="modifica",
                    etichetta="Modificata dal proponente — da reinviare",
                    stato_da=Stato.INVIATA, stato_a=Stato.BOZZA, attore=request.user,
                    nota="Scheda aggiornata dal proponente; riportata in bozza per il reinvio alla Funzione AI.",
                )
                messages.success(request, "Richiesta aggiornata. Reinviala alla Funzione AI per applicare le modifiche.")
            else:
                messages.success(request, "Richiesta aggiornata.")
            return redirect(richiesta)
    else:
        form = RichiestaForm(instance=richiesta, funzione_owner=funz)

    return render(request, "flusso/richiesta_form.html", {"form": form, "nuova": False, "richiesta": richiesta})


@login_required
@require_POST
def esegui_azione(request, pk):
    richiesta = get_object_or_404(Richiesta, pk=pk)
    azione = request.POST.get("azione", "")
    nota = request.POST.get("nota", "").strip()

    t = transizione(azione)
    if t is None or not puo_eseguire(richiesta, request.user, azione):
        return HttpResponseForbidden("Azione non consentita.")
    if t.richiede_nota and not nota:
        messages.error(request, f"L'azione «{t.label}» richiede una nota.")
        return redirect(richiesta)

    # GATE: l'invio all'owner per la decisione di budget richiede il costo stimato.
    if azione == "invia_a_budget" and richiesta.costo_progetto_stimato is None:
        messages.error(
            request,
            "Completa prima il costo del progetto (token e/o altri costi, con periodicità, "
            "ambito e numero utenti dove serve) prima di inviarlo all'owner per il budget.",
        )
        return redirect(richiesta)

    # GATE: niente passaggio alla Direzione senza decisione di budget + tre validazioni.
    if azione == "presenta_approvazione":
        if not richiesta.esito_budget:
            messages.error(
                request,
                "Prima dell'approvazione l'owner deve indicare se l'importo è a budget o extra budget.",
            )
            return redirect(richiesta)
        richiesta.assicura_classificazioni()
        if not richiesta.rischi_tutti_validati:
            messages.error(
                request,
                "Prima di presentare alla Direzione servono le tre validazioni di rischio. "
                f"Mancano: {richiesta.rischi_mancanti_label}.",
            )
            return redirect(richiesta)

    evento = richiesta.applica(azione, attore=request.user, nota=nota)
    if azione == "approva":
        try:
            servizi.pianifica_su_approvazione(richiesta)
        except Exception:
            pass  # le date sono utili ai KPI ma non devono bloccare l'approvazione
    notifica_transizione(request, richiesta, evento)
    messages.success(request, f"{evento.etichetta}: {richiesta.stato_label}.")
    return redirect(richiesta)


@login_required
@require_POST
def decidi_budget(request, pk):
    """L'owner indica se l'importo stimato è a budget o extra budget.

    Decisione obbligatoria a valle dell'analisi della Funzione AI: alla conferma
    l'AI genera i rischi (AI Act/NIS2/GDPR) e il flusso prosegue come di consueto.
    In alternativa l'owner rifiuta il progetto (azione 'rifiuta_progetto') e la
    pratica viene archiviata con motivazione.
    """
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not puo_eseguire(richiesta, request.user, "conferma_budget"):
        return HttpResponseForbidden("Azione non consentita.")
    esito = request.POST.get("esito_budget", "")
    if esito not in EsitoBudget.values:
        messages.error(request, "Indica se l'importo è a budget oppure extra budget.")
        return redirect(richiesta)
    richiesta.esito_budget = esito
    richiesta.save(update_fields=["esito_budget"])
    evento = richiesta.applica("conferma_budget", attore=request.user)
    notifica_transizione(request, richiesta, evento)
    # Alla conferma del budget l'AI genera le tre dimensioni di rischio (best-effort).
    risk_msg = ""
    res = servizi.classifica_tutti_i_rischi(richiesta, attore=request.user)
    if res["ok"]:
        dims = ", ".join(_NOMI_RISCHIO[x] for x in res["ok"])
        risk_msg = (f" Rischio stimato dall'AI per: {dims}. "
                    "Da validare da Legale (AI Act), CISO (NIS2) e DPO (GDPR).")
    elif "_" not in res["errori"]:
        risk_msg = " Nota: classificazione del rischio non riuscita per alcune dimensioni."
    messages.success(
        request,
        f"Budget confermato come «{richiesta.get_esito_budget_display()}». "
        f"{richiesta.stato_label}.{risk_msg}",
    )
    return redirect(richiesta)


@login_required
@require_POST
def aggiorna_analisi(request, pk):
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Solo la Funzione AI puo' compilare l'analisi.")
    if richiesta.modifica_bloccata:
        messages.error(request, "La richiesta è in approvazione o approvata: analisi non modificabile.")
        return redirect(richiesta)
    form = AnalisiAIForm(request.POST, instance=richiesta)
    if form.is_valid():
        form.save()
        # Importo inserito a mano: non è (più) una stima AI.
        if "costo_token_ai" in form.changed_data and richiesta.costo_token_ai is not None:
            if richiesta.costo_token_ai_stimato:
                richiesta.costo_token_ai_stimato = False
                richiesta.save(update_fields=["costo_token_ai_stimato"])
        # Importo mancante: prova a stimarlo con l'AI (una volta), se c'è abbastanza contesto.
        stimato = servizi.stima_costo_token_se_serve(richiesta, attore=request.user)
        msg = ("Analisi aggiornata. Importo token proposto dall'AI: "
               f"€ {richiesta.costo_token_ai} (modificabile)." if stimato
               else "Analisi della Funzione AI aggiornata.")
        rip = richiesta.ripartizione_budget
        if rip:
            msg += (f" Costo € {rip['costo']:.2f}: a budget € {rip['a_budget']:.2f}, "
                    f"extra budget € {rip['extra']:.2f} ({richiesta.budget_stato_label}).")
        messages.success(request, msg)
    else:
        messages.error(request, "Controlla i dati dell'analisi: alcuni valori non sono validi.")
    return redirect(richiesta)


@login_required
@require_POST
def compila_analisi_ai(request, pk):
    """Bottone «AI»: l'AI precompila l'intera analisi; l'AI Officer poi verifica, modifica e salva."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Solo la Funzione AI puo' usare la compilazione automatica.")
    if richiesta.modifica_bloccata:
        messages.error(request, "La richiesta è in approvazione o approvata: analisi non modificabile.")
        return redirect(richiesta)
    ok, errore = servizi.compila_analisi_con_ai(richiesta, attore=request.user)
    if ok:
        messages.success(
            request,
            "Analisi precompilata dall'AI. Verifica i campi, modificali se serve e salva con «Salva analisi».",
        )
    else:
        messages.error(request, f"Compilazione AI non riuscita: {errore}")
    return redirect(richiesta)


@login_required
@require_POST
def aggiorna_beneficio(request, pk):
    """Beneficio economico e incrementi: modificabili da owner e Funzione AI fino al blocco."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    is_owner = richiesta.proponente_id == request.user.id
    if richiesta.modifica_bloccata:
        messages.error(request, "La richiesta è in approvazione o approvata: beneficio non modificabile.")
        return redirect(richiesta)
    if not (is_owner or request.user.is_ai_officer):
        return HttpResponseForbidden("Non puoi modificare il beneficio di questa richiesta.")
    form = BeneficioForm(request.POST, instance=richiesta)
    if form.is_valid():
        form.save()
        # I valori inseriti o corretti a mano non sono (più) una stima AI.
        if richiesta.incrementi_ai_stimati:
            richiesta.incrementi_ai_stimati = False
            richiesta.save(update_fields=["incrementi_ai_stimati"])
        messages.success(request, "Beneficio economico e incrementi aggiornati.")
    else:
        messages.error(request, "Valori non validi: controlla beneficio e percentuali.")
    return redirect(richiesta)


@login_required
def schedulazione(request):
    """Pianificazione dei soli progetti approvati (date utili ai KPI, modificabili a mano)."""
    if not request.user.is_gestore:
        return HttpResponseForbidden("Pagina riservata.")
    qs = (Richiesta.objects
          .filter(stato__in=[Stato.APPROVATA, Stato.ATTIVO, Stato.MONITORAGGIO, Stato.COMPLETATO])
          .select_related("proponente")
          .order_by("data_inizio", "creata_il"))
    puo_modificare = request.user.is_ai_officer
    righe = [{"r": r, "form": PianificazioneForm(instance=r) if puo_modificare else None} for r in qs]
    return render(request, "flusso/schedulazione.html", {
        "righe": righe, "puo_modificare": puo_modificare, "totale": qs.count(),
    })


@login_required
@require_POST
def salva_pianificazione(request, pk):
    """Salvataggio manuale delle date di un progetto dalla schedulazione."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Solo la Funzione AI puo' modificare la pianificazione.")
    form = PianificazioneForm(request.POST, instance=richiesta)
    if form.is_valid():
        form.save()
        messages.success(request, f"Date aggiornate per {richiesta.codice}.")
    else:
        messages.error(request, "Date non valide: controlla inizio e consegna.")
    return redirect("flusso:schedulazione")


@login_required
@require_POST
def aggiorna_sal(request, pk):
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_ai_officer or not richiesta.is_operativa:
        return HttpResponseForbidden("Aggiornamento SAL non consentito.")
    form = SalForm(request.POST)
    if form.is_valid():
        richiesta.aggiorna_sal(form.cleaned_data["sal"], attore=request.user, nota=form.cleaned_data["nota"])
        messages.success(request, f"SAL aggiornato a {richiesta.sal}%.")
    else:
        messages.error(request, "Valore SAL non valido.")
    return redirect(richiesta)


# --- KPI direzionali --------------------------------------------------------

@login_required
def kpi(request):
    """Cruscotto KPI del portafoglio. Visibile ai ruoli con visibilità completa."""
    if not request.user.is_gestore:
        return HttpResponseForbidden("Pagina riservata.")
    dati = calcola_kpi()
    config = ConfigurazioneAI.load()
    return render(request, "flusso/kpi.html", {"k": dati, "config": config})


@login_required
@require_POST
def genera_analisi_kpi(request):
    """Genera/aggiorna la lettura esecutiva AI dei KPI (solo Funzione AI)."""
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Solo la Funzione AI può generare l'analisi.")
    config = ConfigurazioneAI.load()
    if not config.abilitato:
        messages.error(request, "Analisi AI non abilitata: attivala in Admin → Configurazione AI.")
        return redirect("flusso:kpi")
    testo, errore = genera_analisi(calcola_kpi(), config)
    if errore:
        messages.error(request, f"Generazione non riuscita: {errore}")
    else:
        config.ultima_analisi = testo
        config.ultima_analisi_il = timezone.now()
        config.ultimo_modello = config.modello
        config.save()
        messages.success(request, "Lettura esecutiva aggiornata.")
    return redirect("flusso:kpi")


@login_required
def impostazioni_ai(request):
    """Form grafico di configurazione AI (solo Funzione AI)."""
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Pagina riservata alla Funzione AI.")
    config = ConfigurazioneAI.load()
    if request.method == "POST":
        form = ImpostazioniAIForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Impostazioni AI salvate.")
            return redirect("flusso:impostazioni_ai")
    else:
        form = ImpostazioniAIForm(instance=config)
    contesto = {
        "form": form, "config": config,
        "env_override": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "prompt_default": PROMPT_SISTEMA_DEFAULT,
        "prompt_rischio_default": PROMPT_RISCHIO_DEFAULT,
    }
    return render(request, "flusso/impostazioni_ai.html", contesto)


@login_required
@require_POST
def prova_connessione_ai(request):
    """Prova la connessione all'API usando i valori del form (chiave/modello)."""
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Azione riservata alla Funzione AI.")
    config = ConfigurazioneAI.load()
    chiave_form = (request.POST.get("api_key") or "").strip()
    chiave = chiave_form or os.environ.get("ANTHROPIC_API_KEY") or config.api_key
    modello = request.POST.get("modello") or config.modello
    ok, dettaglio = prova_connessione(chiave, modello)
    if ok:
        messages.success(request, f"Connessione riuscita — {dettaglio}.")
    else:
        messages.error(request, f"Test fallito — {dettaglio}")
    return redirect("flusso:impostazioni_ai")


@login_required
@require_POST
def elimina(request, pk):
    """Elimina una richiesta. AI Officer: qualsiasi; owner: solo le proprie in bozza."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    puo = request.user.is_ai_officer or (
        request.user.is_owner and richiesta.proponente_id == request.user.id and richiesta.is_bozza
    )
    if not puo:
        return HttpResponseForbidden("Non hai i permessi per eliminare questa richiesta.")
    codice = richiesta.codice
    richiesta.delete()
    messages.success(request, f"Richiesta {codice} eliminata definitivamente.")
    return redirect("flusso:lista")


# --- Rischio & conformità ---------------------------------------------------

@login_required
def rischio(request):
    """Registro delle tre dimensioni di rischio (Funzione AI e presìdi Legale/CISO/DPO)."""
    if not (request.user.is_ai_officer or request.user.is_validatore_rischio):
        return HttpResponseForbidden("Pagina riservata alla Funzione AI e ai presìdi (Legale, CISO, DPO).")
    voci = (Richiesta.objects.select_related("proponente")
            .prefetch_related("classificazioni").order_by("numero"))
    righe, pronti = [], 0
    da_validare = {"AIACT": 0, "NIS2": 0, "GDPR": 0}
    for r in voci:
        classi = {c.tipo: c for c in r.classificazioni.all()}
        validati = sum(1 for t in ("AIACT", "NIS2", "GDPR")
                       if classi.get(t) and classi[t].validato)
        pronto = validati == 3
        pronti += 1 if pronto else 0
        for t in ("AIACT", "NIS2", "GDPR"):
            c = classi.get(t)
            if c and c.da_validare:
                da_validare[t] += 1
        righe.append({"r": r, "aiact": classi.get("AIACT"), "nis2": classi.get("NIS2"),
                      "gdpr": classi.get("GDPR"), "validati": validati, "pronto": pronto})
    return render(request, "flusso/rischio.html", {
        "righe": righe, "totale": len(righe), "pronti": pronti,
        "da_validare": da_validare, "config": ConfigurazioneAI.load(),
    })


@login_required
@require_POST
def analizza_rischio(request, pk):
    """(Ri)analizza con l'AI le tre dimensioni di rischio di una richiesta (Funzione AI)."""
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Azione riservata alla Funzione AI.")
    richiesta = get_object_or_404(Richiesta, pk=pk)
    esito = servizi.classifica_tutti_i_rischi(richiesta, attore=request.user)
    if esito["ok"]:
        dims = ", ".join(_NOMI_RISCHIO[x] for x in esito["ok"])
        messages.success(request, f"Rischio stimato dall'AI per: {dims}. Da validare dai presìdi.")
    if esito["errori"]:
        if "_" in esito["errori"]:
            messages.error(request, f"Analisi AI non disponibile: {esito['errori']['_']}")
        else:
            falliti = ", ".join(_NOMI_RISCHIO.get(t, t) for t in esito["errori"])
            messages.warning(request, f"Classificazione non riuscita per: {falliti}.")
    return redirect(richiesta)


@login_required
@require_POST
def valida_rischio(request, pk, tipo):
    """Il presidio competente conferma o modifica UNA dimensione di rischio."""
    tipo = (tipo or "").upper()
    if tipo not in dict(TipoRischio.choices):
        return HttpResponseForbidden("Dimensione di rischio non valida.")
    if not _puo_validare(request.user, tipo):
        return HttpResponseForbidden("Non sei il presidio competente per questa dimensione di rischio.")
    richiesta = get_object_or_404(Richiesta, pk=pk)
    richiesta.assicura_classificazioni()
    classificazione = richiesta.classificazioni.get(tipo=tipo)
    form = ValidazioneRischioForm(request.POST, tipo=tipo)
    if form.is_valid():
        classificazione.valida(
            form.cleaned_data["categoria"], attore=request.user,
            nota=form.cleaned_data["nota"], motivazione=form.cleaned_data["motivazione"],
            obblighi=form.cleaned_data.get("obblighi"),
        )
        verbo = "modificato" if classificazione.stato == "MODIFICATO" else "validato"
        messages.success(request, f"Rischio {_NOMI_RISCHIO[tipo]} {verbo}: {classificazione.categoria_label}.")
    else:
        messages.error(request, "Selezione non valida.")
    return redirect(richiesta)


@login_required
@require_POST
def tratta_rischio(request, pk, tipo):
    """Il presidio competente registra il TRATTAMENTO del rischio di UNA dimensione.

    Strategia (accetta/mitiga/trasferisci/evita), azioni con data, rischio
    residuo e relativa convalida. Stesso vincolo di competenza della validazione.
    """
    tipo = (tipo or "").upper()
    if tipo not in dict(TipoRischio.choices):
        return HttpResponseForbidden("Dimensione di rischio non valida.")
    if not _puo_validare(request.user, tipo):
        return HttpResponseForbidden("Non sei il presidio competente per questa dimensione di rischio.")
    richiesta = get_object_or_404(Richiesta, pk=pk)
    richiesta.assicura_classificazioni()
    classificazione = richiesta.classificazioni.get(tipo=tipo)
    form = TrattamentoRischioForm(request.POST, instance=classificazione, tipo=tipo, prefix=f"tr_{tipo}")
    formset = AzioneTrattamentoFormSet(request.POST, instance=classificazione, prefix=f"az_{tipo}")
    if form.is_valid() and formset.is_valid():
        form.save()
        formset.save()
        classificazione.registra_trattamento(attore=request.user)
        msg = f"Trattamento {_NOMI_RISCHIO[tipo]} salvato: {classificazione.get_strategia_display()}."
        if classificazione.residuo_da_convalidare:
            msg += " Rischio residuo da convalidare."
        messages.success(request, msg)
    else:
        messages.error(request, "Trattamento non valido: controlla le azioni e il livello residuo.")
    return redirect(richiesta)
