from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_procedurematerialbundle_procedurematerialbundleitem_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="modality",
            name="requires_contrast",
            field=models.BooleanField(default=True, verbose_name="Requires Contrast & Materials"),
        ),
    ]
