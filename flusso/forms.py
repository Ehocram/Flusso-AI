"""Form per compilazione/modifica della scheda (rettangolo slide 8-9)."""

from django import forms
from django.forms import inlineformset_factory

from .models import (AzioneTrattamento, CATEGORIE_RISCHIO, ClassificazioneRischio,
                     ConfigurazioneAI, Richiesta)


class AnalisiAIForm(forms.ModelForm):
    """Analisi della Funzione AI: fattibilità, effort, tempi e costi."""

    class Meta:
        model = Richiesta
        fields = [
            "analisi_fattibilita", "ai_autonomia", "ai_deployment", "effort_ore",
            "costo_token_ai", "costo_token_periodicita",
            "costo_token_ambito", "altri_costi", "altri_costi_note",
        ]
        widgets = {
            "analisi_fattibilita": forms.Textarea(attrs={"rows": 4, "placeholder": "Valutazione di fattibilità, approccio, rischi, dipendenze…"}),
            "effort_ore": forms.NumberInput(attrs={"min": 0, "placeholder": "es. 120"}),
            "costo_token_ai": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "€ (vuoto = stima AI)"}),
            "altri_costi": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "€"}),
            "altri_costi_note": forms.TextInput(attrs={"placeholder": "es. licenze, infrastruttura on-prem"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nome in ("ai_autonomia", "ai_deployment", "costo_token_periodicita", "costo_token_ambito"):
            if nome in self.fields:
                self.fields[nome].choices = (
                    [("", "— non specificato —")]
                    + [c for c in self.fields[nome].choices if c[0]]
                )
        for campo in self.fields.values():
            css = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (css + " campo").strip()


class RichiestaForm(forms.ModelForm):
    """Form di intake compilato dall'owner di funzione."""

    class Meta:
        model = Richiesta
        fields = [
            "tipo",
            "funzione",
            "titolo",
            "tipo_soluzione",
            "descrizione",
            "referente_area",
            "numero_utenti",
            "saving_economico",
            "saving_economico_note",
            "incremento_qualitativo",
            "incremento_qualitativo_note",
            "incremento_efficienza",
            "incremento_efficienza_note",
        ]
        widgets = {
            "descrizione": forms.Textarea(attrs={"rows": 3}),
            "titolo": forms.TextInput(attrs={"placeholder": "Es. Knowledge management"}),
            "tipo_soluzione": forms.TextInput(attrs={"placeholder": "Es. Assistente AI interno"}),
            "numero_utenti": forms.NumberInput(attrs={"min": 1, "placeholder": "n. utenti che useranno il tool"}),
            "saving_economico": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "€"}),
            "incremento_qualitativo": forms.NumberInput(attrs={"min": 0, "step": "0.1", "placeholder": "% (vuoto = stima AI)"}),
            "incremento_efficienza": forms.NumberInput(attrs={"min": 0, "step": "0.1", "placeholder": "% (vuoto = stima AI)"}),
            "saving_economico_note": forms.TextInput(attrs={"placeholder": "Note (facoltative)"}),
            "incremento_qualitativo_note": forms.TextInput(attrs={"placeholder": "Note (facoltative)"}),
            "incremento_efficienza_note": forms.TextInput(attrs={"placeholder": "Note (facoltative)"}),
        }

    def __init__(self, *args, funzione_owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Un owner invia richieste solo per la propria funzione: campo bloccato.
        if funzione_owner:
            self.fields["funzione"].initial = funzione_owner
            self.fields["funzione"].disabled = True
        for campo in self.fields.values():
            css = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (css + " campo").strip()


class SalForm(forms.Form):
    """Aggiornamento rapido del SAL di un progetto attivo."""

    sal = forms.IntegerField(min_value=0, max_value=100, label="SAL %")
    nota = forms.CharField(required=False, max_length=200, label="Nota (opzionale)")


class AzioneForm(forms.Form):
    """Conferma di una transizione, con nota eventualmente obbligatoria."""

    azione = forms.CharField(widget=forms.HiddenInput)
    nota = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Nota")


class ImpostazioniAIForm(forms.ModelForm):
    """Configurazione AI dall'interfaccia (form grafico)."""

    api_key = forms.CharField(
        label="API key Anthropic",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={
            "placeholder": "•••••••••• (lascia vuoto per non modificare)",
            "autocomplete": "new-password",
        }),
        help_text="Inserisci una nuova chiave per aggiornarla; lascia vuoto per mantenere quella salvata.",
    )

    class Meta:
        model = ConfigurazioneAI
        fields = ["abilitato", "modello", "max_tokens", "includi_titoli", "prompt_sistema",
                  "prompt_rischio_aiact", "prompt_rischio_nis2", "prompt_rischio_gdpr",
                  "teams_abilitato", "teams_webhook_url", "teams_eventi"]
        widgets = {
            "max_tokens": forms.NumberInput(attrs={"min": 256, "max": 4096, "step": 1}),
            "prompt_sistema": forms.Textarea(attrs={
                "rows": 5, "placeholder": "Lascia vuoto per usare le istruzioni predefinite.",
            }),
            "prompt_rischio_aiact": forms.Textarea(attrs={"rows": 6, "placeholder": "Lascia vuoto per usare le istruzioni predefinite."}),
            "prompt_rischio_nis2": forms.Textarea(attrs={"rows": 6, "placeholder": "Lascia vuoto per usare le istruzioni predefinite."}),
            "prompt_rischio_gdpr": forms.Textarea(attrs={"rows": 6, "placeholder": "Lascia vuoto per usare le istruzioni predefinite."}),
            "teams_webhook_url": forms.TextInput(attrs={
                "placeholder": "https://… (URL del flusso Power Automate)", "autocomplete": "off",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # classe .campo solo ai controlli testuali/selezione (no checkbox)
        for nome, campo in self.fields.items():
            if isinstance(campo.widget, forms.CheckboxInput):
                continue
            css = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (css + " campo").strip()

    def save(self, commit=True):
        obj = super().save(commit=False)
        nuova = (self.cleaned_data.get("api_key") or "").strip()
        if nuova:
            obj.api_key = nuova  # altrimenti resta quella già salvata sull'istanza
        if commit:
            obj.save()
        return obj


class ValidazioneRischioForm(forms.Form):
    """Il presidio competente conferma o modifica una classificazione di rischio.

    Le scelte di categoria dipendono dalla dimensione (tipo) e vengono impostate
    al momento dell'istanza.
    """

    categoria = forms.ChoiceField(choices=[], label="Categoria")
    motivazione = forms.CharField(
        required=False, label="Motivazione (opzionale)",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Lascia vuoto per mantenere la motivazione dell'AI."}),
    )
    obblighi = forms.CharField(
        required=False, label="Obblighi e misure di trattamento",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Una misura per riga. Vuoto = mantiene le misure proposte dall'AI."}),
        help_text="Una voce per riga: misure e adempimenti con cui il presidio tratta il rischio.",
    )
    nota = forms.CharField(
        required=False, max_length=300, label="Nota del presidio",
        widget=forms.TextInput(attrs={"placeholder": "Es. confermato dopo verifica interna."}),
    )

    def __init__(self, *args, tipo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].choices = CATEGORIE_RISCHIO.get(tipo, [])
        for campo in self.fields.values():
            css = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (css + " campo").strip()


class TrattamentoRischioForm(forms.ModelForm):
    """Trattamento del rischio da parte del presidio competente (ISO 27005).

    Strategia (accetta/mitiga/trasferisci/evita) + livello residuo + convalida.
    Le azioni di mitigazione (con data) sono gestite dal formset collegato.
    """

    rischio_residuo = forms.ChoiceField(choices=[], required=False, label="Rischio residuo")

    class Meta:
        model = ClassificazioneRischio
        fields = ["strategia", "rischio_residuo", "residuo_convalidato", "trattamento_note"]
        widgets = {
            "trattamento_note": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Trasferimento: a chi e come (assicurazione, contratto, fornitore). "
                               "Accettazione: motivazione. Lascia vuoto se non serve.",
            }),
        }
        labels = {
            "strategia": "Strategia di trattamento",
            "residuo_convalidato": "Convalida il rischio residuo",
            "trattamento_note": "Note di trattamento",
        }

    def __init__(self, *args, tipo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rischio_residuo"].choices = (
            [("", "— pari al rischio inerente —")] + list(CATEGORIE_RISCHIO.get(tipo, []))
        )
        for nome, campo in self.fields.items():
            if isinstance(campo.widget, forms.CheckboxInput):
                continue
            css = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (css + " campo").strip()


AzioneTrattamentoFormSet = inlineformset_factory(
    ClassificazioneRischio,
    AzioneTrattamento,
    fields=["descrizione", "data_prevista"],
    extra=1,
    can_delete=True,
    widgets={
        "descrizione": forms.TextInput(attrs={
            "class": "campo",
            "placeholder": "Es. Implementare la cifratura at-rest sui repository indicizzati",
        }),
        "data_prevista": forms.DateInput(attrs={"type": "date", "class": "campo"}, format="%Y-%m-%d"),
    },
)


class PianificazioneForm(forms.ModelForm):
    """Date di pianificazione del progetto, modificabili a mano nella schedulazione."""

    class Meta:
        model = Richiesta
        fields = ["data_inizio", "data_consegna_prevista"]
        widgets = {
            "data_inizio": forms.DateInput(attrs={"type": "date", "class": "campo"}, format="%Y-%m-%d"),
            "data_consegna_prevista": forms.DateInput(attrs={"type": "date", "class": "campo"}, format="%Y-%m-%d"),
        }

    def clean(self):
        dati = super().clean()
        inizio = dati.get("data_inizio")
        fine = dati.get("data_consegna_prevista")
        if inizio and fine and fine < inizio:
            self.add_error("data_consegna_prevista", "La consegna non può precedere l'inizio.")
        return dati


class BeneficioForm(forms.ModelForm):
    """Beneficio economico e incrementi attesi.

    Modificabili dall'owner e dalla Funzione AI in tutti gli stati non bloccati
    (fino all'ingresso in approvazione). Restano la business case del richiedente.
    """

    class Meta:
        model = Richiesta
        fields = [
            "saving_economico", "saving_economico_note",
            "incremento_qualitativo", "incremento_qualitativo_note",
            "incremento_efficienza", "incremento_efficienza_note",
        ]
        widgets = {
            "saving_economico": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "€/anno"}),
            "saving_economico_note": forms.TextInput(attrs={"placeholder": "Come è stato stimato il beneficio"}),
            "incremento_qualitativo": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "1", "placeholder": "%"}),
            "incremento_qualitativo_note": forms.TextInput(attrs={"placeholder": "Nota"}),
            "incremento_efficienza": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "1", "placeholder": "%"}),
            "incremento_efficienza_note": forms.TextInput(attrs={"placeholder": "Nota"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            css = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (css + " campo").strip()
