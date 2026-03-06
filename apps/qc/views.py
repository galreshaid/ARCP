from __future__ import annotations

import base64
import binascii
import json
import re
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.core.files.base import ContentFile
from django.http import HttpResponseForbidden
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.core.constants import Permission, UserRole
from apps.core.deeplinks.validator import DeepLinkValidationError, deeplink_validator
from apps.core.models import Exam, ExamStatus
from apps.protocols.services.notifications import send_direct_user_message
from apps.qc.models import (
    AnnotationTool,
    QCChecklist,
    QCAnnotation,
    QCImage,
    QCResult,
    QCSession,
    QCSessionStatus,
)
from apps.qc.services.access import qc_scope_label, role_of, supervisor_modality_scope
from apps.qc.services.notifications import (
    notify_quality_department_of_qc_escalation,
    notify_supervisors_of_qc_concern,
)
from apps.users.decorators import app_permission_required


DATA_URL_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(?P<payload>.+)$")
DEFAULT_CHECKLIST_ITEMS = (
    ("positioning", "Positioning"),
    ("motion", "Motion Artifact"),
    ("exposure", "Exposure"),
    ("completeness", "Completeness"),
    ("acquisition", "Acquisition"),
    ("collimation", "Collimation"),
    ("delay_to_end_exam", "Delay to End Exam"),
    ("machine_related_artifacts", "Machine Related Artifacts"),
    ("medications", "Medications"),
    ("misprotocoled", "Misprotocoled"),
    ("missing_images_in_pacs", "Missing Images in PACS"),
    ("motion", "Motion"),
    ("no_contrast", "No Contrast"),
    ("no_labeling", "No Labeling"),
    ("noisy_study", "Noisy Study"),
    ("overexpose_underexpose_xrays", "Overexpose and Underexpose X-rays"),
    ("patient_factors", "Patient Factors"),
    ("position", "Position"),
    ("processing", "Processing"),
    ("technique", "Technique"),
    ("wrong_tech_markers", "Wrong Tech Markers"),
    ("extravasation", "Extravasation"),
    ("excellent", "Excellent"),
    ("good_job", "Good Job"),
    ("no_comment", "No Comment"),
)
IMAGE_MIME_TO_EXTENSION = {
    "image/png": "png",
}
PACS_EXAM_URL_TEMPLATE = (
    "https://192.168.101.67/ZFP?lights=off&mode=proxy#view"
    "&un=zfpuser"
    "&pw=hEHFlBFUFpMk0x2j7Sdc8DRqJZZVXlI6%2fegPQMaz7szyvaSxcNo7Gy8avdZZv%2bbt"
    "&ris_exam_id={exam_id}"
    "&authority=RKFMRN"
)
PACS_PATIENT_URL_TEMPLATE = (
    "https://192.168.101.67/ZFP?lights=off&mode=proxy#view"
    "&un=zfpuser"
    "&pw=hEHFlBFUFpMk0x2j7Sdc8DRqJZZVXlI6%2fegPQMaz7szyvaSxcNo7Gy8avdZZv%2bbt"
    "&ris_pat_id={patient_id}"
    "&authority=RKFMRN"
)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def _title_for_key(value: str) -> str:
    normalized = _normalize_key(value)
    if not normalized:
        return "Checklist Item"
    return normalized.replace("_", " ").title()


def _default_items_for_modality(_modality_code: str) -> tuple[tuple[str, str], ...]:
    # Apply one shared default checklist to all modalities and keep unique keys only.
    deduplicated: dict[str, str] = {}
    for key, label in DEFAULT_CHECKLIST_ITEMS:
        deduplicated.setdefault(key, label)
    return tuple(deduplicated.items())


def _resolve_checklist_items(modality) -> list[dict]:
    checklist_rows = list(
        QCChecklist.objects.filter(
            modality=modality,
            is_active=True,
        ).order_by("sort_order", "key")
    )
    if checklist_rows:
        return [
            {
                "key": row.key,
                "label": row.label,
                "required": bool(row.is_required),
                "help_text": row.help_text or "",
            }
            for row in checklist_rows
        ]

    template = modality.qc_checklist_template or {}
    items: list[dict] = []

    if isinstance(template, dict):
        nested_items = template.get("items")
        if isinstance(nested_items, list):
            iterable = nested_items
        else:
            iterable = [
                {"key": key, "required": bool(value)}
                for key, value in template.items()
            ]
    elif isinstance(template, list):
        iterable = template
    else:
        iterable = []

    for item in iterable:
        if isinstance(item, str):
            key = _normalize_key(item)
            if not key:
                continue
            items.append(
                {
                    "key": key,
                    "label": _title_for_key(key),
                    "required": True,
                    "help_text": "",
                }
            )
            continue

        if not isinstance(item, dict):
            continue

        key = _normalize_key(item.get("key") or item.get("code") or item.get("name"))
        if not key:
            continue

        items.append(
            {
                "key": key,
                "label": str(item.get("label") or item.get("title") or _title_for_key(key)).strip(),
                "required": bool(item.get("required", True)),
                "help_text": str(item.get("help_text") or "").strip(),
            }
        )

    if items:
        modality_defaults = _default_items_for_modality(getattr(modality, "code", ""))
        existing_keys = {entry["key"] for entry in items}
        for key, label in modality_defaults:
            if key in existing_keys:
                continue
            items.append(
                {
                    "key": key,
                    "label": label,
                    "required": True,
                    "help_text": "",
                }
            )
        return items

    default_items = _default_items_for_modality(getattr(modality, "code", ""))
    return [
        {
            "key": key,
            "label": label,
            "required": True,
            "help_text": "",
        }
        for key, label in default_items
    ]


def _build_pacs_link(accession_number: str) -> str:
    accession = str(accession_number or "").strip()
    if not accession:
        return ""

    template = str(getattr(settings, "PACS_STUDY_URL_TEMPLATE", "") or "").strip()
    if not template:
        return f"/pacs/studies/{quote(accession)}"

    try:
        return template.format(accession=quote(accession))
    except Exception:
        if template.endswith("/"):
            return f"{template}{quote(accession)}"
        return f"{template}/{quote(accession)}"


def _build_pacs_exam_link(accession_number: str) -> str:
    accession = str(accession_number or "").strip()
    if not accession:
        return ""
    return PACS_EXAM_URL_TEMPLATE.replace("{exam_id}", quote(accession))


def _build_pacs_patient_link(mrn: str) -> str:
    patient_id = str(mrn or "").strip()
    if not patient_id:
        return ""
    return PACS_PATIENT_URL_TEMPLATE.replace("{patient_id}", quote(patient_id))


def _has_facility_restrictions(user) -> bool:
    try:
        return not user.is_superuser and user.facilities.exists()
    except Exception:
        return False


def _role_scoped_exam_queryset(user):
    queryset = Exam.objects.select_related("modality", "facility").filter(
        modality__requires_qc=True,
        modality__is_active=True,
    )

    if _has_facility_restrictions(user):
        queryset = queryset.filter(facility__in=user.facilities.all())

    role = role_of(user)
    if user.is_superuser or role == UserRole.ADMIN:
        return queryset

    if role == UserRole.SUPERVISOR:
        allowed_modalities = supervisor_modality_scope(user)
        if not allowed_modalities:
            return queryset.none()
        return queryset.filter(modality__code__in=allowed_modalities)

    if role == UserRole.RADIOLOGIST:
        concern_exam_ids = (
            QCSession.objects.filter(reviewer=user)
            .values_list("exam_id", flat=True)
        )
        return queryset.filter(id__in=concern_exam_ids)

    return queryset


def _effective_exam_status(exam: Exam) -> str:
    metadata = dict(getattr(exam, "metadata", {}) or {})
    order_control = str(metadata.get("hl7_order_control") or "").strip()
    order_status = str(metadata.get("hl7_order_status") or "").strip()

    if not (order_control or order_status):
        return exam.status

    try:
        from apps.core.services.hl7_orm import _map_exam_status_from_hl7
    except Exception:
        return exam.status

    try:
        return _map_exam_status_from_hl7(
            order_control=order_control,
            order_status=order_status,
            fallback=exam.status,
        )
    except Exception:
        return exam.status


def _exam_status_label(status_value: str) -> str:
    normalized = str(status_value or "").strip()
    if not normalized:
        return "Unknown"

    return dict(ExamStatus.choices).get(
        normalized,
        normalized.replace("_", " ").title(),
    )


def _session_or_result_counts_for_exam_ids(exam_ids):
    return {
        "saved_count": QCSession.objects.filter(
            exam_id__in=exam_ids,
            status__in=(QCSessionStatus.SAVED, QCSessionStatus.DRAFT),
        ).count(),
        "acknowledged_count": QCSession.objects.filter(
            exam_id__in=exam_ids,
            status=QCSessionStatus.ACKNOWLEDGED,
        ).count(),
        "replied_count": QCSession.objects.filter(
            exam_id__in=exam_ids,
            status=QCSessionStatus.REPLIED,
        ).count(),
    }


def _checklist_checked(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "checked", "on"}
    if isinstance(value, dict):
        if "checked" in value:
            return _checklist_checked(value.get("checked"))
        if "selected" in value:
            return _checklist_checked(value.get("selected"))
        if "value" in value:
            return _checklist_checked(value.get("value"))
    return bool(value)


def _can_access_exam(user, exam) -> bool:
    if user.is_superuser:
        return True

    if role_of(user) == UserRole.ADMIN:
        return True

    if _has_facility_restrictions(user) and not user.facilities.filter(id=exam.facility_id).exists():
        return False

    if role_of(user) == UserRole.SUPERVISOR:
        return exam.modality.code in supervisor_modality_scope(user)

    return True


def _can_save_session(user) -> bool:
    if user.is_superuser:
        return True

    user_role = role_of(user)
    if user_role not in {UserRole.RADIOLOGIST, UserRole.ADMIN}:
        return False

    return user.has_permission(Permission.QC_CREATE) or user.has_permission(Permission.QC_EDIT)


def _can_capture_evidence(user) -> bool:
    if user.is_superuser:
        return True

    user_role = role_of(user)
    if user_role not in {UserRole.RADIOLOGIST, UserRole.ADMIN}:
        return False

    return user.has_permission(Permission.QC_EVIDENCE_CAPTURE)


def _can_acknowledge_or_reply(user) -> bool:
    if user.is_superuser:
        return True

    user_role = role_of(user)
    if user_role not in {UserRole.TECHNOLOGIST, UserRole.SUPERVISOR, UserRole.ADMIN}:
        return False

    if user_role == UserRole.TECHNOLOGIST:
        return user.has_permission(Permission.QC_VIEW)

    return user.has_permission(Permission.QC_APPROVE) or user.has_permission(Permission.QC_VIEW)


def _latest_issue_owner_for_exam(exam):
    issue_session = (
        QCSession.objects.filter(
            exam=exam,
            reviewer__isnull=False,
            reviewer__role=UserRole.RADIOLOGIST,
        )
        .filter(
            models.Q(concern_raised=True)
            | ~models.Q(notes="")
        )
        .select_related("reviewer")
        .order_by("-created_at")
        .first()
    )
    return getattr(issue_session, "reviewer", None)


def _json_error(message: str, *, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _json_body(request) -> dict:
    raw_body = (request.body or b"").decode("utf-8").strip()
    if not raw_body:
        return {}
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON payload.") from exc

    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object.")
    return payload


def _safe_int(value, default: int = 2) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, 32))


def _normalized_tool(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in (AnnotationTool.ARROW, AnnotationTool.CIRCLE, AnnotationTool.TEXT):
        return normalized
    if normalized in ("FREE", "FREEDRAW", "FREE_DRAW"):
        return AnnotationTool.FREE_DRAW
    return AnnotationTool.FREE_DRAW


def _decode_image_data_url(data_url: str) -> tuple[ContentFile, int, int]:
    match = DATA_URL_RE.match(str(data_url or "").strip())
    if not match:
        raise ValueError("Only base64 image data URLs are accepted.")

    prefix = str(data_url).split(",", 1)[0]
    mime_type = prefix.replace("data:", "").split(";", 1)[0].strip().lower()
    if mime_type not in IMAGE_MIME_TO_EXTENSION:
        raise ValueError("Unsupported image format. Save evidence as PNG.")

    try:
        raw_image = base64.b64decode(match.group("payload"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Unable to decode image payload.") from exc

    max_bytes = int(getattr(settings, "QC_EVIDENCE_MAX_FILE_SIZE_MB", 10) or 10) * 1024 * 1024
    if len(raw_image) > max_bytes:
        raise ValueError("Image exceeds configured QC evidence size limit.")

    return ContentFile(raw_image), 0, 0


def _persist_images(session: QCSession, payload_images: list, *, requested_by) -> int:
    image_count = 0
    pacs_link = _build_pacs_link(session.accession_number)

    for index, image_payload in enumerate(payload_images, start=1):
        if not isinstance(image_payload, dict):
            continue

        data_url = str(image_payload.get("data_url") or "").strip()
        if not data_url:
            continue

        image_content, width, height = _decode_image_data_url(data_url)
        original_name = str(image_payload.get("name") or f"capture-{index}.png").strip()[:255]

        qc_image = QCImage.objects.create(
            session=session,
            accession_number=session.accession_number,
            original_filename=original_name,
            pacs_link=pacs_link,
            width=width,
            height=height,
            capture_order=index,
        )
        safe_accession = re.sub(r"[^A-Za-z0-9_-]+", "-", session.accession_number or "ACC")
        qc_image.image.save(
            f"{safe_accession}-{session.id}-{index}.png",
            image_content,
            save=True,
        )

        annotations = image_payload.get("annotations") or []
        if isinstance(annotations, list):
            for annotation_payload in annotations:
                if not isinstance(annotation_payload, dict):
                    continue

                QCAnnotation.objects.create(
                    image=qc_image,
                    created_by=requested_by,
                    tool=_normalized_tool(annotation_payload.get("tool")),
                    payload=annotation_payload,
                    text_note=str(annotation_payload.get("text") or annotation_payload.get("label") or "").strip(),
                    color=str(annotation_payload.get("color") or "").strip()[:16],
                    stroke_width=_safe_int(
                        annotation_payload.get("strokeWidth") or annotation_payload.get("stroke_width"),
                        default=2,
                    ),
                )

        image_count += 1

    return image_count


@app_permission_required(Permission.QC_VIEW)
def qc_worklist(request):
    accession = str(request.GET.get("accession") or "").strip()
    if accession:
        exam = Exam.objects.filter(accession_number__iexact=accession).first()
        if exam is not None:
            return redirect("qc:review", exam_id=exam.id)

    visible_exams = _role_scoped_exam_queryset(request.user)
    visible_exam_ids = list(visible_exams.values_list("id", flat=True))
    counts = _session_or_result_counts_for_exam_ids(visible_exam_ids)
    supervisor_scope = sorted(supervisor_modality_scope(request.user))

    context = {
        "current_nav": "qc",
        "total_qc_exams": len(visible_exam_ids),
        "saved_count": counts["saved_count"],
        "acknowledged_count": counts["acknowledged_count"],
        "replied_count": counts["replied_count"],
        "worklist_api_url": reverse("qc:exams-api"),
        "analytics_url": reverse("qc:analytics"),
        "worklist_scope_label": qc_scope_label(request.user),
        "supervisor_scope_codes": supervisor_scope,
        "scope_requires_setup": (
            role_of(request.user) == UserRole.SUPERVISOR
            and not bool(supervisor_scope)
            and not request.user.is_superuser
        ),
        "initial_search_query": str(request.GET.get("q") or "").strip(),
        "initial_modality_filter": supervisor_scope[0] if len(supervisor_scope) == 1 else "",
    }
    return render(request, "qc/worklist.html", context)


@app_permission_required(Permission.REPORT_VIEW)
def qc_analytics(request):
    visible_exams = _role_scoped_exam_queryset(request.user)
    exam_ids = list(visible_exams.values_list("id", flat=True))

    sessions_qs = QCSession.objects.filter(exam_id__in=exam_ids).select_related("reviewer", "exam", "exam__modality")
    results_qs = QCResult.objects.filter(exam_id__in=exam_ids).select_related("reviewed_by", "exam", "exam__modality")
    images_qs = QCImage.objects.filter(session__exam_id__in=exam_ids)
    annotations_qs = QCAnnotation.objects.filter(image__session__exam_id__in=exam_ids)

    latest_sessions: dict = {}
    for session in (
        QCSession.objects.filter(exam_id__in=exam_ids)
        .select_related("reviewer")
        .order_by("-created_at")
    ):
        latest_sessions.setdefault(session.exam_id, session)

    latest_results: dict = {}
    for result in (
        QCResult.objects.filter(exam_id__in=exam_ids)
        .select_related("reviewed_by")
        .order_by("-reviewed_at", "-created_at")
    ):
        latest_results.setdefault(result.exam_id, result)

    latest_status_counts: dict[str, int] = {}
    latest_concern_count = 0
    for exam_id in exam_ids:
        latest_result = latest_results.get(exam_id)
        latest_session = latest_sessions.get(exam_id)
        status = "PENDING"
        if latest_result is not None:
            status = str(latest_result.decision or "PENDING").strip().upper() or "PENDING"
        elif latest_session is not None:
            status = str(latest_session.status or "PENDING").strip().upper() or "PENDING"
            if latest_session.concern_raised:
                latest_concern_count += 1
        latest_status_counts[status] = int(latest_status_counts.get(status, 0) or 0) + 1

    status_order = [
        "PENDING",
        QCSessionStatus.DRAFT,
        QCSessionStatus.SAVED,
        QCSessionStatus.ACKNOWLEDGED,
        QCSessionStatus.REPLIED,
        "APPROVED",
        "REJECTED",
    ]
    latest_status_rows = []
    seen_status = set()
    for status in status_order:
        count = int(latest_status_counts.get(status, 0) or 0)
        if count <= 0 and status != "PENDING":
            continue
        latest_status_rows.append({"status": status, "count": count})
        seen_status.add(status)
    for status, count in sorted(latest_status_counts.items(), key=lambda item: item[0]):
        if status in seen_status:
            continue
        latest_status_rows.append({"status": status, "count": int(count or 0)})

    totals = {
        "total_exams": len(exam_ids),
        "total_patients": visible_exams.exclude(mrn="").values("mrn").distinct().count(),
        "total_sessions": sessions_qs.count(),
        "total_results": results_qs.count(),
        "total_images": images_qs.count(),
        "total_annotations": annotations_qs.count(),
        "concern_sessions": sessions_qs.filter(concern_raised=True).count(),
        "latest_concern_exams": latest_concern_count,
    }
    totals["documented_exams"] = visible_exams.filter(
        models.Q(qc_sessions__isnull=False) | models.Q(qc_results__isnull=False)
    ).distinct().count()
    totals["pending_exams"] = max(totals["total_exams"] - totals["documented_exams"], 0)

    modality_summary_map: dict[str, dict] = {}
    for row in (
        visible_exams.values("modality__code", "modality__name")
        .annotate(exam_count=models.Count("id"))
        .order_by("modality__code")
    ):
        code = str(row.get("modality__code") or "").strip().upper()
        if not code:
            continue
        modality_summary_map[code] = {
            "code": code,
            "name": row.get("modality__name") or code,
            "exam_count": int(row.get("exam_count") or 0),
            "documented_count": 0,
            "pending_count": 0,
            "session_count": 0,
            "concern_count": 0,
            "saved_count": 0,
            "acknowledged_count": 0,
            "replied_count": 0,
            "approved_count": 0,
            "rejected_count": 0,
            "image_count": 0,
            "annotation_count": 0,
        }

    for row in (
        visible_exams.values("modality__code")
        .annotate(
            documented_count=models.Count(
                "id",
                filter=models.Q(qc_sessions__isnull=False) | models.Q(qc_results__isnull=False),
                distinct=True,
            ),
        )
        .order_by("modality__code")
    ):
        code = str(row.get("modality__code") or "").strip().upper()
        bucket = modality_summary_map.get(code)
        if bucket is None:
            continue
        bucket["documented_count"] = int(row.get("documented_count") or 0)

    for row in (
        sessions_qs.values("exam__modality__code")
        .annotate(
            session_count=models.Count("id"),
            concern_count=models.Count("id", filter=models.Q(concern_raised=True)),
            saved_count=models.Count("id", filter=models.Q(status__in=(QCSessionStatus.SAVED, QCSessionStatus.DRAFT))),
            acknowledged_count=models.Count("id", filter=models.Q(status=QCSessionStatus.ACKNOWLEDGED)),
            replied_count=models.Count("id", filter=models.Q(status=QCSessionStatus.REPLIED)),
        )
        .order_by("exam__modality__code")
    ):
        code = str(row.get("exam__modality__code") or "").strip().upper()
        bucket = modality_summary_map.get(code)
        if bucket is None:
            continue
        bucket["session_count"] = int(row.get("session_count") or 0)
        bucket["concern_count"] = int(row.get("concern_count") or 0)
        bucket["saved_count"] = int(row.get("saved_count") or 0)
        bucket["acknowledged_count"] = int(row.get("acknowledged_count") or 0)
        bucket["replied_count"] = int(row.get("replied_count") or 0)

    for row in (
        results_qs.values("exam__modality__code")
        .annotate(
            approved_count=models.Count("id", filter=models.Q(decision="APPROVED")),
            rejected_count=models.Count("id", filter=models.Q(decision="REJECTED")),
        )
        .order_by("exam__modality__code")
    ):
        code = str(row.get("exam__modality__code") or "").strip().upper()
        bucket = modality_summary_map.get(code)
        if bucket is None:
            continue
        bucket["approved_count"] = int(row.get("approved_count") or 0)
        bucket["rejected_count"] = int(row.get("rejected_count") or 0)

    for row in (
        images_qs.values("session__exam__modality__code")
        .annotate(image_count=models.Count("id"))
        .order_by("session__exam__modality__code")
    ):
        code = str(row.get("session__exam__modality__code") or "").strip().upper()
        bucket = modality_summary_map.get(code)
        if bucket is None:
            continue
        bucket["image_count"] = int(row.get("image_count") or 0)

    for row in (
        annotations_qs.values("image__session__exam__modality__code")
        .annotate(annotation_count=models.Count("id"))
        .order_by("image__session__exam__modality__code")
    ):
        code = str(row.get("image__session__exam__modality__code") or "").strip().upper()
        bucket = modality_summary_map.get(code)
        if bucket is None:
            continue
        bucket["annotation_count"] = int(row.get("annotation_count") or 0)

    modality_summary = []
    for code in sorted(modality_summary_map):
        bucket = modality_summary_map[code]
        bucket["pending_count"] = max(bucket["exam_count"] - bucket["documented_count"], 0)
        modality_summary.append(bucket)

    patient_summary = list(
        visible_exams.values("mrn", "patient_name")
        .annotate(
            exam_count=models.Count("id", distinct=True),
            modality_count=models.Count("modality", distinct=True),
            session_count=models.Count("qc_sessions", distinct=True),
            result_count=models.Count("qc_results", distinct=True),
            concern_count=models.Count(
                "qc_sessions",
                filter=models.Q(qc_sessions__concern_raised=True),
                distinct=True,
            ),
            image_count=models.Count("qc_sessions__images", distinct=True),
            annotation_count=models.Count("qc_sessions__images__annotations", distinct=True),
        )
        .order_by("-session_count", "-result_count", "patient_name")[:250]
    )
    for row in patient_summary:
        row["mrn_label"] = str(row.get("mrn") or "").strip() or "-"
        row["patient_label"] = str(row.get("patient_name") or "").strip() or "Unknown Patient"
        row["documentation_status"] = (
            "Documented"
            if int(row.get("session_count") or 0) > 0 or int(row.get("result_count") or 0) > 0
            else "Pending"
        )

    reviewer_activity_map: dict[str, dict] = {}
    for row in (
        sessions_qs.values(
            "reviewer_id",
            "reviewer__first_name",
            "reviewer__last_name",
            "reviewer__username",
            "reviewer__role",
        )
        .annotate(
            session_count=models.Count("id"),
            concern_count=models.Count("id", filter=models.Q(concern_raised=True)),
            saved_count=models.Count("id", filter=models.Q(status__in=(QCSessionStatus.SAVED, QCSessionStatus.DRAFT))),
            acknowledged_count=models.Count("id", filter=models.Q(status=QCSessionStatus.ACKNOWLEDGED)),
            replied_count=models.Count("id", filter=models.Q(status=QCSessionStatus.REPLIED)),
            last_session_at=models.Max("created_at"),
        )
    ):
        key = str(row.get("reviewer_id") or "unassigned")
        reviewer_activity_map[key] = {
            "reviewer_label": (
                " ".join(
                    part
                    for part in [
                        str(row.get("reviewer__first_name") or "").strip(),
                        str(row.get("reviewer__last_name") or "").strip(),
                    ]
                    if part
                )
                or str(row.get("reviewer__username") or "").strip()
                or "Unassigned"
            ),
            "role": str(row.get("reviewer__role") or "").strip() or "-",
            "session_count": int(row.get("session_count") or 0),
            "result_count": 0,
            "concern_count": int(row.get("concern_count") or 0),
            "saved_count": int(row.get("saved_count") or 0),
            "acknowledged_count": int(row.get("acknowledged_count") or 0),
            "replied_count": int(row.get("replied_count") or 0),
            "approved_count": 0,
            "rejected_count": 0,
            "last_activity": row.get("last_session_at"),
        }

    for row in (
        results_qs.values(
            "reviewed_by_id",
            "reviewed_by__first_name",
            "reviewed_by__last_name",
            "reviewed_by__username",
            "reviewed_by__role",
        )
        .annotate(
            result_count=models.Count("id"),
            approved_count=models.Count("id", filter=models.Q(decision="APPROVED")),
            rejected_count=models.Count("id", filter=models.Q(decision="REJECTED")),
            last_result_at=models.Max("reviewed_at"),
        )
    ):
        key = str(row.get("reviewed_by_id") or "unassigned")
        bucket = reviewer_activity_map.get(key)
        if bucket is None:
            bucket = {
                "reviewer_label": (
                    " ".join(
                        part
                        for part in [
                            str(row.get("reviewed_by__first_name") or "").strip(),
                            str(row.get("reviewed_by__last_name") or "").strip(),
                        ]
                        if part
                    )
                    or str(row.get("reviewed_by__username") or "").strip()
                    or "Unassigned"
                ),
                "role": str(row.get("reviewed_by__role") or "").strip() or "-",
                "session_count": 0,
                "result_count": 0,
                "concern_count": 0,
                "saved_count": 0,
                "acknowledged_count": 0,
                "replied_count": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "last_activity": row.get("last_result_at"),
            }
            reviewer_activity_map[key] = bucket

        bucket["result_count"] += int(row.get("result_count") or 0)
        bucket["approved_count"] += int(row.get("approved_count") or 0)
        bucket["rejected_count"] += int(row.get("rejected_count") or 0)

        result_timestamp = row.get("last_result_at")
        if result_timestamp and (not bucket["last_activity"] or result_timestamp > bucket["last_activity"]):
            bucket["last_activity"] = result_timestamp

    reviewer_activity = sorted(
        reviewer_activity_map.values(),
        key=lambda item: (
            -(int(item.get("session_count") or 0) + int(item.get("result_count") or 0)),
            -int(item.get("approved_count") or 0),
            str(item.get("reviewer_label") or ""),
        ),
    )[:150]

    checklist_item_map: dict[str, dict] = {}
    for session in sessions_qs:
        checklist_state = dict(getattr(session, "checklist_state", {}) or {})
        for key, raw_value in checklist_state.items():
            normalized_key = _normalize_key(key)
            if not normalized_key:
                continue
            bucket = checklist_item_map.setdefault(
                normalized_key,
                {
                    "key": normalized_key,
                    "label": _title_for_key(normalized_key),
                    "checked_count": 0,
                    "unchecked_count": 0,
                    "total_count": 0,
                },
            )
            is_checked = _checklist_checked(raw_value)
            if is_checked:
                bucket["checked_count"] += 1
            else:
                bucket["unchecked_count"] += 1
            bucket["total_count"] += 1

    checklist_summary = sorted(
        checklist_item_map.values(),
        key=lambda item: (-int(item["checked_count"]), -int(item["total_count"]), item["label"]),
    )[:200]

    annotation_tool_summary = list(
        annotations_qs.values("tool")
        .annotate(count=models.Count("id"))
        .order_by("-count", "tool")
    )

    context = {
        "current_nav": "qc-analytics",
        "worklist_scope_label": qc_scope_label(request.user),
        "totals": totals,
        "latest_status_rows": latest_status_rows,
        "modality_summary": modality_summary,
        "patient_summary": patient_summary,
        "reviewer_activity": reviewer_activity,
        "checklist_summary": checklist_summary,
        "annotation_tool_summary": annotation_tool_summary,
        "worklist_url": reverse("qc:worklist"),
    }
    return render(request, "qc/analytics.html", context)


@app_permission_required(Permission.QC_VIEW)
def qc_review(request, exam_id):
    exam = get_object_or_404(
        Exam.objects.select_related("modality", "facility"),
        id=exam_id,
    )
    if not _can_access_exam(request.user, exam):
        return HttpResponseForbidden("Not allowed")

    checklist_items = _resolve_checklist_items(exam.modality)
    recent_sessions = (
        QCSession.objects.filter(exam=exam)
        .select_related("reviewer")
        .prefetch_related("images")
        .order_by("-created_at")[:5]
    )
    latest_session = recent_sessions[0] if recent_sessions else None
    latest_checklist_state = dict(getattr(latest_session, "checklist_state", {}) or {})
    has_existing_annotated_evidence = QCAnnotation.objects.filter(image__session__exam=exam).exists()
    user_role = role_of(request.user)
    is_supervisor_user = user_role == UserRole.SUPERVISOR
    is_technologist_user = user_role == UserRole.TECHNOLOGIST
    is_qc_editor_user = request.user.is_superuser or user_role in {UserRole.RADIOLOGIST, UserRole.ADMIN}
    is_acknowledgement_user = is_supervisor_user or is_technologist_user
    issue_owner = _latest_issue_owner_for_exam(exam)
    launch_base_url = request.build_absolute_uri(reverse("qc:launch"))
    pacs_exam_link = _build_pacs_exam_link(exam.accession_number)
    pacs_patient_link = _build_pacs_patient_link(exam.mrn)

    context = {
        "current_nav": "qc",
        "exam": exam,
        "checklist_items": checklist_items,
        "recent_sessions": recent_sessions,
        "latest_session": latest_session,
        "latest_checklist_state": latest_checklist_state,
        "has_existing_annotated_evidence": has_existing_annotated_evidence,
        "user_role": user_role,
        "is_supervisor_user": is_supervisor_user,
        "is_technologist_user": is_technologist_user,
        "is_qc_editor_user": is_qc_editor_user,
        "is_acknowledgement_user": is_acknowledgement_user,
        "issue_owner": issue_owner,
        "issue_owner_id": str(issue_owner.id) if issue_owner else "",
        "pacs_study_link": pacs_exam_link or _build_pacs_link(exam.accession_number),
        "pacs_exam_link": pacs_exam_link,
        "pacs_patient_link": pacs_patient_link,
        "launch_by_accession_url": f"{launch_base_url}?accession={quote(exam.accession_number)}",
        "launch_by_order_url": f"{launch_base_url}?order_id={quote(exam.order_id)}",
        "session_api_url": reverse("qc:session-api", args=[exam.id]),
        "can_save_session": _can_save_session(request.user) and is_qc_editor_user,
        "can_acknowledge_or_reply": _can_acknowledge_or_reply(request.user) and is_acknowledgement_user,
        "can_escalate_to_quality": (
            _can_acknowledge_or_reply(request.user)
            and is_supervisor_user
        ),
        "can_capture_evidence": _can_capture_evidence(request.user) and is_qc_editor_user,
    }
    return render(request, "qc/review.html", context)


@app_permission_required(Permission.QC_VIEW)
@require_GET
def qc_exams_api(request):
    search_query = str(request.GET.get("q") or "").strip()
    modality_filter = str(request.GET.get("modality") or "").strip().upper()

    exams_qs = _role_scoped_exam_queryset(request.user)
    if modality_filter:
        exams_qs = exams_qs.filter(modality__code=modality_filter)

    if search_query:
        exams_qs = exams_qs.filter(
            models.Q(accession_number__icontains=search_query)
            | models.Q(order_id__icontains=search_query)
            | models.Q(mrn__icontains=search_query)
            | models.Q(patient_name__icontains=search_query)
            | models.Q(procedure_name__icontains=search_query)
        )

    exams = list(exams_qs.order_by("-exam_datetime", "-created_at")[:250])
    exam_ids = [exam.id for exam in exams]

    latest_sessions: dict = {}
    for session in (
        QCSession.objects.filter(exam_id__in=exam_ids)
        .select_related("reviewer")
        .order_by("-created_at")
    ):
        latest_sessions.setdefault(session.exam_id, session)

    latest_results: dict = {}
    for result in (
        QCResult.objects.filter(exam_id__in=exam_ids)
        .select_related("reviewed_by")
        .order_by("-reviewed_at", "-created_at")
    ):
        latest_results.setdefault(result.exam_id, result)

    rows = []
    for exam in exams:
        effective_status = _effective_exam_status(exam)
        pacs_exam_link = _build_pacs_exam_link(exam.accession_number)
        pacs_patient_link = _build_pacs_patient_link(exam.mrn)
        latest_result = latest_results.get(exam.id)
        latest_session = latest_sessions.get(exam.id)

        status = "PENDING"
        reviewer = ""
        updated_at = None
        if latest_result is not None:
            status = latest_result.decision
            reviewer = (latest_result.reviewed_by.get_full_name() if latest_result.reviewed_by else "") or (
                latest_result.reviewed_by.username if latest_result.reviewed_by else ""
            )
            updated_at = latest_result.reviewed_at
        elif latest_session is not None:
            status = latest_session.status
            reviewer = (latest_session.reviewer.get_full_name() if latest_session.reviewer else "") or (
                latest_session.reviewer.username if latest_session.reviewer else ""
            )
            updated_at = latest_session.updated_at

        rows.append(
            {
                "id": str(exam.id),
                "order_id": exam.order_id,
                "accession_number": exam.accession_number,
                "mrn": exam.mrn,
                "patient_name": exam.patient_name,
                "study": exam.procedure_name,
                "modality": {
                    "code": exam.modality.code,
                    "name": exam.modality.name,
                },
                "facility": {
                    "code": exam.facility.code,
                    "name": exam.facility.name,
                },
                "exam_datetime": exam.exam_datetime.isoformat() if exam.exam_datetime else None,
                "exam_status": effective_status,
                "exam_status_label": _exam_status_label(effective_status),
                "status": status,
                "reviewer": reviewer,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "concern_raised": bool(latest_session.concern_raised) if latest_session else False,
                "review_url": reverse("qc:review", args=[exam.id]),
                "pacs_study_link": pacs_exam_link or _build_pacs_link(exam.accession_number),
                "pacs_exam_link": pacs_exam_link,
                "pacs_patient_link": pacs_patient_link,
            }
        )

    return JsonResponse({"results": rows})


@app_permission_required(Permission.QC_VIEW)
@require_http_methods(["POST"])
def qc_session_api(request, exam_id):
    exam = get_object_or_404(
        Exam.objects.select_related("modality", "facility"),
        id=exam_id,
    )
    if not _can_access_exam(request.user, exam):
        return _json_error("Not allowed.", status=403)

    if not exam.modality.requires_qc:
        return _json_error("This modality is not configured for QC.", status=409)

    try:
        payload = _json_body(request)
    except ValueError as exc:
        return _json_error(str(exc), status=400)

    action = str(payload.get("action") or "save").strip().lower()
    legacy_action_map = {
        "draft": "save",
        "approve": "acknowledge",
        "reject": "reply",
    }
    action = legacy_action_map.get(action, action)
    if action not in {"save", "acknowledge", "reply", "escalate_quality"}:
        return _json_error("Invalid QC action.", status=400)

    user_role = role_of(request.user)
    if action == "save":
        if not _can_save_session(request.user):
            return _json_error(
                "Only radiologists/admin can save QC fields. Technologist and supervisor are acknowledgment-only.",
                status=403,
            )
    if action in {"acknowledge", "reply", "escalate_quality"}:
        if user_role == UserRole.RADIOLOGIST and not request.user.is_superuser:
            return _json_error("Radiologists can only save QC issues.", status=403)
        if not _can_acknowledge_or_reply(request.user):
            return _json_error("Missing permission: qc.view/qc.approve", status=403)
        if action == "reply" and user_role != UserRole.SUPERVISOR and not request.user.is_superuser:
            return _json_error("Only supervisors can send QC reply notes.", status=403)
        if action == "escalate_quality" and user_role != UserRole.SUPERVISOR and not request.user.is_superuser:
            return _json_error("Only supervisors can escalate QC concern to Quality Department.", status=403)

    images_payload = payload.get("images") or []
    if not isinstance(images_payload, list):
        return _json_error("Images must be an array.", status=400)

    if action in {"acknowledge", "reply", "escalate_quality"} and images_payload:
        return _json_error(
            "Acknowledgement/escalation mode is read-only for QC fields and cannot upload new evidence.",
            status=403,
        )

    if images_payload and not _can_capture_evidence(request.user):
        return _json_error("Missing permission: qc.evidence_capture", status=403)

    checklist_state = payload.get("checklist") or {}
    if not isinstance(checklist_state, dict):
        return _json_error("Checklist must be a key/value object.", status=400)

    notes = str(payload.get("notes") or "").strip()
    concern_raised = bool(payload.get("concern_raised"))
    supervisor_reply = str(payload.get("supervisor_reply") or "").strip()
    quality_escalation_note = str(payload.get("quality_escalation_note") or "").strip()
    if action == "reply" and not supervisor_reply:
        return _json_error("Reply message is required for supervisor reply.", status=400)
    if action == "escalate_quality" and not quality_escalation_note:
        return _json_error("Escalation note is required for quality escalation.", status=400)

    if action in {"acknowledge", "reply", "escalate_quality"}:
        source_session = (
            QCSession.objects.filter(
                exam=exam,
                reviewer__role=UserRole.RADIOLOGIST,
                concern_raised=True,
            )
            .order_by("-created_at")
            .first()
        )
        if source_session is None:
            return _json_error(
                "No radiologist-raised QC concern is available for acknowledgement/escalation.",
                status=409,
            )

        checklist_state = dict(source_session.checklist_state or {})
        concern_raised = bool(source_session.concern_raised)

    direct_message_payload = payload.get("direct_message") or {}
    if direct_message_payload and not isinstance(direct_message_payload, dict):
        return _json_error("Direct message must be an object.", status=400)
    direct_message_recipient_id = str(direct_message_payload.get("recipient_id") or "").strip()
    direct_message_title = str(direct_message_payload.get("title") or "").strip()
    direct_message_body = str(direct_message_payload.get("message") or "").strip()
    if direct_message_recipient_id and not direct_message_body:
        return _json_error("Direct message body is required.", status=400)

    session_status = QCSessionStatus.SAVED
    if action == "acknowledge":
        session_status = QCSessionStatus.ACKNOWLEDGED
    elif action in {"reply", "escalate_quality"}:
        session_status = QCSessionStatus.REPLIED

    session_note = notes
    if action == "reply":
        session_note = supervisor_reply
    elif action == "escalate_quality":
        session_note = quality_escalation_note

    session = QCSession.objects.create(
        exam=exam,
        reviewer=request.user,
        accession_number=exam.accession_number,
        mrn=exam.mrn,
        modality_code=exam.modality.code,
        study_name=exam.procedure_name,
        checklist_state=checklist_state,
        notes=session_note,
        concern_raised=concern_raised,
        status=session_status,
        submitted_at=timezone.now() if action in {"acknowledge", "reply", "escalate_quality"} else None,
    )

    try:
        image_count = _persist_images(
            session,
            images_payload,
            requested_by=request.user,
        )
    except ValueError as exc:
        session.delete()
        return _json_error(str(exc), status=400)

    concern_or_note = concern_raised or bool(notes)
    if user_role == UserRole.RADIOLOGIST and concern_or_note:
        notify_supervisors_of_qc_concern(
            session=session,
            raised_by=request.user,
        )

    if action == "escalate_quality":
        notify_quality_department_of_qc_escalation(
            session=session,
            escalated_by=request.user,
            escalation_note=quality_escalation_note,
        )

    review_url = reverse("qc:review", args=[exam.id])
    issue_owner = _latest_issue_owner_for_exam(exam)
    if action in {"acknowledge", "reply", "escalate_quality"} and issue_owner and issue_owner.pk != request.user.pk:
        if action == "acknowledge":
            automated_message = f"QC concern acknowledged for accession {exam.accession_number}."
        elif action == "reply":
            automated_message = f"Supervisor reply for accession {exam.accession_number}:\n{supervisor_reply}"
        else:
            automated_message = (
                f"Supervisor escalated QC concern for accession {exam.accession_number} "
                f"to Quality Department:\n{quality_escalation_note}"
            )
        send_direct_user_message(
            sender=request.user,
            recipient=issue_owner,
            title=f"QC {action.replace('_', ' ').title()}: {exam.accession_number}",
            message=automated_message,
            target_url=review_url,
        )

    if direct_message_recipient_id and direct_message_body:
        user_model = get_user_model()
        try:
            recipient = user_model.objects.filter(
                id=direct_message_recipient_id,
                is_active=True,
            ).exclude(pk=request.user.pk).first()
        except (ValidationError, ValueError):
            recipient = None

        if recipient is None:
            session.delete()
            return _json_error("Selected direct-message recipient is invalid.", status=400)

        send_direct_user_message(
            sender=request.user,
            recipient=recipient,
            title=direct_message_title or f"QC message: {exam.accession_number}",
            message=direct_message_body,
            target_url=review_url,
        )

    metadata = dict(exam.metadata or {})
    metadata["qc_latest_session_id"] = str(session.id)
    metadata["qc_latest_status"] = session.status
    metadata["qc_latest_concern_raised"] = concern_raised
    metadata["qc_latest_by"] = (
        (request.user.get_full_name() or "").strip()
        or getattr(request.user, "username", "")
    )
    metadata["qc_latest_at"] = timezone.now().isoformat()
    if supervisor_reply:
        metadata["qc_latest_supervisor_reply"] = supervisor_reply
    if quality_escalation_note:
        metadata["qc_latest_quality_escalation_note"] = quality_escalation_note
    exam.metadata = metadata
    exam.save(update_fields=["metadata"])

    return JsonResponse(
        {
            "ok": True,
            "session_id": str(session.id),
            "status": session.status,
            "decision": "",
            "concern_raised": concern_raised,
            "image_count": image_count,
            "review_url": review_url,
        }
    )


@app_permission_required(Permission.QC_VIEW)
def qc_launch(request):
    exam_id = str(request.GET.get("exam_id") or "").strip()
    accession = str(request.GET.get("accession") or "").strip()
    order_id = str(request.GET.get("order_id") or "").strip()

    exam = None
    if exam_id:
        try:
            exam = Exam.objects.filter(id=exam_id).first()
        except (ValueError, ValidationError):
            exam = None
    elif accession:
        exam = Exam.objects.filter(accession_number__iexact=accession).first()
    elif order_id:
        exam = Exam.objects.filter(order_id__iexact=order_id).order_by("-created_at").first()

    if exam is not None:
        if not _can_access_exam(request.user, exam):
            return HttpResponseForbidden("Not allowed")
        return redirect("qc:review", exam_id=exam.id)

    fallback_query = accession or order_id or exam_id
    if fallback_query:
        return redirect(f"{reverse('qc:worklist')}?q={quote(fallback_query)}")

    return redirect("qc:worklist")


@app_permission_required(Permission.QC_VIEW)
def qc_deeplink_entry(request):
    token = str(request.GET.get("token") or "").strip()
    if not token:
        return _json_error("Token is required.", status=400)

    try:
        payload = deeplink_validator.validate_for_user(
            token=token,
            user=request.user,
            required_type="qc",
        )
    except DeepLinkValidationError as exc:
        return _json_error(str(exc), status=400)

    context = deeplink_validator.extract_exam_context(payload)
    exam = get_object_or_404(Exam, id=context["exam_id"])
    if not _can_access_exam(request.user, exam):
        return HttpResponseForbidden("Not allowed")
    return redirect("qc:review", exam_id=exam.id)
