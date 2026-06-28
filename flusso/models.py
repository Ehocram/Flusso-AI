"""
Modelli di dominio.

Richiesta    = la "scheda progetto" (il rettangolo delle slide 8-9), arricchita
               con lo stato del flusso e il proponente.
Transizione  = registro immutabile dei passaggi di stato (audit trail). E'
               l'evidenza eseguibile della procedura: chi, cosa, quando.
"""

import logging
from datetime import date

from accounts.models import Funzione
from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

from .workflow import STATI_OPERATIVI, STATI_TERMINALI, Stato, transizione

audit = logging.getLogger("flusso.audit")


class TipoRischio(models.TextChoices):
    """Le tre dimensioni di rischio/conformità valutate per ogni progetto."""

    AIACT = "AIACT", "AI Act"
    NIS2 = "NIS2", "NIS2"
    GDPR = "GDPR", "GDPR"


class StatoRischio(models.TextChoices):
    """Stato di lavorazione di una classificazione di rischio."""

    DA_ANALIZZARE = "DA_ANALIZZARE", "Da analizzare"
    PROPOSTO_AI = "PROPOSTO_AI", "Proposto dall'AI — da validare"
    VALIDATO = "VALIDATO", "Validato dal presidio"
    MODIFICATO = "MODIFICATO", "Modificato dal presidio"


class StrategiaTrattamento(models.TextChoices):
    """Strategia di trattamento del rischio (ISO 27005 / ISO 31000)."""

    ACCETTATO = "ACCETTATO", "Accettato"
    MITIGATO = "MITIGATO", "Mitigato (ridotto)"
    TRASFERITO = "TRASFERITO", "Trasferito"
    EVITATO = "EVITATO", "Evitato (eliminato)"


# Categorie ammesse per ciascuna dimensione (codice, etichetta).
CATEGORIE_RISCHIO = {
    "AIACT": [
        ("VIETATO", "Inaccettabile / Vietato (art. 5)"),
        ("ALTO", "Alto rischio (Allegato III / I)"),
        ("LIMITATO", "Rischio limitato — trasparenza (art. 50)"),
        ("MINIMO", "Rischio minimo"),
    ],
    "NIS2": [
        ("ALTO", "Alto"),
        ("MEDIO", "Medio"),
        ("BASSO", "Basso"),
        ("NA", "Non applicabile"),
    ],
    "GDPR": [
        ("ALTO", "Alto"),
        ("MEDIO", "Medio"),
        ("BASSO", "Basso"),
        ("NA", "Non applicabile"),
    ],
}

# Ruolo (processo) che valida/modifica ciascuna dimensione.
RUOLO_VALIDATORE = {"AIACT": "LEGALE", "NIS2": "CISO", "GDPR": "DPO"}
ETICHETTA_VALIDATORE = {"AIACT": "Funzione Legale", "NIS2": "CISO", "GDPR": "DPO"}
# Mappa inversa: ruolo del presidio -> dimensione di rischio di sua competenza.
DIMENSIONE_PER_RUOLO = {ruolo: dim for dim, ruolo in RUOLO_VALIDATORE.items()}


def obblighi_in_voci(testo) -> list:
    """Normalizza il campo `obblighi` in un elenco pulito di voci.

    Accetta indifferentemente: un array JSON, la repr di una lista Python
    (compatibilita' con dati storici salvati come str(list)), oppure testo con
    voci separate da a-capo o ';'. Rimuove marcatori iniziali e voci vuote.
    """
    import ast
    import json
    import re

    t = (testo or "").strip()
    if not t:
        return []
    voci = None
    if t[0] in "[(":
        for parser in (json.loads, ast.literal_eval):
            try:
                val = parser(t)
            except Exception:
                continue
            if isinstance(val, (list, tuple)):
                voci = [str(x) for x in val]
                break
    if voci is None:
        voci = re.split(r"[\n;]+", t)
    out = []
    for v in voci:
        v = re.sub(r"^[\s\-\u2022\u00b7\*]+", "", str(v)).strip()
        if v:
            out.append(v)
    return out

_CSS_AIACT = {"VIETATO": "rk-vietato", "ALTO": "rk-alto", "LIMITATO": "rk-limitato", "MINIMO": "rk-minimo"}
_CSS_LIVELLO = {"ALTO": "rk-vietato", "MEDIO": "rk-alto", "BASSO": "rk-minimo", "NA": "rk-nd"}


def categorie_valide(tipo) -> set:
    return {c for c, _ in CATEGORIE_RISCHIO.get(tipo, [])}


def categoria_label(tipo, code) -> str:
    if not code:
        return "Non valutato"
    return dict(CATEGORIE_RISCHIO.get(tipo, [])).get(code, code)


def categoria_css(tipo, code) -> str:
    if not code:
        return "rk-nd"
    tabella = _CSS_AIACT if tipo == "AIACT" else _CSS_LIVELLO
    return tabella.get(code, "rk-nd")


# Scala ordinale dei livelli per dimensione (serve a confrontare inerente vs residuo).
_ORDINE_LIVELLO = {
    "AIACT": {"MINIMO": 1, "LIMITATO": 2, "ALTO": 3, "VIETATO": 4},
    "NIS2": {"NA": 0, "BASSO": 1, "MEDIO": 2, "ALTO": 3},
    "GDPR": {"NA": 0, "BASSO": 1, "MEDIO": 2, "ALTO": 3},
}


def livello_ordinale(tipo, code):
    """Posizione del livello nella scala della dimensione (None se sconosciuto)."""
    return _ORDINE_LIVELLO.get(tipo, {}).get(code)


def livello_suggerito_residuo(tipo, code):
    """Suggerimento INDICATIVO del residuo: un livello sotto l'inerente (mai negativo).

    Non e' un calcolo vincolante: il residuo resta una valutazione del presidio.
    """
    scala = _ORDINE_LIVELLO.get(tipo, {})
    pos = scala.get(code)
    if pos is None:
        return ""
    target = max(min(scala.values()), pos - 1)
    for c, p in scala.items():
        if p == target:
            return c
    return code


class PeriodicitaCosto(models.TextChoices):
    """Periodicità con cui va letto un costo (per dare senso alla cifra)."""

    MENSILE = "MENSILE", "Mensile"
    ANNUALE = "ANNUALE", "Annuale"
    UNA_TANTUM = "UNA_TANTUM", "Una tantum"


class AmbitoCosto(models.TextChoices):
    """Ambito a cui si riferisce un costo."""

    UTENTE = "UTENTE", "Per utente"
    TEAM = "TEAM", "Per team"
    COMPLESSIVO = "COMPLESSIVO", "Complessivo"


class AutonomiaAI(models.TextChoices):
    """Grado di autonomia della soluzione AI (rilevante per l'AI Act)."""

    NON_AGENTICA = "NON_AGENTICA", "Non agentica"
    AGENTICA_SUPPORTO = "AGENTICA_SUPPORTO", "Agentica — a supporto dell'utente (human-in-the-loop)"
    AGENTICA_AUTONOMA = "AGENTICA_AUTONOMA", "Agentica — autonoma"


class DeploymentAI(models.TextChoices):
    """Infrastruttura prevista per i modelli (rilevante per GDPR/NIS2)."""

    API = "API", "API (cloud)"
    LOCALE = "LOCALE", "LLM locale (on-premise)"
    IBRIDO = "IBRIDO", "Ibrido (API + locale)"


class EsitoBudget(models.TextChoices):
    """Decisione dell'owner sull'importo stimato dalla Funzione AI."""

    A_BUDGET = "A_BUDGET", "A budget"
    EXTRA_BUDGET = "EXTRA_BUDGET", "Extra budget"


class Richiesta(models.Model):
    """Esigenza/opportunita' AT proposta da una funzione aziendale."""

    numero = models.PositiveIntegerField(unique=True, editable=False, db_index=True)

    # --- Campi della scheda (rettangolo slide 8-9) ---------------------------
    funzione = models.CharField("Funzione", max_length=10, choices=Funzione.choices)
    titolo = models.CharField("Titolo (case study)", max_length=140)
    tipo_soluzione = models.CharField(
        "Tipo soluzione", max_length=140, blank=True,
        help_text="Es. 'Assistente AI interno', 'Agentiche AI, sviluppo interno'.",
    )
    descrizione = models.TextField("Descrizione")

    saving_economico = models.DecimalField(
        "Beneficio economico atteso (€)", max_digits=12, decimal_places=2, null=True, blank=True,
    )
    incremento_qualitativo = models.DecimalField(
        "Incremento qualitativo (%)", max_digits=6, decimal_places=2, null=True, blank=True,
    )
    incremento_efficienza = models.DecimalField(
        "Incremento efficienza (%)", max_digits=6, decimal_places=2, null=True, blank=True,
    )

    sal = models.PositiveSmallIntegerField("SAL %", default=0)
    referente_area = models.CharField("Referente di area", max_length=120, blank=True)

    # --- Analisi della Funzione AI (compilata da AI Officer) -----------------
    analisi_fattibilita = models.TextField("Analisi di fattibilità", blank=True)
    ai_autonomia = models.CharField(
        "Tipo di AI", max_length=20, choices=AutonomiaAI.choices, blank=True,
        help_text="Grado di autonomia: non agentica, agentica a supporto, agentica autonoma.",
    )
    ai_deployment = models.CharField(
        "Infrastruttura prevista", max_length=10, choices=DeploymentAI.choices, blank=True,
        help_text="API (cloud), LLM locale (on-premise) o ibrido. Impatta GDPR/NIS2.",
    )
    effort_ore = models.PositiveIntegerField("Effort stimato (ore)", null=True, blank=True)
    data_inizio = models.DateField("Data inizio lavori", null=True, blank=True)
    data_consegna_prevista = models.DateField("Data prevista consegna", null=True, blank=True)
    costo_token_ai = models.DecimalField("Costi token AI (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    costo_token_periodicita = models.CharField(
        "Periodicità costo token", max_length=12, choices=PeriodicitaCosto.choices, blank=True,
    )
    costo_token_ambito = models.CharField(
        "Ambito costo token", max_length=12, choices=AmbitoCosto.choices, blank=True,
    )
    numero_utenti = models.PositiveIntegerField(
        "Numero utenti/team", null=True, blank=True,
        help_text="Numero di utenti (o di team) su cui scalare il costo per ottenere il totale.",
    )
    costo_token_ai_stimato = models.BooleanField(
        default=False, editable=False,
        help_text="True se l'importo token è stato proposto dall'AI (modificabile).",
    )
    altri_costi = models.DecimalField("Altri costi (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    altri_costi_note = models.CharField("Dettaglio altri costi", max_length=200, blank=True)

    # --- Budget (compilato dall'owner) --------------------------------------
    budget_massimo = models.DecimalField(
        "Budget massimo disponibile (€)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Importo massimo a budget per il progetto.",
    )
    extra_budget_massimo = models.DecimalField(
        "Extra budget richiedibile (€)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Importo massimo extra budget che l'owner è disposto a richiedere.",
    )
    esito_budget = models.CharField(
        "Esito budget", max_length=12, choices=EsitoBudget.choices, blank=True,
        help_text="Decisione dell'owner sull'importo stimato: a budget o extra budget.",
    )

    # --- Note sui ritorni (compilabili dall'owner) --------------------------
    saving_economico_note = models.CharField("Note beneficio economico", max_length=200, blank=True)
    incremento_qualitativo_note = models.CharField("Note incremento qualitativo", max_length=200, blank=True)
    incremento_efficienza_note = models.CharField("Note incremento efficienza", max_length=200, blank=True)
    # Gli incrementi possono essere stimati una sola volta dall'AI (poi sempre
    # modificabili dall'owner): questo flag traccia che la stima è già avvenuta.
    incrementi_ai_stimati = models.BooleanField(default=False, editable=False)

    # --- Stato e tracciabilita' ---------------------------------------------
    stato = models.CharField(max_length=24, choices=Stato.choices, default=Stato.BOZZA, db_index=True)
    proponente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="richieste"
    )
    creata_il = models.DateTimeField(auto_now_add=True)
    aggiornata_il = models.DateTimeField(auto_now=True)

    def applica_analisi_ai(self, dati: dict) -> list:
        """Mappa l'output di ai_client.genera_analisi_completa sui campi dell'analisi e salva.

        Tutti i valori restano modificabili: e' una precompilazione, non un vincolo.
        Ritorna l'elenco dei campi effettivamente aggiornati.
        """
        from decimal import Decimal, InvalidOperation

        def _dec(v):
            if v is None or v == "":
                return None
            try:
                return Decimal(str(v))
            except (InvalidOperation, ValueError, TypeError):
                return None

        def _intero(v):
            d = _dec(v)
            return int(d) if d is not None else None

        campi = []
        fatt = (dati.get("fattibilita") or "").strip()
        if fatt:
            self.analisi_fattibilita = fatt
            campi.append("analisi_fattibilita")
        aut = dati.get("autonomia")
        if aut in dict(AutonomiaAI.choices):
            self.ai_autonomia = aut
            campi.append("ai_autonomia")
        dep = dati.get("deployment")
        if dep in dict(DeploymentAI.choices):
            self.ai_deployment = dep
            campi.append("ai_deployment")
        eff = _intero(dati.get("effort_ore"))
        if eff is not None and eff >= 0:
            self.effort_ore = eff
            campi.append("effort_ore")
        costo = _dec(dati.get("costo_token_mensile_per_utente"))
        if costo is not None and costo > 0:
            self.costo_token_ai = costo
            self.costo_token_ai_stimato = True
            if not self.costo_token_periodicita:
                self.costo_token_periodicita = PeriodicitaCosto.MENSILE
            if not self.costo_token_ambito:
                self.costo_token_ambito = AmbitoCosto.UTENTE
            campi += ["costo_token_ai", "costo_token_ai_stimato",
                      "costo_token_periodicita", "costo_token_ambito"]
        effi = _dec(dati.get("efficienza"))
        if effi is not None:
            self.incremento_efficienza = effi
            campi.append("incremento_efficienza")
        qual = _dec(dati.get("qualita"))
        if qual is not None:
            self.incremento_qualitativo = qual
            campi.append("incremento_qualitativo")
        ben = _dec(dati.get("beneficio_euro"))
        if ben is not None:
            self.saving_economico = ben
            campi.append("saving_economico")
        nota = (dati.get("beneficio_nota") or "").strip()[:200]
        if nota:
            self.saving_economico_note = nota
            campi.append("saving_economico_note")
        if campi:
            self.save(update_fields=sorted(set(campi)))
        return campi

    class Meta:
        verbose_name = "Richiesta"
        verbose_name_plural = "Richieste"
        ordering = ["-creata_il"]

    def __str__(self) -> str:
        return f"{self.codice} — {self.titolo}"

    def save(self, *args, **kwargs):
        if self.numero is None:
            ultimo = Richiesta.objects.aggregate(m=models.Max("numero"))["m"] or 0
            self.numero = ultimo + 1
        if self.sal > 100:
            self.sal = 100
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("flusso:dettaglio", args=[self.pk])

    # --- Proprieta' di comodo per i template --------------------------------
    @property
    def codice(self) -> str:
        return f"ID {self.numero:02d}"

    @property
    def is_terminale(self) -> bool:
        return self.stato in STATI_TERMINALI

    @property
    def is_operativa(self) -> bool:
        return self.stato in STATI_OPERATIVI

    @property
    def is_bozza(self) -> bool:
        return self.stato == Stato.BOZZA

    @property
    def is_respinta(self) -> bool:
        return self.stato == Stato.RESPINTA

    @property
    def stato_label(self) -> str:
        return self.get_stato_display()

    @property
    def costo_totale_stimato(self):
        """Costo totale di progetto: alias di costo_progetto_stimato (token AI
        annualizzato e scalato per numero utenti + altri costi).

        In precedenza sommava il costo token GREZZO (importo mensile per utente)
        senza annualizzare né moltiplicare per gli utenti, producendo totali
        errati (es. € 5.035 invece di € 9.200). Ora delega all'unica logica
        corretta, così pannello di dettaglio e KPI restano coerenti.
        """
        return self.costo_progetto_stimato

    @property
    def costo_token_annuo(self):
        """Costo token ANNUALIZZATO (ricorrente), per confronti tra progetti.

        Mensile ×12, Annuale ×1. Restituisce None se la periodicità manca o è
        'una tantum' (un costo una tantum non è ricorrente: non va annualizzato).
        Mantiene l'AMBITO (per utente/team/complessivo): per gli ambiti per-unità
        è un valore unitario, non un totale d'azienda.
        """
        if self.costo_token_ai is None:
            return None
        fattore = {"MENSILE": 12, "ANNUALE": 1}.get(self.costo_token_periodicita)
        if fattore is None:
            return None
        return self.costo_token_ai * fattore

    @property
    def costo_token_annuo_nota(self):
        """Etichetta sintetica del calcolo annualizzato (None se non calcolabile)."""
        if self.costo_token_annuo is None:
            return None
        period = self.get_costo_token_periodicita_display()
        nota = f"{period} ×{12 if self.costo_token_periodicita == 'MENSILE' else 1}"
        if self.costo_token_ambito:
            nota += f" · {self.get_costo_token_ambito_display().lower()}"
        return nota

    @property
    def costo_token_annuo_totale(self):
        """Costo token annuo TOTALE. Per ambiti per-unità moltiplica per numero_utenti.

        - Ambito 'per utente'/'per team': annualizzato × numero_utenti (None se manca
          il numero, perché il totale non è determinabile senza la numerosità).
        - Ambito 'complessivo' o non indicato: coincide con l'annualizzato.
        - None se l'annualizzato non è calcolabile.
        """
        base = self.costo_token_annuo
        if base is None:
            return None
        if self.costo_token_ambito in ("UTENTE", "TEAM"):
            if not self.numero_utenti:
                return None
            return base * self.numero_utenti
        return base

    @property
    def costo_progetto_stimato(self):
        """Costo di progetto per il confronto a budget.

        Token (annualizzato se ricorrente, importo grezzo se una tantum/senza
        periodicità), scalato per numero_utenti se l'ambito è per-unità, più gli
        altri costi. None se il costo token è impostato ma non scalabile (manca il
        numero utenti per gli ambiti per-unità).
        """
        parti = []
        if self.costo_token_ai is not None:
            base = self.costo_token_annuo
            if base is None:  # una tantum o periodicità non indicata
                base = self.costo_token_ai
            if self.costo_token_ambito in ("UTENTE", "TEAM"):
                if not self.numero_utenti:
                    return None
                base = base * self.numero_utenti
            parti.append(base)
        if self.altri_costi is not None:
            parti.append(self.altri_costi)
        if not parti:
            return None
        return sum(parti)

    @property
    def ripartizione_budget(self):
        """Quanto del costo di progetto è a budget e quanto extra budget.

        None se manca il budget o il costo. Ritorna un dict con costo, a_budget,
        extra, budget, extra_richiesto e uno stato sintetico.
        """
        from decimal import Decimal as _D
        costo = self.costo_progetto_stimato
        if costo is None or self.budget_massimo is None:
            return None
        a_budget = min(costo, self.budget_massimo)
        extra = costo - self.budget_massimo
        if extra < 0:
            extra = _D(0)
        if extra == 0:
            stato = "a_budget"
        elif self.extra_budget_massimo is not None and extra <= self.extra_budget_massimo:
            stato = "extra_ok"
        elif self.extra_budget_massimo is not None:
            stato = "extra_oltre"
        else:
            stato = "fuori_budget"
        return {
            "costo": costo, "a_budget": a_budget, "extra": extra,
            "budget": self.budget_massimo, "extra_richiesto": self.extra_budget_massimo,
            "stato": stato,
        }

    @property
    def costo_a_budget(self):
        rip = self.ripartizione_budget
        return rip["a_budget"] if rip else None

    @property
    def costo_extra_budget(self):
        rip = self.ripartizione_budget
        return rip["extra"] if rip else None

    @property
    def budget_stato_label(self):
        rip = self.ripartizione_budget
        if not rip:
            return ""
        return {
            "a_budget": "Interamente a budget",
            "extra_ok": "Extra budget entro la richiesta",
            "extra_oltre": "Extra budget OLTRE la richiesta",
            "fuori_budget": "Fuori budget",
        }.get(rip["stato"], "")

    @property
    def budget_stato_css(self):
        rip = self.ripartizione_budget
        if not rip:
            return ""
        return {"a_budget": "bg-ok", "extra_ok": "bg-warn",
                "extra_oltre": "bg-bad", "fuori_budget": "bg-bad"}.get(rip["stato"], "")

    @property
    def esito_budget_css(self):
        return {"A_BUDGET": "bg-ok", "EXTRA_BUDGET": "bg-warn"}.get(self.esito_budget, "")

    @property
    def contributo_budget(self):
        """Quanto del costo di progetto va a budget e quanto extra, per i KPI.

        Priorità alla decisione esplicita dell'owner (esito_budget): l'intero costo
        finisce in un'unica voce. In assenza del flag (dati storici) ripiega sulla
        ripartizione per importi, se l'owner aveva indicato un budget. Ritorna
        (a_budget, extra) oppure None se il costo non è calcolabile.
        """
        from decimal import Decimal as _D
        costo = self.costo_progetto_stimato
        if costo is None:
            return None
        if self.esito_budget == "A_BUDGET":
            return (costo, _D(0))
        if self.esito_budget == "EXTRA_BUDGET":
            return (_D(0), costo)
        rip = self.ripartizione_budget
        if rip:
            return (rip["a_budget"], rip["extra"])
        return None

    @property
    def ha_analisi(self) -> bool:
        """True se almeno un campo dell'analisi Funzione AI e' stato compilato."""
        return any([
            self.analisi_fattibilita, self.ai_autonomia, self.ai_deployment,
            self.effort_ore, self.data_inizio, self.data_consegna_prevista,
            self.costo_token_ai is not None, self.altri_costi is not None, self.altri_costi_note,
        ])

    # --- Rischio & conformità (tre dimensioni) ------------------------------
    def assicura_classificazioni(self):
        """Crea, se mancanti, le tre righe di classificazione (AI Act, NIS2, GDPR)."""
        for tipo, _ in TipoRischio.choices:
            ClassificazioneRischio.objects.get_or_create(richiesta=self, tipo=tipo)

    def lista_rischi(self):
        """Le tre classificazioni in ordine fisso (AI Act, NIS2, GDPR)."""
        self.assicura_classificazioni()
        ordine = {"AIACT": 0, "NIS2": 1, "GDPR": 2}
        return sorted(self.classificazioni.all(), key=lambda c: ordine.get(c.tipo, 9))

    @property
    def rischi_validati_n(self) -> int:
        return sum(1 for c in self.classificazioni.all()
                   if c.stato in (StatoRischio.VALIDATO, StatoRischio.MODIFICATO))

    @property
    def rischi_tutti_validati(self) -> bool:
        stati = {c.tipo: c.stato for c in self.classificazioni.all()}
        return all(stati.get(t) in (StatoRischio.VALIDATO, StatoRischio.MODIFICATO)
                   for t, _ in TipoRischio.choices)

    @property
    def rischi_mancanti_label(self) -> str:
        """Etichette dei presidi che non hanno ancora validato (per i messaggi)."""
        stati = {c.tipo: c.stato for c in self.classificazioni.all()}
        manca = [ETICHETTA_VALIDATORE[t] for t, _ in TipoRischio.choices
                 if stati.get(t) not in (StatoRischio.VALIDATO, StatoRischio.MODIFICATO)]
        return ", ".join(manca)

    # --- Formattazione per i template ---------------------------------------
    @staticmethod
    def _fmt_pct(v):
        if v is None:
            return None
        f = float(v)
        s = f"{f:.0f}" if f == int(f) else f"{f:g}"
        return s.replace(".", ",") + "%"

    @property
    def saving_economico_fmt(self):
        if self.saving_economico is None:
            return None
        return "€ " + f"{float(self.saving_economico):,.0f}".replace(",", ".")

    @property
    def incremento_qualitativo_fmt(self):
        return self._fmt_pct(self.incremento_qualitativo)

    @property
    def incremento_efficienza_fmt(self):
        return self._fmt_pct(self.incremento_efficienza)

    @property
    def data_completamento(self):
        """Data del passaggio a COMPLETATO (dall'audit trail), se presente."""
        if self.stato != Stato.COMPLETATO:
            return None
        dt = (self.transizioni.filter(azione="completa")
              .order_by("-creata_il").values_list("creata_il", flat=True).first())
        return dt.date() if dt else None

    def avanzamento_temporale(self):
        """Avanzamento temporale automatico del progetto rispetto alla consegna prevista.

        Calcolato dalle date (inizio lavori → consegna prevista) una volta approvato
        il progetto. Restituisce None se non applicabile (non ancora approvato o date
        mancanti). Evidenzia l'eventuale ritardo e conta i giorni di progetto/ritardo.
        """
        stati_validi = {Stato.APPROVATA, Stato.ATTIVO, Stato.MONITORAGGIO, Stato.COMPLETATO}
        if (self.stato not in stati_validi
                or not self.data_inizio or not self.data_consegna_prevista):
            return None

        inizio, fine = self.data_inizio, self.data_consegna_prevista
        completato = self.stato == Stato.COMPLETATO
        rif = (self.data_completamento or date.today()) if completato else date.today()

        durata = (fine - inizio).days
        giorni_totali = max(0, durata)
        durata_eff = durata if durata > 0 else 1
        trascorsi = (rif - inizio).days
        giorni_progetto = max(0, trascorsi)
        overrun = (rif - fine).days

        if overrun > 0:
            # In ritardo: barra piena divisa in pianificato (verde) + ritardo (rosso).
            tot = trascorsi if trascorsi > 0 else 1
            perc_pianificato = max(0, min(100, round(100 * durata_eff / tot)))
            perc_ritardo = 100 - perc_pianificato
            ritardo_giorni = overrun
            in_ritardo = True
            perc_display = 100
        else:
            perc = max(0, min(100, round(100 * trascorsi / durata_eff)))
            perc_pianificato = 100 if completato else perc
            perc_ritardo = 0
            ritardo_giorni = 0
            in_ritardo = False
            perc_display = 100 if completato else perc

        return {
            "applicabile": True, "completato": completato, "in_ritardo": in_ritardo,
            "giorni_progetto": giorni_progetto, "giorni_totali": giorni_totali,
            "ritardo_giorni": ritardo_giorni, "perc_pianificato": perc_pianificato,
            "perc_ritardo": perc_ritardo, "perc_display": perc_display,
        }

    @transaction.atomic
    def applica(self, azione: str, attore, nota: str = "") -> "Transizione":
        """
        Esegue una transizione di workflow registrando l'evento di audit.

        L'autorizzazione va verificata a monte (workflow.puo_eseguire); qui si
        applica il cambio di stato in modo atomico e si scrive il log.
        """
        t = transizione(azione)
        if t is None:
            raise ValueError(f"Azione sconosciuta: {azione}")
        if self.stato not in t.da:
            raise ValueError(f"Transizione '{azione}' non valida dallo stato {self.stato}.")

        stato_da = self.stato
        self.stato = t.a
        self.save(update_fields=["stato", "aggiornata_il"])

        evento = Transizione.objects.create(
            richiesta=self,
            azione=azione,
            etichetta=t.label,
            stato_da=stato_da,
            stato_a=t.a,
            attore=attore,
            nota=nota,
        )
        audit.info(
            "richiesta=%s azione=%s %s->%s attore=%s",
            self.codice, azione, stato_da, t.a, getattr(attore, "username", "sistema"),
        )
        return evento

    @transaction.atomic
    def aggiorna_sal(self, nuovo_sal: int, attore, nota: str = "") -> "Transizione":
        """Aggiorna l'avanzamento e ne lascia traccia nel log (stato invariato)."""
        nuovo_sal = max(0, min(100, int(nuovo_sal)))
        vecchio = self.sal
        self.sal = nuovo_sal
        self.save(update_fields=["sal", "aggiornata_il"])
        testo = f"SAL {vecchio}% → {nuovo_sal}%"
        if nota:
            testo = f"{testo} · {nota}"
        evento = Transizione.objects.create(
            richiesta=self,
            azione="aggiorna_sal",
            etichetta="Aggiornamento SAL",
            stato_da=self.stato,
            stato_a=self.stato,
            attore=attore,
            nota=testo,
        )
        audit.info(
            "richiesta=%s azione=aggiorna_sal %s%%->%s%% attore=%s",
            self.codice, vecchio, nuovo_sal, getattr(attore, "username", "sistema"),
        )
        return evento

    @transaction.atomic
    def applica_stima_incrementi(self, efficienza=None, qualita=None, modello="", attore=None):
        """Applica le percentuali stimate dall'AI ai SOLI campi lasciati vuoti dall'owner.

        Imposta sempre il flag «incrementi_ai_stimati» (la stima va fatta una sola
        volta); i valori restano comunque modificabili dall'owner. Lascia traccia
        nell'audit trail se almeno un campo è stato valorizzato.
        """
        campi, valorizzati = ["incrementi_ai_stimati", "aggiornata_il"], []
        if self.incremento_efficienza is None and efficienza is not None:
            self.incremento_efficienza = efficienza
            campi.append("incremento_efficienza")
            valorizzati.append(f"efficienza {efficienza}%")
        if self.incremento_qualitativo is None and qualita is not None:
            self.incremento_qualitativo = qualita
            campi.append("incremento_qualitativo")
            valorizzati.append(f"qualità {qualita}%")
        self.incrementi_ai_stimati = True
        self.save(update_fields=campi)
        if valorizzati:
            Transizione.objects.create(
                richiesta=self, azione="incrementi_ai",
                etichetta="Stima incrementi (AI)",
                stato_da=self.stato, stato_a=self.stato, attore=attore,
                nota="Stimati dall'AI: " + ", ".join(valorizzati)
                     + (f" — {modello}" if modello else ""),
            )
            audit.info("richiesta=%s incrementi_ai=%s modello=%s", self.codice,
                       ";".join(valorizzati), modello)
        return valorizzati


class Transizione(models.Model):
    """Voce immutabile dell'audit trail di una richiesta."""

    richiesta = models.ForeignKey(Richiesta, on_delete=models.CASCADE, related_name="transizioni")
    azione = models.CharField(max_length=40)
    etichetta = models.CharField(max_length=120)
    stato_da = models.CharField(max_length=24)
    stato_a = models.CharField(max_length=24)
    attore = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="azioni"
    )
    nota = models.TextField(blank=True)
    creata_il = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Evento di workflow"
        verbose_name_plural = "Audit trail"
        ordering = ["creata_il"]

    def __str__(self) -> str:
        return f"{self.richiesta.codice} · {self.etichetta} · {self.creata_il:%d/%m/%Y %H:%M}"

    @property
    def is_sal(self) -> bool:
        return self.azione == "aggiorna_sal"

    @property
    def stato_da_label(self) -> str:
        return Stato(self.stato_da).label if self.stato_da in Stato.values else self.stato_da

    @property
    def stato_a_label(self) -> str:
        return Stato(self.stato_a).label if self.stato_a in Stato.values else self.stato_a


class ClassificazioneRischio(models.Model):
    """Una delle tre classificazioni di rischio di un progetto (AI Act, NIS2, GDPR).

    La proposta è prodotta dall'AI; la validazione/modifica spetta al presidio
    competente (Funzione Legale per l'AI Act, CISO per NIS2, DPO per il GDPR).
    """

    richiesta = models.ForeignKey(Richiesta, on_delete=models.CASCADE, related_name="classificazioni")
    tipo = models.CharField(max_length=6, choices=TipoRischio.choices, db_index=True)
    categoria = models.CharField(max_length=12, blank=True)
    stato = models.CharField(max_length=16, choices=StatoRischio.choices,
                             default=StatoRischio.DA_ANALIZZARE, db_index=True)
    motivazione = models.TextField(blank=True)
    riferimenti = models.CharField(max_length=300, blank=True)
    obblighi = models.TextField(blank=True)
    # Proposta dell'AI, tracciata a parte per il confronto con la decisione del presidio.
    ai_categoria = models.CharField(max_length=12, blank=True)
    ai_il = models.DateTimeField(null=True, blank=True)
    ai_modello = models.CharField(max_length=60, blank=True)
    # Validazione del presidio competente.
    validato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rischi_validati",
    )
    validato_il = models.DateTimeField(null=True, blank=True)
    nota_validatore = models.TextField(blank=True)
    # --- Trattamento del rischio (ISO 27005): strategia + residuo --------------
    strategia = models.CharField(
        max_length=12, choices=StrategiaTrattamento.choices,
        default=StrategiaTrattamento.ACCETTATO,
        verbose_name="Strategia di trattamento",
    )
    rischio_residuo = models.CharField(
        max_length=12, blank=True, verbose_name="Rischio residuo",
        help_text="Livello atteso dopo il trattamento. Vuoto = pari al rischio inerente.",
    )
    residuo_convalidato = models.BooleanField(
        default=False, verbose_name="Rischio residuo convalidato dal presidio",
    )
    trattamento_note = models.TextField(
        blank=True, verbose_name="Note di trattamento",
        help_text="Per il trasferimento: a chi/come. Per l'accettazione: motivazione.",
    )
    trattato_da = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rischi_trattati",
    )
    trattato_il = models.DateTimeField(null=True, blank=True)
    aggiornata_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Classificazione di rischio"
        verbose_name_plural = "Classificazioni di rischio"
        unique_together = [("richiesta", "tipo")]
        ordering = ["richiesta", "tipo"]

    def __str__(self) -> str:
        return f"{self.richiesta.codice} · {self.get_tipo_display()} · {self.categoria or '—'}"

    @property
    def categoria_label(self) -> str:
        return categoria_label(self.tipo, self.categoria)

    @property
    def categoria_css(self) -> str:
        return categoria_css(self.tipo, self.categoria)

    @property
    def ai_label(self) -> str:
        return categoria_label(self.tipo, self.ai_categoria) if self.ai_categoria else "—"

    @property
    def validato(self) -> bool:
        return self.stato in (StatoRischio.VALIDATO, StatoRischio.MODIFICATO)

    @property
    def da_validare(self) -> bool:
        return self.stato == StatoRischio.PROPOSTO_AI

    @property
    def analizzato(self) -> bool:
        return bool(self.ai_categoria) or bool(self.categoria)

    @property
    def difforme(self) -> bool:
        return bool(self.validato and self.ai_categoria and self.ai_categoria != self.categoria)

    @property
    def validatore_label(self) -> str:
        return ETICHETTA_VALIDATORE.get(self.tipo, "")

    @property
    def ruolo_validatore(self) -> str:
        return RUOLO_VALIDATORE.get(self.tipo, "")

    @property
    def obblighi_voci(self) -> list:
        """Elenco pulito delle misure/obblighi di trattamento (robusto ai dati storici)."""
        return obblighi_in_voci(self.obblighi)

    # --- Trattamento del rischio: residuo, direzione, etichetta dell'operazione ---
    @property
    def residuo_codice(self) -> str:
        """Livello residuo EFFETTIVO. Evitato => nessun residuo; altrimenti residuo o inerente."""
        if self.strategia == StrategiaTrattamento.EVITATO:
            return ""
        return self.rischio_residuo or self.categoria

    @property
    def residuo_label(self) -> str:
        if self.strategia == StrategiaTrattamento.EVITATO:
            return "Eliminato"
        return categoria_label(self.tipo, self.residuo_codice)

    @property
    def residuo_css(self) -> str:
        if self.strategia == StrategiaTrattamento.EVITATO:
            return "rk-minimo"
        return categoria_css(self.tipo, self.residuo_codice)

    @property
    def residuo_suggerito(self) -> str:
        """Suggerimento indicativo (un livello sotto l'inerente), non vincolante."""
        return livello_suggerito_residuo(self.tipo, self.categoria)

    @property
    def residuo_direzione(self) -> str:
        """'giu' (ridotto), 'pari' (invariato), 'su' (aumentato) o '' se non confrontabile."""
        if self.strategia == StrategiaTrattamento.EVITATO:
            return "giu"
        a = livello_ordinale(self.tipo, self.categoria)
        b = livello_ordinale(self.tipo, self.residuo_codice)
        if a is None or b is None:
            return ""
        return "giu" if b < a else ("su" if b > a else "pari")

    @property
    def trattato(self) -> bool:
        """True se il presidio ha applicato un trattamento diverso dalla semplice accettazione."""
        return self.strategia != StrategiaTrattamento.ACCETTATO or bool(self.trattato_il)

    @property
    def residuo_da_convalidare(self) -> bool:
        return (self.strategia in (StrategiaTrattamento.MITIGATO, StrategiaTrattamento.TRASFERITO)
                and not self.residuo_convalidato)

    @property
    def trattamento_operazione(self) -> str:
        """Etichetta che esplicita l'operazione di calcolo del rischio residuo."""
        iner = self.categoria_label
        if self.strategia == StrategiaTrattamento.ACCETTATO:
            return f"Rischio accettato al livello inerente ({iner}); nessuna mitigazione applicata."
        if self.strategia == StrategiaTrattamento.EVITATO:
            return "Rischio evitato: il caso d'uso non procede nella forma che genera il rischio (residuo eliminato)."
        n = self.azioni.count()
        if self.strategia == StrategiaTrattamento.MITIGATO:
            azioni_txt = f"{n} azione/i di mitigazione" if n else "le misure di trattamento"
            return (f"Rischio residuo {self.residuo_label} = rischio inerente {iner} "
                    f"ridotto tramite {azioni_txt}.")
        # TRASFERITO
        return (f"Rischio trasferito a terzi; livello residuo trattenuto {self.residuo_label} "
                f"(rischio inerente {iner}).")

    @transaction.atomic
    def applica_ai(self, categoria, motivazione="", riferimenti="", obblighi="", modello="", attore=None):
        """Registra la proposta dell'AI; non sovrascrive una decisione già presa dal presidio."""
        categoria = str(categoria)
        self.ai_categoria = categoria
        self.ai_il = timezone.now()
        self.ai_modello = modello or ""
        if not self.validato:
            self.categoria = categoria
            self.motivazione = motivazione or ""
            self.riferimenti = riferimenti or ""
            self.obblighi = obblighi or ""
            self.stato = StatoRischio.PROPOSTO_AI
        self.save()
        Transizione.objects.create(
            richiesta=self.richiesta, azione=f"rischio_ai_{self.tipo.lower()}",
            etichetta=f"Classificazione {self.get_tipo_display()} (AI)",
            stato_da=self.richiesta.stato, stato_a=self.richiesta.stato, attore=attore,
            nota=f"Proposta AI: {self.ai_label}" + (f" — {modello}" if modello else ""),
        )
        audit.info("richiesta=%s rischio_ai tipo=%s cat=%s modello=%s",
                   self.richiesta.codice, self.tipo, categoria, modello)
        return self

    @transaction.atomic
    def valida(self, categoria, attore, nota="", motivazione=None, obblighi=None):
        """Il presidio competente conferma (validato) o cambia categoria (modificato).

        Puo' anche rifinire il trattamento del rischio (misure/obblighi): se
        `obblighi` e' valorizzato, sostituisce le misure proposte dall'AI.
        """
        categoria = str(categoria)
        modificato = bool(self.ai_categoria) and categoria != self.ai_categoria
        if not self.ai_categoria and categoria != self.categoria:
            modificato = True
        self.categoria = categoria
        if motivazione is not None and motivazione.strip():
            self.motivazione = motivazione.strip()
        if obblighi is not None and obblighi.strip():
            self.obblighi = obblighi.strip()
        self.nota_validatore = nota or ""
        self.stato = StatoRischio.MODIFICATO if modificato else StatoRischio.VALIDATO
        self.validato_da = attore
        self.validato_il = timezone.now()
        self.save()
        verbo = "modificato" if modificato else "validato"
        testo = self.categoria_label + (f" · {nota}" if nota else "")
        Transizione.objects.create(
            richiesta=self.richiesta, azione=f"rischio_{verbo}_{self.tipo.lower()}",
            etichetta=f"Rischio {self.get_tipo_display()} {verbo} ({self.validatore_label})",
            stato_da=self.richiesta.stato, stato_a=self.richiesta.stato, attore=attore, nota=testo,
        )
        audit.info("richiesta=%s rischio_%s tipo=%s cat=%s attore=%s", self.richiesta.codice,
                   verbo, self.tipo, categoria, getattr(attore, "username", "?"))
        return self

    @transaction.atomic
    def registra_trattamento(self, attore):
        """Scrive l'audit del trattamento (le azioni sono salvate a parte dal formset)."""
        self.trattato_da = attore
        self.trattato_il = timezone.now()
        # Coerenza: ACCETTATO/EVITATO non hanno un residuo selezionabile.
        if self.strategia in (StrategiaTrattamento.ACCETTATO, StrategiaTrattamento.EVITATO):
            self.rischio_residuo = ""
            self.residuo_convalidato = False
        self.save()
        etichetta = f"Rischio {self.get_tipo_display()} — trattamento: {self.get_strategia_display()}"
        nota = self.trattamento_operazione
        if self.residuo_da_convalidare:
            nota += " · rischio residuo DA CONVALIDARE"
        Transizione.objects.create(
            richiesta=self.richiesta, azione=f"rischio_trattato_{self.tipo.lower()}",
            etichetta=etichetta, stato_da=self.richiesta.stato, stato_a=self.richiesta.stato,
            attore=attore, nota=nota,
        )
        audit.info("richiesta=%s rischio_trattato tipo=%s strategia=%s residuo=%s conv=%s attore=%s",
                   self.richiesta.codice, self.tipo, self.strategia, self.residuo_codice,
                   self.residuo_convalidato, getattr(attore, "username", "?"))
        return self


class AzioneTrattamento(models.Model):
    """Azione di mitigazione pianificata per una classificazione di rischio."""

    classificazione = models.ForeignKey(
        ClassificazioneRischio, on_delete=models.CASCADE, related_name="azioni",
    )
    descrizione = models.CharField(max_length=500, verbose_name="Azione da intraprendere")
    data_prevista = models.DateField(null=True, blank=True, verbose_name="Data prevista di applicazione")
    ordine = models.PositiveIntegerField(default=0)
    creata_il = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Azione di trattamento"
        verbose_name_plural = "Azioni di trattamento"
        ordering = ["ordine", "id"]

    def __str__(self) -> str:
        return f"{self.classificazione.tipo} · {self.descrizione[:40]}"

MODELLI_CLAUDE = [
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 — consigliato (qualità/costo)"),
    ("claude-opus-4-8", "Claude Opus 4.8 — massima qualità"),
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5 — rapido ed economico"),
    ("claude-fable-5", "Claude Fable 5"),
]

PROMPT_SISTEMA_DEFAULT = (
    "Sei l'analista del portafoglio progetti AI di ISEO Group e scrivi per il Comitato AI. "
    "Ricevi KPI GIÀ CALCOLATI del processo interno di adozione dell'AI: non inventare né "
    "ricalcolare numeri, usa solo quelli forniti. Produci una lettura esecutiva in italiano, "
    "concisa e concreta, articolata in: (1) stato di salute del portafoglio; (2) colli di "
    "bottiglia o squilibri (es. troppe richieste ferme in coda, poche approvazioni, aree "
    "scoperte, SAL fermo); (3) da 3 a 5 raccomandazioni operative e prioritarie. "
    "Niente preamboli né chiusure di cortesia. Massimo 250 parole."
)

PROMPT_AIACT_DEFAULT = """Sei un assistente di compliance che effettua una classificazione PRELIMINARE del rischio di un progetto di AI secondo il Regolamento (UE) 2024/1689 (AI Act). ISEO Group è un produttore di sistemi di chiusura e controllo accessi; valuta il progetto come sistema di AI sviluppato o adottato dall'azienda.

Assegna una sola categoria tra:
- VIETATO: pratiche vietate dall'art. 5 (es. social scoring, tecniche manipolative, riconoscimento delle emozioni sul posto di lavoro salvo motivi medici o di sicurezza, categorizzazione biometrica di dati sensibili, scraping non mirato di volti).
- ALTO: casi dell'Allegato III (biometria; infrastrutture critiche; istruzione e formazione; LAVORO e gestione dei lavoratori: selezione, valutazione, promozione o cessazione, monitoraggio; accesso a servizi essenziali e merito creditizio; forze dell'ordine; migrazione; giustizia) oppure AI come componente di sicurezza di un prodotto regolato (Allegato I).
- LIMITATO: obblighi di trasparenza dell'art. 50 (sistemi che interagiscono con persone come i chatbot, che generano contenuti, o che riconoscono emozioni/biometria).
- MINIMO: tutti gli altri casi.

In caso di dubbio fra due categorie scegli la più cautelativa e spiegane il motivo. Non inventare funzionalità non descritte.

Rispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza testo prima o dopo e senza backtick, con ESATTAMENTE queste chiavi:
- "categoria": uno tra "VIETATO", "ALTO", "LIMITATO", "MINIMO"
- "motivazione": 1-3 frasi in italiano che spiegano la classificazione riferendosi al caso d'uso
- "riferimenti": articolo o allegato pertinente (es. "Allegato III, punto 4" oppure "Art. 50")
- "obblighi": array JSON di 3-6 stringhe brevi, ciascuna una misura concreta e attuabile di trattamento del rischio, formulata in modo professionale e specifica per il caso (es. "Condurre una DPIA/FRIA prima del rilascio", "Predisporre una sorveglianza umana effettiva sull'output", "Registrare il sistema nella banca dati UE se richiesto", "Fornire un'informativa chiara agli interessati"); per la categoria MINIMO usa un'unica voce che non vi sono obblighi specifici oltre ai principi generali e al GDPR.

La classificazione è preliminare e dovrà essere validata dalla Funzione Legale."""

PROMPT_NIS2_DEFAULT = """Sei un assistente di compliance che valuta il rischio di CYBERSICUREZZA di un progetto di AI secondo la Direttiva (UE) 2022/2555 (NIS2) e la sua attuazione italiana (D.Lgs. 138/2024). ISEO Group è un produttore di sistemi di chiusura e controllo accessi. Valuta il progetto dal punto di vista della sicurezza delle reti e dei sistemi informativi e degli obblighi NIS2 (misure di gestione del rischio, gestione e notifica degli incidenti, sicurezza della catena di fornitura).

Assegna un solo livello:
- ALTO: incide in modo rilevante su sistemi o servizi essenziali/importanti, espone dati o sistemi critici, introduce superfici di attacco significative o dipendenze da fornitori critici.
- MEDIO: impatto moderato sulla sicurezza, gestibile con misure tecniche e organizzative ordinarie.
- BASSO: impatto limitato sulla sicurezza delle reti e dei sistemi informativi.
- NA: nessuna implicazione di cybersicurezza rilevante ai fini NIS2.

In caso di dubbio scegli il livello più cautelativo e spiegane il motivo. Non inventare funzionalità non descritte.

Rispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza testo prima o dopo e senza backtick, con ESATTAMENTE queste chiavi:
- "categoria": uno tra "ALTO", "MEDIO", "BASSO", "NA"
- "motivazione": 1-3 frasi in italiano riferite al caso d'uso
- "riferimenti": riferimento pertinente (es. "Art. 21 — misure di gestione del rischio" o "Art. 23 — notifica incidenti")
- "obblighi": array JSON di 3-6 stringhe brevi, ciascuna una misura concreta e attuabile di trattamento del rischio, formulata in modo professionale e specifica per il caso (es. "Definire misure di gestione del rischio ex art. 21", "Predisporre il processo di rilevamento e notifica degli incidenti", "Valutare la sicurezza della catena di fornitura coinvolta", "Applicare controllo accessi, segmentazione e cifratura"); per "NA" usa un'unica voce che non vi sono obblighi NIS2 specifici.

La classificazione è preliminare e dovrà essere validata dal CISO."""

PROMPT_GDPR_DEFAULT = """Sei un assistente di compliance che valuta il rischio per la PROTEZIONE DEI DATI PERSONALI di un progetto di AI secondo il Regolamento (UE) 2016/679 (GDPR). ISEO Group è un produttore di sistemi di chiusura e controllo accessi. Valuta il progetto in base al trattamento di dati personali che comporta.

Assegna un solo livello:
- ALTO: tratta dati personali su larga scala, categorie particolari (art. 9), dati di minori, profilazione o decisioni automatizzate con effetti significativi (art. 22), o monitoraggio sistematico; verosimilmente richiede una DPIA (art. 35).
- MEDIO: tratta dati personali comuni con rischi gestibili tramite misure ordinarie.
- BASSO: tratta dati personali in misura marginale o solo dati di contatto professionali.
- NA: nessun trattamento di dati personali (solo dati anonimi/aggregati o tecnici non riferibili a persone).

In caso di dubbio scegli il livello più cautelativo e spiegane il motivo. Non inventare funzionalità non descritte.

Rispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza testo prima o dopo e senza backtick, con ESATTAMENTE queste chiavi:
- "categoria": uno tra "ALTO", "MEDIO", "BASSO", "NA"
- "motivazione": 1-3 frasi in italiano riferite al caso d'uso
- "riferimenti": riferimento pertinente (es. "Art. 35 — DPIA", "Art. 9 — categorie particolari", "Art. 6 — base giuridica")
- "obblighi": array JSON di 3-6 stringhe brevi, ciascuna una misura concreta e attuabile di trattamento del rischio, formulata in modo professionale e specifica per il caso (es. "Individuare e documentare la base giuridica del trattamento", "Aggiornare il Registro dei trattamenti (art. 30)", "Applicare la minimizzazione e limitare l'accesso ai soli autorizzati", "Garantire misure di sicurezza adeguate, quali controllo accessi e cifratura"); per "NA" usa un'unica voce che non vi sono obblighi GDPR specifici.

La classificazione è preliminare e dovrà essere validata dal DPO."""

PROMPT_RISCHIO_DEFAULT = {
    "AIACT": PROMPT_AIACT_DEFAULT,
    "NIS2": PROMPT_NIS2_DEFAULT,
    "GDPR": PROMPT_GDPR_DEFAULT,
}


class ConfigurazioneAI(models.Model):
    """Parametri (riga unica) per la generazione AI della lettura KPI."""

    abilitato = models.BooleanField("Analisi AI abilitata", default=False)
    api_key = models.CharField(
        "API key Anthropic", max_length=200, blank=True,
        help_text="In alternativa impostare la variabile d'ambiente ANTHROPIC_API_KEY, "
                  "che ha la precedenza ed evita di salvare la chiave nel database.",
    )
    modello = models.CharField("Modello", max_length=60, choices=MODELLI_CLAUDE,
                               default="claude-sonnet-4-6")
    max_tokens = models.PositiveIntegerField("Lunghezza massima risposta (token)", default=1200)
    includi_titoli = models.BooleanField(
        "Includi i titoli dei progetti attivi nel prompt", default=False,
        help_text="Se disattivo, all'AI vengono inviati solo numeri aggregati (nessun titolo).",
    )
    prompt_sistema = models.TextField(
        "Istruzioni di sistema", blank=True,
        help_text="Lascia vuoto per usare le istruzioni predefinite.",
    )
    # --- Notifiche Teams (opzionali, opt-in) --------------------------------
    teams_abilitato = models.BooleanField("Notifiche Teams abilitate", default=False)
    teams_webhook_url = models.CharField(
        "URL webhook Teams", max_length=500, blank=True,
        help_text="URL del flusso Power Automate «Pubblica su un canale quando "
                  "viene ricevuta una richiesta webhook».",
    )
    teams_eventi = models.CharField(
        "Eventi da notificare", max_length=12,
        choices=[("importanti", "Solo cambi di stato importanti"),
                 ("tutti", "Tutti i cambi di stato")],
        default="importanti",
    )
    # --- Classificazione del rischio (AI Act, NIS2, GDPR) -------------------
    prompt_rischio_aiact = models.TextField(
        "Istruzioni classificazione AI Act", blank=True,
        help_text="Lascia vuoto per usare le istruzioni predefinite.")
    prompt_rischio_nis2 = models.TextField(
        "Istruzioni classificazione NIS2", blank=True,
        help_text="Lascia vuoto per usare le istruzioni predefinite.")
    prompt_rischio_gdpr = models.TextField(
        "Istruzioni classificazione GDPR", blank=True,
        help_text="Lascia vuoto per usare le istruzioni predefinite.")

    ultima_analisi = models.TextField("Ultima analisi generata", blank=True)
    ultima_analisi_il = models.DateTimeField(null=True, blank=True)
    ultimo_modello = models.CharField(max_length=60, blank=True)

    class Meta:
        verbose_name = "Configurazione AI"
        verbose_name_plural = "Configurazione AI"

    def __str__(self) -> str:
        return "Configurazione AI"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "ConfigurazioneAI":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def chiave_effettiva(self) -> str:
        import os
        return os.environ.get("ANTHROPIC_API_KEY") or self.api_key

    @property
    def configurata(self) -> bool:
        return bool(self.chiave_effettiva())

    @property
    def prompt_effettivo(self) -> str:
        return self.prompt_sistema.strip() or PROMPT_SISTEMA_DEFAULT

    def prompt_rischio_effettivo(self, tipo) -> str:
        custom = {"AIACT": self.prompt_rischio_aiact, "NIS2": self.prompt_rischio_nis2,
                  "GDPR": self.prompt_rischio_gdpr}.get(tipo, "")
        return (custom or "").strip() or PROMPT_RISCHIO_DEFAULT.get(tipo, "")
