import csv
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Modality, Procedure


class Command(BaseCommand):
    help = "Import procedures from CSV or XLSX"

    MODALITY_MAP = {
        "CT": "CT",
        "MR": "MR",
        "XR": "XR",
        "US": "US",
        "NM": "NM",
        "Fluoro": "FL",
        "Fluoroscopy": "FL",
        "BMD": "DXA",
        "Mammo": "MG",
    }
    MODALITY_DEFAULTS = {
        "CT": {"name": "Computed Tomography"},
        "MR": {"name": "Magnetic Resonance Imaging"},
        "XR": {"name": "X-Ray"},
        "US": {"name": "Ultrasound"},
        "NM": {"name": "Nuclear Medicine"},
        "FL": {"name": "Fluoroscopy"},
        "DXA": {"name": "Bone Densitometry"},
        "MG": {"name": "Mammography"},
    }

    def add_arguments(self, parser):
        parser.add_argument("source_file")
        parser.add_argument(
            "--create-modalities",
            action="store_true",
            help="Create missing modality records when they are referenced by the import file.",
        )

    def _normalize_header(self, value):
        return re.sub(r"\s+", " ", (value or "").strip()).lower()

    def _column_index(self, cell_ref):
        letters = "".join(ch for ch in (cell_ref or "") if ch.isalpha()).upper()
        index = 0
        for ch in letters:
            index = index * 26 + (ord(ch) - 64)
        return max(index - 1, 0)

    def _cell_value(self, cell, ns, shared_strings):
        cell_type = cell.get("t")
        value = cell.find("a:v", ns)

        if value is None:
            inline = cell.find("a:is/a:t", ns)
            return (inline.text or "") if inline is not None else ""

        raw = value.text or ""
        if cell_type == "s":
            return shared_strings[int(raw)] if raw.isdigit() else raw

        return raw

    def _read_xlsx_rows(self, path):
        with zipfile.ZipFile(path) as workbook:
            ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            shared_strings = []

            if "xl/sharedStrings.xml" in workbook.namelist():
                shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
                for item in shared_root.findall("a:si", ns):
                    text = "".join(node.text or "" for node in item.findall(".//a:t", ns))
                    shared_strings.append(text)

            worksheet_names = sorted(
                name for name in workbook.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            if not worksheet_names:
                raise CommandError("No worksheet was found in the XLSX file.")

            sheet_root = ET.fromstring(workbook.read(worksheet_names[0]))
            rows = sheet_root.findall(".//a:sheetData/a:row", ns)
            if not rows:
                return

            header_map = {}
            max_index = 0
            for cell in rows[0].findall("a:c", ns):
                index = self._column_index(cell.get("r"))
                header_map[index] = self._normalize_header(self._cell_value(cell, ns, shared_strings))
                max_index = max(max_index, index)

            for row in rows[1:]:
                indexed_values = {}
                for cell in row.findall("a:c", ns):
                    index = self._column_index(cell.get("r"))
                    indexed_values[index] = self._cell_value(cell, ns, shared_strings).strip()
                    max_index = max(max_index, index)

                if not indexed_values:
                    continue

                yield {
                    header_map.get(index, f"column_{index + 1}"): indexed_values.get(index, "").strip()
                    for index in range(max_index + 1)
                    if header_map.get(index)
                }

    def _read_csv_rows(self, path):
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as source:
            reader = csv.DictReader(source)
            for row in reader:
                yield {
                    self._normalize_header(key): (value or "").strip()
                    for key, value in row.items()
                }

    def _iter_rows(self, path):
        suffix = Path(path).suffix.lower()
        if suffix == ".xlsx":
            yield from self._read_xlsx_rows(path)
            return
        if suffix == ".csv":
            yield from self._read_csv_rows(path)
            return

        raise CommandError("Unsupported file type. Use .csv or .xlsx.")

    def handle(self, *args, **options):
        path = options["source_file"]
        create_modalities = options["create_modalities"]

        created = 0
        updated = 0
        skipped = 0
        created_modalities = 0

        if not Path(path).exists():
            raise CommandError(f"File not found: {path}")

        for row in self._iter_rows(path):
            code = row.get("code", "").strip()
            name = row.get("concept name", "").strip()
            body_region = row.get("relationship to body region", "").strip()
            modality_name = row.get("relationship to modality", "").strip()

            if not code or not name:
                skipped += 1
                continue

            modality_code = self.MODALITY_MAP.get(modality_name)
            if not modality_code:
                self.stderr.write(f"Unknown modality: {modality_name} (Code={code})")
                skipped += 1
                continue

            try:
                modality = Modality.objects.get(code=modality_code)
            except Modality.DoesNotExist:
                if not create_modalities:
                    self.stderr.write(
                        f"Modality not found in DB: {modality_code} (Procedure={code})"
                    )
                    skipped += 1
                    continue

                modality, was_created = Modality.objects.get_or_create(
                    code=modality_code,
                    defaults={
                        "name": self.MODALITY_DEFAULTS.get(modality_code, {}).get("name", modality_code),
                        "is_active": True,
                    },
                )
                if was_created:
                    created_modalities += 1
                    self.stdout.write(f"Created missing modality: {modality.code} - {modality.name}")

            _, is_created = Procedure.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "body_region": body_region,
                    "modality": modality,
                    "is_active": True,
                }
            )

            if is_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Import finished: {created} created, {updated} updated, {skipped} skipped, "
            f"{created_modalities} modalities created"
        ))
