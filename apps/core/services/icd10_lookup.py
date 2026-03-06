from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings


def _normalize_code(value: str) -> str:
    return str(value or "").strip().upper()


def _compact_code(value: str) -> str:
    return _normalize_code(value).replace(".", "")


def _clean_label(value: str) -> str:
    return " ".join(str(value or "").split())


def _resolve_xml_path() -> str:
    configured = str(getattr(settings, "ICD10_XML_PATH", "") or "").strip()
    if not configured:
        return ""

    path = Path(configured)
    if not path.exists():
        return ""

    return str(path)


@lru_cache(maxsize=4)
def _load_icd10_index(xml_path: str) -> dict[str, str]:
    if not xml_path:
        return {}

    index: dict[str, str] = {}

    try:
        for _, elem in ElementTree.iterparse(xml_path, events=("end",)):
            if elem.tag != "Class":
                continue

            code = _normalize_code(elem.attrib.get("code"))
            if not code:
                elem.clear()
                continue

            description = ""
            for rubric in elem.findall("Rubric"):
                if rubric.attrib.get("kind") != "preferred":
                    continue

                label = rubric.find("Label")
                if label is None:
                    continue

                description = _clean_label("".join(label.itertext()))
                if description:
                    break

            if description:
                index[code] = description
                compact_code = _compact_code(code)
                if compact_code and compact_code not in index:
                    index[compact_code] = description

            elem.clear()
    except (ElementTree.ParseError, OSError):
        return {}

    return index


def lookup_icd10_description(code: str) -> str:
    normalized_code = _normalize_code(code)
    if not normalized_code:
        return ""

    index = _load_icd10_index(_resolve_xml_path())
    if not index:
        return ""

    if normalized_code in index:
        return index[normalized_code]

    compact_code = _compact_code(normalized_code)
    if compact_code in index:
        return index[compact_code]

    if "." not in normalized_code and len(compact_code) > 3:
        dotted_code = f"{compact_code[:3]}.{compact_code[3:]}"
        return index.get(dotted_code, "")

    return ""
