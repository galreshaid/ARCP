from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import models
from django.db.utils import OperationalError, ProgrammingError
from django.urls import reverse
from django.utils import timezone

from apps.core.constants import Permission as AppPermission, UserRole
from apps.qc.services.access import user_can_supervise_modality
from apps.users.models import (
    DOMAIN_PERMISSION_TO_DJANGO_PERMISSION,
    User,
    UserNotification,
)


logger = logging.getLogger(__name__)
QUALITY_DEPARTMENT_GROUP_NAMES = (
    "Quality Department",
    "Quality Department Representative",
    "Quality Officer",
    "Quality Representative",
)


def _display_name(user) -> str:
    if not user:
        return "System"

    full_name = ""
    if hasattr(user, "get_full_name"):
        full_name = str(user.get_full_name() or "").strip()

    return full_name or str(getattr(user, "username", "") or "").strip() or "User"


def _send_email_to_address(email: str, subject: str, body: str) -> bool:
    email = str(email or "").strip()
    if not email:
        return False

    from_email = (
        getattr(settings, "DEFAULT_FROM_EMAIL", "")
        or getattr(settings, "SERVER_EMAIL", "")
        or "no-reply@localhost"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Unable to send QC notification email.")
        return False


def _send_email(recipient, subject: str, body: str) -> bool:
    return _send_email_to_address(
        str(getattr(recipient, "email", "") or "").strip(),
        subject,
        body,
    )


def _create_notification(
    *,
    recipient,
    sender,
    title: str,
    message: str,
    target_url: str = "",
    category: str = "QC",
):
    if not recipient:
        return None

    email_subject = title
    email_body = message
    if target_url:
        email_body = "\n".join([message, "", f"Open: {target_url}"])

    email_sent = _send_email(recipient, email_subject, email_body)

    try:
        notification = UserNotification.objects.create(
            recipient=recipient,
            sender=sender,
            title=title.strip(),
            message=message.strip(),
            category=category,
            target_url=target_url,
        )
    except (OperationalError, ProgrammingError):
        logger.warning("User notifications table is not available yet; skipping QC inbox notification.")
        return None

    if email_sent:
        notification.email_sent = True
        notification.emailed_at = timezone.now()
        notification.save(update_fields=["email_sent", "emailed_at"])

    return notification


def _domain_permission_codename(domain_permission: str) -> str:
    mapped = str(DOMAIN_PERMISSION_TO_DJANGO_PERMISSION.get(domain_permission, "") or "").strip()
    if "." not in mapped:
        return ""
    return mapped.split(".", 1)[1].strip()


def _is_modality_in_scope(user, modality_code: str) -> bool:
    if getattr(user, "is_superuser", False):
        return True

    if getattr(user, "role", "") == UserRole.ADMIN:
        return True

    if getattr(user, "role", "") == UserRole.SUPERVISOR:
        return user_can_supervise_modality(user, modality_code)

    # Non-supervisor users can still receive scoped notifications if explicitly permissioned.
    return True


def _merge_recipients(*recipient_groups) -> list[User]:
    merged: dict = {}
    for recipient_group in recipient_groups:
        for recipient in recipient_group or []:
            recipient_pk = getattr(recipient, "pk", None)
            if recipient_pk is None:
                continue
            merged[recipient_pk] = recipient
    return list(merged.values())


def _recipients_for_domain_permission(exam, *, domain_permission: str, modality_scoped: bool = False) -> list[User]:
    codename = _domain_permission_codename(domain_permission)
    if not codename:
        return []

    qs = User.objects.filter(
        is_active=True,
    ).filter(
        models.Q(is_superuser=True)
        | models.Q(
            user_permissions__content_type__app_label="users",
            user_permissions__codename=codename,
        )
        | models.Q(
            groups__permissions__content_type__app_label="users",
            groups__permissions__codename=codename,
        )
    )

    if getattr(exam, "facility_id", None):
        qs = qs.filter(
            models.Q(is_superuser=True)
            | models.Q(role=UserRole.ADMIN)
            | models.Q(facilities__id=exam.facility_id)
            | models.Q(primary_facility_id=exam.facility_id)
        )

    recipients = []
    for candidate in qs.distinct():
        if modality_scoped and not _is_modality_in_scope(candidate, exam.modality.code):
            continue

        recipients.append(candidate)

    return recipients


def _quality_representative_recipients_for_exam(exam) -> list[User]:
    permissioned_recipients = _recipients_for_domain_permission(
        exam,
        domain_permission=AppPermission.QC_NOTIFY_OFFICER,
        modality_scoped=False,
    )

    qs = User.objects.filter(
        is_active=True,
    ).filter(
        models.Q(is_superuser=True)
        | models.Q(role=UserRole.ADMIN)
        | models.Q(groups__name__in=QUALITY_DEPARTMENT_GROUP_NAMES)
        | models.Q(department__icontains="quality")
    )

    if getattr(exam, "facility_id", None):
        qs = qs.filter(
            models.Q(is_superuser=True)
            | models.Q(role=UserRole.ADMIN)
            | models.Q(facilities__id=exam.facility_id)
            | models.Q(primary_facility_id=exam.facility_id)
        )

    return _merge_recipients(permissioned_recipients, list(qs.distinct()))


def _resolve_service_desk_email(exam) -> str:
    facility = getattr(exam, "facility", None)
    configured_email = str(getattr(facility, "qc_service_desk_email", "") or "").strip()
    if configured_email:
        return configured_email

    fallback_email = str(getattr(settings, "QC_SERVICE_DESK_EMAIL", "") or "").strip()
    if fallback_email:
        return fallback_email

    return "helpdesk@Aaml.com.sa"


def _send_service_desk_ticket_email(*, session, raised_by) -> bool:
    exam = session.exam
    service_desk_email = _resolve_service_desk_email(exam)
    if not service_desk_email:
        return False

    sender_name = _display_name(raised_by)
    concern_text = str(getattr(session, "notes", "") or "").strip() or "Concern raised without additional note."
    subject = f"QC Service Desk Ticket: {exam.accession_number}"
    body = "\n".join(
        [
            "Please open a service desk ticket for this QC concern.",
            f"Raised by: {sender_name}",
            f"Accession: {exam.accession_number}",
            f"MRN: {exam.mrn}",
            f"Modality: {exam.modality.code}",
            f"Study: {exam.procedure_name}",
            f"Concern note: {concern_text}",
            "",
            f"QC review URL: {reverse('qc:review', args=[exam.id])}",
        ]
    )
    return _send_email_to_address(service_desk_email, subject, body)


def notify_supervisors_of_qc_concern(*, session, raised_by):
    exam = session.exam
    sender_name = _display_name(raised_by)
    target_url = reverse("qc:review", args=[exam.id])
    concern_raised = bool(getattr(session, "concern_raised", False))
    note_text = str(getattr(session, "notes", "") or "").strip()

    if concern_raised:
        action_line = f"{sender_name} raised a QC concern."
        note_line = f"Concern note: {note_text or 'Concern raised without additional note.'}"
        title = f"QC concern: {exam.accession_number}"
        category = "QC_CONCERN"
    else:
        action_line = f"{sender_name} recorded QC checklist or notes."
        note_line = f"QC note: {note_text or 'Checklist update without additional note.'}"
        title = f"QC checklist update: {exam.accession_number}"
        category = "QC_CHECKLIST_UPDATE"

    message = "\n".join(
        [
            action_line,
            f"Accession: {exam.accession_number}",
            f"MRN: {exam.mrn}",
            f"Modality: {exam.modality.code}",
            f"Study: {exam.procedure_name}",
            note_line,
        ]
    )

    recipients = _merge_recipients(
        _recipients_for_domain_permission(
            exam,
            domain_permission=AppPermission.QC_NOTIFY_MODALITY_SUPERVISOR,
            modality_scoped=True,
        ),
        _recipients_for_domain_permission(
            exam,
            domain_permission=AppPermission.QC_NOTIFY_MODALITY_QC_SUPERVISOR,
            modality_scoped=True,
        ),
    )
    if concern_raised:
        recipients = _merge_recipients(recipients, _quality_representative_recipients_for_exam(exam))

    for recipient in recipients:
        if recipient.pk == getattr(raised_by, "pk", None):
            continue
        _create_notification(
            recipient=recipient,
            sender=raised_by,
            title=title,
            message=message,
            target_url=target_url,
            category=category,
        )

    if concern_raised:
        _send_service_desk_ticket_email(
            session=session,
            raised_by=raised_by,
        )


def notify_quality_department_of_qc_escalation(*, session, escalated_by, escalation_note: str = ""):
    exam = session.exam
    sender_name = _display_name(escalated_by)
    target_url = reverse("qc:review", args=[exam.id])
    escalation_text = str(escalation_note or "").strip() or "Escalation raised without additional note."

    message = "\n".join(
        [
            f"{sender_name} escalated a QC concern to Quality Department.",
            f"Accession: {exam.accession_number}",
            f"MRN: {exam.mrn}",
            f"Modality: {exam.modality.code}",
            f"Study: {exam.procedure_name}",
            f"Escalation note: {escalation_text}",
        ]
    )

    for recipient in _quality_representative_recipients_for_exam(exam):
        if recipient.pk == getattr(escalated_by, "pk", None):
            continue

        _create_notification(
            recipient=recipient,
            sender=escalated_by,
            title=f"QC escalation: {exam.accession_number}",
            message=message,
            target_url=target_url,
            category="QC_ESCALATION",
        )


def notify_radiologists_of_qc_decision(*, session, reviewed_by, decision: str):
    exam = session.exam
    reviewer_name = _display_name(reviewed_by)
    target_url = reverse("qc:review", args=[exam.id])

    recipients = User.objects.filter(
        is_active=True,
        role=UserRole.RADIOLOGIST,
        qc_sessions__exam=exam,
    ).filter(
        models.Q(qc_sessions__concern_raised=True) | ~models.Q(qc_sessions__notes="")
    ).distinct()

    message = "\n".join(
        [
            f"{reviewer_name} recorded a QC decision: {decision}.",
            f"Accession: {exam.accession_number}",
            f"MRN: {exam.mrn}",
            f"Modality: {exam.modality.code}",
            f"Study: {exam.procedure_name}",
            f"Review note: {session.notes or 'No additional note.'}",
        ]
    )

    for recipient in recipients:
        if recipient.pk == getattr(reviewed_by, "pk", None):
            continue
        _create_notification(
            recipient=recipient,
            sender=reviewed_by,
            title=f"QC decision update: {exam.accession_number}",
            message=message,
            target_url=target_url,
            category="QC_DECISION",
        )
