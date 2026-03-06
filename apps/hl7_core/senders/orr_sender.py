from __future__ import annotations

import secrets
from datetime import datetime

from django.utils import timezone


def _hl7_escape(value: str | None) -> str:
    text = str(value or '').strip()
    return text.replace('|', '\\F\\').replace('^', '\\S\\')


def _hl7_timestamp(value: datetime | None = None) -> str:
    current = value or timezone.now()
    local_value = timezone.localtime(current) if timezone.is_aware(current) else current
    return local_value.strftime('%Y%m%d%H%M%S')


def _message_control_id(prefix: str = 'AIP') -> str:
    return f'{prefix}{secrets.randbelow(900000000) + 100000000}'


def _first_component(value: str | None) -> str:
    return str(value or '').split('^', 1)[0].strip()


def _hl7_name_from_exam(exam) -> str:
    patient_name = (exam.patient_name or '').strip()
    if not patient_name:
        return 'UNKNOWN^PATIENT'

    parts = [part for part in patient_name.split() if part]
    if len(parts) == 1:
        return f'{_hl7_escape(parts[0])}^'

    family = parts[-1]
    given = ' '.join(parts[:-1])
    return f'{_hl7_escape(family)}^{_hl7_escape(given)}'


def _hl7_gender(value: str | None) -> str:
    normalized = (value or '').strip().upper()
    if normalized == 'M':
        return 'M'
    if normalized == 'F':
        return 'F'
    return ''


def _hl7_date(value) -> str:
    if not value:
        return ''
    return value.strftime('%Y%m%d')


def _facility_identifiers(exam) -> tuple[str, str]:
    facility = getattr(exam, 'facility', None)
    facility_code = getattr(facility, 'code', '') or 'UNKNOWN'
    metadata = getattr(exam, 'metadata', {}) or {}
    hl7_facility = getattr(facility, 'hl7_facility_id', '') or facility_code
    sending_facility = _hl7_escape(facility_code)
    receiving_facility = _hl7_escape(str(metadata.get('hl7_source_facility') or hl7_facility))
    return sending_facility, receiving_facility


def _order_and_accession(exam, order_number: str | None = None, accession_number: str | None = None) -> tuple[str, str]:
    metadata = getattr(exam, 'metadata', {}) or {}
    resolved_order = _first_component(order_number or exam.order_id or metadata.get('hl7_order_number') or exam.accession_number)
    resolved_accession = _first_component(accession_number or metadata.get('hl7_accession_number') or exam.accession_number or resolved_order)
    return resolved_order, resolved_accession


def build_exam_orm(
    exam,
    *,
    accession_number: str | None = None,
    order_number: str | None = None,
    message_control_id: str | None = None,
) -> tuple[str, str]:
    order_id, accession = _order_and_accession(
        exam,
        order_number=order_number,
        accession_number=accession_number,
    )
    sending_facility, receiving_facility = _facility_identifiers(exam)
    message_id = message_control_id or _message_control_id('ORM')
    message_time = _hl7_timestamp()
    requested_time = _hl7_timestamp(
        getattr(exam, 'scheduled_datetime', None)
        or getattr(exam, 'exam_datetime', None)
        or timezone.now()
    )

    procedure_code = _hl7_escape(getattr(exam, 'procedure_code', '') or '')
    procedure_name = _hl7_escape(getattr(exam, 'procedure_name', '') or '')
    clinical_history = _hl7_escape(getattr(exam, 'clinical_history', '') or '')
    ordering_provider = _hl7_escape(getattr(exam, 'ordering_provider', '') or '')

    segments = [
        f'MSH|^~\\&|AIP|{sending_facility}|RIS|{receiving_facility}|{message_time}||ORM^O01|{message_id}|P|2.3.1',
        f'PID|1||{_hl7_escape(exam.mrn)}||{_hl7_name_from_exam(exam)}||{_hl7_date(getattr(exam, "patient_dob", None))}|{_hl7_gender(getattr(exam, "patient_gender", ""))}',
        f'PV1|1||{receiving_facility}',
        f'ORC|NW|{_hl7_escape(order_id)}|{_hl7_escape(accession)}^^^{_hl7_escape(accession)}||NW||1^Once^^{requested_time}||{message_time}|{ordering_provider}',
        (
            f'OBR|1|{_hl7_escape(order_id)}^{_hl7_escape(order_id)}|{_hl7_escape(accession)}|'
            f'{procedure_code}^{procedure_name}||{requested_time}|||||||{clinical_history}'
        ),
    ]
    return message_id, '\r'.join(segments)


def build_protocol_assignment_orr(
    assignment,
    *,
    accession_number: str | None = None,
    order_number: str | None = None,
    message_control_id: str | None = None,
    message_type: str = 'ORR^O02',
    response_code: str = 'SC',
) -> tuple[str, str]:
    exam = assignment.exam
    protocol = assignment.protocol
    order_id, accession = _order_and_accession(
        exam,
        order_number=order_number,
        accession_number=accession_number,
    )
    sending_facility, receiving_facility = _facility_identifiers(exam)
    message_id = message_control_id or _message_control_id('ORR')
    message_time = _hl7_timestamp()
    scheduled_time = _hl7_timestamp(
        getattr(exam, 'scheduled_datetime', None)
        or getattr(exam, 'exam_datetime', None)
        or timezone.now()
    )

    procedure_code = _hl7_escape(getattr(exam, 'procedure_code', '') or getattr(protocol, 'code', ''))
    procedure_name = _hl7_escape(getattr(exam, 'procedure_name', '') or getattr(protocol, 'name', ''))
    assigned_by = _hl7_escape(getattr(assignment, 'assigned_by', '') or '')
    technologist = _hl7_escape(getattr(exam, 'technologist', '') or '')

    radiologist_note = _hl7_escape(getattr(assignment, 'radiologist_note', '') or '')
    assignment_notes = _hl7_escape(getattr(assignment, 'assignment_notes', '') or '')
    protocol_ref = f'{_hl7_escape(protocol.code)}^{_hl7_escape(protocol.name)}'

    segments = [
        f'MSH|^~\\&|AIP|{sending_facility}|RIS|{receiving_facility}|{message_time}||{message_type}|{message_id}|P|2.3.1|||AL',
        f'PID|1||{_hl7_escape(exam.mrn)}||{_hl7_name_from_exam(exam)}||{_hl7_date(getattr(exam, "patient_dob", None))}|{_hl7_gender(getattr(exam, "patient_gender", ""))}',
        f'PV1|1||{receiving_facility}',
        (
            f'ORC|{response_code}|{_hl7_escape(order_id)}|{_hl7_escape(accession)}^^^{_hl7_escape(accession)}||'
            f'{response_code}||^^^{scheduled_time}||30|||{sending_facility}|{assigned_by}'
        ),
        (
            f'OBR|1|{_hl7_escape(order_id)}^{_hl7_escape(order_id)}|{_hl7_escape(accession)}|'
            f'{procedure_code}^{procedure_name}||{scheduled_time}|||||||||||||{_hl7_escape(accession)}'
        ),
        f'NTE|1|L|Protocol Assigned: {protocol_ref}',
    ]

    if radiologist_note:
        segments.append(f'NTE|2|L|Radiologist Note: {radiologist_note}')
    if assignment_notes:
        segments.append(f'NTE|3|L|Assignment Notes: {assignment_notes}')
    if technologist:
        segments.append(f'NTE|4|L|Technologist: {technologist}')

    return message_id, '\r'.join(segments)


def send_protocol_assignment_orr(
    assignment,
    *,
    accession_number: str | None = None,
    order_number: str | None = None,
) -> str:
    message_id, message = build_protocol_assignment_orr(
        assignment,
        accession_number=accession_number,
        order_number=order_number,
    )

    metadata = dict(getattr(assignment, 'metadata', {}) or {})
    metadata['last_outbound_hl7'] = {
        'message_type': 'ORR^O02',
        'message_control_id': message_id,
        'raw_message': message,
        'built_at': timezone.now().isoformat(),
    }
    assignment.metadata = metadata
    assignment.save(update_fields=['metadata'])

    return message_id
