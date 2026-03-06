from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_user_nid"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="professional_id",
            field=models.CharField(blank=True, max_length=100, verbose_name="Employee ID"),
        ),
    ]

