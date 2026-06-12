"""Configurazione admin. L'audit trail e' in sola lettura."""

from django.contrib import admin

from .models import Richiesta, Transizione


class TransizioneInline(admin.TabularInline):
    model = Transizione
    extra = 0
    can_delete = False
    readonly_fields = ("azione", "etichetta", "stato_da", "stato_a", "attore", "nota", "creata_il")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Richiesta)
class RichiestaAdmin(admin.ModelAdmin):
    list_display = ("codice", "titolo", "funzione", "stato", "sal", "proponente", "aggiornata_il")
    list_filter = ("stato", "funzione")
    search_fields = ("titolo", "descrizione")
    readonly_fields = ("numero", "creata_il", "aggiornata_il")
    inlines = [TransizioneInline]


@admin.register(Transizione)
class TransizioneAdmin(admin.ModelAdmin):
    list_display = ("richiesta", "etichetta", "stato_da", "stato_a", "attore", "creata_il")
    list_filter = ("azione",)
    search_fields = ("richiesta__titolo",)
    readonly_fields = ("richiesta", "azione", "etichetta", "stato_da", "stato_a", "attore", "nota", "creata_il")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from django import forms  # noqa: E402

from .models import ConfigurazioneAI  # noqa: E402


class ConfigurazioneAIForm(forms.ModelForm):
    class Meta:
        model = ConfigurazioneAI
        fields = "__all__"
        widgets = {
            "api_key": forms.PasswordInput(render_value=True,
                                           attrs={"autocomplete": "new-password"}),
        }


@admin.register(ConfigurazioneAI)
class ConfigurazioneAIAdmin(admin.ModelAdmin):
    form = ConfigurazioneAIForm
    fieldsets = (
        ("Attivazione", {"fields": ("abilitato", "api_key", "modello")}),
        ("Parametri", {"fields": ("max_tokens", "includi_titoli", "prompt_sistema")}),
        ("Ultima analisi generata", {"fields": ("ultimo_modello", "ultima_analisi_il", "ultima_analisi")}),
    )
    readonly_fields = ("ultimo_modello", "ultima_analisi_il", "ultima_analisi")

    def has_add_permission(self, request):
        # singleton: una sola riga di configurazione
        return not ConfigurazioneAI.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
