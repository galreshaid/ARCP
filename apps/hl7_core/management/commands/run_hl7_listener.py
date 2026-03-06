from django import http
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.hl7_core.services.inbound_listener import serve_hl7_listener


class Command(BaseCommand):
    help = "Run the inbound HL7 MLLP listener on the configured dedicated port."

    def add_arguments(self, parser):
        parser.add_argument(
            "--host",
            default=settings.HL7_LISTENER_HOST,
            help="Host interface to bind the HL7 listener to.",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=settings.HL7_LISTENER_PORT,
            help="Dedicated TCP port to bind the HL7 listener to.",
        )

    def handle(self, *args, **options):
        host = options["host"]
        port = options["port"]
        self.stdout.write(self.style.SUCCESS(f"Starting HL7 listener on {host}:{port}"))

        try:
            serve_hl7_listener(host=host, port=port)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("HL7 listener stopped."))
