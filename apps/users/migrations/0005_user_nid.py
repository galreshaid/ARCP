from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_rename_users_usern_recipie_5f60ef_idx_users_usern_recipie_69d882_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="nid",
            field=models.CharField(blank=True, db_index=True, max_length=30, verbose_name="NID"),
        ),
    ]

