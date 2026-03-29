from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = (
        "Prepare deployment in one step: repair migration sequence, apply migrations, "
        "and collect static files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-migrate",
            action="store_true",
            help="Skip running Django migrations.",
        )
        parser.add_argument(
            "--skip-collectstatic",
            action="store_true",
            help="Skip running collectstatic.",
        )

    def handle(self, *args, **options):
        verbosity = int(options.get("verbosity", 1) or 1)

        self.stdout.write("Step 1/3: Repair migration sequence (PostgreSQL only).")
        call_command("repair_migration_sequence", verbosity=verbosity)

        if not options.get("skip_migrate"):
            self.stdout.write("Step 2/3: Apply migrations.")
            call_command("migrate", verbosity=verbosity, interactive=False)
        else:
            self.stdout.write(self.style.WARNING("Step 2/3 skipped: migrate."))

        if not options.get("skip_collectstatic"):
            self.stdout.write("Step 3/3: Collect static files.")
            call_command("collectstatic", verbosity=verbosity, interactive=False)
        else:
            self.stdout.write(self.style.WARNING("Step 3/3 skipped: collectstatic."))

        self.stdout.write(self.style.SUCCESS("Deployment preparation completed."))
