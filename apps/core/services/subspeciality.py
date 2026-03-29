from __future__ import annotations

import re
from datetime import date, datetime, timezone as dt_timezone


SUBSPECIALITY_POOL = (
    "Neuro",
    "Chest and Cardiac",
    "Body",
    "MSK",
    "NM",
    "Breast",
    "Pedia",
)
SUBSPECIALITY_CHANGE_LOG_KEY = "subspeciality_change_log"

PEDIA_MAX_AGE_YEARS = 14

_SUBSPECIALITY_ALIASES = {
    "neuro": "Neuro",
    "chest and cardiac": "Chest and Cardiac",
    "chest & cardiac": "Chest and Cardiac",
    "body": "Body",
    "msk": "MSK",
    "musculoskeletal": "MSK",
    "nm": "NM",
    "nuclear medicine": "NM",
    "breast": "Breast",
    "pedia": "Pedia",
    "pediatric": "Pedia",
    "paediatric": "Pedia",
}

_REGION_ALIASES = {
    "head": "head",
    "neck": "neck",
    "spine": "spine",
    "chest": "chest",
    "thorax": "chest",
    "abdomen": "abdomen",
    "abdominal": "abdomen",
    "pelvis": "pelvis",
    "pelvic": "pelvis",
    "upper extremity": "upper extremity",
    "upper limb": "upper extremity",
    "lower extremity": "lower extremity",
    "lower limb": "lower extremity",
    "breast": "breast",
    "body": "body",
    "multi area": "multi area",
    "multiple area": "multi area",
    "nonspecific": "nonspecific",
    "non specific": "nonspecific",
}

_NEURO_KEYWORDS = {
    "brain",
    "head",
    "skull",
    "neck",
    "spine",
    "cervical",
    "thoracic",
    "lumbar",
    "sacral",
    "sacrum",
    "pituitary",
    "sinus",
    "orbit",
    "iam",
    "tmj",
}

_CHEST_CARDIAC_KEYWORDS = {
    "chest",
    "thorax",
    "lung",
    "pulmonary",
    "cardiac",
    "cardio",
    "coronary",
    "heart",
    "aorta",
    "mediastinum",
    "rib",
    "ribs",
    "sternum",
}

_MSK_KEYWORDS = {
    "musculoskeletal",
    "msk",
    "joint",
    "arthrogram",
    "shoulder",
    "arm",
    "elbow",
    "forearm",
    "wrist",
    "hand",
    "finger",
    "thumb",
    "hip",
    "thigh",
    "knee",
    "leg",
    "ankle",
    "foot",
    "toe",
    "calcaneus",
    "patella",
    "femur",
    "humerus",
    "radius",
    "ulna",
    "scaphoid",
}

_BREAST_KEYWORDS = {
    "breast",
    "mammogram",
    "mammary",
    "mammo",
    "tomosynthesis",
}


def normalize_subspeciality(value) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""

    alias = _SUBSPECIALITY_ALIASES.get(normalized.casefold())
    if alias:
        return alias

    return normalized if normalized in SUBSPECIALITY_POOL else ""


def patient_age_years(patient_dob, *, reference_datetime=None):
    if not patient_dob:
        return None

    if isinstance(reference_datetime, datetime):
        reference_date = reference_datetime.date()
    elif isinstance(reference_datetime, date):
        reference_date = reference_datetime
    else:
        reference_date = date.today()

    if not isinstance(patient_dob, date):
        return None

    years = reference_date.year - patient_dob.year
    if (reference_date.month, reference_date.day) < (patient_dob.month, patient_dob.day):
        years -= 1

    return years if years >= 0 else None


def infer_subspeciality(
    *,
    modality_code: str = "",
    body_region: str = "",
    procedure_name: str = "",
    patient_age: int | None = None,
) -> str:
    modality = str(modality_code or "").strip().upper()
    normalized_region = _normalize_region(body_region)
    tokens = _extract_tokens(body_region, procedure_name)

    if modality == "NM":
        inferred = "NM"
    elif normalized_region == "breast" or _has_keyword(tokens, _BREAST_KEYWORDS):
        inferred = "Breast"
    elif normalized_region in {"head", "neck", "spine"} or _has_keyword(tokens, _NEURO_KEYWORDS):
        inferred = "Neuro"
    elif normalized_region == "chest" or _has_keyword(tokens, _CHEST_CARDIAC_KEYWORDS):
        inferred = "Chest and Cardiac"
    elif normalized_region in {"upper extremity", "lower extremity"} or _has_keyword(tokens, _MSK_KEYWORDS):
        inferred = "MSK"
    else:
        inferred = "Body"

    if (
        patient_age is not None
        and patient_age <= PEDIA_MAX_AGE_YEARS
        and inferred in {"Chest and Cardiac", "Body", "MSK"}
    ):
        return "Pedia"

    return inferred


def resolve_exam_subspeciality(exam, *, body_region: str = "") -> tuple[str, str]:
    metadata = dict(getattr(exam, "metadata", {}) or {})
    explicit_subspeciality = normalize_subspeciality(
        metadata.get("subspeciality") or metadata.get("subspecialty")
    )

    age = patient_age_years(
        getattr(exam, "patient_dob", None),
        reference_datetime=(
            getattr(exam, "exam_datetime", None)
            or getattr(exam, "scheduled_datetime", None)
        ),
    )

    inferred = infer_subspeciality(
        modality_code=str(getattr(getattr(exam, "modality", None), "code", "") or ""),
        body_region=str(body_region or ""),
        procedure_name=str(getattr(exam, "procedure_name", "") or ""),
        patient_age=age,
    )

    return explicit_subspeciality or inferred, inferred


def _normalize_region(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not normalized:
        return ""
    return _REGION_ALIASES.get(normalized, normalized)


def _extract_tokens(*values: str) -> set[str]:
    tokens = set()
    for value in values:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
        tokens.update(part for part in normalized.split() if part)
    return tokens


def _has_keyword(tokens: set[str], keyword_set: set[str]) -> bool:
    if not tokens:
        return False
    return any(token in keyword_set for token in tokens)


def subspeciality_change_events(metadata) -> list[dict]:
    payload = dict(metadata or {})
    raw_events = payload.get(SUBSPECIALITY_CHANGE_LOG_KEY)
    if not isinstance(raw_events, list):
        return []

    events = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue

        to_value = normalize_subspeciality(item.get("to"))
        if not to_value:
            continue

        from_value = normalize_subspeciality(item.get("from"))
        actor = str(item.get("by") or "").strip() or "System"
        at_value = str(item.get("at") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not summary:
            if from_value:
                summary = f"Subspeciality changed from {from_value} to {to_value}."
            else:
                summary = f"Subspeciality set to {to_value}."

        events.append(
            {
                "at": at_value,
                "by": actor,
                "from": from_value,
                "to": to_value,
                "summary": summary,
            }
        )

    return events


def append_subspeciality_change_event(
    metadata,
    *,
    previous_subspeciality: str,
    new_subspeciality: str,
    changed_by: str,
    changed_at=None,
):
    payload = dict(metadata or {})
    previous_value = normalize_subspeciality(previous_subspeciality)
    new_value = normalize_subspeciality(new_subspeciality)
    if not new_value or new_value == previous_value:
        return payload

    actor = str(changed_by or "").strip() or "System"
    when = changed_at
    if isinstance(when, datetime):
        timestamp = when.isoformat()
    else:
        timestamp = datetime.now(dt_timezone.utc).isoformat()

    if previous_value:
        summary = f"Subspeciality changed from {previous_value} to {new_value}."
    else:
        summary = f"Subspeciality set to {new_value}."

    events = list(subspeciality_change_events(payload))
    events.append(
        {
            "at": timestamp,
            "by": actor,
            "from": previous_value,
            "to": new_value,
            "summary": summary,
        }
    )
    payload[SUBSPECIALITY_CHANGE_LOG_KEY] = events[-200:]
    return payload
