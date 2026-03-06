import re
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET


WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
MODALITY_NAMES = {
    "CT": "Computed Tomography",
    "MR": "Magnetic Resonance Imaging",
}
IGNORED_TITLE_PREFIXES = (
    "indications:",
    "objectives",
    "scanner preference",
    "computerized tomography",
    "power injection",
    "patient in ",
    "the patient",
    "positioning",
    "same as",
    "define ",
    "assess ",
    "coils",
    "contrast:",
    "contrast ",
    "phase is ",
    "thumb is ",
    "the shoulder is ",
    "the feet are ",
    "no ",
    "add ",
    "scan table:",
    "interventional:",
    "take ap ",
    "kv ",
    "gas blower device",
    "ecg monitor",
    "ax -",
    "ax ",
)
IGNORED_TITLE_VALUES = {
    "appendix",
    "appendix i",
    "sequences",
    "sequence",
    "neuro",
    "upper limbs",
    "lower limbs",
}
BODY_REGION_PATTERNS = (
    ("whole body", "BODY"),
    ("multi system", "MULTI AREA"),
    ("multisystem", "MULTI AREA"),
    ("head and neck", "HEAD"),
    ("mediastinum", "CHEST"),
    ("pulmonary", "CHEST"),
    ("cardiac", "CHEST"),
    ("breast", "BREAST"),
    ("brain", "HEAD"),
    ("head", "HEAD"),
    ("neck", "NECK"),
    ("chest", "CHEST"),
    ("thorax", "CHEST"),
    ("abdominal", "ABDOMEN"),
    ("abdomen", "ABDOMEN"),
    ("pelvic", "PELVIS"),
    ("pelvis", "PELVIS"),
    ("spine", "SPINE"),
    ("shoulder", "UPPER EXTREMITY"),
    ("arm", "UPPER EXTREMITY"),
    ("elbow", "UPPER EXTREMITY"),
    ("wrist", "UPPER EXTREMITY"),
    ("hand", "UPPER EXTREMITY"),
    ("upper limb", "UPPER EXTREMITY"),
    ("hip", "LOWER EXTREMITY"),
    ("leg", "LOWER EXTREMITY"),
    ("knee", "LOWER EXTREMITY"),
    ("ankle", "LOWER EXTREMITY"),
    ("foot", "LOWER EXTREMITY"),
    ("lower limb", "LOWER EXTREMITY"),
    ("body", "BODY"),
)
STOP_WORDS = {
    "and",
    "the",
    "for",
    "with",
    "without",
    "from",
    "that",
    "this",
    "into",
    "through",
    "case",
    "cases",
    "protocol",
    "protocols",
    "routine",
    "updated",
    "appendix",
    "mri",
    "mr",
    "ct",
    "cpg",
    "only",
}
TITLE_KEYWORDS = {
    "abdomen",
    "abdominal",
    "accreta",
    "adrenals",
    "adnexal",
    "aneurysm",
    "ankle",
    "appendicitis",
    "artery",
    "arthrogram",
    "aorta",
    "avm",
    "avf",
    "bladder",
    "biceps",
    "brain",
    "breast",
    "cancer",
    "carotid",
    "cavity",
    "cervical",
    "chest",
    "choesteatoma",
    "circle",
    "colonography",
    "cord",
    "crmo",
    "csf",
    "cta",
    "ct",
    "demyelinating",
    "discs",
    "dissection",
    "drop",
    "elbow",
    "enterography",
    "epilepsy",
    "era",
    "fibroid",
    "fingers",
    "fistula",
    "foot",
    "gauchers",
    "glioma",
    "hamstring",
    "head",
    "hip",
    "hrct",
    "iac",
    "infection",
    "infrahyoid",
    "interventional",
    "ischemia",
    "joint",
    "knee",
    "labrum",
    "limbs",
    "liver",
    "lower",
    "lumbar",
    "lung",
    "lymph",
    "lymphangiogram",
    "mandibular",
    "mass",
    "malformation",
    "mediastinum",
    "mets",
    "morton",
    "mra",
    "mrcp",
    "mr",
    "mri",
    "ms",
    "multisystem",
    "myopathy",
    "myositis",
    "nasopharynx",
    "neck",
    "neuroma",
    "neuromuscular",
    "oncology",
    "orbits",
    "oropharynx",
    "pancreas",
    "paraganglioma",
    "paranasal",
    "parotids",
    "pelvis",
    "penile",
    "perianal",
    "pituitary",
    "placenta",
    "pleural",
    "post",
    "prostate",
    "protocol",
    "pulmonary",
    "pubalgia",
    "rectal",
    "renal",
    "rheumatoid",
    "rhinorrhea",
    "routine",
    "sacroiliac",
    "schwannoma",
    "scoliosis",
    "shoulder",
    "sinuses",
    "skull",
    "spine",
    "stroke",
    "suprasellar",
    "temporal",
    "testes",
    "testicular",
    "thoracic",
    "thumb",
    "tmj",
    "trauma",
    "trigeminal",
    "tumor",
    "upper",
    "uterine",
    "vaginal",
    "venogram",
    "venous",
    "vascular",
    "wall",
    "whole",
    "willis",
}


def normalize_text(value):
    text = (value or "").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(value):
    text = normalize_text(value).rstrip(":").strip()
    return text or "Imported Protocol"


def infer_modality_code(file_path):
    name = normalize_text(Path(file_path).stem).upper()
    if "MRI" in name or re.search(r"\bMR\b", name):
        return "MR"
    if re.search(r"\bCT\b", name):
        return "CT"
    raise ValueError(f"Unable to infer modality from file name: {Path(file_path).name}")


def infer_body_region(*values):
    haystack = normalize_text(" ".join(values)).lower()
    if not haystack:
        return "NONSPECIFIC"

    haystack = re.sub(r"[_/]+", " ", haystack)

    for phrase, region in BODY_REGION_PATTERNS:
        if phrase in haystack:
            return region

    return "NONSPECIFIC"


def extract_keywords(*values):
    text = normalize_text(" ".join(values)).lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    keywords = []
    seen = set()

    for token in tokens:
        if len(token) < 3 or token.isdigit() or token in STOP_WORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= 12:
            break

    return keywords


class ProtocolDocumentImporter:
    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self.modality_code = infer_modality_code(file_path)

    def extract_sections(self):
        sections = []
        paragraph_buffer = []
        last_title = normalize_title(self.file_path.stem)
        title_counts = {}

        for block_type, payload in self._iter_document_blocks():
            if block_type == "paragraph":
                if payload:
                    paragraph_buffer.append(payload)
                continue

            title = self._select_title(paragraph_buffer, last_title)
            title_counts[title.lower()] = title_counts.get(title.lower(), 0) + 1
            occurrence = title_counts[title.lower()]
            display_name = title if occurrence == 1 else f"{title} - Variant {occurrence}"
            notes = self._collect_notes(paragraph_buffer, title)
            indications = self._extract_prefixed_lines(notes, "INDICATIONS:")
            sequences = self._parse_sequences(payload)

            sections.append(
                {
                    "name": display_name,
                    "title": title,
                    "body_region": infer_body_region(display_name, indications, self.file_path.stem),
                    "requires_contrast": self._requires_contrast(display_name, notes),
                    "indications": indications,
                    "general_notes": "\n".join(notes).strip(),
                    "clinical_keywords": extract_keywords(display_name, " ".join(notes)),
                    "sequences": sequences,
                }
            )

            last_title = title
            paragraph_buffer = []

        return sections

    def _iter_document_blocks(self):
        with ZipFile(self.file_path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))

        body = root.find("w:body", WORD_NS)
        if body is None:
            return

        for child in body:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                yield "paragraph", self._paragraph_text(child)
            elif tag == "tbl":
                yield "table", self._table_rows(child)

    def _paragraph_text(self, paragraph):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS))
        return normalize_text(text)

    def _table_rows(self, table):
        rows = []
        for row in table.findall("./w:tr", WORD_NS):
            values = []
            for cell in row.findall("./w:tc", WORD_NS):
                cell_text = []
                for paragraph in cell.findall("./w:p", WORD_NS):
                    text = self._paragraph_text(paragraph)
                    if text:
                        cell_text.append(text)
                values.append(normalize_text(" ".join(cell_text)))
            rows.append(values)
        return rows

    def _select_title(self, paragraphs, fallback):
        clean_lines = [normalize_text(line) for line in paragraphs if normalize_text(line)]
        scored_candidates = []
        for index, line in enumerate(clean_lines):
            score = self._title_score(line)
            if score > 0:
                scored_candidates.append((score, index, line))

        if scored_candidates:
            best_score = max(item[0] for item in scored_candidates)
            for score, _, line in reversed(scored_candidates):
                if score == best_score:
                    return normalize_title(line)

        return normalize_title(fallback)

    def _is_title_candidate(self, text):
        return self._title_score(text) > 0

    def _title_score(self, text):
        line = normalize_text(text)
        lower_line = line.lower()
        if not line or len(line) > 160:
            return 0
        if self._is_ignored_title(line):
            return 0
        if re.match(r"^\d", line):
            return 0
        if lower_line.startswith("the "):
            return 0
        if lower_line.startswith("-"):
            return 0

        score = 0
        if line.endswith(".") and "(" not in line and ":" not in line:
            score -= 3
        if line.endswith(":"):
            score += 2
        if "(" in line and ")" in line:
            score += 2

        tokens = re.findall(r"[a-z0-9]+", lower_line)
        if len(tokens) > 8:
            score -= 1
        if len(tokens) > 12:
            score -= 1
        if any(token in TITLE_KEYWORDS for token in tokens):
            score += 3

        alpha_chars = [char for char in line if char.isalpha()]
        if alpha_chars:
            uppercase_ratio = sum(1 for char in alpha_chars if char.isupper()) / len(alpha_chars)
            if uppercase_ratio >= 0.7:
                score += 1

        if len(tokens) <= 6 and not line.endswith("."):
            score += 1

        return max(score, 0)

    def _is_ignored_title(self, text):
        line = normalize_text(text).lower()
        if line in IGNORED_TITLE_VALUES:
            return True
        return any(line.startswith(prefix) for prefix in IGNORED_TITLE_PREFIXES)

    def _collect_notes(self, paragraphs, selected_title):
        notes = []
        normalized_title = normalize_title(selected_title)

        for line in paragraphs:
            text = normalize_text(line)
            if not text:
                continue
            if normalize_title(text) == normalized_title:
                continue
            if text.lower() in IGNORED_TITLE_VALUES:
                continue
            notes.append(text)

        return notes

    def _extract_prefixed_lines(self, lines, prefix):
        prefix_lower = prefix.lower()
        values = []
        for line in lines:
            if line.lower().startswith(prefix_lower):
                values.append(line[len(prefix):].strip())
        return "\n".join(value for value in values if value).strip()

    def _requires_contrast(self, title, notes):
        text = normalize_text(" ".join([title] + list(notes))).lower()
        return any(marker in text for marker in ("contrast", "angi", "cta", "gadolinium", "enhanced"))

    def _parse_sequences(self, rows):
        if not rows:
            return []

        header_rows = []
        data_rows = []
        found_data = False

        for row in rows:
            if not found_data and not self._is_data_row(row):
                header_rows.append(row)
                continue

            found_data = True
            data_rows.append(row)

        if not header_rows:
            header_rows = [rows[0]]
            data_rows = rows[1:]

        headers = self._combine_headers(header_rows)
        mapping = self._map_headers(headers)

        sequences = []
        next_ser = 1
        for row in data_rows:
            sequence = self._build_sequence(row, mapping, next_ser)
            if not sequence:
                continue
            sequences.append(sequence)
            next_ser = max(next_ser + 1, sequence["ser"] + 1)

        return sequences

    def _is_data_row(self, row):
        if not row:
            return False

        first_value = normalize_text(row[0])
        if not first_value:
            return False

        return bool(re.match(r"^\d+\b", first_value))

    def _combine_headers(self, header_rows):
        width = max((len(row) for row in header_rows), default=0)
        headers = []

        for index in range(width):
            parts = []
            for row in header_rows:
                if index < len(row) and row[index]:
                    parts.append(normalize_text(row[index]))
            headers.append(normalize_text(" ".join(parts)))

        return headers

    def _map_headers(self, headers):
        mapping = {}

        for index, header in enumerate(headers):
            token = re.sub(r"[^a-z0-9]+", "", header.lower())
            if not token:
                continue
            if token.startswith("ser"):
                mapping["ser"] = index
            elif "scanplane" in token or token == "scan":
                mapping["scan_plane"] = index
            elif "pulseseq" in token or token == "pulse":
                mapping["pulse_sequence"] = index
            elif "comment" in token:
                mapping["comments"] = index
            elif "option" in token or token == "note":
                mapping["options"] = index
            elif "coil" in token or "pharray" in token:
                mapping["phase_array"] = index

        return mapping

    def _build_sequence(self, row, mapping, default_ser):
        values = [normalize_text(value) for value in row]
        if not any(values):
            return None

        ser = self._parse_ser(values, mapping.get("ser"), default_ser)
        scan_plane = self._cell(values, mapping.get("scan_plane")) or "GENERAL"
        pulse_sequence = self._cell(values, mapping.get("pulse_sequence"))
        options = self._cell(values, mapping.get("options"))
        comments = self._cell(values, mapping.get("comments"))

        if not pulse_sequence:
            pulse_sequence = "CT Acquisition" if self.modality_code == "CT" else "Standard Sequence"

        if not any([scan_plane, pulse_sequence, options, comments]):
            return None

        return {
            "ser": ser,
            "coil": "",
            "phase_array": self._cell(values, mapping.get("phase_array")),
            "scan_plane": scan_plane,
            "pulse_sequence": pulse_sequence,
            "options": options,
            "comments": comments,
        }

    def _parse_ser(self, values, index, default_ser):
        if index is None:
            return default_ser

        raw_value = self._cell(values, index)
        if not raw_value:
            return default_ser

        match = re.search(r"\d+", raw_value)
        if not match:
            return default_ser

        return int(match.group())

    def _cell(self, row, index):
        if index is None or index >= len(row):
            return ""
        return normalize_text(row[index])
