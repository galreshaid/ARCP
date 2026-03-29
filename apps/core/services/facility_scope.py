from __future__ import annotations


def _normalize_facility_id(value) -> str:
    return str(value or "").strip()


def scoped_facility_ids(user) -> set[str]:
    """
    Return facility scope for a user.
    Uses both the explicit facilities M2M and primary_facility as fallback.
    """
    ids: set[str] = set()

    try:
        ids.update(
            normalized
            for normalized in (
                _normalize_facility_id(value)
                for value in user.facilities.values_list("id", flat=True)
            )
            if normalized
        )
    except Exception:
        pass

    primary_id = getattr(user, "primary_facility_id", None)
    normalized_primary_id = _normalize_facility_id(primary_id)
    if normalized_primary_id:
        ids.add(normalized_primary_id)

    return ids


def has_scoped_facilities(user) -> bool:
    return bool(scoped_facility_ids(user))


def apply_facility_scope(queryset, user):
    ids = scoped_facility_ids(user)
    if ids:
        return queryset.filter(facility_id__in=ids)

    if getattr(user, "is_superuser", False):
        return queryset

    return queryset.none()


def can_access_facility(user, facility_id) -> bool:
    ids = scoped_facility_ids(user)
    if ids:
        return _normalize_facility_id(facility_id) in ids

    return bool(getattr(user, "is_superuser", False))
