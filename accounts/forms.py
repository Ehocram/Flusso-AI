"""Form per la creazione utente da admin (Funzione AI)."""

from django import forms

from .models import Funzione, Ruolo, Utente
from .utils import genera_password


class CreazioneUtenteForm(forms.ModelForm):
    """Crea un utente senza digitare la password: viene generata automaticamente,
    l'utente è obbligato a cambiarla al primo accesso. Il nome utente è la mail."""

    class Meta:
        model = Utente
        fields = ("username", "first_name", "last_name", "ruolo", "funzione", "dipartimento")
        labels = {"username": "Email aziendale (nome utente)"}
        help_texts = {
            "username": "Usare la mail aziendale come nome utente (es. nome.cognome@iseo.com).",
            "funzione": "Obbligatoria per gli Owner; ignorata per gli altri ruoli.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        for f in self.fields.values():
            css = f.widget.attrs.get("class", "")
            f.widget.attrs["class"] = (css + " campo").strip()

    def save(self, commit=True):
        user = super().save(commit=False)
        if not user.email and "@" in user.username:
            user.email = user.username
        pwd = genera_password()
        user.set_password(pwd)
        user.deve_cambiare_password = True
        user._password_generata = pwd  # mostrata una sola volta in admin
        if commit:
            user.save()
        return user
