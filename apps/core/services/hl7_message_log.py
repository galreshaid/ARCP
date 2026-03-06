from __future__ import annotations

from typing import Any

from django.utils import timezone


def create_hl7_message_log(
    *,
    direction: str,
    message_type: str,
    message_control_id: str,
    raw_message: str,
    parsed_data: dict[str, Any] | None = None,
    exam=None,
    status: str = 'PROCESSED',
    sending_application: str = '',
    sending_facility: str = '',
    receiving_application: str = '',
    receiving_facility: str = '',
    error_message: str = '',
):
    try:
        from apps.hl7_core.models import HL7Message
    except Exception:
        return None

    try:
        return HL7Message.objects.create(
            direction=direction,
            message_type=message_type or 'UNKNOWN',
            message_control_id=message_control_id or '',
            raw_message=raw_message or '',
            parsed_data=parsed_data or {},
            status=status,
            error_message=error_message,
            exam=exam,
            sending_application=sending_application or '',
            sending_facility=sending_facility or '',
            receiving_application=receiving_application or '',
            receiving_facility=receiving_facility or '',
            processed_at=timezone.now(),
        )
    except Exception:
        return None
