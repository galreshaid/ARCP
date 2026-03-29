"""
Core Views
"""
import csv
import json
from collections import Counter
from datetime import datetime, time, timedelta
from urllib.parse import quote, urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import FieldDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.forms import modelform_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core.constants import Permission, UserRole, PROTOCOL_REQUIRED_MODALITY_CODES
from apps.core.forms import SystemAdminModalityForm, SystemAdminProcedureForm
from apps.core.models import (
    ContrastUsage,
    Exam,
    ExamStatus,
    Facility,
    MaterialCategory,
    MaterialCatalog,
    MaterialMeasurement,
    MaterialUsage,
    Modality,
    ProcedureMaterialBundle,
    ProcedureMaterialBundleItem,
    Procedure,
)
from apps.core.services.subspeciality import (
    append_subspeciality_change_event,
    normalize_subspeciality,
    resolve_exam_subspeciality,
)
from apps.core.services.subspeciality import SUBSPECIALITY_POOL
from apps.core.services.facility_scope import (
    apply_facility_scope,
    can_access_facility,
    scoped_facility_ids,
)
from apps.hl7_core.parsers.orm_parser import ORMParser
from apps.hl7_core.models import HL7Message
from apps.protocols.models import ProtocolTemplate, ProtocolAssignment
from apps.users.decorators import app_permission_required
from apps.users.forms import SystemAdminUserForm
from apps.users.models import User, UserSession, UserPreference


SYSTEM_ADMIN_RESOURCES = {
    'exams': {
        'label': 'Exams',
        'model': Exam,
        'list_fields': ('accession_number', 'patient_name', 'modality', 'facility', 'status'),
        'form_fields': (
            'accession_number',
            'order_id',
            'mrn',
            'facility',
            'modality',
            'procedure_code',
            'procedure_name',
            'patient_name',
            'patient_dob',
            'patient_gender',
            'clinical_history',
            'reason_for_exam',
            'scheduled_datetime',
            'exam_datetime',
            'ordering_provider',
            'technologist',
            'status',
            'hl7_message_control_id',
            'raw_hl7_message',
            'metadata',
        ),
        'search_fields': ('accession_number', 'mrn', 'patient_name', 'procedure_name'),
        'ordering': ('-exam_datetime', '-scheduled_datetime'),
        'description': 'Orders, patient context, and study status.',
        'allow_create': True,
    },
    'assignments': {
        'label': 'Protocol Assignments',
        'model': ProtocolAssignment,
        'list_fields': ('exam', 'protocol', 'assignment_method', 'status', 'assigned_by'),
        'form_fields': (
            'exam',
            'protocol',
            'assigned_by',
            'assignment_method',
            'status',
            'radiologist_note',
            'technologist_note',
            'assignment_notes',
        ),
        'search_fields': ('exam__accession_number', 'protocol__code', 'protocol__name'),
        'ordering': ('-created_at',),
        'description': 'Manual and AI-driven protocol decisions.',
        'allow_create': True,
    },
    'contrast_usages': {
        'label': 'Contrast Usages',
        'model': ContrastUsage,
        'list_fields': (
            'exam',
            'pec_number',
            'contrast_name',
            'route',
            'volume_ml',
            'concentration_mg_ml',
            'total_mg',
            'created_at',
        ),
        'form_fields': (
            'exam',
            'pec_number',
            'contrast_name',
            'concentration_mg_ml',
            'volume_ml',
            'injection_rate_ml_s',
            'route',
            'lot_number',
            'expiry_date',
            'patient_weight_kg',
            'metadata',
        ),
        'search_fields': ('exam__accession_number', 'exam__order_id', 'pec_number', 'contrast_name', 'lot_number'),
        'ordering': ('-created_at',),
        'description': 'Documented contrast administrations with auto dose calculation.',
        'allow_create': True,
    },
    'material_usages': {
        'label': 'Material Usages',
        'model': MaterialUsage,
        'list_fields': (
            'exam',
            'pec_number',
            'material_item',
            'material_name',
            'measurement',
            'unit',
            'quantity',
            'created_at',
        ),
        'form_fields': (
            'exam',
            'pec_number',
            'material_item',
            'material_name',
            'measurement',
            'unit',
            'quantity',
            'metadata',
        ),
        'search_fields': (
            'exam__accession_number',
            'exam__order_id',
            'pec_number',
            'material_name',
            'material_item__name',
        ),
        'ordering': ('-created_at',),
        'description': 'Consumable material records tied to exam and PEC.',
        'allow_create': True,
    },
    'material_catalog': {
        'label': 'Material Catalog',
        'model': MaterialCatalog,
        'list_fields': (
            'material_code',
            'name',
            'category',
            'unit',
            'charge_code',
            'nphies_code',
            'billable',
            'cost_center_only',
            'is_active',
        ),
        'form_fields': (
            'material_code',
            'name',
            'category',
            'unit',
            'pack_size',
            'modality_scope',
            'procedure_mapping_tags',
            'charge_code',
            'billing_ref_example',
            'nphies_code',
            'typical_cost_sar',
            'default_price_sar',
            'billable',
            'cost_center_only',
            'reorder_level',
            'notes',
            'default_measurement',
            'is_active',
            'metadata',
        ),
        'search_fields': ('material_code', 'name', 'charge_code', 'nphies_code', 'procedure_mapping_tags'),
        'ordering': ('name',),
        'description': 'Master consumables list with charge, billing, and NPHIES details.',
        'allow_create': True,
    },
    'material_measurements': {
        'label': 'Material Measurements',
        'model': MaterialMeasurement,
        'list_fields': ('code', 'label', 'is_active'),
        'form_fields': ('code', 'label', 'is_active', 'metadata'),
        'search_fields': ('code', 'label'),
        'ordering': ('code',),
        'description': 'Admin-managed measurement units for consumables and materials.',
        'allow_create': True,
    },
    'procedure_material_bundles': {
        'label': 'Procedure Material Bundles',
        'model': ProcedureMaterialBundle,
        'list_fields': ('procedure_code', 'procedure_name', 'modality_scope', 'is_active'),
        'form_fields': (
            'procedure',
            'procedure_code',
            'procedure_name',
            'modality_scope',
            'rules_notes',
            'is_active',
            'metadata',
        ),
        'search_fields': ('procedure_code', 'procedure_name', 'modality_scope', 'rules_notes'),
        'ordering': ('procedure_code',),
        'description': 'Default material bundles mapped to procedure codes.',
        'allow_create': True,
    },
    'procedure_material_bundle_items': {
        'label': 'Procedure Bundle Items',
        'model': ProcedureMaterialBundleItem,
        'list_fields': ('bundle', 'material', 'material_code', 'quantity', 'sort_order', 'is_optional'),
        'form_fields': (
            'bundle',
            'material',
            'material_code',
            'quantity',
            'sort_order',
            'is_optional',
            'notes',
            'metadata',
        ),
        'search_fields': ('bundle__procedure_code', 'bundle__procedure_name', 'material__name', 'material_code'),
        'ordering': ('bundle__procedure_code', 'sort_order', 'id'),
        'description': 'Item-level quantities for each procedure consumables bundle.',
        'allow_create': True,
    },
    'facilities': {
        'label': 'Facilities',
        'model': Facility,
        'list_fields': ('code', 'name', 'hl7_facility_id', 'qc_service_desk_email', 'is_active'),
        'form_fields': (
            'code',
            'name',
            'hl7_facility_id',
            'address',
            'contact_email',
            'contact_phone',
            'qc_service_desk_email',
            'is_active',
            'config_json',
        ),
        'search_fields': ('code', 'name', 'hl7_facility_id'),
        'ordering': ('name',),
        'description': 'Hospitals, sites, and integration metadata.',
        'allow_create': True,
    },
    'modalities': {
        'label': 'Modalities',
        'model': Modality,
        'list_fields': ('code', 'name', 'requires_qc', 'requires_contrast', 'is_active'),
        'form_class': SystemAdminModalityForm,
        'form_fields': (
            'code',
            'name',
            'description',
            'is_active',
            'requires_qc',
            'requires_contrast',
            'qc_checklist_template',
        ),
        'search_fields': ('code', 'name', 'description'),
        'ordering': ('code',),
        'description': 'Imaging device types, QC settings, contrast visibility, and Protocol Worklist visibility control.',
        'allow_create': True,
    },
    'procedures': {
        'label': 'Procedures',
        'model': Procedure,
        'list_fields': ('code', 'name', 'modality', 'body_region', 'is_active'),
        'form_class': SystemAdminProcedureForm,
        'form_fields': ('code', 'name', 'modality', 'body_region', 'is_active', 'metadata'),
        'search_fields': ('code', 'name'),
        'ordering': ('modality__code', 'code'),
        'description': 'RIS procedure dictionary for exam matching and Protocol Worklist visibility.',
        'allow_create': True,
    },
    'protocols': {
        'label': 'Protocol Templates',
        'model': ProtocolTemplate,
        'list_fields': ('code', 'name', 'modality', 'procedure', 'is_active'),
        'form_fields': (
            'code',
            'name',
            'facility',
            'modality',
            'procedure',
            'body_part',
            'body_region',
            'laterality',
            'is_active',
            'is_default',
            'priority',
            'requires_contrast',
            'contrast_type',
            'contrast_phase',
            'contrast_notes',
            'indications',
            'patient_prep',
            'contraindications',
            'safety_notes',
            'post_processing',
            'general_notes',
            'clinical_keywords',
            'technical_parameters',
            'tags',
            'metadata',
        ),
        'search_fields': ('code', 'name', 'body_part', 'body_region'),
        'ordering': ('modality__code', 'priority', 'code'),
        'description': 'Master protocol definitions.',
        'allow_create': True,
    },
    'users': {
        'label': 'Users',
        'model': User,
        'list_fields': ('email', 'username', 'role', 'professional_id', 'nid', 'is_active', 'is_staff'),
        'search_fields': ('email', 'username', 'first_name', 'last_name', 'professional_id', 'nid', 'specialty'),
        'ordering': ('email',),
        'description': 'User accounts, access, and role configuration.',
        'form_class': SystemAdminUserForm,
        'allow_create': True,
    },
    'groups': {
        'label': 'Groups',
        'model': Group,
        'list_fields': ('name',),
        'form_fields': ('name', 'permissions'),
        'search_fields': ('name',),
        'ordering': ('name',),
        'description': 'Role templates and custom permission groups.',
        'allow_create': True,
    },
    'sessions': {
        'label': 'User Sessions',
        'model': UserSession,
        'list_fields': ('user', 'ip_address', 'login_at', 'logout_at', 'is_active'),
        'form_fields': ('user', 'ip_address', 'user_agent', 'logout_at', 'is_active'),
        'search_fields': ('user__email', 'ip_address', 'session_key'),
        'ordering': ('-login_at',),
        'description': 'Session tracking for audit and support.',
        'allow_create': False,
    },
    'preferences': {
        'label': 'User Preferences',
        'model': UserPreference,
        'list_fields': ('user', 'preference_type', 'preference_key'),
        'form_fields': ('user', 'preference_type', 'preference_key', 'preference_value'),
        'search_fields': ('user__email', 'preference_key'),
        'ordering': ('user__email', 'preference_type', 'preference_key'),
        'description': 'Saved defaults and display settings.',
        'allow_create': True,
    },
    'hl7_messages': {
        'label': 'HL7 Message Logs',
        'model': HL7Message,
        'list_fields': (
            'created_at',
            'message_type',
            'message_control_id',
            'exam_order_number',
            'exam_accession_number',
            'status',
            'direction',
        ),
        'search_fields': (
            'message_control_id',
            'message_type',
            'exam__order_id',
            'exam__accession_number',
            'sending_facility',
            'receiving_facility',
        ),
        'ordering': ('-created_at',),
        'description': 'Inbound and outbound HL7 transaction history.',
        'allow_create': False,
        'allow_edit': False,
    },
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

SYSTEM_ADMIN_SECTIONS = (
    (
        'Operational Workflows',
        'Live operational records and workflow state.',
        ('exams', 'assignments', 'contrast_usages', 'material_usages'),
    ),
    (
        'Clinical Reference Data',
        'Master data used by protocoling and routing logic.',
        (
            'facilities',
            'modalities',
            'procedures',
            'protocols',
            'material_catalog',
            'material_measurements',
            'procedure_material_bundles',
            'procedure_material_bundle_items',
        ),
    ),
    (
        'Access & Preferences',
        'User access, sessions, and stored preferences.',
        ('users', 'groups', 'sessions', 'preferences'),
    ),
    (
        'Integration & Messaging',
        'HL7 traffic history and interface audit visibility.',
        ('hl7_messages',),
    ),
)


def _resource_urls(resource_key):
    return {
        'list_url': reverse('system-admin-resource-list', args=[resource_key]),
        'create_url': reverse('system-admin-resource-create', args=[resource_key]),
    }


def _get_resource_config(resource_key):
    config = SYSTEM_ADMIN_RESOURCES.get(resource_key)
    if not config:
        raise KeyError(resource_key)
    return config


def _get_form_class(config):
    form_class = config.get('form_class')
    if form_class:
        return form_class

    return modelform_factory(
        config['model'],
        fields=config.get('form_fields'),
    )


def _apply_search(queryset, config, search_query):
    if not search_query:
        return queryset

    query = Q()
    for field in config.get('search_fields', ()):
        query |= Q(**{f'{field}__icontains': search_query})
    return queryset.filter(query)


def _format_cell_value(value):
    if isinstance(value, bool):
        return 'Yes' if value else 'No'

    if value is None or value == '':
        return '—'

    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d %H:%M')

    return str(value)


def _get_column_label(model, field_name):
    try:
        return model._meta.get_field(field_name).verbose_name.title()
    except FieldDoesNotExist:
        return field_name.replace('_', ' ').title()


def _build_rows(page_obj, config, resource_key):
    rows = []

    for obj in page_obj.object_list:
        values = []
        for field_name in config.get('list_fields', ()):
            value = getattr(obj, field_name)
            if callable(value):
                value = value()
            values.append(_format_cell_value(value))

        action_url = None
        action_label = 'Open'
        if config.get('allow_edit', True):
            action_url = reverse('system-admin-resource-update', args=[resource_key, obj.pk])
        elif resource_key == 'hl7_messages':
            action_url = reverse('system-admin-hl7-message-detail', args=[obj.pk])
            action_label = 'View'

        rows.append({
            'object': obj,
            'values': values,
            'action_url': action_url,
            'action_label': action_label,
            'object_label': str(obj),
        })

    return rows


def _material_usage_form_context(form, obj=None):
    selected_exam = None
    candidate_exam_id = None

    if obj and getattr(obj, "exam_id", None):
        candidate_exam_id = obj.exam_id
    elif form is not None:
        raw_exam = ""
        if getattr(form, "is_bound", False):
            raw_exam = form.data.get("exam", "")
        if not raw_exam:
            raw_exam = form.initial.get("exam")
        if hasattr(raw_exam, "pk"):
            candidate_exam_id = raw_exam.pk
        else:
            candidate_exam_id = str(raw_exam or "").strip() or None

    if candidate_exam_id:
        selected_exam = Exam.objects.select_related("modality", "facility").filter(pk=candidate_exam_id).first()

    exam_usage_count = 0
    recent_rows_qs = MaterialUsage.objects.select_related(
        "exam",
        "material_item",
        "measurement",
        "exam__modality",
        "exam__facility",
    ).order_by("-created_at")
    if selected_exam:
        recent_rows_qs = recent_rows_qs.filter(exam_id=selected_exam.id)
        exam_usage_count = MaterialUsage.objects.filter(exam_id=selected_exam.id).count()

    return {
        "material_usage_total_count": MaterialUsage.objects.count(),
        "material_usage_catalog_count": MaterialCatalog.objects.filter(is_active=True).count(),
        "material_usage_measurement_count": MaterialMeasurement.objects.filter(is_active=True).count(),
        "material_usage_exam_count": exam_usage_count,
        "material_usage_recent_rows": list(recent_rows_qs[:10]),
        "material_usage_selected_exam": selected_exam,
    }


HL7_SEGMENT_EXPLANATIONS = {
    'MSH': ('Message Header', 'Routing, source system, destination system, message type, and control ID.'),
    'PID': ('Patient Identification', 'Patient identifiers, demographic details, and name components.'),
    'PV1': ('Patient Visit', 'Visit class, encounter location, and visit context such as emergency vs inpatient.'),
    'ORC': ('Common Order', 'Order control, order status, placer/filler numbers, and ordering provider.'),
    'OBR': ('Observation Request', 'Requested procedure, study timing, clinical indication, and exam context.'),
    'OBX': ('Observation Result', 'Observation/result values attached to the order or response.'),
    'NTE': ('Notes and Comments', 'Free-text comments or narrative notes sent with the message.'),
}


def _split_hl7_segments(raw_message):
    normalized = str(raw_message or '').replace('\r\n', '\n').replace('\r', '\n')
    segments = []
    for raw_line in normalized.split('\n'):
        line = raw_line.strip()
        if not line:
            continue
        segment_code = line.split('|', 1)[0].strip().upper()
        label, description = HL7_SEGMENT_EXPLANATIONS.get(
            segment_code,
            (segment_code, 'HL7 segment content.'),
        )
        segments.append({
            'code': segment_code,
            'label': label,
            'description': description,
            'raw': line,
        })
    return segments


def _hl7_message_overview(message):
    parsed = dict(getattr(message, 'parsed_data', {}) or {})
    message_info = dict(parsed.get('message_info') or {})
    patient = dict(parsed.get('patient') or {})
    visit = dict(parsed.get('visit') or {})
    order = dict(parsed.get('order') or {})
    observation = dict(parsed.get('observation_request') or {})

    patient_name = ''
    patient_name_parts = dict(patient.get('patient_name') or {})
    if patient_name_parts:
        patient_name = ' '.join(
            part for part in [
                str(patient_name_parts.get('given') or '').strip(),
                str(patient_name_parts.get('middle') or '').strip(),
                str(patient_name_parts.get('family') or '').strip(),
            ]
            if part
        )

    fields = [
        ('Message Type', message.message_type),
        ('Control ID', message.message_control_id),
        ('Direction', message.direction),
        ('Status', message.status),
        ('Sending App', message.sending_application or message_info.get('sending_application') or '—'),
        ('Sending Facility', message.sending_facility or message_info.get('sending_facility') or '—'),
        ('Receiving App', message.receiving_application or message_info.get('receiving_application') or '—'),
        ('Receiving Facility', message.receiving_facility or message_info.get('receiving_facility') or '—'),
        ('Order Control', order.get('order_control') or '—'),
        ('Order Status', order.get('order_status') or '—'),
        ('Order Number', message.exam_order_number() or order.get('placer_order_number') or '—'),
        ('Accession', message.exam_accession_number() or order.get('filler_order_number') or observation.get('filler_order_number') or '—'),
        ('Procedure', observation.get('procedure_name') or '—'),
        ('Procedure Code', observation.get('procedure_code') or '—'),
        ('Patient', patient_name or getattr(message.exam, 'patient_name', '') or '—'),
        ('MRN', patient.get('mrn') or getattr(message.exam, 'mrn', '') or '—'),
        ('Patient Class', visit.get('patient_class') or getattr(message.exam, 'patient_class', '') or '—'),
        ('Ordering Provider', order.get('ordering_provider') or getattr(message.exam, 'ordering_provider', '') or '—'),
    ]

    normalized_fields = []
    for label, value in fields:
        if isinstance(value, dict):
            safe_value = ', '.join(
                f'{key}: {inner_value}' for key, inner_value in value.items() if str(inner_value or '').strip()
            ) or '—'
        else:
            safe_value = str(value or '').strip() or '—'
        normalized_fields.append((label, safe_value))

    return normalized_fields


def _hl7_segment_interpretation(message):
    parsed = dict(getattr(message, 'parsed_data', {}) or {})
    if not parsed and str(getattr(message, 'raw_message', '') or '').strip():
        try:
            parsed = ORMParser(message.raw_message).parse()
        except Exception:
            parsed = {}

    interpretations = []
    section_map = [
        ('message_info', 'MSH'),
        ('patient', 'PID'),
        ('visit', 'PV1'),
        ('order', 'ORC'),
        ('observation_request', 'OBR'),
    ]
    for section_name, segment_code in section_map:
        payload = parsed.get(section_name)
        if not payload:
            continue

        rows = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, dict):
                    inner = ', '.join(
                        f'{inner_key}: {inner_value}'
                        for inner_key, inner_value in value.items()
                        if str(inner_value or '').strip()
                    )
                    display_value = inner or '—'
                else:
                    display_value = str(value or '').strip() or '—'
                rows.append((key.replace('_', ' ').title(), display_value))
        elif isinstance(payload, list):
            rows.append(('Items', ', '.join(str(item) for item in payload) or '—'))
        else:
            rows.append(('Value', str(payload)))

        label, description = HL7_SEGMENT_EXPLANATIONS.get(
            segment_code,
            (segment_code, 'HL7 segment content.'),
        )
        interpretations.append({
            'code': segment_code,
            'label': label,
            'description': description,
            'rows': rows,
        })

    return interpretations


def _hl7_issue_reason_context_values(status_value: str, technical_reason: str):
    technical_reason = str(technical_reason or "").strip()
    status_value = str(status_value or "").strip().upper()
    normalized_reason = technical_reason.casefold()

    if "duplicate message control id" in normalized_reason:
        return {
            "category": "Duplicate Message Control ID",
            "plain_reason": (
                "The same HL7 message was already imported earlier, so this copy was ignored "
                "to prevent duplicate exams."
            ),
            "next_step": "No action needed unless the source system should send a corrected message ID.",
            "technical_reason": technical_reason,
        }

    if "duplicate order id" in normalized_reason:
        return {
            "category": "Duplicate Order ID",
            "plain_reason": (
                "An exam with this order number already exists, so the system rejected this message "
                "to avoid creating a second exam."
            ),
            "next_step": "No action needed unless this was intended to be a new order with a unique order ID.",
            "technical_reason": technical_reason,
        }

    if "unsupported inbound hl7 flow" in normalized_reason:
        return {
            "category": "Unsupported HL7 Flow",
            "plain_reason": (
                "This HL7 message pattern is outside the supported workflow. "
                "Only ORM with order control NW (new order), ORM/ORR with IP/CM/CA "
                "(status updates), and SIU scheduling are processed."
            ),
            "next_step": (
                "If the order control is SC, it is intentionally ignored. "
                "Otherwise check interface mapping in the source system or integration engine."
            ),
            "technical_reason": technical_reason,
        }

    if status_value == "ERROR":
        return {
            "category": "Processing Error",
            "plain_reason": (
                "The message was accepted by transport but failed during internal processing."
            ),
            "next_step": "Open HL7 message detail and review parsed fields and raw payload.",
            "technical_reason": technical_reason or "Message processing failed.",
        }

    return {
        "category": "Rejected",
        "plain_reason": "The message was rejected by validation rules.",
        "next_step": "Open HL7 message detail for full payload and validation context.",
        "technical_reason": technical_reason or "Message rejected.",
    }


def _hl7_issue_reason_context(message):
    return _hl7_issue_reason_context_values(
        getattr(message, "status", ""),
        getattr(message, "error_message", ""),
    )


def _hl7_message_plain_summary(message):
    parsed = dict(getattr(message, "parsed_data", {}) or {})
    order = dict(parsed.get("order") or {})
    observation = dict(parsed.get("observation_request") or {})
    patient = dict(parsed.get("patient") or {})
    patient_name_map = dict(patient.get("patient_name") or {})

    order_id = (
        getattr(message, "exam_order_number", lambda: "")()
        or str(order.get("placer_order_number") or "").strip()
        or str(observation.get("placer_order_number") or "").strip()
        or "—"
    )
    accession = (
        getattr(message, "exam_accession_number", lambda: "")()
        or str(order.get("filler_order_number") or "").strip()
        or str(observation.get("filler_order_number") or "").strip()
        or "—"
    )
    procedure = (
        str(observation.get("procedure_name") or "").strip()
        or str(getattr(getattr(message, "exam", None), "procedure_name", "") or "").strip()
        or "procedure not provided"
    )
    patient_name = (
        " ".join(
            part
            for part in (
                patient_name_map.get("given"),
                patient_name_map.get("middle"),
                patient_name_map.get("family"),
            )
            if str(part or "").strip()
        ).strip()
        or str(getattr(getattr(message, "exam", None), "patient_name", "") or "").strip()
        or "patient name not provided"
    )
    facility = (
        str(getattr(message, "sending_facility", "") or "").strip()
        or str(((parsed.get("message_info") or {}).get("sending_facility") or "")).strip()
        or "unknown facility"
    )

    return (
        f"{message.message_type} from {facility}: order {order_id}, accession {accession}, "
        f"procedure {procedure}, patient {patient_name}."
    )


def _hl7_reporting_window(range_key: str):
    selected = str(range_key or "").strip().lower() or "yesterday"
    today = timezone.localdate()

    if selected == "today":
        start_date = today
        end_date = today
    elif selected == "last7":
        start_date = today - timedelta(days=6)
        end_date = today
    elif selected == "last30":
        start_date = today - timedelta(days=29)
        end_date = today
    else:
        selected = "yesterday"
        start_date = today - timedelta(days=1)
        end_date = start_date

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max), tz)
    return selected, start_date, end_date, start_dt, end_dt


def _singular_label(label):
    irregular = {
        'Facilities': 'Facility',
        'Modalities': 'Modality',
        'Protocol Templates': 'Protocol Template',
        'User Preferences': 'User Preference',
        'User Sessions': 'User Session',
        'Groups': 'Group',
        'Users': 'User',
    }
    if label in irregular:
        return irregular[label]
    if label.endswith('s'):
        return label[:-1]
    return label


def _is_exam_visible_in_protocol_workflow(
    exam,
    *,
    visible_procedure_codes: set[str],
    configured_procedure_codes: set[str],
):
    if not exam.supports_protocol_workflow:
        return False

    procedure_code = str(getattr(exam, 'procedure_code', '') or '').strip()
    if not procedure_code:
        return True

    if procedure_code not in configured_procedure_codes:
        return True

    return procedure_code in visible_procedure_codes


def _role(user):
    return getattr(user, 'role', '') or ''


def _user_default_subspeciality(user):
    return normalize_subspeciality(getattr(user, 'specialty', '') or '')


def _can_access_radiologist_review(user):
    if user.is_superuser:
        return True

    return (
        user.has_permission(Permission.PROTOCOL_ASSIGN)
        and _role(user) in (UserRole.RADIOLOGIST, UserRole.ADMIN)
    )


def _can_access_technologist_review(user):
    if user.is_superuser:
        return True

    return (
        user.has_permission(Permission.PROTOCOL_VIEW)
        and _role(user) in (UserRole.TECHNOLOGIST, UserRole.ADMIN)
    )


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


def _contrast_exam_queryset_for_user(user):
    queryset = Exam.objects.select_related("facility", "modality").filter(
        modality__requires_contrast=True,
        modality__is_active=True,
        status__in=(
            ExamStatus.IN_PROGRESS,
            ExamStatus.COMPLETED,
            ExamStatus.CANCELLED,
        ),
    )

    queryset = apply_facility_scope(queryset, user)

    return queryset.annotate(
        contrast_entry_count=Count("contrast_usages", distinct=True),
        material_entry_count=Count("material_usages", distinct=True),
    )


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
            actionable_response_only=True,
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


def _can_access_contrast_exam(user, exam) -> bool:
    if not can_access_facility(user, exam.facility_id):
        return False

    if user.is_superuser:
        return True

    if _role(user) == UserRole.ADMIN:
        return True

    return user.has_permission(Permission.CONTRAST_VIEW)


def _can_edit_contrast_exam(user) -> bool:
    if user.is_superuser:
        return True

    if _role(user) != UserRole.TECHNOLOGIST:
        return False

    return (
        user.has_permission(Permission.CONTRAST_CREATE)
        or user.has_permission(Permission.CONTRAST_EDIT)
    )


def _can_edit_saved_contrast_entries(user) -> bool:
    if user.is_superuser:
        return True

    if _role(user) not in (UserRole.SUPERVISOR, UserRole.ADMIN):
        return False

    return user.has_permission(Permission.CONTRAST_VIEW)


def _parse_iso_date(raw_value):
    value = str(raw_value or "").strip()
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_iso_month(raw_value):
    value = str(raw_value or "").strip()
    if not value:
        return None

    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError:
        return None

    return parsed.year, parsed.month


def _contrast_documented_filter_state(request):
    entry_kind = str(request.GET.get("entry_kind") or "all").strip().lower()
    if entry_kind not in {"all", "contrast", "material"}:
        entry_kind = "all"

    return {
        "date_from": _parse_iso_date(request.GET.get("date_from")),
        "date_to": _parse_iso_date(request.GET.get("date_to")),
        "month_raw": str(request.GET.get("month") or "").strip(),
        "modality": str(request.GET.get("modality") or "").strip().upper(),
        "entry_kind": entry_kind,
        "item_type": str(request.GET.get("item_type") or "").strip(),
    }


def _apply_documentation_filters(queryset, filters, *, is_contrast):
    modality = str(filters.get("modality") or "").strip().upper()
    if modality:
        queryset = queryset.filter(exam__modality__code__iexact=modality)

    date_from = filters.get("date_from")
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)

    date_to = filters.get("date_to")
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)

    parsed_month = _parse_iso_month(filters.get("month_raw"))
    if parsed_month:
        month_year, month_number = parsed_month
        queryset = queryset.filter(
            created_at__year=month_year,
            created_at__month=month_number,
        )

    item_type = str(filters.get("item_type") or "").strip()
    if item_type:
        if is_contrast:
            queryset = queryset.filter(contrast_name__icontains=item_type)
        else:
            queryset = queryset.filter(
                Q(material_name__icontains=item_type)
                | Q(material_item__name__icontains=item_type)
                | Q(material_item__material_code__icontains=item_type)
            )

    return queryset


def _build_contrast_documented_rows(exam_ids, filters, *, limit=500):
    rows = []
    entry_kind = str(filters.get("entry_kind") or "all").strip().lower()

    if entry_kind in {"all", "contrast"}:
        contrast_qs = _apply_documentation_filters(
            ContrastUsage.objects.filter(exam_id__in=exam_ids).select_related("exam", "exam__modality"),
            filters,
            is_contrast=True,
        ).order_by("-created_at")

        if limit:
            contrast_qs = contrast_qs[:limit]

        for entry in contrast_qs:
            rows.append(
                {
                    "documented_at": entry.created_at,
                    "entry_kind": "Contrast",
                    "modality_code": str(getattr(entry.exam.modality, "code", "") or "").strip() or "-",
                    "accession_number": str(getattr(entry.exam, "accession_number", "") or "").strip() or "-",
                    "order_id": str(getattr(entry.exam, "order_id", "") or "").strip() or "-",
                    "patient_name": str(getattr(entry.exam, "patient_name", "") or "").strip() or "-",
                    "item_name": str(entry.contrast_name or "").strip() or "-",
                    "type_value": str(entry.route or "").strip() or "-",
                    "quantity_value": entry.volume_ml,
                    "quantity_unit": "mL",
                    "total_mg": entry.total_mg,
                }
            )

    if entry_kind in {"all", "material"}:
        material_qs = _apply_documentation_filters(
            MaterialUsage.objects.filter(exam_id__in=exam_ids).select_related(
                "exam",
                "exam__modality",
                "material_item",
            ),
            filters,
            is_contrast=False,
        ).order_by("-created_at")

        if limit:
            material_qs = material_qs[:limit]

        for entry in material_qs:
            material_name = (
                str(entry.material_name or "").strip()
                or str(getattr(entry.material_item, "name", "") or "").strip()
            )
            rows.append(
                {
                    "documented_at": entry.created_at,
                    "entry_kind": "Material",
                    "modality_code": str(getattr(entry.exam.modality, "code", "") or "").strip() or "-",
                    "accession_number": str(getattr(entry.exam, "accession_number", "") or "").strip() or "-",
                    "order_id": str(getattr(entry.exam, "order_id", "") or "").strip() or "-",
                    "patient_name": str(getattr(entry.exam, "patient_name", "") or "").strip() or "-",
                    "item_name": material_name or "-",
                    "type_value": str(entry.unit or "").strip() or "-",
                    "quantity_value": entry.quantity,
                    "quantity_unit": str(entry.unit or "").strip() or "-",
                    "total_mg": None,
                }
            )

    rows.sort(key=lambda item: item["documented_at"], reverse=True)
    if limit:
        rows = rows[:limit]

    return rows


def _contrast_documentation_export_url(filters):
    query = {}

    date_from = filters.get("date_from")
    if date_from:
        query["date_from"] = date_from.isoformat()

    date_to = filters.get("date_to")
    if date_to:
        query["date_to"] = date_to.isoformat()

    month_raw = str(filters.get("month_raw") or "").strip()
    if month_raw:
        query["month"] = month_raw

    modality = str(filters.get("modality") or "").strip().upper()
    if modality:
        query["modality"] = modality

    entry_kind = str(filters.get("entry_kind") or "all").strip().lower()
    if entry_kind and entry_kind != "all":
        query["entry_kind"] = entry_kind

    item_type = str(filters.get("item_type") or "").strip()
    if item_type:
        query["item_type"] = item_type

    url = reverse("contrast-materials-analytics-export")
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def _display_actor(user_obj):
    if not user_obj:
        return "System"

    full_name = ""
    if hasattr(user_obj, "get_full_name"):
        full_name = str(user_obj.get_full_name() or "").strip()

    return full_name or str(getattr(user_obj, "username", "") or "").strip() or "System"


def _contrast_workflow_timeline(exam, contrast_entries, material_entries):
    timeline_events = []

    def append_event(event_type, occurred_at, title, body, actor):
        if not occurred_at:
            return
        timeline_events.append(
            {
                "event_type": str(event_type or "update").strip().lower(),
                "occurred_at": occurred_at,
                "title": str(title or "").strip(),
                "body": str(body or "").strip(),
                "actor": str(actor or "").strip() or "System",
            }
        )

    append_event(
        "notification",
        getattr(exam, "created_at", None),
        "Exam Registered",
        "Exam entered the contrast/material workflow queue.",
        "System",
    )
    append_event(
        "update",
        getattr(exam, "scheduled_datetime", None),
        "Exam Scheduled",
        "Exam has a scheduled datetime in RIS.",
        "RIS",
    )
    append_event(
        "update",
        getattr(exam, "exam_datetime", None),
        "Exam DateTime Recorded",
        "Exam execution timestamp was recorded.",
        "RIS",
    )

    assignment = getattr(exam, "protocol_assignment", None)
    if assignment:
        protocol_label = (
            f"{assignment.protocol.code} - {assignment.protocol.name}"
            if getattr(assignment, "protocol", None)
            else "Protocol assignment updated."
        )
        append_event(
            "assignment",
            getattr(assignment, "assigned_at", None) or getattr(assignment, "created_at", None),
            "Protocol Assigned",
            protocol_label,
            _display_actor(getattr(assignment, "assigned_by", None)),
        )
        append_event(
            "acknowledged",
            getattr(assignment, "acknowledged_at", None),
            "Technologist Acknowledged Protocol",
            "Protocol assignment acknowledged by technologist.",
            _display_actor(getattr(assignment, "acknowledged_by", None)),
        )

    default_actor = str(getattr(exam, "technologist", "") or "").strip() or "Technologist"

    for row in contrast_entries:
        append_event(
            "contrast",
            getattr(row, "created_at", None),
            "Contrast Documented",
            (
                f"{row.contrast_name} | {row.volume_ml} mL | "
                f"{row.concentration_mg_ml} mg/mL | Total {row.total_mg} mg"
            ),
            default_actor,
        )

    for row in material_entries:
        material_label = (
            str(getattr(row, "material_name", "") or "").strip()
            or str(getattr(getattr(row, "material_item", None), "name", "") or "").strip()
            or "Material"
        )
        append_event(
            "material",
            getattr(row, "created_at", None),
            "Material Documented",
            f"{material_label} | Qty {row.quantity} {row.unit or ''}".strip(),
            default_actor,
        )

    timeline_events.sort(key=lambda item: item["occurred_at"], reverse=True)
    return timeline_events[:120]


def _material_documentation_status(material_count: int) -> str:
    return "Documented" if int(material_count or 0) > 0 else "Pending"


def _recommended_bundle_for_exam(exam) -> dict:
    procedure_code = str(getattr(exam, "procedure_code", "") or "").strip()
    procedure_name = str(getattr(exam, "procedure_name", "") or "").strip()

    bundle_qs = ProcedureMaterialBundle.objects.filter(is_active=True)
    if procedure_code:
        bundle_qs = bundle_qs.filter(procedure_code__iexact=procedure_code)
    elif procedure_name:
        bundle_qs = bundle_qs.filter(procedure_name__iexact=procedure_name)
    else:
        return {}

    bundle = (
        bundle_qs
        .prefetch_related("items__material__default_measurement")
        .order_by("-updated_at")
        .first()
    )
    if bundle is None:
        return {}

    rows = []
    for row in bundle.items.all().order_by("sort_order", "id"):
        material = row.material
        measurement = getattr(material, "default_measurement", None) if material else None
        material_code = (
            str(getattr(material, "material_code", "") or "").strip()
            or str(row.material_code or "").strip()
        )
        rows.append(
            {
                "material_item_id": str(material.id) if material else "",
                "material_code": material_code,
                "material_name": (
                    str(getattr(material, "name", "") or "").strip()
                    or str(row.material_code or "").strip()
                ),
                "measurement_id": str(measurement.id) if measurement else "",
                "measurement_code": str(getattr(measurement, "code", "") or "").strip(),
                "unit": (
                    str(getattr(material, "unit", "") or "").strip()
                    or str(getattr(measurement, "code", "") or "").strip()
                ),
                "quantity": str(row.quantity),
                "is_optional": bool(row.is_optional),
                "notes": str(row.notes or "").strip(),
            }
        )

    return {
        "id": str(bundle.id),
        "procedure_code": str(bundle.procedure_code or "").strip(),
        "procedure_name": str(bundle.procedure_name or "").strip(),
        "modality_scope": str(bundle.modality_scope or "").strip(),
        "rules_notes": str(bundle.rules_notes or "").strip(),
        "items": rows,
    }


def _json_payload(request):
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


def _optional_numeric_input(raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        value = raw_value.strip()
        return value or None
    return raw_value


def _validation_error_text(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict") and isinstance(exc.message_dict, dict):
        parts = []
        for field_name, field_errors in exc.message_dict.items():
            if isinstance(field_errors, (list, tuple)):
                field_text = ", ".join(str(item) for item in field_errors if str(item).strip())
            else:
                field_text = str(field_errors or "").strip()
            if field_text:
                parts.append(f"{field_name}: {field_text}")
        if parts:
            return "; ".join(parts)

    messages = getattr(exc, "messages", None)
    if messages:
        return ", ".join(str(item) for item in messages if str(item).strip())

    return "Invalid input."


@login_required
def home(request):
    """Home page"""
    total_exams = Exam.objects.count()
    unassigned_exams = Exam.objects.filter(protocol_assignment__isnull=True).count()
    active_protocols = ProtocolTemplate.objects.filter(is_active=True).count()
    qc_modalities = Modality.objects.filter(requires_qc=True, is_active=True).count()
    contrast_protocols = ProtocolTemplate.objects.filter(requires_contrast=True, is_active=True).count()

    context = {
        'title': 'AAML RadCore Platform - Radiology Workflow System',
        'admin_url': reverse('system-admin'),
        'api_docs_url': '/api/schema/swagger/',
        'home_metrics': [
            {
                'label': 'Active Queue',
                'value': total_exams,
                'detail': f'{unassigned_exams} studies still need a first protocol decision.',
            },
            {
                'label': 'Live Protocols',
                'value': active_protocols,
                'detail': 'Active protocol templates available for review and assignment.',
            },
            {
                'label': 'QC-Ready Modalities',
                'value': qc_modalities,
                'detail': 'Modality definitions with QC checklist requirements enabled.',
            },
            {
                'label': 'Contrast Protocols',
                'value': contrast_protocols,
                'detail': 'Active templates that require contrast or material planning.',
            },
        ],
    }
    return render(request, 'home.html', context)


def health_check(request):
    """Health check endpoint"""
    return JsonResponse({
        'status': 'ok',
        'service': 'AAML RadCore Platform',
        'version': '1.0.0'
    })


WORKLIST_FILTER_CONTEXTS = {
    "protocol": {
        "permission": Permission.PROTOCOL_VIEW,
        "allowed_keys": {
            "modality",
            "body_part",
            "subspeciality",
            "patient_class",
            "status",
            "exam_status",
            "query",
            "order_date_query",
            "schedule_date_query",
            "search_date",
            "search_date_mode",
            "schedule_date",
            "schedule_date_mode",
            "sort_by",
            "facilities",
        },
        "list_keys": {
            "modality",
            "body_part",
            "subspeciality",
            "patient_class",
            "status",
            "exam_status",
            "facilities",
        },
    },
    "qc": {
        "permission": Permission.QC_VIEW,
        "allowed_keys": {
            "modality",
            "query",
        },
        "list_keys": set(),
    },
    "contrast": {
        "permission": Permission.CONTRAST_VIEW,
        "allowed_keys": {
            "query",
        },
        "list_keys": set(),
    },
}
WORKLIST_FILTER_PREFERENCE_TYPE = "display"
WORKLIST_FILTER_PREFERENCE_KEY_PREFIX = "worklist_filters."


def _sanitize_worklist_filter_value(value, *, max_length=120):
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:max_length]


def _sanitize_worklist_filters(context_key: str, raw_filters):
    context = WORKLIST_FILTER_CONTEXTS.get(context_key) or {}
    allowed_keys = set(context.get("allowed_keys") or set())
    list_keys = set(context.get("list_keys") or set())

    if not isinstance(raw_filters, dict):
        return {}

    cleaned = {}
    for key in allowed_keys:
        raw_value = raw_filters.get(key)
        if raw_value in (None, ""):
            continue

        if key in list_keys:
            if isinstance(raw_value, str):
                raw_values = [raw_value]
            elif isinstance(raw_value, (list, tuple, set)):
                raw_values = raw_value
            else:
                continue
            values = []
            for item in raw_values:
                normalized = _sanitize_worklist_filter_value(item, max_length=64)
                if not normalized:
                    continue
                values.append(normalized)
                if len(values) >= 50:
                    break
            if values:
                cleaned[key] = values
            continue

        max_length = 200 if key == "query" else 80
        normalized = _sanitize_worklist_filter_value(raw_value, max_length=max_length)
        if normalized:
            cleaned[key] = normalized

    return cleaned


@login_required
@require_http_methods(["GET", "POST"])
def worklist_filter_preferences_api(request, context_key):
    context = WORKLIST_FILTER_CONTEXTS.get(context_key)
    if context is None:
        return JsonResponse({"error": "Unknown worklist context."}, status=404)

    required_permission = context["permission"]
    if not request.user.has_permission(required_permission):
        return JsonResponse({"error": f"Missing permission: {required_permission}"}, status=403)

    preference_key = f"{WORKLIST_FILTER_PREFERENCE_KEY_PREFIX}{context_key}"

    if request.method == "GET":
        preference = UserPreference.objects.filter(
            user=request.user,
            preference_type=WORKLIST_FILTER_PREFERENCE_TYPE,
            preference_key=preference_key,
        ).first()
        stored_filters = {}
        if preference and isinstance(preference.preference_value, dict):
            stored_filters = preference.preference_value

        return JsonResponse(
            {
                "context": context_key,
                "has_saved": bool(preference),
                "filters": _sanitize_worklist_filters(context_key, stored_filters),
            }
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "Payload must be a JSON object."}, status=400)

    incoming_filters = payload.get("filters", payload)
    if not isinstance(incoming_filters, dict):
        return JsonResponse({"error": "filters must be a JSON object."}, status=400)

    normalized_filters = _sanitize_worklist_filters(context_key, incoming_filters)
    UserPreference.objects.update_or_create(
        user=request.user,
        preference_type=WORKLIST_FILTER_PREFERENCE_TYPE,
        preference_key=preference_key,
        defaults={"preference_value": normalized_filters},
    )

    return JsonResponse(
        {
            "ok": True,
            "context": context_key,
            "filters": normalized_filters,
        }
    )


@app_permission_required(Permission.PROTOCOL_VIEW)
def worklist_page(request):
    """Protocol worklist page"""
    return render(request, 'protocoling/worklist.html')


@app_permission_required(Permission.PROTOCOL_ASSIGN)
def protocoling_page(request):
    """Protocoling assignment page"""
    exam_id = str(request.GET.get('exam_id') or '').strip()
    if exam_id:
        return redirect('protocoling-radiologist-review', exam_id=exam_id)
    return redirect('protocoling-worklist')


@app_permission_required(Permission.QC_VIEW)
def quality_control_page(request):
    """Quality control landing page."""
    context = {
        'hero_title': 'Quality Control (QC)',
        'hero_copy': (
            'Monitor QC readiness, review modality checklist coverage, and route the team '
            'back to the operational queue when study follow-up is needed.'
        ),
        'primary_url': reverse('system-admin-resource-list', args=['modalities']),
        'primary_label': 'Open QC Settings',
        'secondary_url': reverse('protocoling-worklist'),
        'secondary_label': 'Open Protocol Worklist',
        'metric_cards': [
            {
                'label': 'QC Modalities',
                'value': Modality.objects.filter(requires_qc=True, is_active=True).count(),
                'detail': 'Active modalities that require QC checklist governance.',
            },
            {
                'label': 'Active Modalities',
                'value': Modality.objects.filter(is_active=True).count(),
                'detail': 'Total active modality definitions available in the system.',
            },
            {
                'label': 'Scheduled Exams',
                'value': Exam.objects.filter(status='SCHEDULED').count(),
                'detail': 'Visible exams that may still need operational review.',
            },
        ],
        'focus_cards': [
            {
                'title': 'Checklist Governance',
                'description': 'Maintain QC checklist templates and review which modality definitions currently enforce QC.',
                'url': reverse('system-admin-resource-list', args=['modalities']),
                'action': 'Manage modalities',
            },
            {
                'title': 'Operational Queue',
                'description': 'Return to the protocol worklist to review the studies actively moving through the operational queue.',
                'url': reverse('protocoling-worklist'),
                'action': 'Open queue',
            },
        ],
        'current_nav': 'qc',
    }
    return render(request, 'operations/hub_page.html', context)


@app_permission_required(Permission.CONTRAST_VIEW)
def contrast_materials_page(request):
    """Contrast and materials worklist page."""
    visible_exams = _contrast_exam_queryset_for_user(request.user)
    exam_ids = list(visible_exams.values_list("id", flat=True))
    contrast_count = ContrastUsage.objects.filter(exam_id__in=exam_ids).count()
    material_count = MaterialUsage.objects.filter(exam_id__in=exam_ids).count()
    context = {
        "current_nav": "contrast",
        "worklist_api_url": reverse("contrast-materials-api-exams"),
        "total_exams": len(exam_ids),
        "contrast_usage_count": contrast_count,
        "material_usage_count": material_count,
        "catalog_url": reverse("system-admin-resource-list", args=["material_catalog"]),
        "measurement_url": reverse("system-admin-resource-list", args=["material_measurements"]),
        "analytics_url": reverse("contrast-materials-analytics"),
        "initial_search_query": str(request.GET.get("q") or "").strip(),
    }
    return render(request, "contrast/worklist.html", context)


@app_permission_required(Permission.CONTRAST_VIEW)
@require_http_methods(["GET"])
def contrast_materials_exams_api(request):
    query = str(request.GET.get("q") or "").strip()
    exams_qs = _contrast_exam_queryset_for_user(request.user)
    if query:
        exams_qs = exams_qs.filter(
            Q(accession_number__icontains=query)
            | Q(order_id__icontains=query)
            | Q(mrn__icontains=query)
            | Q(patient_name__icontains=query)
            | Q(procedure_name__icontains=query)
        )

    exams = list(exams_qs.order_by("-updated_at", "-scheduled_datetime", "-exam_datetime")[:1000])
    rows = []
    for exam in exams:
        effective_status = _effective_exam_status(exam)
        pacs_exam_link = _build_pacs_exam_link(exam.accession_number)
        pacs_patient_link = _build_pacs_patient_link(exam.mrn)
        rows.append(
            {
                "id": str(exam.id),
                "accession_number": exam.accession_number,
                "order_id": exam.order_id,
                "mrn": exam.mrn,
                "patient_name": exam.patient_name,
                "procedure_name": exam.procedure_name,
                "modality_code": exam.modality.code,
                "facility_name": exam.facility.name,
                "exam_status": effective_status,
                "exam_status_label": _exam_status_label(effective_status),
                "exam_datetime": exam.exam_datetime.isoformat() if exam.exam_datetime else None,
                "hl7_order_status": str((exam.metadata or {}).get("hl7_order_status") or ""),
                "contrast_count": int(getattr(exam, "contrast_entry_count", 0) or 0),
                "material_count": int(getattr(exam, "material_entry_count", 0) or 0),
                "material_status": _material_documentation_status(getattr(exam, "material_entry_count", 0)),
                "can_open": _can_access_contrast_exam(request.user, exam),
                "review_url": reverse("contrast-materials-review", args=[exam.id]),
                "pacs_exam_link": pacs_exam_link,
                "pacs_patient_link": pacs_patient_link,
            }
        )
    return JsonResponse({"results": rows})


@app_permission_required(Permission.CONTRAST_VIEW)
def contrast_materials_review_page(request, exam_id):
    exam = get_object_or_404(
        Exam.objects.select_related(
            "facility",
            "modality",
            "protocol_assignment__protocol",
            "protocol_assignment__assigned_by",
            "protocol_assignment__acknowledged_by",
        ),
        id=exam_id,
    )
    if not _can_access_contrast_exam(request.user, exam):
        raise PermissionDenied("Not allowed.")

    can_edit = _can_edit_contrast_exam(request.user)
    can_edit_saved_entries = _can_edit_saved_contrast_entries(request.user)

    recommended_bundle = _recommended_bundle_for_exam(exam)
    material_catalog = list(
        MaterialCatalog.objects.filter(is_active=True)
        .select_related("default_measurement")
        .order_by("name")
    )
    contrast_catalog = list(
        MaterialCatalog.objects.filter(
            is_active=True,
        ).filter(
            Q(category=MaterialCategory.CONTRAST)
            | Q(category__icontains="contrast")
        )
        .select_related("default_measurement")
        .order_by("name")
    )
    contrast_catalog_options = []
    for item in contrast_catalog:
        metadata = dict(getattr(item, "metadata", {}) or {})
        concentration = str(
            metadata.get("default_concentration_mg_ml")
            or metadata.get("concentration_mg_ml")
            or metadata.get("concentration")
            or ""
        ).strip()
        route = str(metadata.get("default_route") or "IV").strip().upper() or "IV"
        injection_rate = str(
            metadata.get("default_injection_rate_ml_s")
            or metadata.get("injection_rate_ml_s")
            or ""
        ).strip()

        contrast_catalog_options.append(
            {
                "option_value": str(item.id),
                "name": str(item.name or "").strip(),
                "code": str(item.material_code or "").strip(),
                "concentration_mg_ml": concentration,
                "route": route,
                "injection_rate_ml_s": injection_rate,
                "brand_name": str(metadata.get("brand_name") or "").strip(),
                "generic_name": str(metadata.get("generic_name") or "").strip(),
                "form_strength": str(metadata.get("form_strength") or "").strip(),
                "typical_adult_dose": str(metadata.get("typical_adult_dose") or "").strip(),
                "typical_peds_dose": str(metadata.get("typical_peds_dose") or "").strip(),
                "indications": str(metadata.get("indications") or "").strip(),
                "contraindications": str(metadata.get("contraindications") or "").strip(),
                "storage": str(metadata.get("storage") or "").strip(),
                "manufacturer": str(metadata.get("manufacturer") or "").strip(),
                "osmolality": str(metadata.get("osmolality") or "").strip(),
                "notes": str(getattr(item, "notes", "") or "").strip(),
                "label": (
                    (f"{item.material_code} - " if item.material_code else "")
                    + str(item.name or "").strip()
                ),
            }
        )

    recent_contrast_rows = list(
        ContrastUsage.objects.exclude(contrast_name="")
        .order_by("-created_at")
        .values(
            "contrast_name",
            "concentration_mg_ml",
            "route",
            "injection_rate_ml_s",
        )[:200]
    )
    contrast_quick_presets = []
    seen_preset_keys = set()
    seen_name_keys = set()
    for row in recent_contrast_rows:
        contrast_name = str(row.get("contrast_name") or "").strip()
        if not contrast_name:
            continue
        name_key = contrast_name.casefold()
        concentration_value = row.get("concentration_mg_ml")
        concentration_text = (
            str(concentration_value).rstrip("0").rstrip(".")
            if concentration_value is not None
            else ""
        )
        injection_rate_value = row.get("injection_rate_ml_s")
        injection_rate_text = (
            str(injection_rate_value).rstrip("0").rstrip(".")
            if injection_rate_value is not None
            else ""
        )
        route = str(row.get("route") or "IV").strip().upper() or "IV"

        preset_key = f"{name_key}|{concentration_text}|{route}|{injection_rate_text}"
        if preset_key in seen_preset_keys:
            continue
        seen_preset_keys.add(preset_key)
        if name_key not in seen_name_keys:
            seen_name_keys.add(name_key)

        contrast_quick_presets.append(
            {
                "name": contrast_name,
                "concentration_mg_ml": concentration_text,
                "route": route,
                "injection_rate_ml_s": injection_rate_text,
            }
        )
        if len(contrast_quick_presets) >= 10:
            break

    contrast_name_options = []
    for item in contrast_catalog:
        material_name = str(getattr(item, "name", "") or "").strip()
        if not material_name:
            continue
        key = material_name.casefold()
        if key in seen_name_keys:
            continue
        seen_name_keys.add(key)
        contrast_name_options.append(material_name)

    if len(contrast_name_options) < 80:
        for row in recent_contrast_rows:
            contrast_name = str(row.get("contrast_name") or "").strip()
            if not contrast_name:
                continue
            key = contrast_name.casefold()
            if key in seen_name_keys:
                continue
            seen_name_keys.add(key)
            contrast_name_options.append(contrast_name)
            if len(contrast_name_options) >= 80:
                break

    contrast_catalog_fallback = []
    if not contrast_catalog:
        fallback_seen = set()
        for row in recent_contrast_rows:
            contrast_name = str(row.get("contrast_name") or "").strip()
            if not contrast_name:
                continue
            key = contrast_name.casefold()
            if key in fallback_seen:
                continue
            fallback_seen.add(key)
            concentration_value = row.get("concentration_mg_ml")
            concentration_text = (
                str(concentration_value).rstrip("0").rstrip(".")
                if concentration_value is not None
                else ""
            )
            route = str(row.get("route") or "IV").strip().upper() or "IV"
            injection_rate_value = row.get("injection_rate_ml_s")
            injection_rate_text = (
                str(injection_rate_value).rstrip("0").rstrip(".")
                if injection_rate_value is not None
                else ""
            )
            contrast_catalog_fallback.append(
                {
                    "option_value": f"recent-contrast-{len(contrast_catalog_fallback) + 1}",
                    "name": contrast_name,
                    "code": "",
                    "concentration_mg_ml": concentration_text,
                    "route": route,
                    "injection_rate_ml_s": injection_rate_text,
                    "label": (
                        f"Recent: {contrast_name}"
                        + (f" ({concentration_text} mg/mL)" if concentration_text else "")
                    ),
                }
            )
            if len(contrast_catalog_fallback) >= 40:
                break

    material_catalog_fallback = []
    if not material_catalog:
        fallback_seen = set()

        for item in (recommended_bundle.get("items") or []):
            material_name = str(item.get("material_name") or "").strip()
            if not material_name:
                continue
            material_code = str(item.get("material_code") or "").strip()
            measurement_id = str(item.get("measurement_id") or "").strip()
            measurement_code = str(item.get("measurement_code") or "").strip()
            unit = str(item.get("unit") or measurement_code).strip()
            key = f"{material_name.casefold()}|{material_code.casefold()}|{measurement_code.casefold()}|{unit.casefold()}"
            if key in fallback_seen:
                continue
            fallback_seen.add(key)

            material_catalog_fallback.append(
                {
                    "option_value": f"bundle-material-{len(material_catalog_fallback) + 1}",
                    "name": material_name,
                    "code": material_code,
                    "measurement_id": measurement_id,
                    "measurement_code": measurement_code,
                    "unit": unit,
                    "label": (
                        f"Bundle: {material_name}"
                        + (f" ({material_code})" if material_code else "")
                    ),
                }
            )
            if len(material_catalog_fallback) >= 120:
                break

        recent_material_rows = list(
            MaterialUsage.objects.exclude(material_name="")
            .select_related("measurement", "material_item")
            .order_by("-created_at")[:250]
        )
        for row in recent_material_rows:
            material_name = str(getattr(row, "material_name", "") or "").strip()
            if not material_name:
                continue
            material_item = getattr(row, "material_item", None)
            measurement = getattr(row, "measurement", None)
            material_code = str(getattr(material_item, "material_code", "") or "").strip()
            measurement_id = str(getattr(measurement, "id", "") or "").strip()
            measurement_code = str(getattr(measurement, "code", "") or "").strip()
            unit = str(getattr(row, "unit", "") or measurement_code).strip()
            key = f"{material_name.casefold()}|{material_code.casefold()}|{measurement_code.casefold()}|{unit.casefold()}"
            if key in fallback_seen:
                continue
            fallback_seen.add(key)

            material_catalog_fallback.append(
                {
                    "option_value": f"recent-material-{len(material_catalog_fallback) + 1}",
                    "name": material_name,
                    "code": material_code,
                    "measurement_id": measurement_id,
                    "measurement_code": measurement_code,
                    "unit": unit,
                    "label": (
                        f"Recent: {material_name}"
                        + (f" ({material_code})" if material_code else "")
                    ),
                }
            )
            if len(material_catalog_fallback) >= 120:
                break

    contrast_entries = list(exam.contrast_usages.select_related("exam").order_by("-created_at"))
    material_entries = list(
        exam.material_usages.select_related("material_item", "measurement").order_by("-created_at")
    )
    timeline_events = _contrast_workflow_timeline(exam, contrast_entries, material_entries)
    pacs_exam_link = _build_pacs_exam_link(exam.accession_number)
    pacs_patient_link = _build_pacs_patient_link(exam.mrn)

    context = {
        "current_nav": "contrast",
        "exam": exam,
        "contrast_entries": contrast_entries,
        "material_entries": material_entries,
        "material_catalog": material_catalog,
        "material_catalog_fallback": material_catalog_fallback,
        "contrast_catalog": contrast_catalog,
        "contrast_catalog_options": contrast_catalog_options,
        "contrast_catalog_fallback": contrast_catalog_fallback,
        "contrast_name_options": contrast_name_options,
        "contrast_quick_presets": contrast_quick_presets,
        "measurement_options": MaterialMeasurement.objects.filter(is_active=True).order_by("code"),
        "recommended_bundle": recommended_bundle,
        "session_api_url": reverse("contrast-materials-api-session", args=[exam.id]),
        "can_edit": can_edit,
        "can_edit_saved_entries": can_edit_saved_entries,
        "saved_entry_api_url": reverse("contrast-materials-api-entry-update", args=[exam.id]),
        "timeline_events": timeline_events,
        "pacs_exam_link": pacs_exam_link,
        "pacs_patient_link": pacs_patient_link,
    }
    return render(request, "contrast/review.html", context)


@app_permission_required(Permission.CONTRAST_VIEW)
@require_http_methods(["POST"])
def contrast_materials_session_api(request, exam_id):
    exam = get_object_or_404(Exam.objects.select_related("facility", "modality"), id=exam_id)
    if not _can_access_contrast_exam(request.user, exam):
        return JsonResponse({"error": "Not allowed."}, status=403)

    if not _can_edit_contrast_exam(request.user):
        return JsonResponse(
            {"error": "Only technologists are allowed to add new contrast/material entries."},
            status=403,
        )

    try:
        payload = _json_payload(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    contrast_entries = payload.get("contrast_entries") or []
    material_entries = payload.get("material_entries") or []
    if not isinstance(contrast_entries, list) or not isinstance(material_entries, list):
        return JsonResponse({"error": "contrast_entries and material_entries must be arrays."}, status=400)

    created_contrast = 0
    created_material = 0

    with transaction.atomic():
        for index, item in enumerate(contrast_entries, start=1):
            if not isinstance(item, dict):
                continue
            contrast_name = str(item.get("contrast_name") or "").strip()
            concentration = _optional_numeric_input(item.get("concentration_mg_ml"))
            volume = _optional_numeric_input(item.get("volume_ml"))
            if not contrast_name or concentration is None or volume is None:
                continue

            try:
                ContrastUsage.objects.create(
                    exam=exam,
                    pec_number=str(item.get("pec_number") or exam.order_id or "").strip(),
                    contrast_name=contrast_name,
                    concentration_mg_ml=concentration,
                    volume_ml=volume,
                    injection_rate_ml_s=_optional_numeric_input(item.get("injection_rate_ml_s")),
                    route=str(item.get("route") or "IV").strip().upper() or "IV",
                    lot_number=str(item.get("lot_number") or "").strip(),
                    expiry_date=item.get("expiry_date") or None,
                    patient_weight_kg=_optional_numeric_input(item.get("patient_weight_kg")),
                    metadata=dict(item.get("metadata") or {}),
                )
            except ValidationError as exc:
                error_text = _validation_error_text(exc)
                return JsonResponse(
                    {"error": f"Invalid contrast row #{index}. {error_text}"},
                    status=400,
                )
            created_contrast += 1

        for index, item in enumerate(material_entries, start=1):
            if not isinstance(item, dict):
                continue
            quantity = _optional_numeric_input(item.get("quantity"))
            if quantity is None:
                continue

            material_item = None
            measurement = None
            material_code = str(item.get("material_code") or "").strip()

            material_item_id = str(item.get("material_item_id") or "").strip()
            if material_item_id:
                material_item = MaterialCatalog.objects.filter(id=material_item_id, is_active=True).first()
            if material_item is None and material_code:
                material_item = MaterialCatalog.objects.filter(
                    material_code__iexact=material_code,
                    is_active=True,
                ).first()

            measurement_id = str(item.get("measurement_id") or "").strip()
            if measurement_id:
                measurement = MaterialMeasurement.objects.filter(id=measurement_id, is_active=True).first()

            material_metadata = dict(item.get("metadata") or {})
            if material_code:
                material_metadata["material_code"] = material_code

            try:
                MaterialUsage.objects.create(
                    exam=exam,
                    pec_number=str(item.get("pec_number") or exam.order_id or "").strip(),
                    material_item=material_item,
                    material_name=(
                        str(item.get("material_name") or "").strip()
                        or str(getattr(material_item, "name", "") or "").strip()
                    ),
                    measurement=measurement,
                    unit=(
                        str(item.get("unit") or "").strip()
                        or str(getattr(measurement, "code", "") or "").strip()
                        or str(getattr(material_item, "unit", "") or "").strip()
                    ),
                    quantity=quantity,
                    metadata=material_metadata,
                )
            except ValidationError as exc:
                error_text = _validation_error_text(exc)
                return JsonResponse(
                    {"error": f"Invalid material row #{index}. {error_text}"},
                    status=400,
                )
            created_material += 1

    if created_contrast == 0 and created_material == 0:
        return JsonResponse({"error": "No valid contrast/material entries were submitted."}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "created_contrast": created_contrast,
            "created_material": created_material,
            "contrast_count": exam.contrast_usages.count(),
            "material_count": exam.material_usages.count(),
            "review_url": reverse("contrast-materials-review", args=[exam.id]),
        }
    )


@app_permission_required(Permission.CONTRAST_VIEW)
@require_http_methods(["POST"])
def contrast_materials_saved_entry_update_api(request, exam_id):
    exam = get_object_or_404(Exam.objects.select_related("facility", "modality"), id=exam_id)
    if not _can_access_contrast_exam(request.user, exam):
        return JsonResponse({"error": "Not allowed."}, status=403)

    if not _can_edit_saved_contrast_entries(request.user):
        return JsonResponse(
            {"error": "Only supervisors and administrators can edit saved entries."},
            status=403,
        )

    try:
        payload = _json_payload(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    entry_type = str(payload.get("entry_type") or "").strip().lower()
    entry_id = str(payload.get("entry_id") or "").strip()
    data = payload.get("data") or {}
    if entry_type not in {"contrast", "material"}:
        return JsonResponse({"error": "entry_type must be either contrast or material."}, status=400)
    if not entry_id:
        return JsonResponse({"error": "entry_id is required."}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "data must be an object."}, status=400)

    try:
        if entry_type == "contrast":
            row = get_object_or_404(ContrastUsage, id=entry_id, exam_id=exam.id)

            if "contrast_name" in data:
                row.contrast_name = str(data.get("contrast_name") or "").strip()
            if "concentration_mg_ml" in data:
                row.concentration_mg_ml = data.get("concentration_mg_ml")
            if "volume_ml" in data:
                row.volume_ml = data.get("volume_ml")
            if "injection_rate_ml_s" in data:
                raw_rate = str(data.get("injection_rate_ml_s") or "").strip()
                row.injection_rate_ml_s = raw_rate or None
            if "route" in data:
                row.route = str(data.get("route") or "IV").strip().upper() or "IV"
            if "lot_number" in data:
                row.lot_number = str(data.get("lot_number") or "").strip()
            if "expiry_date" in data:
                row.expiry_date = _parse_iso_date(data.get("expiry_date"))
            if "patient_weight_kg" in data:
                raw_weight = str(data.get("patient_weight_kg") or "").strip()
                row.patient_weight_kg = raw_weight or None

            row.save()
            return JsonResponse(
                {
                    "ok": True,
                    "entry_type": "contrast",
                    "entry_id": str(row.id),
                    "contrast_name": row.contrast_name,
                    "concentration_mg_ml": str(row.concentration_mg_ml),
                    "volume_ml": str(row.volume_ml),
                    "total_mg": str(row.total_mg),
                    "route": row.route,
                    "lot_number": row.lot_number,
                }
            )

        row = get_object_or_404(MaterialUsage, id=entry_id, exam_id=exam.id)

        if "material_item_id" in data:
            material_item_id = str(data.get("material_item_id") or "").strip()
            if material_item_id:
                row.material_item = MaterialCatalog.objects.filter(id=material_item_id, is_active=True).first()
            else:
                row.material_item = None
        if "material_name" in data:
            row.material_name = str(data.get("material_name") or "").strip()
        if "measurement_id" in data:
            measurement_id = str(data.get("measurement_id") or "").strip()
            if measurement_id:
                row.measurement = MaterialMeasurement.objects.filter(id=measurement_id, is_active=True).first()
            else:
                row.measurement = None
        if "unit" in data:
            row.unit = str(data.get("unit") or "").strip()
        if "quantity" in data:
            row.quantity = data.get("quantity")

        row.save()
        return JsonResponse(
            {
                "ok": True,
                "entry_type": "material",
                "entry_id": str(row.id),
                "material_name": row.material_name,
                "measurement_code": str(getattr(row.measurement, "code", "") or ""),
                "unit": row.unit,
                "quantity": str(row.quantity),
            }
        )
    except ValidationError as exc:
        if hasattr(exc, "message_dict"):
            return JsonResponse({"error": exc.message_dict}, status=400)
        return JsonResponse({"error": exc.messages}, status=400)


@app_permission_required(Permission.REPORT_VIEW)
def contrast_materials_analytics_page(request):
    visible_exams = _contrast_exam_queryset_for_user(request.user)
    exam_ids = list(visible_exams.values_list("id", flat=True))
    filter_state = _contrast_documented_filter_state(request)

    contrast_qs = ContrastUsage.objects.filter(exam_id__in=exam_ids).select_related("exam", "exam__modality")
    material_qs = MaterialUsage.objects.filter(exam_id__in=exam_ids).select_related("exam", "exam__modality")

    documented_exams = visible_exams.filter(
        Q(contrast_entry_count__gt=0) | Q(material_entry_count__gt=0)
    ).count()

    totals = {
        "total_exams": len(exam_ids),
        "documented_exams": documented_exams,
        "pending_exams": max(len(exam_ids) - documented_exams, 0),
        "total_patients": visible_exams.values("mrn").exclude(mrn="").distinct().count(),
        "contrast_entries": contrast_qs.count(),
        "material_entries": material_qs.count(),
        "total_contrast_mg": contrast_qs.aggregate(total=Sum("total_mg")).get("total"),
    }

    per_modality = list(
        visible_exams.values("modality__code", "modality__name")
        .annotate(
            exam_count=Count("id", distinct=True),
            documented_count=Count(
                "id",
                filter=Q(contrast_usages__isnull=False) | Q(material_usages__isnull=False),
                distinct=True,
            ),
            contrast_entries=Count("contrast_usages", distinct=True),
            material_entries=Count("material_usages", distinct=True),
        )
        .order_by("modality__code")
    )
    for row in per_modality:
        exam_count = int(row.get("exam_count") or 0)
        documented_count = int(row.get("documented_count") or 0)
        row["pending_count"] = max(exam_count - documented_count, 0)

    per_patient = list(
        visible_exams.values("mrn", "patient_name")
        .annotate(
            exam_count=Count("id", distinct=True),
            modality_count=Count("modality", distinct=True),
            contrast_entries=Count("contrast_usages", distinct=True),
            material_entries=Count("material_usages", distinct=True),
        )
        .order_by("-contrast_entries", "-material_entries", "patient_name")[:200]
    )
    for row in per_patient:
        material_entries = int(row.get("material_entries") or 0)
        contrast_entries = int(row.get("contrast_entries") or 0)
        row["documentation_status"] = "Documented" if (material_entries > 0 or contrast_entries > 0) else "Pending"
        row["patient_label"] = str(row.get("patient_name") or "").strip() or "Unknown Patient"
        row["mrn_label"] = str(row.get("mrn") or "").strip() or "-"

    top_materials = list(
        material_qs.values(
            "material_name",
            "material_item__material_code",
            "material_item__name",
            "material_item__charge_code",
            "material_item__nphies_code",
            "material_item__typical_cost_sar",
            "material_item__default_price_sar",
            "material_item__category",
        )
        .annotate(
            entries=Count("id"),
            total_quantity=Sum("quantity"),
            patient_count=Count("exam__mrn", distinct=True),
            modality_count=Count("exam__modality", distinct=True),
        )
        .order_by("-entries", "material_item__name", "material_name")[:80]
    )
    for row in top_materials:
        row["material_label"] = (
            str(row.get("material_item__name") or "").strip()
            or str(row.get("material_name") or "").strip()
            or "-"
        )
        row["material_code"] = str(row.get("material_item__material_code") or "").strip() or "-"
        row["charge_code"] = str(row.get("material_item__charge_code") or "").strip() or "-"
        row["nphies_code"] = str(row.get("material_item__nphies_code") or "").strip() or "-"
        row["category"] = str(row.get("material_item__category") or "").strip() or "-"

    top_contrasts = list(
        contrast_qs.values("contrast_name", "route")
        .annotate(
            entries=Count("id"),
            total_volume_ml=Sum("volume_ml"),
            total_mg=Sum("total_mg"),
            patient_count=Count("exam__mrn", distinct=True),
            modality_count=Count("exam__modality", distinct=True),
        )
        .order_by("-entries", "contrast_name")[:50]
    )
    documented_rows = _build_contrast_documented_rows(exam_ids, filter_state, limit=500)
    modality_options = list(
        visible_exams.values("modality__code", "modality__name")
        .distinct()
        .order_by("modality__code")
    )

    context = {
        "current_nav": "contrast-analytics",
        "totals": totals,
        "per_modality": per_modality,
        "per_patient": per_patient,
        "top_materials": top_materials,
        "top_contrasts": top_contrasts,
        "documented_rows": documented_rows,
        "modality_options": modality_options,
        "filters": {
            "date_from": filter_state["date_from"].isoformat() if filter_state["date_from"] else "",
            "date_to": filter_state["date_to"].isoformat() if filter_state["date_to"] else "",
            "month": filter_state["month_raw"],
            "modality": filter_state["modality"],
            "entry_kind": filter_state["entry_kind"],
            "item_type": filter_state["item_type"],
        },
        "export_csv_url": _contrast_documentation_export_url(filter_state),
        "can_export": request.user.has_permission(Permission.REPORT_EXPORT),
        "worklist_url": reverse("contrast-materials"),
    }
    return render(request, "contrast/analytics.html", context)


@app_permission_required(Permission.REPORT_EXPORT)
@require_http_methods(["GET"])
def contrast_materials_analytics_export_csv(request):
    visible_exams = _contrast_exam_queryset_for_user(request.user)
    exam_ids = list(visible_exams.values_list("id", flat=True))
    filter_state = _contrast_documented_filter_state(request)
    rows = _build_contrast_documented_rows(exam_ids, filter_state, limit=0)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="contrast_documented_list.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "Documented At",
            "Entry Kind",
            "Modality",
            "Accession Number",
            "Order ID",
            "Patient",
            "Type",
            "Name",
            "Quantity",
            "Quantity Unit",
            "Total mg",
        ]
    )
    for row in rows:
        documented_at = row.get("documented_at")
        writer.writerow(
            [
                documented_at.strftime("%Y-%m-%d %H:%M") if documented_at else "",
                row.get("entry_kind", ""),
                row.get("modality_code", ""),
                row.get("accession_number", ""),
                row.get("order_id", ""),
                row.get("patient_name", ""),
                row.get("type_value", ""),
                row.get("item_name", ""),
                row.get("quantity_value", ""),
                row.get("quantity_unit", ""),
                row.get("total_mg", ""),
            ]
        )

    return response


def _has_system_admin_resource_access(user, resource_key: str, action: str) -> bool:
    if user.has_permission(Permission.ADMIN_ACCESS):
        return True

    if resource_key == "material_catalog":
        if action == "list":
            return (
                user.has_permission(Permission.MATERIAL_CATALOG_ADD)
                or user.has_permission(Permission.MATERIAL_CATALOG_EDIT)
            )
        if action == "create":
            return user.has_permission(Permission.MATERIAL_CATALOG_ADD)
        if action == "edit":
            return user.has_permission(Permission.MATERIAL_CATALOG_EDIT)

    return False


def _ensure_system_admin_resource_access(user, resource_key: str, action: str) -> None:
    if _has_system_admin_resource_access(user, resource_key, action):
        return
    raise PermissionDenied(f"Missing permission for {resource_key}:{action}")


@app_permission_required(Permission.ADMIN_ACCESS)
def system_admin_page(request):
    """Internal admin dashboard with native management screens."""
    summary_cards = [
        {
            'label': 'Facilities',
            'value': Facility.objects.count(),
            'detail': f"{Facility.objects.filter(is_active=True).count()} active",
        },
        {
            'label': 'Modalities',
            'value': Modality.objects.count(),
            'detail': f"{Modality.objects.filter(is_active=True).count()} active",
        },
        {
            'label': 'Procedures',
            'value': Procedure.objects.count(),
            'detail': 'dictionary records',
        },
        {
            'label': 'Exams',
            'value': Exam.objects.count(),
            'detail': f"{Exam.objects.filter(status='SCHEDULED').count()} scheduled",
        },
        {
            'label': 'Protocols',
            'value': ProtocolTemplate.objects.count(),
            'detail': f"{ProtocolTemplate.objects.filter(is_active=True).count()} active",
        },
        {
            'label': 'Assignments',
            'value': ProtocolAssignment.objects.count(),
            'detail': f"{ProtocolAssignment.objects.filter(status='PENDING').count()} pending",
        },
        {
            'label': 'Users',
            'value': User.objects.count(),
            'detail': f"{User.objects.filter(is_active=True).count()} active",
        },
        {
            'label': 'Groups',
            'value': Group.objects.count(),
            'detail': 'role-based access',
        },
        {
            'label': 'Sessions',
            'value': UserSession.objects.count(),
            'detail': f"{UserSession.objects.filter(is_active=True).count()} active",
        },
        {
            'label': 'HL7 Messages',
            'value': HL7Message.objects.count(),
            'detail': f"{HL7Message.objects.filter(status='PROCESSED').count()} processed",
        },
    ]

    admin_sections = []
    for title, description, resource_keys in SYSTEM_ADMIN_SECTIONS:
        links = []
        for resource_key in resource_keys:
            config = _get_resource_config(resource_key)
            urls = _resource_urls(resource_key)
            issues_url = None
            if resource_key == 'hl7_messages':
                issues_url = reverse('system-admin-hl7-issues')
            links.append({
                'label': config['label'],
                'description': config['description'],
                'count': config['model'].objects.count(),
                'manage_url': urls['list_url'],
                'add_url': urls['create_url'] if config.get('allow_create', True) else None,
                'issues_url': issues_url,
            })

        admin_sections.append({
            'title': title,
            'description': description,
            'links': links,
        })

    quick_links = [
        {
            'label': _get_resource_config(resource_key)['label'],
            'description': _get_resource_config(resource_key)['description'],
            'url': _resource_urls(resource_key)['list_url'],
        }
        for resource_key in ('exams', 'assignments', 'protocols', 'users', 'groups', 'hl7_messages')
    ]

    context = {
        'summary_cards': summary_cards,
        'admin_sections': admin_sections,
        'quick_links': quick_links,
        'primary_admin_url': _resource_urls('users')['list_url'],
    }
    return render(request, 'system_admin/dashboard.html', context)


@login_required
def system_admin_resource_list(request, resource_key):
    _ensure_system_admin_resource_access(request.user, resource_key, "list")
    try:
        config = _get_resource_config(resource_key)
    except KeyError:
        return JsonResponse({'error': 'Unknown resource'}, status=404)

    queryset = config['model'].objects.all()
    queryset = _apply_search(queryset, config, request.GET.get('q', '').strip())

    ordering = config.get('ordering')
    if ordering:
        queryset = queryset.order_by(*ordering)

    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'resource_key': resource_key,
        'resource_label': config['label'],
        'resource_description': config['description'],
        'columns': [
            _get_column_label(config['model'], field_name)
            for field_name in config.get('list_fields', ())
        ],
        'rows': _build_rows(page_obj, config, resource_key),
        'page_obj': page_obj,
        'search_query': request.GET.get('q', '').strip(),
        'dashboard_url': reverse('system-admin'),
        'create_url': (
            _resource_urls(resource_key)['create_url']
            if config.get('allow_create', True)
            and _has_system_admin_resource_access(request.user, resource_key, "create")
            else None
        ),
    }
    if resource_key == 'hl7_messages':
        context['extra_actions'] = [
            {
                'label': 'Rejected & Errors',
                'url': reverse('system-admin-hl7-issues'),
            }
        ]
    return render(request, 'system_admin/resource_list.html', context)


@login_required
def system_admin_resource_create(request, resource_key):
    _ensure_system_admin_resource_access(request.user, resource_key, "create")
    try:
        config = _get_resource_config(resource_key)
    except KeyError:
        return JsonResponse({'error': 'Unknown resource'}, status=404)

    if not config.get('allow_create', True):
        return JsonResponse({'error': 'Creation is not available for this resource'}, status=405)

    form_class = _get_form_class(config)
    form = form_class(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, f'{_singular_label(config["label"])} created.')
        if _has_system_admin_resource_access(request.user, resource_key, "edit"):
            return redirect('system-admin-resource-update', resource_key=resource_key, object_id=obj.pk)
        return redirect('system-admin-resource-list', resource_key=resource_key)

    context = {
        'resource_key': resource_key,
        'resource_label': config['label'],
        'resource_description': config['description'],
        'form': form,
        'dashboard_url': reverse('system-admin'),
        'list_url': reverse('system-admin-resource-list', args=[resource_key]),
        'create_url': reverse('system-admin-resource-create', args=[resource_key]) if config.get('allow_create', True) else None,
        'page_title': f'Add {config["label"]}',
        'submit_label': 'Create record',
    }
    if resource_key == 'material_usages':
        context.update(_material_usage_form_context(form))
    return render(request, 'system_admin/resource_form.html', context)


@login_required
def system_admin_resource_update(request, resource_key, object_id):
    _ensure_system_admin_resource_access(request.user, resource_key, "edit")
    try:
        config = _get_resource_config(resource_key)
    except KeyError:
        return JsonResponse({'error': 'Unknown resource'}, status=404)

    if not config.get('allow_edit', True):
        return JsonResponse({'error': 'Editing is not available for this resource'}, status=405)

    obj = get_object_or_404(config['model'], pk=object_id)
    form_class = _get_form_class(config)
    form = form_class(request.POST or None, instance=obj)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'{_singular_label(config["label"])} updated.')
        return redirect('system-admin-resource-update', resource_key=resource_key, object_id=obj.pk)

    context = {
        'resource_key': resource_key,
        'resource_label': config['label'],
        'resource_description': config['description'],
        'form': form,
        'object_label': str(obj),
        'dashboard_url': reverse('system-admin'),
        'list_url': reverse('system-admin-resource-list', args=[resource_key]),
        'create_url': (
            reverse('system-admin-resource-create', args=[resource_key])
            if config.get('allow_create', True)
            and _has_system_admin_resource_access(request.user, resource_key, "create")
            else None
        ),
        'page_title': f'Edit {config["label"]}',
        'submit_label': 'Save changes',
    }
    if resource_key == 'material_usages':
        context.update(_material_usage_form_context(form, obj=obj))
    return render(request, 'system_admin/resource_form.html', context)


@app_permission_required(Permission.ADMIN_ACCESS)
def system_admin_hl7_message_detail(request, object_id):
    message = get_object_or_404(
        HL7Message.objects.select_related('exam'),
        pk=object_id,
    )

    parsed_data = dict(message.parsed_data or {})
    context = {
        'message': message,
        'overview_fields': _hl7_message_overview(message),
        'segment_cards': _split_hl7_segments(message.raw_message),
        'segment_interpretations': _hl7_segment_interpretation(message),
        'parsed_data_pretty': json.dumps(parsed_data, indent=2, ensure_ascii=False) if parsed_data else '',
        'dashboard_url': reverse('system-admin'),
        'list_url': reverse('system-admin-resource-list', args=['hl7_messages']),
    }
    return render(request, 'system_admin/hl7_message_detail.html', context)


@app_permission_required(Permission.ADMIN_ACCESS)
def system_admin_hl7_issues(request):
    selected_range, start_date, end_date, start_dt, end_dt = _hl7_reporting_window(
        request.GET.get("range")
    )

    inbound_qs = HL7Message.objects.filter(
        direction='INBOUND',
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    )
    candidate_qs = inbound_qs.filter(
        Q(message_type__iexact='ORM^O01')
        | Q(message_type__iexact='ORR^O02')
        | Q(message_type__istartswith='SIU^')
    )
    issues_qs = (
        candidate_qs
        .filter(status__in=('REJECTED', 'ERROR'))
        .select_related('exam')
        .order_by('-created_at')
    )

    category_counter = Counter()
    for status_value, technical_reason in issues_qs.values_list("status", "error_message"):
        reason_context = _hl7_issue_reason_context_values(status_value, technical_reason)
        category_counter[reason_context["category"]] += 1

    rows = []
    for item in issues_qs[:500]:
        reason_context = _hl7_issue_reason_context(item)
        rows.append(
            {
                "message": item,
                "status": item.status,
                "category": reason_context["category"],
                "technical_reason": reason_context["technical_reason"],
                "plain_reason": reason_context["plain_reason"],
                "next_step": reason_context["next_step"],
                "summary": _hl7_message_plain_summary(item),
                "detail_url": reverse('system-admin-hl7-message-detail', args=[item.id]),
            }
        )

    top_reasons = list(
        issues_qs
        .exclude(error_message__isnull=True)
        .exclude(error_message__exact="")
        .values("error_message")
        .annotate(total=Count("id"))
        .order_by("-total", "error_message")[:8]
    )

    category_breakdown = [
        {"label": label, "count": count}
        for label, count in category_counter.most_common()
    ]

    summary = {
        "parsed_blocks": inbound_qs.count(),
        "candidate_messages": candidate_qs.count(),
        "processed_count": candidate_qs.filter(status='PROCESSED').count(),
        "rejected_count": candidate_qs.filter(status='REJECTED').count(),
        "error_count": candidate_qs.filter(status='ERROR').count(),
        "duplicate_count": candidate_qs.filter(
            status='REJECTED',
            error_message__icontains='duplicate',
        ).count(),
    }

    context = {
        "rows": rows,
        "summary": summary,
        "category_breakdown": category_breakdown,
        "top_reasons": top_reasons,
        "selected_range": selected_range,
        "range_start": start_date,
        "range_end": end_date,
        "dashboard_url": reverse('system-admin'),
        "hl7_list_url": reverse('system-admin-resource-list', args=['hl7_messages']),
    }
    return render(request, 'system_admin/hl7_issues.html', context)


@app_permission_required(Permission.PROTOCOL_VIEW)
def exams_api(request):
    """API endpoint for exams list"""
    from apps.core.models import Exam

    def display_name(user):
        if not user:
            return ''

        full_name = ''
        if hasattr(user, 'get_full_name'):
            full_name = (user.get_full_name() or '').strip()

        return full_name or getattr(user, 'username', '') or ''
    
    try:
        exams_qs = (
            Exam.objects.select_related(
                'modality',
                'facility',
                'protocol_assignment__protocol',
                'protocol_assignment__assigned_by',
                'protocol_assignment__acknowledged_by',
            ).filter(
                modality__code__in=PROTOCOL_REQUIRED_MODALITY_CODES,
                modality__is_active=True,
            )
        )

        exams_qs = apply_facility_scope(exams_qs, request.user)

        exams = list(exams_qs.order_by('-exam_datetime')[:1000])
        visible_procedure_codes = set(
            Procedure.objects.filter(
                is_active=True,
                modality__code__in=PROTOCOL_REQUIRED_MODALITY_CODES,
            ).values_list('code', flat=True)
        )
        configured_procedure_codes = set(
            Procedure.objects.filter(
                modality__code__in=PROTOCOL_REQUIRED_MODALITY_CODES,
            ).values_list('code', flat=True)
        )
        exams = [
            exam for exam in exams
            if _is_exam_visible_in_protocol_workflow(
                exam,
                visible_procedure_codes=visible_procedure_codes,
                configured_procedure_codes=configured_procedure_codes,
            )
        ]
        procedure_code_map = {
            str(code or '').strip().upper(): str(body_region or '').strip()
            for code, body_region in Procedure.objects.filter(
                code__in={
                    str(getattr(exam, 'procedure_code', '') or '').strip()
                    for exam in exams
                    if str(getattr(exam, 'procedure_code', '') or '').strip()
                }
            ).values_list('code', 'body_region')
        }

        def exam_body_part(exam):
            metadata = dict(getattr(exam, 'metadata', {}) or {})
            explicit_body_part = (
                str(metadata.get('body_part') or metadata.get('body_region') or '').strip()
            )
            if explicit_body_part:
                return explicit_body_part
            return procedure_code_map.get(str(getattr(exam, 'procedure_code', '') or '').strip().upper(), '')

        def exam_order_datetime(exam):
            metadata = dict(getattr(exam, 'metadata', {}) or {})
            candidates = [
                metadata.get('hl7_order_datetime'),
                ((metadata.get('hl7_payload') or {}).get('order') or {}).get('order_datetime'),
                ((metadata.get('hl7_response_payload') or {}).get('order') or {}).get('order_datetime'),
            ]

            for candidate in candidates:
                value = str(candidate or '').strip()
                if value:
                    return value

            return None

        viewer = {
            'role': _role(request.user),
            'can_assign_protocol': _can_access_radiologist_review(request.user),
            'can_review_protocol': _can_access_radiologist_review(request.user),
            'can_view_protocol': _can_access_technologist_review(request.user),
            'can_confirm_protocol': _can_access_technologist_review(request.user),
            'can_view_contrast': request.user.has_permission(Permission.CONTRAST_VIEW),
            'default_subspeciality': _user_default_subspeciality(request.user),
            'subspeciality_pool': list(SUBSPECIALITY_POOL),
        }
        
        results = []
        for exam in exams:
            resolved_body_part = exam_body_part(exam)
            subspeciality, inferred_subspeciality = resolve_exam_subspeciality(
                exam,
                body_region=resolved_body_part,
            )
            effective_exam_status = _effective_exam_status(exam)

            results.append(
                {
                    'id': str(exam.id),
                    'order_id': exam.order_id,
                    'order_datetime': exam_order_datetime(exam),
                    'accession_number': exam.accession_number,
                    'patient_name': exam.patient_name,
                    'patient_dob': exam.patient_dob.isoformat() if exam.patient_dob else None,
                    'patient_class': exam.patient_class,
                    'patient_class_label': exam.patient_class_display,
                    'mrn': exam.mrn,
                    'clinical_history': exam.clinical_history,
                    'modality': {
                        'code': exam.modality.code,
                        'name': exam.modality.name
                    },
                    'procedure_name': exam.procedure_name,
                    'body_part': resolved_body_part,
                    'subspeciality': subspeciality,
                    'inferred_subspeciality': inferred_subspeciality,
                    'scheduled_datetime': (
                        exam.scheduled_datetime.isoformat()
                        if exam.scheduled_datetime else None
                    ),
                    'exam_datetime': exam.exam_datetime.isoformat() if exam.exam_datetime else None,
                    'exam_status': effective_exam_status,
                    'exam_status_label': _exam_status_label(effective_exam_status),
                    'facility': {
                        'code': exam.facility.code,
                        'name': exam.facility.name
                    },
                    'has_protocol': exam.has_protocol,
                    'protocol_not_required': bool((exam.metadata or {}).get('protocol_not_required')),
                    'protocol_not_required_by': str((exam.metadata or {}).get('protocol_not_required_by') or ''),
                    'protocol_not_required_at': (
                        str((exam.metadata or {}).get('protocol_not_required_at') or '') or None
                    ),
                    'workflow_status': exam.protocol_workflow_status,
                    'assignment_status': (
                        exam.protocol_assignment.status
                        if exam.has_protocol else ''
                    ),
                    'assigned_protocol': (
                        {
                            'code': exam.protocol_assignment.protocol.code,
                            'name': exam.protocol_assignment.protocol.name,
                        }
                        if exam.has_protocol else None
                    ),
                    'assigned_by': (
                        display_name(exam.protocol_assignment.assigned_by)
                        if exam.has_protocol else ''
                    ),
                    'radiologist_name': (
                        display_name(exam.protocol_assignment.assigned_by)
                        if exam.has_protocol else ''
                    ),
                    'assigned_at': (
                        exam.protocol_assignment.assigned_at.isoformat()
                        if exam.has_protocol and exam.protocol_assignment.assigned_at else None
                    ),
                    'technologist_name': (
                        (
                            display_name(exam.protocol_assignment.acknowledged_by)
                            or (exam.technologist or '')
                        )
                        if exam.has_protocol else ''
                    ),
                    'acknowledged_at': (
                        exam.protocol_assignment.acknowledged_at.isoformat()
                        if exam.has_protocol and exam.protocol_assignment.acknowledged_at else None
                    ),
                    'review_url': reverse('protocoling-radiologist-review', args=[exam.id]),
                    'technologist_view_url': reverse('protocoling-technologist-view', args=[exam.id]),
                    'technologist_print_url': (
                        reverse('protocoling-technologist-print', args=[exam.id])
                        if exam.has_protocol else ''
                    ),
                    'can_open_contrast': _can_access_contrast_exam(request.user, exam),
                    'contrast_review_url': reverse('contrast-materials-review', args=[exam.id]),
                }
            )

        data = {
            'viewer': viewer,
            'results': results,
        }
        
        return JsonResponse(data)
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@app_permission_required(Permission.PROTOCOL_ASSIGN)
@require_http_methods(["POST"])
def mark_exam_protocol_not_required(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if not can_access_facility(request.user, exam.facility_id):
        return JsonResponse({'error': 'You do not have access to this exam facility.'}, status=403)

    if exam.has_protocol:
        return JsonResponse(
            {'error': 'This exam already has a protocol assignment.'},
            status=409,
        )

    metadata = dict(exam.metadata or {})
    if metadata.get('protocol_not_required'):
        return JsonResponse(
            {
                'ok': True,
                'status': 'NOT_REQUIRED',
                'marked_at': metadata.get('protocol_not_required_at'),
                'marked_by': metadata.get('protocol_not_required_by', ''),
            }
        )

    full_name = ''
    if hasattr(request.user, 'get_full_name'):
        full_name = (request.user.get_full_name() or '').strip()

    metadata['protocol_not_required'] = True
    metadata['protocol_not_required_at'] = timezone.now().isoformat()
    metadata['protocol_not_required_by'] = full_name or getattr(request.user, 'username', '') or ''

    exam.metadata = metadata
    exam.save(update_fields=['metadata'])

    return JsonResponse(
        {
            'ok': True,
            'status': 'NOT_REQUIRED',
            'marked_at': metadata['protocol_not_required_at'],
            'marked_by': metadata['protocol_not_required_by'],
        }
    )


@app_permission_required(Permission.PROTOCOL_ASSIGN)
@require_http_methods(["POST"])
def set_exam_subspeciality(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)

    if exam.has_protocol:
        return JsonResponse(
            {'error': 'Subspeciality can only be changed before protocol assignment.'},
            status=409,
        )

    if not can_access_facility(request.user, exam.facility_id):
        return JsonResponse({'error': 'You do not have access to this exam facility.'}, status=403)

    raw_value = str(
        request.POST.get('subspeciality')
        or request.POST.get('subspecialty')
        or ''
    ).strip()

    if not raw_value and request.body:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
            raw_value = str(
                payload.get('subspeciality')
                or payload.get('subspecialty')
                or ''
            ).strip()
        except Exception:
            raw_value = ''

    value = normalize_subspeciality(raw_value)
    if not value:
        return JsonResponse(
            {
                'error': 'Invalid subspeciality value.',
                'allowed_values': list(SUBSPECIALITY_POOL),
            },
            status=400,
        )

    procedure_body_region = ""
    procedure_code = str(getattr(exam, "procedure_code", "") or "").strip()
    if procedure_code:
        procedure_body_region = (
            Procedure.objects.filter(code__iexact=procedure_code)
            .values_list("body_region", flat=True)
            .first()
            or ""
        )

    metadata = dict(exam.metadata or {})
    resolved_body_region = (
        str(metadata.get("body_part") or metadata.get("body_region") or "").strip()
        or str(procedure_body_region or "").strip()
    )
    current_subspeciality, _ = resolve_exam_subspeciality(
        exam,
        body_region=resolved_body_region,
    )

    actor_name = ""
    if hasattr(request.user, "get_full_name"):
        actor_name = (request.user.get_full_name() or "").strip()
    actor_name = actor_name or getattr(request.user, "username", "") or "System"

    metadata = append_subspeciality_change_event(
        metadata,
        previous_subspeciality=current_subspeciality,
        new_subspeciality=value,
        changed_by=actor_name,
        changed_at=timezone.now(),
    )
    metadata['subspeciality'] = value
    metadata['subspecialty'] = value
    exam.metadata = metadata
    exam.save(update_fields=['metadata'])

    return JsonResponse(
        {
            'ok': True,
            'exam_id': str(exam.id),
            'subspeciality': value,
        }
    )
