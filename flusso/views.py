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
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import servizi
from .ai_client import genera_analisi, prova_connessione
from .forms import (AnalisiAIForm, AzioneTrattamentoFormSet, BeneficioForm, ImpostazioniAIForm,
                    PianificazioneForm, RichiestaForm, SalForm, TrattamentoRischioForm,
                    ValidazioneRischioForm)
from .kpi import calcola_kpi, riepilogo_aree
from .models import (AttivitaEffort, ClassificazioneRischio, ConfigurazioneAI, TipoProgetto,
                     DIMENSIONE_PER_RUOLO, DIMENSIONI_PER_RUOLO, FiguraEffort, ORDINE_ATTIVITA, VoceEffort,
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
    al ruolo, non un privilegio amministrativo. Il CISO è il presidio unico di
    compliance (AI Act, NIS2, GDPR). La Funzione tecnica (che è superuser per
    gestire l'applicazione) e ogni altro superuser NON possono validare al suo posto.
    """
    return utente.is_ciso and tipo in ("AIACT", "NIS2", "GDPR")



def _filtro_tipo(request, qs):
    """Filtro per tipo di richiesta (AI / Application / IT Operation).

    Default: il tipo di competenza della funzione tecnica dell'utente; per owner,
    presìdi e approvatore nessun filtro. 'tutti' o un tipo esplicito via GET.
    Le chip conservano gli altri parametri della pagina (ricerca, stato, fase...).
    Ritorna (tipo_attivo | None, chips).
    """
    scelto = request.GET.get("tipo")
    if scelto == "tutti":
        tipo = None
    elif scelto in dict(TipoProgetto.choices):
        tipo = scelto
    else:
        tipo = request.user.tipo_competenza
    conteggi = Counter(qs.values_list("tipo", flat=True))

    def _href(val):
        q = request.GET.copy()
        q["tipo"] = val
        return "?" + q.urlencode()

    brevi = {"AI": "AI", "APPLICATION": "Application", "IT_OPERATION": "IT Operation"}
    chips = [{"label": "Tutti", "href": _href("tutti"), "attivo": tipo is None, "n": qs.count()}]
    chips += [{"label": brevi[c], "href": _href(c), "attivo": tipo == c, "n": conteggi.get(c, 0)}
              for c, _ in TipoProgetto.choices]
    return tipo, chips


def _href_tipo(request, val):
    q = request.GET.copy()
    q["tipo"] = val
    return "?" + q.urlencode()


_NOMI_TIPO = {"AI": "AI", "APPLICATION": "Application", "IT_OPERATION": "IT Operation"}


def _schede_effort(request, qs_tutti, tipo_attivo):
    """Schede effort per tipo (AI/Application/IT Operation) sul perimetro dato."""
    schede = []
    for codice, _ in TipoProgetto.choices:
        sub = qs_tutti.filter(tipo=codice)
        voci = VoceEffort.objects.filter(richiesta__in=sub)
        tot_rip = voci.aggregate(t=Sum("ore"))["t"] or 0
        svil = voci.filter(attivita=AttivitaEffort.SVILUPPO).aggregate(t=Sum("ore"))["t"] or 0
        schede.append({
            "codice": codice, "nome": _NOMI_TIPO[codice], "n": sub.count(),
            "ore": sub.aggregate(t=Sum("effort_ore"))["t"] or 0, "ripartite": tot_rip,
            "sviluppo": svil, "pct_sviluppo": round(svil * 100 / tot_rip) if tot_rip else 0,
            "resto": tot_rip - svil,
            "senza_rip": sub.annotate(nv=Count("voci_effort")).filter(nv=0).count(),
            "href": _href_tipo(request, codice), "attiva": tipo_attivo == codice,
        })
    return schede


def _schede_costi(request, qs_tutti, tipo_attivo):
    """Schede costi per tipo sul perimetro dato (stessa logica della pagina Costi)."""
    from decimal import Decimal as _D
    schede = []
    for codice, _ in TipoProgetto.choices:
        tot = a_budget = extra = da_def = _D(0)
        n = n_inc = 0
        for r in qs_tutti.filter(tipo=codice):
            n += 1
            costo = r.costo_progetto_stimato
            if costo is None:
                if r.costo_token_ai is not None or r.altri_costi is not None:
                    n_inc += 1
                continue
            tot += costo
            rip = r.ripartizione_budget
            if rip:
                a_budget += rip["a_budget"]
                extra += rip["extra"]
            else:
                da_def += costo
        schede.append({
            "codice": codice, "nome": _NOMI_TIPO[codice], "n": n, "tot": tot,
            "a_budget": a_budget, "extra": extra, "da_definire": da_def, "incompleti": n_inc,
            "href": _href_tipo(request, codice), "attiva": tipo_attivo == codice,
        })
    return schede

@login_required
def dashboard(request):
    qs = _richieste_visibili(request.user)
    tipo_attivo, tipo_filtri = _filtro_tipo(request, qs)
    if tipo_attivo:
        qs = qs.filter(tipo=tipo_attivo)
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
        dims = DIMENSIONI_PER_RUOLO.get(request.user.ruolo, [])
        dim = dims[0] if dims else None
        if dim:
            dimensione_presidio = dict(TipoRischio.choices).get(dim, dim)
            rischi_da_validare = list(
                ClassificazioneRischio.objects
                .filter(tipo=dim, stato=StatoRischio.PROPOSTO_AI)
                .select_related("richiesta", "richiesta__proponente")
                .order_by("richiesta__numero")
            )

    return render(request, "flusso/dashboard.html", {
        "tipo_filtri": tipo_filtri, "tipo_attivo": tipo_attivo,
        "totale": totale, "aperte": aperte, "attivi": attivi,
        "conteggio_fasi": conteggio_fasi, "distribuzione": distribuzione,
        "max_funzione": max_funzione, "da_fare": da_fare[:8], "da_fare_totale": len(da_fare),
        "rischi_da_validare": rischi_da_validare, "dimensione_presidio": dimensione_presidio,
        "rischi_da_validare_n": len(rischi_da_validare),
    })


@login_required
def lista(request):
    qs = _richieste_visibili(request.user)
    tipo_attivo, tipo_filtri = _filtro_tipo(request, qs)
    if tipo_attivo:
        qs = qs.filter(tipo=tipo_attivo)
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
        "tipo_filtri": tipo_filtri, "tipo_attivo": tipo_attivo,
        "richieste": richieste, "stati": Stato.choices, "funzioni": Funzione.choices,
        "f_stato": stato, "f_funzione": funzione, "q": cerca,
    })


@login_required
def kanban(request):
    qs = _richieste_visibili(request.user)
    tipo_attivo, tipo_filtri = _filtro_tipo(request, qs)
    if tipo_attivo:
        qs = qs.filter(tipo=tipo_attivo)
    colonne = []
    etichette = {
        "in_coda": "In coda", "in_analisi": "In analisi (Funzione tecnica)",
        "pronte": "Pronte per approvazione", "in_approvazione": "In approvazione",
        "approvati": "Approvati / attivi", "chiusi": "Chiusi",
    }
    for chiave, stati in FASI.items():
        items = [r for r in qs if r.stato in stati]
        colonne.append({"chiave": chiave, "titolo": etichette[chiave], "items": items})
    return render(request, "flusso/kanban.html", {
        "tipo_filtri": tipo_filtri, "tipo_attivo": tipo_attivo,"colonne": colonne})


@login_required
def dettaglio(request, pk):
    richiesta = get_object_or_404(Richiesta.objects.select_related("proponente"), pk=pk)
    if not request.user.is_gestore and richiesta.proponente_id != request.user.id:
        return HttpResponseForbidden("Non hai i permessi per questa richiesta.")

    azioni = azioni_disponibili(richiesta, request.user)
    timeline = richiesta.transizioni.select_related("attore").all()
    bloccata = richiesta.modifica_bloccata
    # La Funzione tecnica modifica la scheda completa in tutti gli stati non bloccati;
    # l'owner solo prima della presa in carico (bozza/inviata).
    puo_modificare = (not bloccata) and (
        request.user.is_funzione
        or (richiesta.proponente_id == request.user.id and richiesta.stato in (Stato.BOZZA, Stato.INVIATA))
    )
    puo_eliminare = request.user.is_funzione or (
        request.user.is_owner and richiesta.proponente_id == request.user.id and richiesta.is_bozza
    )
    sal_form = SalForm(initial={"sal": richiesta.sal}) if (
        richiesta.is_operativa and request.user.is_funzione
    ) else None
    analisi_form = AnalisiAIForm(instance=richiesta) if (request.user.is_funzione and not bloccata) else None
    # Beneficio economico e incrementi: modificabili da owner e Funzione tecnica fino al blocco.
    puo_beneficio = (not bloccata) and (
        request.user.is_funzione or richiesta.proponente_id == request.user.id
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
        "rischi": rischi, "puo_analizza_rischio": request.user.is_funzione,
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
    if not request.user.is_funzione and not (is_owner and richiesta.stato in (Stato.BOZZA, Stato.INVIATA)):
        return HttpResponseForbidden("Non puoi modificare questa richiesta in questo stato.")
    funz = (richiesta.proponente.funzione or None) if request.user.is_funzione else (request.user.funzione or None)

    if request.method == "POST":
        form = RichiestaForm(request.POST, instance=richiesta, funzione_owner=funz)
        if form.is_valid():
            era_inviata = richiesta.stato == Stato.INVIATA
            form.save()
            # Solo lato owner: prima volta utile, stima beneficio/incrementi ancora vuoti (best-effort).
            # La Funzione tecnica non genera mai questi valori (li corregge solo a mano).
            if is_owner and not request.user.is_funzione:
                servizi.stima_incrementi_se_serve(richiesta, attore=request.user)
            if is_owner and not request.user.is_funzione and era_inviata:
                richiesta.stato = Stato.BOZZA
                richiesta.save(update_fields=["stato", "aggiornata_il"])
                richiesta.transizioni.create(
                    azione="modifica",
                    etichetta="Modificata dal proponente — da reinviare",
                    stato_da=Stato.INVIATA, stato_a=Stato.BOZZA, attore=request.user,
                    nota=f"Scheda aggiornata dal proponente; riportata in bozza per il reinvio alla {richiesta.funzione_competente_label}.",
                )
                messages.success(request, f"Richiesta aggiornata. Reinviala alla {richiesta.funzione_competente_label} per applicare le modifiche.")
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
    # In caso di blocco il motivo è puntuale (es. ambito per-utente senza numero utenti).
    if azione == "invia_a_budget":
        if richiesta.tipo != TipoProgetto.AI:
            messages.error(request, "La decisione di budget dell'owner riguarda i soli progetti AI. "
                                    "Su Application / IT Operation la copertura si indica nel campo «Budget IT» dell'analisi.")
            return redirect(richiesta)
        # Il costo token e il tipo di AI devono essere compilati: sono ciò su cui
        # l'owner decide. Nessun invio "al buio".
        mancano = []
        if richiesta.costo_token_ai is None:
            mancano.append("il costo token AI")
        if not richiesta.ai_autonomia:
            mancano.append("il tipo di AI")
        if mancano:
            messages.error(request, "Non posso inviare all'owner: manca " + " e ".join(mancano)
                                    + ". Completa l'analisi e riprova.")
            return redirect(richiesta)
        motivo = richiesta.costo_progetto_motivo_incompleto
        if motivo:
            messages.error(request, "Non posso inviare all'owner: " + motivo)
            return redirect(richiesta)

    # GATE: niente passaggio alla Direzione senza decisione di budget + tre validazioni.
    if azione in ("presenta_approvazione", "invia_in_approvazione"):
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
    if azione in ("riporta_in_bozza", "riporta_in_bozza_owner"):
        richiesta.azzera_per_bozza()  # budget, date e validazioni: si riparte
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

    Decisione obbligatoria a valle dell'analisi della Funzione tecnica: alla conferma
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
    if not request.user.is_funzione:
        return HttpResponseForbidden("Solo la Funzione tecnica puo' compilare l'analisi.")
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
        # La stima token vale solo per i progetti AI: su Application / IT Operation
        # il costo lo indica la funzione nel campo dedicato.
        stimato = (servizi.stima_costo_token_se_serve(richiesta, attore=request.user)
                   if richiesta.tipo == TipoProgetto.AI else False)
        msg = ("Analisi aggiornata. Importo token proposto dall'AI: "
               f"€ {richiesta.costo_token_ai} (modificabile)." if stimato
               else f"Analisi della {richiesta.funzione_competente_label} aggiornata.")
        # Casi senza decisione di budget dell'owner:
        #  - progetti AI a costo zero (nulla da approvare);
        #  - progetti Application / IT Operation: la copertura è il campo «Budget IT»
        #    compilato dalla funzione, l'owner non entra nel merito dei costi IT.
        # In entrambi i casi si generano subito i rischi e si prosegue.
        salta_budget, nota_budget = False, ""
        if richiesta.stato == Stato.IN_QUALIFICA and not richiesta.esito_budget:
            if richiesta.tipo != TipoProgetto.AI:
                if richiesta.budget_it:
                    richiesta.esito_budget = (EsitoBudget.A_BUDGET
                                              if richiesta.budget_it == "BUDGET"
                                              else EsitoBudget.EXTRA_BUDGET)
                    salta_budget = True
                    nota_budget = (f" Copertura IT: {richiesta.get_budget_it_display()} "
                                   "(nessuna decisione di budget dell'owner sui progetti non AI).")
                else:
                    msg += (" Indica il «Budget IT» (Budget / Extra Budget) per far proseguire "
                            "la pratica: su Application / IT Operation sostituisce la decisione dell'owner.")
            elif richiesta.costo_progetto_stimato == 0:
                richiesta.esito_budget = EsitoBudget.A_BUDGET
                salta_budget = True
                nota_budget = " Costo zero: nessuna approvazione di budget richiesta."
        if salta_budget:
            richiesta.save(update_fields=["esito_budget"])
            res = servizi.classifica_tutti_i_rischi(richiesta, attore=request.user)
            if res["ok"]:
                dims = ", ".join(_NOMI_RISCHIO[x] for x in res["ok"])
                msg += nota_budget + f" Rischio stimato dall'AI per: {dims}. Da validare dal CISO."
            else:
                msg += nota_budget + (" Rischi da completare (classificazione AI non riuscita "
                                      "su alcune dimensioni).")
        else:
            rip = richiesta.ripartizione_budget
            if rip:
                msg += (f" Costo € {rip['costo']:.2f}: a budget € {rip['a_budget']:.2f}, "
                        f"extra budget € {rip['extra']:.2f} ({richiesta.budget_stato_label}).")
        cloni = servizi.clona_per_funzioni(richiesta, attore=request.user)
        if cloni:
            elenco = ", ".join(f"{c.codice} ({c.get_tipo_display()})" for c in cloni)
            msg += f" Create le schede dedicate: {elenco}, ora in carico alla funzione competente."
        messages.success(request, msg)
    else:
        messages.error(request, "Controlla i dati dell'analisi: alcuni valori non sono validi.")
    return redirect(richiesta)


@login_required
@require_POST
def compila_analisi_ai(request, pk):
    """Bottone «AI»: l'AI precompila l'intera analisi; l'AI Officer poi verifica, modifica e salva."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_funzione:
        return HttpResponseForbidden("Solo la Funzione tecnica puo' usare la compilazione automatica.")
    if richiesta.modifica_bloccata:
        messages.error(request, "La richiesta è in approvazione o approvata: analisi non modificabile.")
        return redirect(richiesta)
    ok, errore = servizi.compila_analisi_con_ai(richiesta, attore=request.user)
    if ok:
        messages.success(
            request,
            "Analisi di fattibilità redatta dall'AI. Rivedila, completa i campi tecnici "
            "(tipo di AI, effort, costi) e salva con «Salva analisi».",
        )
    else:
        messages.error(request, f"Compilazione AI non riuscita: {errore}")
    return redirect(richiesta)


@login_required
@require_POST
def aggiorna_beneficio(request, pk):
    """Beneficio economico e incrementi: modificabili da owner e Funzione tecnica fino al blocco."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    is_owner = richiesta.proponente_id == request.user.id
    if richiesta.modifica_bloccata:
        messages.error(request, "La richiesta è in approvazione o approvata: beneficio non modificabile.")
        return redirect(richiesta)
    if not (is_owner or request.user.is_funzione):
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
    base = Richiesta.objects.filter(stato__in=[Stato.APPROVATA, Stato.ATTIVO, Stato.MONITORAGGIO, Stato.COMPLETATO])
    tipo_attivo, tipo_filtri = _filtro_tipo(request, base)
    if tipo_attivo:
        base = base.filter(tipo=tipo_attivo)
    qs = (base
          .select_related("proponente")
          .order_by("data_inizio", "creata_il"))
    puo_modificare = request.user.is_funzione
    righe = [{"r": r, "form": PianificazioneForm(instance=r) if puo_modificare else None} for r in qs]
    return render(request, "flusso/schedulazione.html", {
        "tipo_filtri": tipo_filtri, "tipo_attivo": tipo_attivo,
        "righe": righe, "puo_modificare": puo_modificare, "totale": qs.count(),
    })


@login_required
@require_POST
def salva_pianificazione(request, pk):
    """Salvataggio manuale delle date di un progetto dalla schedulazione."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_funzione:
        return HttpResponseForbidden("Solo la Funzione tecnica puo' modificare la pianificazione.")
    form = PianificazioneForm(request.POST, instance=richiesta)
    if form.is_valid():
        form.save()
        messages.success(request, f"Date aggiornate per {richiesta.codice}.")
    else:
        messages.error(request, "Date non valide: controlla inizio e consegna.")
    return redirect("flusso:schedulazione")


def _url_effort(request, pk):
    """URL della pagina Effort che conserva il filtro di fase attivo (se valido)."""
    fase = request.POST.get("fase", "")
    suffisso = f"?fase={fase}" if fase in FASI else ""
    return reverse("flusso:ripartizione_effort") + suffisso + f"#r{pk}"


@login_required
def ripartizione_effort(request):
    """Ripartizione dell'effort per attività e figure: la vista per spiegare il carico.

    Aggregati di portafoglio (sviluppo vs resto, per attività, per figura) e
    dettaglio per progetto. Include OGNI stato, anche in approvazione e approvati:
    la ripartizione non tocca l'effort totale, lo spiega.
    """
    if not request.user.is_gestore:
        return HttpResponseForbidden("Pagina riservata.")
    base_tutti = Richiesta.objects.filter(effort_ore__gt=0)
    base_qs = (base_tutti.select_related("proponente").prefetch_related("voci_effort")
               .order_by("-effort_ore", "-creata_il"))
    tipo_attivo, tipo_filtri = _filtro_tipo(request, base_qs)
    if tipo_attivo:
        base_qs = base_qs.filter(tipo=tipo_attivo)
    # Filtro per fase (le stesse di board e KPI), con conteggi che guidano la scelta.
    etichette_fase = {
        "in_coda": "In coda", "in_analisi": "In analisi", "pronte": "Pronte per approvazione",
        "in_approvazione": "In approvazione", "approvati": "Approvati / attivi", "chiusi": "Chiusi",
    }
    fase_per_stato = {s.value: k for k, stati in FASI.items() for s in stati}
    conteggi = Counter(fase_per_stato.get(st) for st in base_qs.values_list("stato", flat=True))
    fase = request.GET.get("fase") or ""
    if fase in FASI:
        qs = base_qs.filter(stato__in=FASI[fase])
        qs_tutti_tipi = base_tutti.filter(stato__in=FASI[fase])
    else:
        fase = ""
        qs = base_qs
        qs_tutti_tipi = base_tutti
    schede_tipo = _schede_effort(request, qs_tutti_tipi, tipo_attivo)
    filtri = ([{"chiave": "", "label": "Tutto", "n": base_qs.count(), "attivo": fase == ""}]
              + [{"chiave": k, "label": etichette_fase[k], "n": conteggi.get(k, 0),
                  "attivo": fase == k} for k in FASI])
    ordine = {a: i for i, a in enumerate(ORDINE_ATTIVITA)}
    righe = []
    for r in qs:
        voci = sorted(r.voci_effort.all(), key=lambda v: ordine.get(v.attivita, 99))
        righe.append({"r": r, "voci": voci, "quadra": r.ripartizione_quadra,
                      "delta": r.ripartizione_delta})
    voci_tutte = VoceEffort.objects.filter(richiesta__in=qs)
    tot_rip = voci_tutte.aggregate(t=Sum("ore"))["t"] or 0
    per_att = {x["attivita"]: x["t"] for x in voci_tutte.values("attivita").annotate(t=Sum("ore"))}
    per_fig = {x["figura"]: x["t"] for x in voci_tutte.values("figura").annotate(t=Sum("ore"))}
    att_labels, fig_labels = dict(AttivitaEffort.choices), dict(FiguraEffort.choices)
    agg_att = [{"codice": a, "label": att_labels[a], "ore": per_att.get(a, 0),
                "pct": round(per_att.get(a, 0) * 100 / tot_rip) if tot_rip else 0}
               for a in ORDINE_ATTIVITA]
    agg_fig = sorted([{"label": fig_labels[f], "ore": o,
                       "pct": round(o * 100 / tot_rip) if tot_rip else 0}
                      for f, o in per_fig.items()], key=lambda x: -x["ore"])
    ore_sviluppo = per_att.get(AttivitaEffort.SVILUPPO.value, 0)
    pct_sviluppo = round(ore_sviluppo * 100 / tot_rip) if tot_rip else 0
    return render(request, "flusso/ripartizione.html", {
        "tipo_filtri": tipo_filtri, "tipo_attivo": tipo_attivo,
        "righe": righe, "puo_modificare": request.user.is_funzione,
        "tot_effort": qs.aggregate(t=Sum("effort_ore"))["t"] or 0, "tot_rip": tot_rip,
        "agg_att": agg_att, "agg_fig": agg_fig,
        "ore_sviluppo": ore_sviluppo, "pct_sviluppo": pct_sviluppo,
        "ore_resto": tot_rip - ore_sviluppo, "pct_resto": (100 - pct_sviluppo) if tot_rip else 0,
        "senza_ripartizione": sum(1 for x in righe if not x["voci"]),
        "senza_effort": Richiesta.objects.exclude(effort_ore__gt=0).count(),
        "fig_choices": FiguraEffort.choices,
        "filtri": filtri, "fase_attiva": fase, "schede_tipo": schede_tipo,
    })


@login_required
@require_POST
def genera_ripartizione(request, pk):
    """L'AI propone la ripartizione dell'effort (sostituisce le voci; totale invariato)."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_funzione:
        return HttpResponseForbidden("Solo la Funzione tecnica puo' generare la ripartizione.")
    ok, errore = servizi.ripartisci_effort_con_ai(richiesta)
    if ok:
        messages.success(request, f"Ripartizione effort proposta dall'AI per {richiesta.codice} "
                                  "(totale invariato, modificabile).")
    else:
        messages.error(request, f"Ripartizione {richiesta.codice} non riuscita: {errore}")
    return redirect(_url_effort(request, richiesta.pk))


@login_required
@require_POST
def salva_ripartizione(request, pk):
    """Salvataggio manuale delle voci: la somma DEVE quadrare con l'effort registrato."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_funzione:
        return HttpResponseForbidden("Solo la Funzione tecnica puo' modificare la ripartizione.")
    voci = list(richiesta.voci_effort.all())
    figure_valide = dict(FiguraEffort.choices)
    nuove = []
    for v in voci:
        try:
            ore = int(request.POST.get(f"ore_{v.id}", v.ore))
        except (TypeError, ValueError):
            messages.error(request, f"Ore non valide su «{v.get_attivita_display()}».")
            return redirect(_url_effort(request, richiesta.pk))
        if ore < 0:
            messages.error(request, f"Ore negative su «{v.get_attivita_display()}».")
            return redirect(_url_effort(request, richiesta.pk))
        figura = request.POST.get(f"figura_{v.id}", v.figura)
        if figura not in figure_valide:
            figura = v.figura
        nuove.append((v, ore, figura))
    somma = sum(o for _, o, _ in nuove)
    atteso = richiesta.effort_ore or 0
    if somma != atteso:
        messages.error(request, f"La ripartizione di {richiesta.codice} non quadra: "
                                f"somma {somma} h contro un effort di {atteso} h "
                                f"(Δ {somma - atteso:+d} h). Correggi e risalva.")
        return redirect(_url_effort(request, richiesta.pk))
    for v, ore, figura in nuove:
        if v.ore != ore or v.figura != figura:
            v.ore, v.figura, v.stimata_ai = ore, figura, False
            v.save(update_fields=["ore", "figura", "stimata_ai", "aggiornata_il"])
    messages.success(request, f"Ripartizione di {richiesta.codice} aggiornata (quadra: {atteso} h).")
    return redirect(_url_effort(request, richiesta.pk))


@login_required
@require_POST
def crea_griglia_effort_view(request, pk):
    """Griglia manuale a 0 ore, da compilare (nessun numero inventato)."""
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_funzione:
        return HttpResponseForbidden("Solo la Funzione tecnica puo' creare la griglia.")
    if servizi.crea_griglia_effort(richiesta):
        messages.success(request, f"Griglia creata per {richiesta.codice}: distribuisci le "
                                  f"{richiesta.effort_ore} h e salva.")
    else:
        messages.error(request, "Esistono già voci di ripartizione per questa richiesta.")
    return redirect(_url_effort(request, richiesta.pk))


@login_required
def costi(request):
    """Vista costi del portafoglio, di default sulla coda di approvazione.

    Totale annuo stimato (token AI + altri costi), quota a budget / extra budget /
    da definire, aggregato per funzione e dettaglio per progetto. Sola lettura:
    i costi si modificano nell'analisi. Stesse fasi di board/KPI/Effort.
    """
    if not request.user.is_gestore:
        return HttpResponseForbidden("Pagina riservata.")
    from decimal import Decimal as _D
    base_tutti = Richiesta.objects.select_related("proponente")
    base_qs = base_tutti
    tipo_attivo, tipo_filtri = _filtro_tipo(request, base_qs)
    if tipo_attivo:
        base_qs = base_qs.filter(tipo=tipo_attivo)
    etichette_fase = {
        "in_coda": "In coda", "in_analisi": "In analisi", "pronte": "Pronte per approvazione",
        "in_approvazione": "In approvazione", "approvati": "Approvati / attivi", "chiusi": "Chiusi",
    }
    fase_per_stato = {s.value: k for k, stati in FASI.items() for s in stati}
    conteggi = Counter(fase_per_stato.get(st) for st in base_qs.values_list("stato", flat=True))
    fase = request.GET.get("fase")
    if fase == "tutte":
        qs, qs_tutti_tipi = base_qs, base_tutti
    elif fase in FASI:
        qs, qs_tutti_tipi = base_qs.filter(stato__in=FASI[fase]), base_tutti.filter(stato__in=FASI[fase])
    else:
        fase = "in_approvazione"  # default: la coda della Direzione
        qs, qs_tutti_tipi = base_qs.filter(stato__in=FASI[fase]), base_tutti.filter(stato__in=FASI[fase])
    schede_tipo = _schede_costi(request, qs_tutti_tipi, tipo_attivo)
    filtri = ([{"chiave": "tutte", "label": "Tutto", "n": base_qs.count(), "attivo": fase == "tutte"}]
              + [{"chiave": k, "label": etichette_fase[k], "n": conteggi.get(k, 0),
                  "attivo": fase == k} for k in FASI])

    righe = []
    tot = tot_token = tot_altri = tot_a_budget = tot_extra = tot_da_definire = _D(0)
    n_incompleti = 0
    per_funzione = {}
    for r in qs:
        costo = r.costo_progetto_stimato
        rip = r.ripartizione_budget
        motivo = None
        if costo is None and (r.costo_token_ai is not None or r.altri_costi is not None):
            motivo = r.costo_progetto_motivo_incompleto
            n_incompleti += 1
        # La componente token è derivata per differenza dal totale: così la
        # scomposizione ricompone SEMPRE il costo (una tantum inclusa).
        token_comp = (costo - (r.altri_costi or 0)) if costo is not None else None
        righe.append({
            "r": r, "costo": costo, "token": token_comp,
            "altri": r.altri_costi, "rip": rip, "motivo": motivo,
        })
        if costo is not None:
            tot += costo
            tot_token += token_comp
            tot_altri += r.altri_costi or 0
            if rip:
                tot_a_budget += rip["a_budget"]
                tot_extra += rip["extra"]
            else:
                tot_da_definire += costo
            label_f = r.get_funzione_display()
            per_funzione[label_f] = per_funzione.get(label_f, _D(0)) + costo
    righe.sort(key=lambda x: (x["costo"] is None, -(x["costo"] or 0)))
    agg_funzioni = sorted(
        [{"label": k, "costo": v, "pct": round(v * 100 / tot) if tot else 0}
         for k, v in per_funzione.items()], key=lambda x: -x["costo"])
    return render(request, "flusso/costi.html", {
        "tipo_filtri": tipo_filtri, "tipo_attivo": tipo_attivo,
        "righe": righe, "filtri": filtri, "fase_attiva": fase, "schede_tipo": schede_tipo,
        "tot": tot, "tot_token": tot_token, "tot_altri": tot_altri,
        "tot_a_budget": tot_a_budget, "tot_extra": tot_extra,
        "tot_da_definire": tot_da_definire, "n_incompleti": n_incompleti,
        "agg_funzioni": agg_funzioni, "n_progetti": len(righe),
    })


@login_required
@require_POST
def aggiorna_sal(request, pk):
    richiesta = get_object_or_404(Richiesta, pk=pk)
    if not request.user.is_funzione or not richiesta.is_operativa:
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
    tipo_attivo, tipo_filtri = _filtro_tipo(request, Richiesta.objects.all())
    dati = calcola_kpi(tipo=tipo_attivo)
    config = ConfigurazioneAI.load()
    return render(request, "flusso/kpi.html", {"k": dati, "config": config, "aree": riepilogo_aree(),
                                               "tipo_filtri": tipo_filtri, "tipo_attivo": tipo_attivo})


@login_required
@require_POST
def genera_analisi_kpi(request):
    """Genera/aggiorna la lettura esecutiva AI dei KPI (solo Funzione tecnica)."""
    if not request.user.is_funzione:
        return HttpResponseForbidden("Solo la Funzione tecnica può generare l'analisi.")
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
    """Form grafico di configurazione AI (solo Funzione tecnica)."""
    if not request.user.is_funzione:
        return HttpResponseForbidden("Pagina riservata alla Funzione tecnica.")
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
    if not request.user.is_funzione:
        return HttpResponseForbidden("Azione riservata alla Funzione tecnica.")
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
    puo = request.user.is_funzione or (
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
    """Registro delle tre dimensioni di rischio (Funzione tecnica e presìdi Legale/CISO/DPO)."""
    if not (request.user.is_funzione or request.user.is_validatore_rischio):
        return HttpResponseForbidden("Pagina riservata alla Funzione tecnica e ai presìdi (Legale, CISO, DPO).")
    voci = (Richiesta.objects.select_related("proponente")
            .prefetch_related("classificazioni").order_by("numero"))
    tipo_attivo, tipo_filtri = _filtro_tipo(request, voci)
    if tipo_attivo:
        voci = voci.filter(tipo=tipo_attivo)
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
        "tipo_filtri": tipo_filtri, "tipo_attivo": tipo_attivo,
        "righe": righe, "totale": len(righe), "pronti": pronti,
        "da_validare": da_validare, "config": ConfigurazioneAI.load(),
    })


@login_required
@require_POST
def analizza_rischio(request, pk):
    """(Ri)analizza con l'AI le tre dimensioni di rischio di una richiesta (Funzione tecnica)."""
    if not request.user.is_funzione:
        return HttpResponseForbidden("Azione riservata alla Funzione tecnica.")
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


def _segna_pronta_se_validata(richiesta, attore) -> bool:
    """Auto-avanzamento: se tutte le dimensioni di rischio sono validate/corrette e il
    budget è definito, la pratica passa a «Pronta per approvazione». L'invio alla
    Direzione resta un'azione manuale riservata alla Funzione tecnica."""
    if (richiesta.stato == Stato.IN_QUALIFICA and richiesta.esito_budget
            and richiesta.rischi_tutti_validati):
        try:
            richiesta.applica("presenta_approvazione", attore=attore,
                              nota="Avanzamento automatico: tutte le dimensioni di rischio validate.")
            return True
        except Exception:
            return False
    return False


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
        if _segna_pronta_se_validata(richiesta, request.user):
            messages.success(request, "Tutte le dimensioni di rischio sono validate: la pratica è «Pronta per approvazione». La " + richiesta.funzione_competente_label + " deciderà quando inviarla alla Direzione.")
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
