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
from django.views.decorators.http import require_POST

from .forms import RichiestaForm, SalForm, AnalisiAIForm, ImpostazioniAIForm
from .models import ConfigurazioneAI, PROMPT_SISTEMA_DEFAULT, Richiesta
from .kpi import calcola_kpi
from .ai_client import genera_analisi, prova_connessione
from django.utils import timezone
from .workflow import FASI, STATI_OPERATIVI, STATI_TERMINALI, Stato, azioni_disponibili, puo_eseguire, transizione


def _richieste_visibili(utente):
    """Owner: solo le proprie. Gestori (AI/Comitato/CEO/Admin): tutte."""
    qs = Richiesta.objects.select_related("proponente")
    if utente.is_gestore:
        return qs
    return qs.filter(proponente=utente)


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

    # Distribuzione per funzione (come la slide 'Raccolta esigenze').
    per_funzione = (
        qs.values("funzione").annotate(n=Count("id")).order_by("-n")
    )
    label_funzione = dict(Funzione.choices)
    distribuzione = [
        {"label": label_funzione.get(r["funzione"], r["funzione"]), "n": r["n"]}
        for r in per_funzione
    ]
    max_funzione = max((d["n"] for d in distribuzione), default=1)

    # Code che richiedono un'azione del ruolo corrente.
    da_fare = [r for r in qs if azioni_disponibili(r, request.user)]

    return render(request, "flusso/dashboard.html", {
        "totale": totale,
        "aperte": aperte,
        "attivi": attivi,
        "conteggio_fasi": conteggio_fasi,
        "distribuzione": distribuzione,
        "max_funzione": max_funzione,
        "da_fare": da_fare[:8],
        "da_fare_totale": len(da_fare),
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

    richieste = [
        {"obj": r, "azioni": azioni_disponibili(r, request.user)} for r in qs
    ]
    return render(request, "flusso/lista.html", {
        "richieste": richieste,
        "stati": Stato.choices,
        "funzioni": Funzione.choices,
        "f_stato": stato,
        "f_funzione": funzione,
        "q": cerca,
    })


@login_required
def kanban(request):
    qs = _richieste_visibili(request.user)
    colonne = []
    etichette = {
        "in_coda": "In coda",
        "in_analisi": "In analisi (Funzione AI)",
        "in_approvazione": "In approvazione",
        "approvati": "Approvati / attivi",
        "chiusi": "Chiusi",
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
    puo_modificare = richiesta.is_bozza and (
        request.user.is_superuser or richiesta.proponente_id == request.user.id
    )
    sal_form = SalForm(initial={"sal": richiesta.sal}) if (
        richiesta.is_operativa and request.user.is_ai_officer
    ) else None

    # L'analisi della Funzione AI e' modificabile solo dall'AI Officer.
    analisi_form = AnalisiAIForm(instance=richiesta) if request.user.is_ai_officer else None

    return render(request, "flusso/dettaglio.html", {
        "richiesta": richiesta,
        "azioni": azioni,
        "timeline": timeline,
        "puo_modificare": puo_modificare,
        "sal_form": sal_form,
        "analisi_form": analisi_form,
    })


@login_required
def nuova(request):
    if not (request.user.is_owner or request.user.is_superuser):
        return HttpResponseForbidden("Solo gli owner di funzione possono creare richieste.")

    if request.method == "POST":
        form = RichiestaForm(request.POST, funzione_owner=request.user.funzione or None)
        if form.is_valid():
            richiesta = form.save(commit=False)
            if request.user.funzione:
                richiesta.funzione = request.user.funzione
            richiesta.proponente = request.user
            richiesta.save()
            messages.success(request, f"Richiesta {richiesta.codice} creata in bozza.")
            return redirect(richiesta)
    else:
        form = RichiestaForm(funzione_owner=request.user.funzione or None)

    return render(request, "flusso/richiesta_form.html", {"form": form, "nuova": True})


@login_required
def modifica(request, pk):
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not (request.user.is_superuser or richiesta.proponente_id == request.user.id):
        return HttpResponseForbidden("Non puoi modificare questa richiesta.")
    if not richiesta.is_bozza:
        messages.error(request, "Solo le richieste in bozza sono modificabili.")
        return redirect(richiesta)

    if request.method == "POST":
        form = RichiestaForm(request.POST, instance=richiesta, funzione_owner=request.user.funzione or None)
        if form.is_valid():
            form.save()
            messages.success(request, "Richiesta aggiornata.")
            return redirect(richiesta)
    else:
        form = RichiestaForm(instance=richiesta, funzione_owner=request.user.funzione or None)

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

    evento = richiesta.applica(azione, attore=request.user, nota=nota)
    messages.success(request, f"{evento.etichetta}: {richiesta.stato_label}.")
    return redirect(richiesta)


@login_required
@require_POST
def aggiorna_analisi(request, pk):
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_ai_officer:
        return HttpResponseForbidden("Solo la Funzione AI puo' compilare l'analisi.")
    form = AnalisiAIForm(request.POST, instance=richiesta)
    if form.is_valid():
        form.save()
        messages.success(request, "Analisi della Funzione AI aggiornata.")
    else:
        messages.error(request, "Controlla i dati dell'analisi: alcuni valori non sono validi.")
    return redirect(richiesta)


@login_required
@require_POST
def aggiorna_sal(request, pk):
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_ai_officer or not richiesta.is_operativa:
        return HttpResponseForbidden("Aggiornamento SAL non consentito.")
    form = SalForm(request.POST)
    if form.is_valid():
        richiesta.aggiorna_sal(
            form.cleaned_data["sal"], attore=request.user, nota=form.cleaned_data["nota"]
        )
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
        "form": form,
        "config": config,
        "env_override": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "prompt_default": PROMPT_SISTEMA_DEFAULT,
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
