import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Modality, Procedure
from apps.protocols.models import ProtocolSequence, ProtocolTemplate
from apps.protocols.services.document_import import MODALITY_NAMES, ProtocolDocumentImporter


class Command(BaseCommand):
    help = "Import protocol templates and sequences from .docx protocol documents"

    def add_arguments(self, parser):
        parser.add_argument(
            "file_paths",
            nargs="+",
            help="One or more .docx files to import",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse documents and report changes without saving",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        file_paths = options["file_paths"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be saved"))

        created_count = 0
        updated_count = 0
        skipped_count = 0
        sequence_count = 0

        for file_path in file_paths:
            path = Path(file_path)
            if path.suffix.lower() != ".docx":
                raise CommandError(f"Unsupported file type: {path.name}")
            if not path.exists():
                raise CommandError(f"File not found: {file_path}")

            importer = ProtocolDocumentImporter(path)
            sections = importer.extract_sections()

            if not sections:
                self.stdout.write(self.style.WARNING(f"{path.name}: no protocol tables found"))
                continue

            modality = self._get_or_create_modality(importer.modality_code, dry_run)
            self.stdout.write(f"{path.name}: found {len(sections)} protocol sections")

            for section in sections:
                outcome, imported_sequences = self._import_section(
                    modality=modality,
                    section=section,
                    source_path=path,
                    dry_run=dry_run,
                )

                if outcome == "created":
                    created_count += 1
                elif outcome == "updated":
                    updated_count += 1
                else:
                    skipped_count += 1

                sequence_count += imported_sequences

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                "\nImport summary:\n"
                f"  Created: {created_count}\n"
                f"  Updated: {updated_count}\n"
                f"  Skipped: {skipped_count}\n"
                f"  Sequences: {sequence_count}"
            )
        )

    def _get_or_create_modality(self, code, dry_run):
        existing = Modality.objects.filter(code=code).first()
        if existing:
            return existing

        if dry_run:
            return Modality(code=code, name=MODALITY_NAMES[code], is_active=True)

        modality, _ = Modality.objects.get_or_create(
            code=code,
            defaults={"name": MODALITY_NAMES[code], "is_active": True},
        )
        return modality

    def _import_section(self, modality, section, source_path, dry_run):
        sequences = self._normalize_sequence_numbers(section["sequences"])

        if not sequences:
            self.stdout.write(self.style.WARNING(f"  Skipping {section['name']} (no sequence rows)"))
            return "skipped", 0

        code = self._build_protocol_code(modality.code, section["name"])
        procedure = self._find_matching_procedure(modality, section["title"])
        defaults = {
            "name": section["name"],
            "modality": modality,
            "procedure": procedure,
            "body_part": section["body_region"],
            "body_region": section["body_region"],
            "is_active": True,
            "priority": 50,
            "requires_contrast": section["requires_contrast"],
            "indications": section["indications"],
            "general_notes": section["general_notes"],
            "clinical_keywords": section["clinical_keywords"],
            "metadata": {
                "source": "docx_import",
                "source_document": source_path.name,
                "source_path": str(source_path),
                "source_title": section["title"],
                "sequence_count": len(sequences),
            },
        }

        existing = ProtocolTemplate.objects.filter(code=code).first()
        outcome = "updated" if existing else "created"

        if dry_run:
            self.stdout.write(f"  {outcome.upper()}: {code} ({len(sequences)} sequences)")
            return outcome, len(sequences)

        protocol, created = ProtocolTemplate.objects.update_or_create(
            code=code,
            defaults=defaults,
        )

        protocol.sequences.all().delete()
        ProtocolSequence.objects.bulk_create(
            [
                ProtocolSequence(
                    protocol=protocol,
                    ser=item["ser"],
                    coil=item["coil"],
                    phase_array=item["phase_array"],
                    scan_plane=item["scan_plane"],
                    pulse_sequence=item["pulse_sequence"],
                    options=item["options"],
                    comments=item["comments"],
                )
                for item in sequences
            ]
        )

        final_outcome = "created" if created else "updated"
        self.stdout.write(f"  {final_outcome.upper()}: {code} ({len(sequences)} sequences)")
        return final_outcome, len(sequences)

    def _build_protocol_code(self, modality_code, protocol_name):
        base_slug = re.sub(r"[^A-Z0-9]+", "-", protocol_name.upper()).strip("-")
        if not base_slug:
            base_slug = "IMPORTED-PROTOCOL"

        base_slug = re.sub(r"-+", "-", base_slug)
        max_base_length = 80 - len(modality_code) - 1
        base_slug = base_slug[:max_base_length].rstrip("-") or "IMPORTED-PROTOCOL"
        candidate = f"{modality_code}-{base_slug}"

        existing = ProtocolTemplate.objects.filter(code=candidate).first()
        if existing is None or existing.name == protocol_name:
            return candidate

        suffix_index = 2
        while True:
            suffix = f"-V{suffix_index}"
            trimmed = base_slug[: max_base_length - len(suffix)].rstrip("-") or "IMPORTED-PROTOCOL"
            candidate = f"{modality_code}-{trimmed}{suffix}"
            existing = ProtocolTemplate.objects.filter(code=candidate).first()
            if existing is None or existing.name == protocol_name:
                return candidate
            suffix_index += 1

    def _find_matching_procedure(self, modality, title):
        clean_title = re.sub(r"\s+", " ", title).strip()
        if not clean_title:
            return None

        return (
            Procedure.objects.filter(modality=modality, is_active=True)
            .filter(name__iexact=clean_title)
            .first()
        )

    def _normalize_sequence_numbers(self, sequences):
        normalized = []
        used_numbers = set()
        next_number = 1

        for item in sequences:
            sequence = dict(item)
            sequence_number = int(sequence.get("ser") or next_number)

            if sequence_number in used_numbers:
                sequence_number = next_number

            while sequence_number in used_numbers:
                sequence_number += 1

            sequence["ser"] = sequence_number
            normalized.append(sequence)
            used_numbers.add(sequence_number)
            next_number = max(next_number, sequence_number + 1)

        return normalized
