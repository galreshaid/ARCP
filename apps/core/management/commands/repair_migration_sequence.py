from django.core.management.base import BaseCommand
from django.db import connection

from apps.core.services.db_sequence_repair import repair_postgres_sequences


class Command(BaseCommand):
    help = "Repair PostgreSQL auto-ID sequences so they match current max IDs in each table."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING(
                    "Skipping sequence repair: current database is not PostgreSQL."
                )
            )
            return

        repaired_count = repair_postgres_sequences(using=connection.alias)
        if repaired_count == 0:
            self.stdout.write(self.style.WARNING("No PostgreSQL serial/identity sequences found."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Aligned {repaired_count} PostgreSQL sequences to next available IDs."
            )
        )
