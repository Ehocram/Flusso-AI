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
