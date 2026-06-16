from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flusso", "0005_valori_numerici"),
    ]

    operations = [
        migrations.AddField(
            model_name="configurazioneai",
            name="teams_abilitato",
            field=models.BooleanField(default=False, verbose_name="Notifiche Teams abilitate"),
        ),
        migrations.AddField(
            model_name="configurazioneai",
            name="teams_webhook_url",
            field=models.CharField(
                blank=True, max_length=500, verbose_name="URL webhook Teams",
                help_text="URL del flusso Power Automate «Pubblica su un canale quando "
                          "viene ricevuta una richiesta webhook».",
            ),
        ),
        migrations.AddField(
            model_name="configurazioneai",
            name="teams_eventi",
            field=models.CharField(
                choices=[("importanti", "Solo cambi di stato importanti"),
                         ("tutti", "Tutti i cambi di stato")],
                default="importanti", max_length=12, verbose_name="Eventi da notificare",
            ),
        ),
    ]
