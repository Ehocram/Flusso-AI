"""
Calcolo deterministico dei KPI del portafoglio progetti AI.

I numeri vengono SEMPRE calcolati qui dai dati reali (Richiesta + audit trail);
l'AI riceve questi valori e ne produce solo la lettura discorsiva.
"""

import math
from decimal import Decimal

from accounts.models import Funzione
from django.db.models import Avg, Count, Sum

from .models import (AutonomiaAI, ClassificazioneRischio, DeploymentAI, Richiesta,
                     StatoRischio, StrategiaTrattamento, Transizione)
from .workflow import FASI, Stato

_COLORI_FASE = {
    "in_coda": "#9aa0a6",
    "in_analisi": "#1d6fb8",
    "in_approvazione": "#e0a800",
    "approvati": "#1f8a4c",
    "chiusi": "#5b5d63",
}
_ETICHETTE_FASE = {
    "in_coda": "In coda",
    "in_analisi": "In analisi",
    "in_approvazione": "In approvazione",
    "approvati": "Approvati / attivi",
    "chiusi": "Chiusi",
}
_ORDINE_FASE = ["in_coda", "in_analisi", "in_approvazione", "approvati", "chiusi"]
_RAGGIO = 52


def _segmenti_donut(coppie):
    """coppie: lista di (chiave, label, n, colore) -> segmenti SVG (dasharray/offset)."""
    circ = 2 * math.pi * _RAGGIO
    tot = sum(n for _, _, n, _ in coppie) or 1
    segs, offset = [], 0.0
    for chiave, label, n, colore in coppie:
        frac = n / tot
        dash = frac * circ
        segs.append({
            "chiave": chiave, "label": label, "n": n, "perc": round(100 * frac),
            "color": colore, "dash": round(dash, 2), "gap": round(circ - dash, 2),
            "offset": round(-offset, 2),
        })
        offset += dash
    return segs, round(circ, 2)


def calcola_kpi() -> dict:
    qs = Richiesta.objects.all()
    tot = qs.count()
    # Base "portafoglio approvato": tutte le metriche di valore e di governance si
    # calcolano SOLO sui progetti approvati dalla Direzione, così restano attendibili
    # (a valore nullo finché non ci sono progetti approvati). L'unica eccezione è
    # "rischi da validare", che resta un contatore di pipeline (azione per i presìdi).
    approvati_qs = qs.filter(stato__in=[Stato.APPROVATA, Stato.ATTIVO,
                                        Stato.MONITORAGGIO, Stato.COMPLETATO])

    per_stato = {s.value: 0 for s in Stato}
    for row in qs.values("stato").annotate(n=Count("id")):
        per_stato[row["stato"]] = row["n"]
    per_fase = {k: sum(per_stato[s.value] for s in membri) for k, membri in FASI.items()}

    attivi = per_stato[Stato.ATTIVO] + per_stato[Stato.MONITORAGGIO]
    completati = per_stato[Stato.COMPLETATO]
    respinti = per_stato[Stato.RESPINTA]
    approvati = per_stato[Stato.APPROVATA] + attivi + completati
    in_approvazione = per_stato[Stato.IN_APPROVAZIONE]
    in_pipeline = per_fase["in_coda"] + per_fase["in_analisi"] + per_fase["in_approvazione"]
    decisi = approvati + respinti
    tasso_appr = round(100 * approvati / decisi) if decisi else None

    sal_qs = qs.filter(stato__in=[Stato.ATTIVO, Stato.MONITORAGGIO])
    sal_medio = round(sal_qs.aggregate(a=Avg("sal"))["a"] or 0)

    investimento = sum((r.costo_totale_stimato or Decimal(0))
                       for r in approvati_qs.only("costo_token_ai", "altri_costi"))
    effort = approvati_qs.aggregate(s=Sum("effort_ore"))["s"] or 0

    # --- Costo token annuo del portafoglio approvato + mix infrastruttura/autonomia -
    costo_annuo_token = sum((r.costo_token_annuo_totale or Decimal(0)) for r in approvati_qs)

    def _distrib(campo, scelte):
        cont = {c: 0 for c, _ in scelte}
        for row in approvati_qs.exclude(**{campo: ""}).values(campo).annotate(n=Count("id")):
            cont[row[campo]] = row["n"]
        etich = dict(scelte)
        return [{"code": c, "label": etich[c], "n": cont[c]} for c, _ in scelte if cont[c]]

    infra = _distrib("ai_deployment", DeploymentAI.choices)
    autonomia = _distrib("ai_autonomia", AutonomiaAI.choices)

    # --- Governance del rischio ----------------------------------------------
    # "Rischi da validare" è l'unico contatore di pipeline (azione per i presìdi):
    # conta tutte le proposte AI in attesa, anche su progetti non ancora approvati.
    rischi_da_validare = ClassificazioneRischio.objects.filter(
        stato=StatoRischio.PROPOSTO_AI).count()
    # Gli altri due si riferiscono al solo portafoglio approvato.
    residui_da_convalidare = ClassificazioneRischio.objects.filter(
        richiesta__in=approvati_qs,
        strategia__in=[StrategiaTrattamento.MITIGATO, StrategiaTrattamento.TRASFERITO],
        residuo_convalidato=False).count()
    progetti_rischio_pronti = 0
    for r in approvati_qs.prefetch_related("classificazioni"):
        cl = {c.tipo: c for c in r.classificazioni.all()}
        if all(cl.get(t) and cl[t].validato for t in ("AIACT", "NIS2", "GDPR")):
            progetti_rischio_pronti += 1

    # --- Budget del portafoglio APPROVATO dalla Direzione --------------------
    costo_a_budget, costo_extra_budget = Decimal(0), Decimal(0)
    progetti_extra_budget = 0
    for r in approvati_qs:
        contrib = r.contributo_budget
        if contrib:
            a_b, ex = contrib
            costo_a_budget += a_b
            costo_extra_budget += ex
            if ex > 0:
                progetti_extra_budget += 1
    costo_annuo_approvati = costo_a_budget + costo_extra_budget
    saving_eco_approvati = approvati_qs.aggregate(s=Sum("saving_economico"))["s"] or 0

    # Metriche di valore: sui soli progetti approvati (saving_eco_tot coincide con
    # saving_eco_approvati: il portafoglio approvato è l'unica base attendibile).
    saving_eco_tot = saving_eco_approvati
    _iq = approvati_qs.aggregate(a=Avg("incremento_qualitativo"))["a"]
    _ie = approvati_qs.aggregate(a=Avg("incremento_efficienza"))["a"]
    incr_qual_medio = round(_iq, 1) if _iq is not None else None
    incr_eff_medio = round(_ie, 1) if _ie is not None else None

    ritardo_tot, progetti_in_ritardo = 0, 0
    for r in qs.filter(stato__in=[Stato.APPROVATA, Stato.ATTIVO,
                                  Stato.MONITORAGGIO, Stato.COMPLETATO]):
        av = r.avanzamento_temporale()
        if av and av["in_ritardo"]:
            ritardo_tot += av["ritardo_giorni"]
            progetti_in_ritardo += 1

    # aree (funzioni)
    aree_count = {f.value: 0 for f in Funzione}
    for row in qs.values("funzione").annotate(n=Count("id")):
        aree_count[row["funzione"]] = row["n"]
    etich = dict(Funzione.choices)
    max_area = max(aree_count.values()) or 1
    aree = [{"label": etich[k], "n": v, "perc": round(100 * v / max_area)}
            for k, v in sorted(aree_count.items(), key=lambda x: -x[1])]
    aree_coinvolte = sum(1 for v in aree_count.values() if v)

    # donut per fase (solo segmenti non vuoti)
    coppie = [(k, _ETICHETTE_FASE[k], per_fase[k], _COLORI_FASE[k]) for k in _ORDINE_FASE]
    donut, circ = _segmenti_donut([c for c in coppie if c[2] > 0])

    # funnel ordinato
    max_f = max(per_fase.values()) or 1
    funnel = [{"label": _ETICHETTE_FASE[k], "n": per_fase[k],
               "perc": round(100 * per_fase[k] / max_f), "color": _COLORI_FASE[k]}
              for k in _ORDINE_FASE]

    # gauge SAL medio
    circ_g = 2 * math.pi * _RAGGIO
    sal_dash = round(circ_g * sal_medio / 100, 2)
    gauge = {"perc": sal_medio, "dash": sal_dash,
             "gap": round(circ_g - sal_dash, 2), "circ": round(circ_g, 2)}

    attivi_sal = [{"codice": r.codice, "titolo": r.titolo, "sal": r.sal,
                   "stato": r.get_stato_display()}
                  for r in sal_qs.order_by("-sal", "numero")]

    # lead time medio: da 'invia' a 'approva' (giorni), dall'audit trail
    inv_t, app_t = {}, {}
    for t in Transizione.objects.filter(azione__in=["invia", "approva"]).values(
            "richiesta_id", "azione", "creata_il"):
        (inv_t if t["azione"] == "invia" else app_t).setdefault(t["richiesta_id"], t["creata_il"])
    deltas = [(app_t[i] - inv_t[i]).days for i in app_t if i in inv_t]
    lead = round(sum(deltas) / len(deltas), 1) if deltas else None

    return {
        "tot": tot, "attivi": attivi, "completati": completati, "respinti": respinti,
        "approvati": approvati, "in_approvazione": in_approvazione, "in_pipeline": in_pipeline,
        "tasso_appr": tasso_appr, "sal_medio": sal_medio, "investimento": investimento,
        "effort": effort, "aree_coinvolte": aree_coinvolte, "lead": lead,
        "saving_eco_tot": saving_eco_tot, "incr_qual_medio": incr_qual_medio,
        "incr_eff_medio": incr_eff_medio, "ritardo_tot": ritardo_tot,
        "progetti_in_ritardo": progetti_in_ritardo,
        "costo_annuo_token": costo_annuo_token, "infra": infra, "autonomia": autonomia,
        "rischi_da_validare": rischi_da_validare, "residui_da_convalidare": residui_da_convalidare,
        "progetti_rischio_pronti": progetti_rischio_pronti,
        "costo_a_budget": costo_a_budget, "costo_extra_budget": costo_extra_budget,
        "costo_annuo_approvati": costo_annuo_approvati, "saving_eco_approvati": saving_eco_approvati,
        "progetti_extra_budget": progetti_extra_budget,
        "per_fase": per_fase, "aree": aree, "donut": donut, "donut_circ": circ,
        "funnel": funnel, "gauge": gauge, "attivi_sal": attivi_sal,
    }


def riassunto_per_ai(kpi: dict, includi_titoli: bool = False) -> str:
    """Sintesi testuale dei KPI da passare al modello (solo numeri aggregati)."""
    tasso = f"{kpi['tasso_appr']}%" if kpi["tasso_appr"] is not None else "n/d"
    lead = f"{kpi['lead']} giorni" if kpi["lead"] is not None else "n/d"
    righe = [
        f"Totale progetti: {kpi['tot']}",
        f"In pipeline (coda + analisi + approvazione): {kpi['in_pipeline']}",
        f"In approvazione: {kpi['in_approvazione']}",
        f"Approvati/attivi cumulati: {kpi['approvati']}",
        f"Attivi e in monitoraggio: {kpi['attivi']}",
        f"Completati: {kpi['completati']}",
        f"Respinti: {kpi['respinti']}",
        f"Tasso di approvazione: {tasso}",
        f"SAL medio progetti attivi: {kpi['sal_medio']}%",
        f"Lead time medio invio→approvazione: {lead}",
        f"Effort stimato (progetti approvati): {kpi['effort']} ore",
        f"Investimento stimato (progetti approvati): € {kpi['investimento']}",
        f"Costo token annuo (progetti approvati): € {kpi['costo_annuo_token']}",
        "Infrastruttura prevista: " + (", ".join(f"{d['label']} {d['n']}" for d in kpi["infra"]) or "n/d"),
        "Tipo di AI (autonomia): " + (", ".join(f"{d['label']} {d['n']}" for d in kpi["autonomia"]) or "n/d"),
        f"Rischi da validare dai presìdi: {kpi['rischi_da_validare']}",
        f"Rischi residui da convalidare: {kpi['residui_da_convalidare']}",
        f"Progetti con le tre validazioni di rischio complete: {kpi['progetti_rischio_pronti']}",
        f"Portafoglio APPROVATO dalla Direzione: costo annuo € {kpi['costo_annuo_approvati']}, "
        f"di cui a budget € {kpi['costo_a_budget']} ed extra budget € {kpi['costo_extra_budget']} "
        f"({kpi['progetti_extra_budget']} progetti fuori dal solo budget)",
        f"Beneficio economico atteso (progetti approvati): € {kpi['saving_eco_approvati']}",
        f"Incremento qualitativo medio (approvati): {kpi['incr_qual_medio'] if kpi['incr_qual_medio'] is not None else 'n/d'}%",
        f"Incremento efficienza medio (approvati): {kpi['incr_eff_medio'] if kpi['incr_eff_medio'] is not None else 'n/d'}%",
        f"Progetti in ritardo: {kpi['progetti_in_ritardo']} (totale {kpi['ritardo_tot']} giorni di ritardo)",
        f"Aree coinvolte: {kpi['aree_coinvolte']} su 7",
        "Distribuzione per fase: " + ", ".join(f"{f['label']} {f['n']}" for f in kpi["funnel"]),
        "Progetti per area: " + ", ".join(f"{a['label']} {a['n']}" for a in kpi["aree"] if a["n"]),
    ]
    if includi_titoli and kpi["attivi_sal"]:
        righe.append("Progetti attivi: "
                     + "; ".join(f"{p['titolo']} (SAL {p['sal']}%)" for p in kpi["attivi_sal"]))
    return "\n".join(righe)
