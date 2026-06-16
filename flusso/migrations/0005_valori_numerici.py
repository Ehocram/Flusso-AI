from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flusso", "0004_configurazioneai"),
    ]

    operations = [
        # Rimozione dei vecchi campi testuali (costo + saving liberi).
        migrations.RemoveField(model_name="richiesta", name="costo"),
        migrations.RemoveField(model_name="richiesta", name="saving_economico"),
        migrations.RemoveField(model_name="richiesta", name="saving_qualitativo"),
        migrations.RemoveField(model_name="richiesta", name="saving_efficienza"),
        # Nuovi campi numerici: saving economico in €, incrementi in %.
        migrations.AddField(
            model_name="richiesta",
            name="saving_economico",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True,
                verbose_name="Saving economico (€)",
            ),
        ),
        migrations.AddField(
            model_name="richiesta",
            name="incremento_qualitativo",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=6, null=True,
                verbose_name="Incremento qualitativo (%)",
            ),
        ),
        migrations.AddField(
            model_name="richiesta",
            name="incremento_efficienza",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=6, null=True,
                verbose_name="Incremento efficienza (%)",
            ),
        ),
    ]
