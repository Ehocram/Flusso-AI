"""Modello utente con ruoli (RBAC) e funzione aziendale di appartenenza."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class Ruolo(models.TextChoices):
    """Quattro ruoli del processo."""

    AI_OFFICER = "AI_OFFICER", "Funzione AI"
    APPROVATORE = "APPROVATORE", "Approvatore"
    OWNER = "OWNER", "Owner di funzione"
    AUDITOR = "AUDITOR", "Auditor (sola lettura)"


class Funzione(models.TextChoices):
    """Funzioni aziendali da cui possono arrivare esigenze/opportunita'."""

    RND = "RND", "R&D"
    IT = "IT", "IT"
    SALES = "SALES", "Sales"
    SUPPLY_CHAIN = "SC", "Supply Chain"
    FINANCE = "FINANCE", "Finance"
    HR = "HR", "HR"
    OPERATIONS = "OPS", "Operations"


class Utente(AbstractUser):
    """Utente applicativo. Estende l'utente Django con ruolo e funzione."""

    ruolo = models.CharField(
        max_length=20,
        choices=Ruolo.choices,
        default=Ruolo.OWNER,
        verbose_name="Ruolo nel processo",
    )
    funzione = models.CharField(
        max_length=10,
        choices=Funzione.choices,
        blank=True,
        verbose_name="Funzione di appartenenza",
        help_text="Per gli owner: la funzione di cui possono inviare richieste.",
    )
    dipartimento = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Dipartimento",
        help_text="Reparto/ufficio (testo libero), da compilare in fase di configurazione.",
    )
    deve_cambiare_password = models.BooleanField(
        default=False,
        verbose_name="Deve cambiare la password al prossimo accesso",
        help_text="Impostato dall'amministratore dopo un reset della password.",
    )

    class Meta:
        verbose_name = "Utente"
        verbose_name_plural = "Utenti"
        ordering = ["last_name", "first_name", "username"]

    def __str__(self) -> str:
        nome = self.get_full_name() or self.username
        return f"{nome} — {self.get_ruolo_display()}"

    def save(self, *args, **kwargs):
        # Requisito operativo: la Funzione AI (AI Officer) è sempre amministratore
        # pieno dell'applicazione, anche quando l'accesso avviene via SSO.
        # Il ruolo AI_OFFICER implica quindi staff + superuser.
        if self.ruolo == Ruolo.AI_OFFICER:
            self.is_staff = True
            self.is_superuser = True
        super().save(*args, **kwargs)

    # Scorciatoie usate nelle view e nei template.
    @property
    def is_owner(self) -> bool:
        return self.ruolo == Ruolo.OWNER

    @property
    def is_ai_officer(self) -> bool:
        return self.ruolo == Ruolo.AI_OFFICER or self.is_superuser

    @property
    def is_approvatore(self) -> bool:
        return self.ruolo == Ruolo.APPROVATORE

    @property
    def is_auditor(self) -> bool:
        return self.ruolo == Ruolo.AUDITOR

    @property
    def is_gestore(self) -> bool:
        """Visibilità completa su tutte le richieste (Auditor incluso, in sola lettura)."""
        return self.ruolo in (Ruolo.AI_OFFICER, Ruolo.APPROVATORE, Ruolo.AUDITOR) or self.is_superuser
