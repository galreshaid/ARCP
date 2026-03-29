from __future__ import annotations

from typing import Any

from apps.core.models import Exam, ExamStatus, Facility
from apps.core.services.hl7_message_log import create_hl7_message_log
from apps.core.services.hl7_orm import (
    ORMParser,
    _extract_preface_identifiers,
    _find_existing_exam_for_order,
    _first_component,
    _format_person_name,
    _format_provider,
    _parse_hl7_date,
    _parse_hl7_datetime,
    _resolve_modality_and_procedure,
)


def ingest_siu_message(raw_message: str) -> tuple[Exam, bool, dict[str, Any]]:
    incoming = (raw_message or '').strip()
    if not incoming:
        raise ValueError('HL7 message is empty.')

    normalized_message, order_hint, accession_hint = _extract_preface_identifiers(incoming)
    if not normalized_message:
        raise ValueError('HL7 message is empty.')

    parsed = ORMParser(normalized_message).parse()
    message_info = parsed.get('message_info') or {}
    patient = parsed.get('patient') or {}
    visit = parsed.get('visit') or {}
    schedule = parsed.get('schedule') or {}
    appointment_service = parsed.get('appointment_service') or {}

    message_type = str(message_info.get('message_type') or '').strip().upper()
    if not message_type.startswith('SIU^'):
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

    order_id = _first_component(
        order_hint
        or schedule.get('placer_appointment_id')
        or schedule.get('filler_appointment_id')
        or ''
    )
    accession_number = _first_component(
        accession_hint
        or schedule.get('filler_appointment_id')
        or schedule.get('placer_appointment_id')
        or order_id
    )
    if not order_id:
        order_id = accession_number
    if not accession_number:
        accession_number = order_id
    if not order_id:
        raise ValueError('The HL7 SIU message does not contain a usable order identifier.')

    procedure_code = _first_component(
        appointment_service.get('procedure_code')
        or schedule.get('procedure_code')
        or ''
    )
    procedure_name = str(
        appointment_service.get('procedure_name')
        or ''
    ).strip()

    existing_exam = _find_existing_exam_for_order(
        order_id=order_id,
        accession_number=accession_number,
        allow_accession_lookup=True,
        prefer_existing_accession=True,
    )

    modality, procedure = _resolve_modality_and_procedure(procedure_code, procedure_name)
    if not modality and existing_exam:
        modality = existing_exam.modality
    if not modality:
        raise ValueError(
            f"Unable to resolve modality for SIU procedure code '{procedure_code}' and name '{procedure_name}'."
        )

    if not procedure_name and procedure:
        procedure_name = procedure.name
    if not procedure_code and existing_exam:
        procedure_code = existing_exam.procedure_code

    patient_name = _format_person_name(patient.get('patient_name'))
    if not patient_name:
        patient_name = getattr(existing_exam, 'patient_name', '') or 'Unknown Patient'

    stored_accession_number = accession_number
    if existing_exam and existing_exam.accession_number and existing_exam.accession_number != order_id:
        stored_accession_number = existing_exam.accession_number

    metadata = dict(getattr(existing_exam, 'metadata', {}) or {})
    schedule_status = str(schedule.get('appointment_status') or '').strip().upper()
    metadata.update({
        'hl7_status': 'SCHEDULE_RECEIVED',
        'hl7_message_type': message_info.get('message_type'),
        'hl7_order_number': order_id,
        'hl7_accession_number': stored_accession_number,
        'hl7_order_request_accession': accession_number,
        'hl7_order_control': '',
        'hl7_order_status': 'SC',
        'hl7_schedule_status': schedule_status,
        'hl7_patient_class': (visit.get('patient_class') or '').strip(),
        'hl7_siu_payload': parsed,
    })

    scheduled_datetime = _parse_hl7_datetime(
        appointment_service.get('start_datetime')
        or schedule.get('start_datetime')
    ) or getattr(existing_exam, 'scheduled_datetime', None)

    exam_defaults = {
        'order_id': order_id,
        'mrn': (
            patient.get('mrn')
            or patient.get('national_id')
            or getattr(existing_exam, 'mrn', '')
            or accession_number
        ).strip(),
        'facility': facility,
        'modality': modality,
        'procedure_code': procedure_code,
        'procedure_name': procedure_name or getattr(existing_exam, 'procedure_name', ''),
        'patient_name': patient_name,
        'patient_dob': _parse_hl7_date(patient.get('dob')) or getattr(existing_exam, 'patient_dob', None),
        'patient_gender': ((patient.get('gender') or getattr(existing_exam, 'patient_gender', '')) or '')[:1],
        'clinical_history': (getattr(existing_exam, 'clinical_history', '') or '').strip(),
        'reason_for_exam': (getattr(existing_exam, 'reason_for_exam', '') or '').strip(),
        'scheduled_datetime': scheduled_datetime,
        'exam_datetime': getattr(existing_exam, 'exam_datetime', None),
        'ordering_provider': (
            _format_provider(visit.get('attending_doctor'))
            or getattr(existing_exam, 'ordering_provider', '')
        ),
        'technologist': getattr(existing_exam, 'technologist', ''),
        'status': ExamStatus.SCHEDULED,
        'hl7_message_control_id': (message_info.get('message_control_id') or '').strip(),
        'raw_hl7_message': incoming,
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
        message_type=message_info.get('message_type') or 'SIU^S12',
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

    return exam, created, parsed
