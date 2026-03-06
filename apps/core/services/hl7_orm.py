from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from apps.core.models import Exam, ExamStatus, Facility, Modality, Procedure
from apps.core.services.hl7_message_log import create_hl7_message_log
from apps.hl7_core.parsers.orm_parser import ORMParser


MODALITY_PREFIX_MAP = {
    'CT': 'CT',
    'MR': 'MR',
    'MRI': 'MR',
    'XR': 'XR',
    'XRAY': 'XR',
    'X-RAY': 'XR',
    'US': 'US',
    'ULTRASOUND': 'US',
    'NM': 'NM',
    'MG': 'MG',
    'MAMMO': 'MG',
    'MAMMOGRAPHY': 'MG',
    'FL': 'FL',
    'FLUORO': 'FL',
    'FLUOROSCOPY': 'FL',
    'DXA': 'DXA',
    'BMD': 'DXA',
}
HL7_SEGMENT_RE = re.compile(r'^[A-Z0-9]{3}\|')

_HL7_COMPLETED_CODES = {'CM', 'COMPLETE', 'COMPLETED', 'DONE'}
_HL7_ORDER_CODES = {'NW', 'ORDER'}
_HL7_IN_PROGRESS_CODES = {'IP', 'INPROGRESS', 'INPROCESS', 'STARTED', 'XO', 'OK', 'ACTIVE'}
_HL7_SCHEDULED_CODES = {'SC', 'SCHEDULED', 'SCHEDULE', 'SCH'}
_HL7_ARRIVED_CODES = {'AR', 'ARRIVED'}
_HL7_CANCELLED_CODES = {'CA', 'CANCEL', 'CANCELLED', 'DC', 'DISCONTINUED'}
_HL7_NO_SHOW_CODES = {'NS', 'NOSHOW'}


def _first_component(value: str) -> str:
    return (value or '').split('^', 1)[0].strip()


def _normalize_hl7_status_code(value: str | None) -> str:
    return str(value or '').strip().upper().replace('-', '').replace('_', '').replace(' ', '')


def _map_exam_status_from_hl7(
    *,
    order_control: str | None,
    order_status: str | None,
    fallback: str = ExamStatus.SCHEDULED,
) -> str:
    for raw_code in (order_status, order_control):
        code = _normalize_hl7_status_code(raw_code)
        if not code:
            continue
        if code in _HL7_ORDER_CODES:
            return ExamStatus.ORDER
        if code in _HL7_COMPLETED_CODES:
            return ExamStatus.COMPLETED
        if code in _HL7_IN_PROGRESS_CODES:
            return ExamStatus.IN_PROGRESS
        if code in _HL7_SCHEDULED_CODES:
            return ExamStatus.SCHEDULED
        if code in _HL7_ARRIVED_CODES:
            return ExamStatus.ARRIVED
        if code in _HL7_CANCELLED_CODES:
            return ExamStatus.CANCELLED
        if code in _HL7_NO_SHOW_CODES:
            return ExamStatus.NO_SHOW
    return fallback


def _extract_preface_identifiers(raw_message: str) -> tuple[str, str | None, str | None]:
    normalized = (raw_message or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = []
    order_hint = None
    accession_hint = None

    for raw_line in normalized.split('\n'):
        line = raw_line.strip()
        if not line:
            continue

        lower_line = line.lower()
        if not HL7_SEGMENT_RE.match(line):
            if lower_line.startswith('order'):
                order_hint = line.split('#', 1)[-1].split(':', 1)[-1].strip()
                continue
            if lower_line.startswith('acc'):
                accession_hint = line.split('#', 1)[-1].split(':', 1)[-1].strip()
                continue
            if lower_line in {'orm message', 'orr message'}:
                continue

        lines.append(line)

    return '\r'.join(lines), order_hint or None, accession_hint or None


def _parse_hl7_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    parsed = datetime.fromisoformat(value)
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _parse_hl7_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _format_person_name(parts: dict[str, Any] | None, family_first: bool = False) -> str:
    if not parts:
        return ''

    ordered = ['family', 'given', 'middle'] if family_first else ['given', 'middle', 'family']
    values = [str(parts.get(key, '')).strip() for key in ordered if str(parts.get(key, '')).strip()]
    return ' '.join(values)


def _format_provider(parts: dict[str, Any] | None) -> str:
    if not parts:
        return ''
    values = [
        str(parts.get('given_name', '')).strip(),
        str(parts.get('middle_name', '')).strip(),
        str(parts.get('family_name', '')).strip(),
    ]
    return ' '.join(value for value in values if value)


def _resolve_modality_and_procedure(procedure_code: str, procedure_name: str) -> tuple[Modality | None, Procedure | None]:
    procedure = None
    if procedure_code:
        procedure = Procedure.objects.select_related('modality').filter(code=procedure_code).first()
        if procedure:
            return procedure.modality, procedure

    tokens = re.split(r'[\s/_-]+', (procedure_name or '').upper())
    for token in tokens:
        modality_code = MODALITY_PREFIX_MAP.get(token)
        if not modality_code:
            continue

        modality = Modality.objects.filter(code=modality_code).first()
        if modality:
            return modality, None

    return None, None


def _find_existing_exam_for_order(
    *,
    order_id: str,
    accession_number: str | None = None,
    allow_accession_lookup: bool = True,
    prefer_existing_accession: bool = False,
) -> Exam | None:
    if order_id:
        order_matches = list(Exam.objects.filter(order_id=order_id).order_by('created_at'))
        if order_matches:
            if accession_number:
                for exam in order_matches:
                    if exam.accession_number == accession_number:
                        return exam

            if prefer_existing_accession:
                for exam in order_matches:
                    if exam.accession_number and exam.accession_number != order_id:
                        return exam

            for exam in order_matches:
                if exam.accession_number == order_id:
                    return exam

            return order_matches[0]

        placeholder_exam = Exam.objects.filter(accession_number=order_id).order_by('created_at').first()
        if placeholder_exam:
            return placeholder_exam

    if allow_accession_lookup and accession_number:
        return Exam.objects.filter(accession_number=accession_number).order_by('created_at').first()

    return None


def ingest_orm_message(raw_message: str) -> tuple[Exam, bool, dict[str, Any]]:
    raw_message = (raw_message or '').strip()
    if not raw_message:
        raise ValueError('HL7 message is empty.')

    normalized_message, order_hint, accession_hint = _extract_preface_identifiers(raw_message)
    if not normalized_message:
        raise ValueError('HL7 message is empty.')

    parsed = ORMParser(normalized_message).parse()
    message_info = parsed.get('message_info') or {}
    order = parsed.get('order') or {}
    visit = parsed.get('visit') or {}
    observation = parsed.get('observation_request') or {}
    patient = parsed.get('patient') or {}
    mapped_exam_status = _map_exam_status_from_hl7(
        order_control=order.get('order_control'),
        order_status=order.get('order_status'),
        fallback=ExamStatus.SCHEDULED,
    )

    if message_info.get('message_type') != 'ORM^O01':
        raise ValueError(f"Unsupported message type: {message_info.get('message_type') or 'unknown'}")

    facility_code = _first_component(message_info.get('receiving_facility') or message_info.get('sending_facility'))
    if not facility_code:
        facility_code = 'UNKNOWN'

    facility, _ = Facility.objects.get_or_create(
        code=facility_code,
        defaults={
            'name': facility_code,
            'hl7_facility_id': facility_code,
            'is_active': True,
        },
    )

    procedure_code = (observation.get('procedure_code') or '').strip()
    procedure_name = (observation.get('procedure_name') or '').strip()
    modality, procedure = _resolve_modality_and_procedure(procedure_code, procedure_name)
    if not modality:
        raise ValueError(
            f"Unable to resolve modality for procedure code '{procedure_code}' and name '{procedure_name}'."
        )

    accession_number = _first_component(
        accession_hint
        or observation.get('filler_order_number')
        or order.get('filler_order_number')
        or order.get('placer_order_number')
        or order_hint
    )
    if not accession_number:
        raise ValueError('The HL7 ORM message does not contain a usable order identifier.')

    order_id = _first_component(
        order_hint
        or order.get('placer_order_number')
        or observation.get('placer_order_number')
        or accession_number
    )

    patient_name = _format_person_name(patient.get('patient_name'))
    if not patient_name:
        patient_name = 'Unknown Patient'

    existing_exam = _find_existing_exam_for_order(
        order_id=order_id,
        accession_number=accession_number,
        allow_accession_lookup=True,
        prefer_existing_accession=True,
    )
    stored_accession_number = accession_number
    if existing_exam and existing_exam.accession_number and existing_exam.accession_number != order_id:
        stored_accession_number = existing_exam.accession_number

    metadata = dict(getattr(existing_exam, 'metadata', {}) or {})
    diagnosis_code = (observation.get('diagnosis_code') or '').strip()
    diagnosis_description = (observation.get('diagnosis_description') or '').strip()
    diagnosis_system = (observation.get('diagnosis_coding_system') or '').strip()
    metadata.update({
        'hl7_status': 'RECEIVED',
        'hl7_message_type': message_info.get('message_type'),
        'hl7_order_number': order_id,
        'hl7_accession_number': stored_accession_number,
        'hl7_order_request_accession': accession_number,
        'hl7_order_control': (order.get('order_control') or '').strip(),
        'hl7_order_status': (order.get('order_status') or '').strip(),
        'hl7_order_reason': (order.get('order_reason') or '').strip(),
        'hl7_patient_class': (visit.get('patient_class') or '').strip(),
        'hl7_icd10_code': diagnosis_code or str(metadata.get('hl7_icd10_code') or '').strip(),
        'hl7_icd10_description': (
            diagnosis_description or str(metadata.get('hl7_icd10_description') or '').strip()
        ),
        'hl7_icd10_system': diagnosis_system or str(metadata.get('hl7_icd10_system') or '').strip(),
        'hl7_payload': parsed,
    })

    exam_defaults = {
        'order_id': order_id,
        'mrn': (patient.get('mrn') or patient.get('national_id') or accession_number).strip(),
        'facility': facility,
        'modality': modality,
        'procedure_code': procedure_code,
        'procedure_name': procedure_name or (procedure.name if procedure else ''),
        'patient_name': patient_name,
        'patient_dob': _parse_hl7_date(patient.get('dob')),
        'patient_gender': (patient.get('gender') or '')[:1],
        'clinical_history': (
            observation.get('clinical_history')
            or getattr(existing_exam, 'clinical_history', '')
            or ''
        ).strip(),
        'reason_for_exam': (
            order.get('order_reason')
            or observation.get('reason_for_study')
            or observation.get('clinical_history')
            or getattr(existing_exam, 'reason_for_exam', '')
            or ''
        ).strip(),
        'scheduled_datetime': _parse_hl7_datetime(
            observation.get('requested_datetime') or order.get('order_datetime')
        ),
        'exam_datetime': None,
        'ordering_provider': _format_provider(order.get('ordering_provider')),
        'technologist': '',
        'status': mapped_exam_status,
        'hl7_message_control_id': (message_info.get('message_control_id') or '').strip(),
        'raw_hl7_message': raw_message,
        'metadata': metadata,
    }

    if existing_exam:
        exam = existing_exam
        created = False
        exam.accession_number = stored_accession_number
        for field_name, value in exam_defaults.items():
            setattr(exam, field_name, value)
        exam.save()
    else:
        exam = Exam.objects.create(
            accession_number=stored_accession_number,
            **exam_defaults,
        )
        created = True

    create_hl7_message_log(
        direction='INBOUND',
        message_type=message_info.get('message_type') or 'ORM^O01',
        message_control_id=(message_info.get('message_control_id') or '').strip(),
        raw_message=normalized_message,
        parsed_data=parsed,
        exam=exam,
        status='PROCESSED',
        sending_application=message_info.get('sending_application') or '',
        sending_facility=_first_component(message_info.get('sending_facility') or ''),
        receiving_application=message_info.get('receiving_application') or '',
        receiving_facility=_first_component(message_info.get('receiving_facility') or ''),
    )

    try:
        from apps.core.services.hl7_orr import replay_deferred_orr_messages_for_order
    except Exception:
        replay_deferred_orr_messages_for_order = None

    if replay_deferred_orr_messages_for_order is not None:
        replayed_count = replay_deferred_orr_messages_for_order(order_id)
        if replayed_count:
            exam.refresh_from_db()

    return exam, created, parsed
