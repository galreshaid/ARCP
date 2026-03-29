from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Repair django_migrations id sequence on PostgreSQL if it is out of sync."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING(
                    "Skipping sequence repair: current database is not PostgreSQL."
                )
            )
            return

        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('django_migrations')")
            table_regclass = cursor.fetchone()[0]
            if not table_regclass:
                self.stdout.write(
                    self.style.WARNING("Skipping sequence repair: django_migrations table not found.")
                )
                return

            cursor.execute("SELECT pg_get_serial_sequence('django_migrations', 'id')")
            sequence_name = cursor.fetchone()[0]
            if not sequence_name:
                self.stdout.write(
                    self.style.WARNING(
                        "Skipping sequence repair: django_migrations.id has no serial sequence."
                    )
                )
                return

            cursor.execute("SELECT COUNT(*), COALESCE(MAX(id), 1) FROM django_migrations")
            row_count, max_id = cursor.fetchone()

            if row_count == 0:
                cursor.execute("SELECT setval(%s, 1, false)", [sequence_name])
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Sequence {sequence_name} reset for empty django_migrations table."
                    )
                )
                return

            # Use max(id)+1 with is_called=false so next nextval() returns exactly the first free id.
            cursor.execute("SELECT setval(%s, %s, false)", [sequence_name, max_id + 1])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Sequence {sequence_name} aligned to next django_migrations id {max_id + 1}."
                )
            )
