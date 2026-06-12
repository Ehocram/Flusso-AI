from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.utils.safestring import mark_safe

from .forms import CreazioneUtenteForm
from .models import Utente
from .utils import genera_password

_PROCESSO_AI = ("Processo AI", {"fields": ("ruolo", "funzione", "dipartimento")})
_PERMESSI_FULL = ("Permessi e sicurezza", {
    "fields": ("is_active", "deve_cambiare_password", "is_staff", "is_superuser", "groups", "user_permissions"),
})


@admin.register(Utente)
class UtenteAdmin(UserAdmin):
    add_form = CreazioneUtenteForm
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("username", "first_name", "last_name", "ruolo", "funzione", "dipartimento")}),
    )
    list_display = ("username", "get_full_name", "ruolo", "funzione",
                    "dipartimento", "is_active", "deve_cambiare_password")
    list_filter = ("ruolo", "funzione", "is_active", "deve_cambiare_password")
    search_fields = ("username", "email", "first_name", "last_name", "dipartimento")
    ordering = ("last_name", "first_name", "username")
    actions = ("resetta_password", "forza_cambio_password", "annulla_obbligo_cambio_password")

    def get_fieldsets(self, request, obj=None):
        if obj is None:  # creazione: usa add_fieldsets/add_form
            return self.add_fieldsets
        return (
            (None, {"fields": ("username", "password")}),
            ("Dati personali", {"fields": ("first_name", "last_name", "email")}),
            _PROCESSO_AI,
            _PERMESSI_FULL,
            ("Date", {"fields": ("last_login", "date_joined")}),
        )

    def response_add(self, request, obj, post_url_continue=None):
        pwd = getattr(obj, "_password_generata", None)
        if pwd:
            self.message_user(request, mark_safe(
                f"Utente <b>{obj.username}</b> creato. Password temporanea: "
                f"<code style='font-size:14px'>{pwd}</code> — comunicala all'utente; "
                f"al primo accesso dovrà cambiarla."), level=messages.WARNING)
        return super().response_add(request, obj, post_url_continue)

    @admin.action(description="Reset password: genera una nuova password e forza il cambio")
    def resetta_password(self, request, queryset):
        righe = []
        for u in queryset:
            pwd = genera_password()
            u.set_password(pwd)
            u.deve_cambiare_password = True
            u.save()
            righe.append(f"{u.username}  →  {pwd}")
        self.message_user(request, mark_safe(
            "Password rigenerate (comunicale agli utenti; dovranno cambiarle al prossimo accesso):<br>"
            + "<br>".join(f"<code style='font-size:14px'>{r}</code>" for r in righe)),
            level=messages.WARNING)

    @admin.action(description="Forza il cambio password al prossimo accesso")
    def forza_cambio_password(self, request, queryset):
        n = queryset.update(deve_cambiare_password=True)
        self.message_user(request, f"{n} utenti dovranno cambiare la password al prossimo accesso.")

    @admin.action(description="Annulla l'obbligo di cambio password")
    def annulla_obbligo_cambio_password(self, request, queryset):
        n = queryset.update(deve_cambiare_password=False)
        self.message_user(request, f"Obbligo rimosso per {n} utenti.")
