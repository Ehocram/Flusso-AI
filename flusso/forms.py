"""Form per compilazione/modifica della scheda (rettangolo slide 8-9)."""

from django import forms

from .models import ConfigurazioneAI, Richiesta


class AnalisiAIForm(forms.ModelForm):
    """Analisi della Funzione AI: fattibilità, effort, tempi e costi."""

    class Meta:
        model = Richiesta
        fields = [
            "analisi_fattibilita", "effort_ore", "data_inizio",
            "data_consegna_prevista", "costo_token_ai", "altri_costi", "altri_costi_note",
        ]
        widgets = {
            "analisi_fattibilita": forms.Textarea(attrs={"rows": 4, "placeholder": "Valutazione di fattibilità, approccio, rischi, dipendenze…"}),
            "data_inizio": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "data_consegna_prevista": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "effort_ore": forms.NumberInput(attrs={"min": 0, "placeholder": "es. 120"}),
            "costo_token_ai": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "€"}),
            "altri_costi": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "€"}),
            "altri_costi_note": forms.TextInput(attrs={"placeholder": "es. licenze, infrastruttura on-prem"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            css = campo.widget.attrs.get("class", "")
            campo.widget.attrs["class"] = (css + " campo").strip()


class RichiestaForm(forms.ModelForm):
    """Form di intake compilato dall'owner di funzione."""

    class Meta:
        model = Richiesta
        fields = [
            "funzione",
            "titolo",
            "tipo_soluzione",
            "descrizione",
            "referente_area",
            "saving_economico",
            "incremento_qualitativo",
            "incremento_efficienza",
        ]
        widgets = {
            "descrizione": forms.Textarea(attrs={"rows": 3}),
            "titolo": forms.TextInput(attrs={"placeholder": "Es. Knowledge management"}),
            "tipo_soluzione": forms.TextInput(attrs={"placeholder": "Es. Assistente AI interno"}),
            "saving_economico": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "€"}),
            "incremento_qualitativo": forms.NumberInput(attrs={"min": 0, "step": "0.1", "placeholder": "%"}),
            "incremento_efficienza": forms.NumberInput(attrs={"min": 0, "step": "0.1", "placeholder": "%"}),
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
                  "teams_abilitato", "teams_webhook_url", "teams_eventi"]
        widgets = {
            "max_tokens": forms.NumberInput(attrs={"min": 256, "max": 4096, "step": 1}),
            "prompt_sistema": forms.Textarea(attrs={
                "rows": 5, "placeholder": "Lascia vuoto per usare le istruzioni predefinite.",
            }),
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
