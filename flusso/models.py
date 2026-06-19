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
        "Saving economico (€)", max_digits=12, decimal_places=2, null=True, blank=True,
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
    effort_ore = models.PositiveIntegerField("Effort stimato (ore)", null=True, blank=True)
    data_inizio = models.DateField("Data inizio lavori", null=True, blank=True)
    data_consegna_prevista = models.DateField("Data prevista consegna", null=True, blank=True)
    costo_token_ai = models.DecimalField("Costi token AI (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    altri_costi = models.DecimalField("Altri costi (€)", max_digits=10, decimal_places=2, null=True, blank=True)
    altri_costi_note = models.CharField("Dettaglio altri costi", max_length=200, blank=True)

    # --- Note sui ritorni (compilabili dall'owner) --------------------------
    saving_economico_note = models.CharField("Note saving economico", max_length=200, blank=True)
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
        """Somma dei costi stimati (token AI + altri). None se non valorizzati."""
        if self.costo_token_ai is None and self.altri_costi is None:
            return None
        return (self.costo_token_ai or 0) + (self.altri_costi or 0)

    @property
    def ha_analisi(self) -> bool:
        """True se almeno un campo dell'analisi Funzione AI e' stato compilato."""
        return any([
            self.analisi_fattibilita, self.effort_ore, self.data_inizio,
            self.data_consegna_prevista, self.costo_token_ai is not None,
            self.altri_costi is not None, self.altri_costi_note,
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
    def valida(self, categoria, attore, nota="", motivazione=None):
        """Il presidio competente conferma (validato) o cambia categoria (modificato)."""
        categoria = str(categoria)
        modificato = bool(self.ai_categoria) and categoria != self.ai_categoria
        if not self.ai_categoria and categoria != self.categoria:
            modificato = True
        self.categoria = categoria
        if motivazione is not None and motivazione.strip():
            self.motivazione = motivazione.strip()
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


# =============================================================================
# Configurazione AI (singleton) per la lettura esecutiva dei KPI
# =============================================================================

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
- "obblighi": elenco sintetico degli obblighi derivati (es. DPIA/FRIA, registrazione UE, sorveglianza umana, informativa agli interessati); per la categoria MINIMO indica che non vi sono obblighi specifici oltre ai principi generali e al GDPR.

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
- "obblighi": elenco sintetico delle misure/obblighi che ne derivano; per "NA" indica che non vi sono obblighi NIS2 specifici.

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
- "obblighi": elenco sintetico degli obblighi che ne derivano (es. base giuridica, informativa, DPIA, minimizzazione); per "NA" indica che non vi sono obblighi GDPR specifici.

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
