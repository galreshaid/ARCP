from __future__ import annotations

from typing import Any
from django.utils import timezone

from apps.core.models import Exam, ExamStatus, Facility
from apps.core.services.hl7_message_log import create_hl7_message_log
from apps.core.services.hl7_orm import (
    ORMParser,
    _extract_preface_identifiers,
    _find_existing_exam_for_order,
    _first_component,
    _format_person_name,
    _format_provider,
    _map_exam_status_from_hl7,
    _normalize_hl7_status_code,
    _parse_hl7_date,
    _parse_hl7_datetime,
    _resolve_modality_and_procedure,
)


DEFERRED_ORR_ERROR_PREFIX = 'Deferred ORR update waiting for ORM order '
_ACTIONABLE_RESPONSE_CODES = {
    'IP',
    'INPROGRESS',
    'INPROCESS',
    'STARTED',
    'CM',
    'COMPLETE',
    'COMPLETED',
    'DONE',
    'CA',
    'CANCEL',
    'CANCELLED',
    'DC',
    'DISCONTINUED',
}


def _deferred_orr_message(order_id: str) -> str:
    normalized_order_id = str(order_id or '').strip() or 'UNKNOWN'
    return f'{DEFERRED_ORR_ERROR_PREFIX}{normalized_order_id}.'


def _deferred_orr_order_id(error_message: str) -> str:
    normalized = str(error_message or '').strip()
    if not normalized.startswith(DEFERRED_ORR_ERROR_PREFIX):
        return ''

    order_id = normalized[len(DEFERRED_ORR_ERROR_PREFIX):].strip()
    if order_id.endswith('.'):
        order_id = order_id[:-1].strip()
    return order_id


def _write_orr_log(
    *,
    message_info: dict[str, Any],
    normalized_message: str,
    parsed: dict[str, Any],
    status: str,
    error_message: str = '',
    exam: Exam | None = None,
    linked_message_log=None,
):
    payload = {
        'direction': 'INBOUND',
        'message_type': message_info.get('message_type') or 'ORM^O01',
        'message_control_id': (message_info.get('message_control_id') or '').strip(),
        'raw_message': normalized_message,
        'parsed_data': parsed,
        'exam': exam,
        'status': status,
        'error_message': error_message,
        'sending_application': message_info.get('sending_application') or '',
        'sending_facility': _first_component(message_info.get('sending_facility') or ''),
        'receiving_application': message_info.get('receiving_application') or '',
        'receiving_facility': _first_component(message_info.get('receiving_facility') or ''),
    }
    if linked_message_log is None:
        create_hl7_message_log(**payload)
        return

    linked_message_log.message_type = payload['message_type']
    linked_message_log.message_control_id = payload['message_control_id']
    linked_message_log.raw_message = payload['raw_message']
    linked_message_log.parsed_data = payload['parsed_data']
    linked_message_log.exam = payload['exam']
    linked_message_log.status = payload['status']
    linked_message_log.error_message = payload['error_message']
    linked_message_log.sending_application = payload['sending_application']
    linked_message_log.sending_facility = payload['sending_facility']
    linked_message_log.receiving_application = payload['receiving_application']
    linked_message_log.receiving_facility = payload['receiving_facility']
    linked_message_log.processed_at = timezone.now()
    linked_message_log.save(
        update_fields=[
            'message_type',
            'message_control_id',
            'raw_message',
            'parsed_data',
            'exam',
            'status',
            'error_message',
            'sending_application',
            'sending_facility',
            'receiving_application',
            'receiving_facility',
            'processed_at',
            'updated_at',
        ]
    )


def replay_deferred_orr_messages_for_order(order_id: str) -> int:
    normalized_order_id = str(order_id or '').strip()
    if not normalized_order_id:
        return 0

    try:
        from apps.hl7_core.models import HL7Message
    except Exception:
        return 0

    deferred_logs = HL7Message.objects.filter(
        direction='INBOUND',
        status='RECEIVED',
        error_message__startswith=DEFERRED_ORR_ERROR_PREFIX,
    ).order_by('created_at')

    replayed_count = 0
    for deferred_log in deferred_logs:
        deferred_order_id = _deferred_orr_order_id(deferred_log.error_message)
        if deferred_order_id != normalized_order_id:
            continue

        try:
            exam, _, _ = ingest_orr_message(
                deferred_log.raw_message,
                allow_defer=False,
                linked_message_log=deferred_log,
            )
        except Exception as exc:
            deferred_log.status = 'ERROR'
            deferred_log.error_message = (
                f'{deferred_log.error_message} Replay failed: {exc}'
            ).strip()
            deferred_log.processed_at = timezone.now()
            deferred_log.save(update_fields=['status', 'error_message', 'processed_at', 'updated_at'])
            continue

        if exam is not None:
            replayed_count += 1

    return replayed_count


def ingest_orr_message(
    raw_message: str,
    *,
    allow_defer: bool = True,
    linked_message_log=None,
) -> tuple[Exam | None, bool, dict[str, Any]]:
    incoming = (raw_message or '').strip()
    if not incoming:
        raise ValueError('HL7 message is empty.')

    normalized_message, order_hint, accession_hint = _extract_preface_identifiers(incoming)
    if not normalized_message:
        raise ValueError('HL7 message is empty.')

    parsed = ORMParser(normalized_message).parse()
    message_info = parsed.get('message_info') or {}
    order = parsed.get('order') or {}
    visit = parsed.get('visit') or {}
    observation = parsed.get('observation_request') or {}
    patient = parsed.get('patient') or {}

    order_control = (order.get('order_control') or '').strip().upper()
    order_status = (order.get('order_status') or '').strip().upper()
    actionable_status_codes = {
        _normalize_hl7_status_code(order_status),
        _normalize_hl7_status_code(order_control),
    }
    has_actionable_status = bool(
        actionable_status_codes.intersection(_ACTIONABLE_RESPONSE_CODES)
    )

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
        or order.get('filler_order_number')
        or observation.get('filler_order_number')
        or ''
    )
    if not accession_number:
        raise ValueError('The HL7 ORR message does not contain a usable accession number.')

    order_id = _first_component(
        order_hint
        or order.get('placer_order_number')
        or observation.get('placer_order_number')
        or accession_number
    )

    patient_name = _format_person_name(patient.get('patient_name'))
    if not patient_name:
        patient_name = 'Unknown Patient'

    target_accession = accession_number
    exam = _find_existing_exam_for_order(
        order_id=order_id,
        accession_number=accession_number,
        allow_accession_lookup=False,
        prefer_existing_accession=False,
    )
    mapped_exam_status = _map_exam_status_from_hl7(
        order_control=order_control,
        order_status=order_status,
        fallback=ExamStatus.SCHEDULED,
        actionable_response_only=True,
    )
    if exam is None and not has_actionable_status:
        error_message = f'No existing ORM exam found for order {order_id}. ORR only updates the accession number.'
        if allow_defer:
            _write_orr_log(
                message_info=message_info,
                normalized_message=normalized_message,
                parsed=parsed,
                status='RECEIVED',
                error_message=_deferred_orr_message(order_id),
                linked_message_log=linked_message_log,
            )
            return None, False, parsed

        _write_orr_log(
            message_info=message_info,
            normalized_message=normalized_message,
            parsed=parsed,
            status='REJECTED',
            error_message=error_message,
            linked_message_log=linked_message_log,
        )
        raise ValueError(error_message)

    created = False
    if exam is None:
        exam = Exam(
            accession_number=accession_number,
            order_id=order_id,
        )
        created = True

    existing_with_accession = Exam.objects.filter(accession_number=target_accession).exclude(pk=exam.pk).first()
    if existing_with_accession:
        error_message = (
            f'Accession {target_accession} is already linked to order '
            f'{existing_with_accession.order_id}.'
        )
        _write_orr_log(
            message_info=message_info,
            normalized_message=normalized_message,
            parsed=parsed,
            exam=exam,
            status='REJECTED',
            error_message=error_message,
            linked_message_log=linked_message_log,
        )
        raise ValueError(error_message)

    metadata = dict(getattr(exam, 'metadata', {}) or {})
    diagnosis_code = (observation.get('diagnosis_code') or '').strip()
    diagnosis_description = (observation.get('diagnosis_description') or '').strip()
    diagnosis_system = (observation.get('diagnosis_coding_system') or '').strip()
    metadata.update({
        'hl7_status': 'RESPONSE_RECEIVED',
        'hl7_message_type': message_info.get('message_type') or 'ORM^O01',
        'hl7_order_control': order_control,
        'hl7_order_number': order_id,
        'hl7_accession_number': accession_number,
        'hl7_patient_class': (visit.get('patient_class') or '').strip(),
        'hl7_icd10_code': diagnosis_code or str(metadata.get('hl7_icd10_code') or '').strip(),
        'hl7_icd10_description': (
            diagnosis_description or str(metadata.get('hl7_icd10_description') or '').strip()
        ),
        'hl7_icd10_system': diagnosis_system or str(metadata.get('hl7_icd10_system') or '').strip(),
        'hl7_response_payload': parsed,
        'hl7_last_response_raw_message': normalized_message,
    })
    metadata['hl7_response_order_status_raw'] = order_status
    if has_actionable_status:
        metadata['hl7_order_status'] = order_status

    exam.accession_number = target_accession
    exam.order_id = order_id
    exam.mrn = (patient.get('mrn') or patient.get('national_id') or accession_number).strip()
    exam.facility = facility
    exam.modality = modality
    exam.procedure_code = procedure_code
    exam.procedure_name = procedure_name or (procedure.name if procedure else '')
    exam.patient_name = patient_name
    exam.patient_dob = _parse_hl7_date(patient.get('dob'))
    exam.patient_gender = (patient.get('gender') or '')[:1]
    exam.clinical_history = (observation.get('clinical_history') or exam.clinical_history or '').strip()
    exam.reason_for_exam = (
        observation.get('reason_for_study')
        or observation.get('clinical_history')
        or exam.reason_for_exam
        or ''
    ).strip()
    exam.scheduled_datetime = _parse_hl7_datetime(
        observation.get('requested_datetime') or order.get('order_datetime')
    ) or exam.scheduled_datetime
    exam.ordering_provider = _format_provider(order.get('ordering_provider')) or exam.ordering_provider
    completion_timestamp = (
        _parse_hl7_datetime(order.get('order_datetime') or observation.get('requested_datetime'))
        or exam.exam_datetime
        or timezone.now()
    )
    mapped_exam_status = _map_exam_status_from_hl7(
        order_control=order_control,
        order_status=order_status,
        fallback=exam.status or mapped_exam_status or ExamStatus.SCHEDULED,
        actionable_response_only=True,
    )
    exam.status = mapped_exam_status
    if mapped_exam_status == ExamStatus.COMPLETED:
        exam.exam_datetime = completion_timestamp
        if getattr(exam, 'protocol_assignment', None):
            metadata['protocol_workflow_status'] = 'DONE'
            metadata['protocol_completed_without_assignment'] = False
            assignment = exam.protocol_assignment
            if assignment.status != 'DONE':
                assignment.status = 'DONE'
                assignment.save(update_fields=['status'])
        else:
            metadata['protocol_workflow_status'] = 'CLOSED'
            metadata['protocol_completed_without_assignment'] = True

        metadata['protocol_completion_source'] = 'HL7_CM'
        metadata['protocol_completed_at'] = completion_timestamp.isoformat()
    exam.hl7_message_control_id = (message_info.get('message_control_id') or '').strip()
    if not exam.raw_hl7_message:
        exam.raw_hl7_message = normalized_message
    exam.metadata = metadata
    exam.save()

    _write_orr_log(
        message_info=message_info,
        normalized_message=normalized_message,
        parsed=parsed,
        exam=exam,
        status='PROCESSED',
        linked_message_log=linked_message_log,
    )

    return exam, created, parsed
