"""Modello utente con ruoli (RBAC) e funzione aziendale di appartenenza."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class Ruolo(models.TextChoices):
    """Ruoli del processo."""

    AI_OFFICER = "AI_OFFICER", "Funzione AI"
    APP_OFFICER = "APP_OFFICER", "Funzione Applicativa"
    ITOPS_OFFICER = "ITOPS_OFFICER", "Funzione IT Operations"
    APPROVATORE = "APPROVATORE", "Approvatore"
    OWNER = "OWNER", "Owner di funzione"
    AUDITOR = "AUDITOR", "Auditor (sola lettura)"
    LEGALE = "LEGALE", "Funzione Legale"
    CISO = "CISO", "CISO (Sicurezza / NIS2)"
    DPO = "DPO", "DPO (Privacy / GDPR)"


# Le tre "funzioni tecniche" hanno permessi identici nel processo: AI, Applicativa, IT Operations.
RUOLI_FUNZIONE = (Ruolo.AI_OFFICER, Ruolo.APP_OFFICER, Ruolo.ITOPS_OFFICER)
# Tipo di progetto di competenza di ciascuna funzione (valori di flusso.models.TipoProgetto).
TIPO_PER_RUOLO = {Ruolo.AI_OFFICER: "AI", Ruolo.APP_OFFICER: "APPLICATION", Ruolo.ITOPS_OFFICER: "IT_OPERATION"}


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
        # Requisito operativo: le funzioni tecniche (AI, Applicativa, IT Operations)
        # sono amministratori pieni dell'applicazione, anche via SSO: staff + superuser.
        if self.ruolo in RUOLI_FUNZIONE:
            self.is_staff = True
            self.is_superuser = True
        super().save(*args, **kwargs)

    # Scorciatoie usate nelle view e nei template.
    @property
    def is_owner(self) -> bool:
        return self.ruolo == Ruolo.OWNER

    @property
    def is_funzione(self) -> bool:
        """Membro di una funzione tecnica (AI, Applicativa, IT Operations): stessi permessi."""
        return self.ruolo in RUOLI_FUNZIONE or self.is_superuser

    @property
    def is_ai_officer(self) -> bool:
        """Alias storico di is_funzione (mantenuto per compatibilità)."""
        return self.is_funzione

    @property
    def tipo_competenza(self):
        """Tipo di progetto di competenza (AI/APPLICATION/IT_OPERATION) o None."""
        return TIPO_PER_RUOLO.get(self.ruolo)

    @property
    def is_approvatore(self) -> bool:
        return self.ruolo == Ruolo.APPROVATORE

    @property
    def is_auditor(self) -> bool:
        return self.ruolo == Ruolo.AUDITOR

    @property
    def is_legale(self) -> bool:
        """Funzione Legale: valida o modifica il rischio AI Act attribuito dall'AI."""
        return self.ruolo == Ruolo.LEGALE

    @property
    def is_ciso(self) -> bool:
        """CISO: valida o modifica il rischio NIS2 attribuito dall'AI."""
        return self.ruolo == Ruolo.CISO

    @property
    def is_dpo(self) -> bool:
        """DPO: valida o modifica il rischio GDPR attribuito dall'AI."""
        return self.ruolo == Ruolo.DPO

    @property
    def is_validatore_rischio(self) -> bool:
        """Presidio di rischio: il CISO valida tutte le dimensioni di compliance."""
        return self.ruolo == Ruolo.CISO

    @property
    def is_gestore(self) -> bool:
        """Visibilità completa su tutte le richieste (Auditor e presìdi inclusi, in sola lettura)."""
        return self.ruolo in RUOLI_FUNZIONE + (
            Ruolo.APPROVATORE, Ruolo.AUDITOR, Ruolo.LEGALE, Ruolo.CISO, Ruolo.DPO,
        ) or self.is_superuser
