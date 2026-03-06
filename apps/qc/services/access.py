from __future__ import annotations

import re

from django.db.utils import OperationalError, ProgrammingError

from apps.core.constants import UserRole


KNOWN_MODALITY_CODES = {
    "CT",
    "MR",
    "XR",
    "US",
    "NM",
    "RF",
    "DX",
    "MG",
    "PT",
}

MODALITY_ALIASES = {
    "CT": "CT",
    "CATSCAN": "CT",
    "CAT SCAN": "CT",
    "MR": "MR",
    "MRI": "MR",
    "XR": "XR",
    "X RAY": "XR",
    "X-RAY": "XR",
    "XRAY": "XR",
    "RADIOGRAPHY": "XR",
    "US": "US",
    "ULTRASOUND": "US",
    "SONOGRAPHY": "US",
    "NM": "NM",
    "NUCLEAR": "NM",
    "NUCLEAR MEDICINE": "NM",
    "RF": "RF",
    "FLUORO": "RF",
    "FLUOROSCOPY": "RF",
    "DX": "DX",
    "MG": "MG",
    "MAMMO": "MG",
    "MAMMOGRAPHY": "MG",
    "PT": "PT",
    "PET": "PT",
}


def role_of(user) -> str:
    return getattr(user, "role", "") or ""


def _normalize_text(value) -> str:
    return str(value or "").strip().upper()


def _parse_modality_tokens_from_string(value: str) -> set[str]:
    text = _normalize_text(value)
    if not text:
        return set()

    normalized = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    tokens = set(part for part in normalized.split(" ") if part)
    codes = set(token for token in tokens if token in KNOWN_MODALITY_CODES)

    for alias, code in MODALITY_ALIASES.items():
        alias_pattern = re.sub(r"[^A-Z0-9]+", " ", alias.upper()).strip()
        if not alias_pattern:
            continue
        if re.search(rf"\b{re.escape(alias_pattern)}\b", normalized):
            codes.add(code)

    return codes


def parse_modality_codes(value) -> set[str]:
    if isinstance(value, dict):
        codes = set()
        for key, enabled in value.items():
            if not enabled:
                continue
            codes.update(parse_modality_codes(key))
        return codes

    if isinstance(value, (list, tuple, set)):
        codes = set()
        for item in value:
            codes.update(parse_modality_codes(item))
        return codes

    return _parse_modality_tokens_from_string(str(value or ""))


def supervisor_modality_scope(user) -> set[str]:
    if getattr(user, "is_superuser", False):
        return set(KNOWN_MODALITY_CODES)

    if role_of(user) == UserRole.ADMIN:
        return set(KNOWN_MODALITY_CODES)

    if role_of(user) != UserRole.SUPERVISOR:
        return set()

    codes = set()
    preferences = dict(getattr(user, "preferences", {}) or {})
    codes.update(parse_modality_codes(preferences.get("qc_modalities")))
    codes.update(parse_modality_codes(preferences.get("qc_worklist_modalities")))
    qc_filter = preferences.get("qc_worklist_filter")
    if isinstance(qc_filter, dict):
        codes.update(parse_modality_codes(qc_filter.get("modalities")))
        codes.update(parse_modality_codes(qc_filter.get("modality_codes")))

    from apps.users.models import UserPreference

    try:
        user_prefs = UserPreference.objects.filter(
            user=user,
            preference_type="qc_worklist_filter",
            preference_key__in=("modalities", "modality_codes", "qc_modalities"),
        ).values_list("preference_value", flat=True)
    except (OperationalError, ProgrammingError):
        user_prefs = []

    for pref_value in user_prefs:
        codes.update(parse_modality_codes(pref_value))

    codes.update(parse_modality_codes(getattr(user, "department", "")))
    codes.update(parse_modality_codes(getattr(user, "specialty", "")))

    try:
        for group_name in user.groups.values_list("name", flat=True):
            codes.update(parse_modality_codes(group_name))
    except Exception:
        pass

    return {code for code in codes if code in KNOWN_MODALITY_CODES}


def user_can_supervise_modality(user, modality_code: str) -> bool:
    if getattr(user, "is_superuser", False):
        return True

    if role_of(user) == UserRole.ADMIN:
        return True

    if role_of(user) != UserRole.SUPERVISOR:
        return False

    allowed_codes = supervisor_modality_scope(user)
    if not allowed_codes:
        return False

    return _normalize_text(modality_code) in allowed_codes


def qc_scope_label(user) -> str:
    role = role_of(user)
    if getattr(user, "is_superuser", False) or role == UserRole.ADMIN:
        return "All modalities"

    if role == UserRole.SUPERVISOR:
        scope = sorted(supervisor_modality_scope(user))
        if scope:
            return ", ".join(scope)
        return "No modality scope configured"

    if role == UserRole.RADIOLOGIST:
        return "My QC cases and concerns"

    return "QC-visible cases"
