"""Validatori password conformi alla Group Policy ISEO #19.11.

Regole sulle password scelte dall'utente (oltre alla lunghezza minima di 12
imposta da MinimumLengthValidator in settings):
- complessita': maiuscole, minuscole, numeri e caratteri speciali;
- divieto del nome azienda e di parole stagionali.

La somiglianza con i dati dell'utente (nome, cognome, email) e' gia' coperta da
UserAttributeSimilarityValidator. Rotazione a 90 giorni e blocco dopo 5 tentativi
sono presidiati a livello di Active Directory (cfr. policy).
"""

import re

from django.core.exceptions import ValidationError


class ComplessitaPasswordValidator:
    """Richiede maiuscole, minuscole, numeri e caratteri speciali (GPO 19.11 §3.1/§4.1)."""

    def validate(self, password, user=None):
        mancanti = []
        if not re.search(r"[a-z]", password):
            mancanti.append("una lettera minuscola")
        if not re.search(r"[A-Z]", password):
            mancanti.append("una lettera maiuscola")
        if not re.search(r"[0-9]", password):
            mancanti.append("un numero")
        if not re.search(r"[^A-Za-z0-9]", password):
            mancanti.append("un carattere speciale")
        if mancanti:
            raise ValidationError(
                "La password deve contenere " + ", ".join(mancanti) + ".",
                code="password_poco_complessa",
            )

    def get_help_text(self):
        return ("La password deve contenere lettere maiuscole e minuscole, "
                "numeri e caratteri speciali.")


class ParoleVietatePasswordValidator:
    """Vieta il nome azienda e le parole stagionali (GPO 19.11 §3.1/§4.1)."""

    VIETATE = (
        "iseo",
        "winter", "spring", "summer", "autumn", "fall",
        "inverno", "primavera", "estate", "autunno",
    )

    def validate(self, password, user=None):
        basso = password.lower()
        for parola in self.VIETATE:
            if parola in basso:
                raise ValidationError(
                    "La password non deve contenere il nome dell'azienda o parole "
                    f"stagionali (trovato: «{parola}»).",
                    code="password_parola_vietata",
                )

    def get_help_text(self):
        return ("La password non deve contenere il nome dell'azienda né parole "
                "stagionali (es. estate, inverno).")
