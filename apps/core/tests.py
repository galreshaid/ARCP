import json
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from decimal import Decimal
from tempfile import NamedTemporaryFile

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.constants import UserRole
from apps.core.models import (
    ContrastUsage,
    Exam,
    ExamStatus,
    Facility,
    MaterialCatalog,
    MaterialMeasurement,
    ProcedureMaterialBundle,
    ProcedureMaterialBundleItem,
    MaterialUsage,
    Modality,
    Procedure,
)
from apps.core.services.icd10_lookup import lookup_icd10_description
from apps.core.services.hl7_orm import ingest_orm_message
from apps.core.services.hl7_orr import ingest_orr_message
from apps.core.services.hl7_siu import ingest_siu_message
from apps.hl7_core.models import HL7Message
from apps.protocols.models import ProtocolAssignment, ProtocolTemplate
from apps.users.models import User, UserPreference


class ICD10LookupTests(SimpleTestCase):
    SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ClaML version="2.0.0">
  <Class code="T14.9" kind="category">
    <Rubric kind="preferred">
      <Label xml:lang="en" xml:space="default">Injury, unspecified</Label>
    </Rubric>
  </Class>
</ClaML>
"""

    def test_lookup_icd10_description_uses_xml_preferred_label(self):
        with TemporaryDirectory() as temp_dir:
            xml_path = Path(temp_dir) / "icd10.xml"
            xml_path.write_text(self.SAMPLE_XML, encoding="utf-8")

            with override_settings(ICD10_XML_PATH=str(xml_path)):
                self.assertEqual(lookup_icd10_description("T14.9"), "Injury, unspecified")
                self.assertEqual(lookup_icd10_description("T149"), "Injury, unspecified")

    def test_exam_property_prefers_xml_lookup_over_raw_hl7_text(self):
        with TemporaryDirectory() as temp_dir:
            xml_path = Path(temp_dir) / "icd10.xml"
            xml_path.write_text(self.SAMPLE_XML, encoding="utf-8")

            with override_settings(ICD10_XML_PATH=str(xml_path)):
                exam = Exam(
                    metadata={
                        "hl7_icd10_code": "T14.9",
                        "hl7_icd10_description": "T14.9",
                    }
                )

                self.assertEqual(exam.icd_10_description, "Injury, unspecified")


class SystemAdminPageTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_superuser(
            email='admin@example.com',
            password='password123',
            username='admin',
            first_name='Admin',
            last_name='User',
        )

    def test_staff_user_can_access_internal_admin_page(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse('system-admin'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Internal Admin Console')
        self.assertNotContains(response, 'Open Django Admin')

    def test_anonymous_user_is_redirected_to_admin_login(self):
        response = self.client.get(reverse('system-admin'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_admin_user_can_access_internal_resource_list(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse('system-admin-resource-list', args=['users'])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'User accounts, access, and role configuration.')

    def test_admin_user_can_access_hl7_message_logs(self):
        self.client.force_login(self.staff_user)
        HL7Message.objects.create(
            direction='INBOUND',
            message_type='ORM^O01',
            message_control_id='TEST-001',
            raw_message='MSH|^~\\&|TEST',
            status='PROCESSED',
        )

        response = self.client.get(
            reverse('system-admin-resource-list', args=['hl7_messages'])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HL7 Message Logs')
        self.assertContains(response, 'TEST-001')
        self.assertContains(response, 'Rejected &amp; Errors')

    def test_admin_user_can_access_hl7_issues_page_with_plain_explanations(self):
        self.client.force_login(self.staff_user)
        HL7Message.objects.create(
            direction='INBOUND',
            message_type='ORM^O01',
            message_control_id='DUP-001',
            raw_message='MSH|^~\\&|TEST',
            status='REJECTED',
            error_message='Duplicate message control ID DUP-001.',
        )
        HL7Message.objects.create(
            direction='INBOUND',
            message_type='SIU^S12',
            message_control_id='ERR-001',
            raw_message='MSH|^~\\&|TEST',
            status='ERROR',
            error_message='Failed to process inbound HL7 message after ACK.',
        )

        response = self.client.get(
            reverse('system-admin-hl7-issues'),
            {'range': 'today'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HL7 Rejected &amp; Error Messages')
        self.assertContains(response, 'Duplicate Message Control ID')
        self.assertContains(response, 'prevent duplicate exams')

    def test_admin_user_can_open_hl7_message_detail_view(self):
        self.client.force_login(self.staff_user)
        message = HL7Message.objects.create(
            direction='INBOUND',
            message_type='ORM^O01',
            message_control_id='TEST-DETAIL-001',
            raw_message='MSH|^~\\&|EPIC|KFMC|AIP|RKF|20260304120000||ORM^O01|TEST-DETAIL-001|P|2.3.1\rPID|1||12345||TEST^PATIENT',
            status='PROCESSED',
            parsed_data={
                'message_info': {
                    'message_type': 'ORM^O01',
                    'message_control_id': 'TEST-DETAIL-001',
                    'sending_application': 'EPIC',
                    'sending_facility': 'KFMC',
                    'receiving_application': 'AIP',
                    'receiving_facility': 'RKF',
                },
                'patient': {
                    'mrn': '12345',
                    'patient_name': {
                        'family': 'TEST',
                        'given': 'PATIENT',
                    },
                },
            },
        )

        response = self.client.get(
            reverse('system-admin-hl7-message-detail', args=[message.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'HL7 Message Detail')
        self.assertContains(response, 'TEST-DETAIL-001')
        self.assertContains(response, 'Message Header')
        self.assertContains(response, 'Patient Identification')

    def test_admin_user_can_access_material_catalog_resources(self):
        self.client.force_login(self.staff_user)

        catalog_response = self.client.get(
            reverse('system-admin-resource-list', args=['material_catalog'])
        )
        measurement_response = self.client.get(
            reverse('system-admin-resource-list', args=['material_measurements'])
        )

        self.assertEqual(catalog_response.status_code, 200)
        self.assertEqual(measurement_response.status_code, 200)
        self.assertContains(catalog_response, 'Material Catalog')
        self.assertContains(measurement_response, 'Material Measurements')


class ContrastMaterialUsageTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(
            code='CON',
            name='Contrast Facility',
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code='CT',
            name='Computed Tomography',
            is_active=True,
        )
        self.exam = Exam.objects.create(
            accession_number='CON-001',
            order_id='PEC-001',
            mrn='MRN-CON-001',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTA',
            procedure_name='CT Angio',
            patient_name='Contrast Patient',
            status='SCHEDULED',
            metadata={'patient_weight_kg': 60},
        )

    def test_contrast_usage_auto_calculates_total_mg(self):
        usage = ContrastUsage.objects.create(
            exam=self.exam,
            contrast_name='Iohexol',
            concentration_mg_ml=Decimal('350'),
            volume_ml=Decimal('75'),
            injection_rate_ml_s=Decimal('4.5'),
            route='IV',
            lot_number='LOT-100',
        )

        self.assertEqual(usage.total_mg, Decimal('26250.000'))
        self.assertEqual(usage.pec_number, 'PEC-001')

    @override_settings(CONTRAST_MAX_DOSE_MG_PER_KG=500)
    def test_contrast_usage_validates_max_dose_per_patient_weight(self):
        ContrastUsage.objects.create(
            exam=self.exam,
            contrast_name='Iohexol',
            concentration_mg_ml=Decimal('350'),
            volume_ml=Decimal('40'),
            route='IV',
        )

        with self.assertRaises(ValidationError):
            ContrastUsage.objects.create(
                exam=self.exam,
                contrast_name='Iohexol',
                concentration_mg_ml=Decimal('400'),
                volume_ml=Decimal('50'),
                route='IV',
            )

    def test_count_helpers_work_per_exam_and_pec(self):
        exam_two = Exam.objects.create(
            accession_number='CON-002',
            order_id='PEC-001',
            mrn='MRN-CON-002',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTA',
            procedure_name='CT Angio Follow-up',
            patient_name='Contrast Patient Two',
            status='SCHEDULED',
            metadata={'patient_weight_kg': 75},
        )
        ContrastUsage.objects.create(
            exam=self.exam,
            contrast_name='Iohexol',
            concentration_mg_ml=Decimal('300'),
            volume_ml=Decimal('50'),
            route='IV',
        )
        ContrastUsage.objects.create(
            exam=self.exam,
            contrast_name='Iohexol',
            concentration_mg_ml=Decimal('300'),
            volume_ml=Decimal('20'),
            route='IV',
        )
        ContrastUsage.objects.create(
            exam=exam_two,
            contrast_name='Iohexol',
            concentration_mg_ml=Decimal('300'),
            volume_ml=Decimal('30'),
            route='IV',
        )

        MaterialUsage.objects.create(
            exam=self.exam,
            material_name='Saline Flush',
            unit='ml',
            quantity=Decimal('10'),
        )
        MaterialUsage.objects.create(
            exam=exam_two,
            material_name='Syringe',
            unit='cc',
            quantity=Decimal('5'),
        )

        self.assertEqual(ContrastUsage.count_for_exam(self.exam), 2)
        self.assertEqual(ContrastUsage.count_for_pec('PEC-001'), 3)
        self.assertEqual(MaterialUsage.count_for_exam(self.exam), 1)
        self.assertEqual(MaterialUsage.count_for_pec('PEC-001'), 2)


class ContrastWorkflowViewsTests(TestCase):
    def setUp(self):
        self.facility = Facility.objects.create(code="CWF", name="Contrast Workflow Facility", is_active=True)
        self.modality = Modality.objects.create(code="CT", name="Computed Tomography", is_active=True)

        self.technologist = User.objects.create_user(
            email="contrast-tech@example.com",
            password="password123",
            username="contrasttech",
            first_name="Contrast",
            last_name="Tech",
            role=UserRole.TECHNOLOGIST,
        )
        self.technologist.facilities.add(self.facility)
        self.report_user = User.objects.create_user(
            email="contrast-report@example.com",
            password="password123",
            username="contrastreport",
            first_name="Finance",
            last_name="Store",
            role=UserRole.VIEWER,
        )
        self.report_user.facilities.add(self.facility)
        self.finance_user = User.objects.create_user(
            email="finance-analytics@example.com",
            password="password123",
            username="financeanalytics",
            first_name="Finance",
            last_name="Analyst",
            role=UserRole.FINANCE,
        )
        self.finance_user.facilities.add(self.facility)
        self.admin_user = User.objects.create_user(
            email="contrast-admin@example.com",
            password="password123",
            username="contrastadmin",
            first_name="Contrast",
            last_name="Admin",
            role=UserRole.ADMIN,
        )
        self.admin_user.facilities.add(self.facility)
        self.supervisor_user = User.objects.create_user(
            email="contrast-supervisor@example.com",
            password="password123",
            username="contrastsupervisor",
            first_name="Contrast",
            last_name="Supervisor",
            role=UserRole.SUPERVISOR,
        )
        self.supervisor_user.facilities.add(self.facility)

        self.exam_cm = Exam.objects.create(
            accession_number="CM-100",
            order_id="PEC-CM-100",
            mrn="MRN-CM-100",
            facility=self.facility,
            modality=self.modality,
            procedure_code="CTA",
            procedure_name="CT ANGIO BRAIN",
            patient_name="CM Patient",
            status=ExamStatus.COMPLETED,
            metadata={"hl7_order_status": "CM"},
        )
        self.exam_in_progress = Exam.objects.create(
            accession_number="CM-200",
            order_id="PEC-CM-200",
            mrn="MRN-CM-200",
            facility=self.facility,
            modality=self.modality,
            procedure_code="CTN",
            procedure_name="CT HEAD",
            patient_name="In Progress Patient",
            status=ExamStatus.IN_PROGRESS,
            metadata={"hl7_order_status": "IP"},
        )
        self.exam_cancelled = Exam.objects.create(
            accession_number="CM-300",
            order_id="PEC-CM-300",
            mrn="MRN-CM-300",
            facility=self.facility,
            modality=self.modality,
            procedure_code="CTA",
            procedure_name="CT Canceled Study",
            patient_name="Canceled Patient",
            status=ExamStatus.CANCELLED,
            metadata={"hl7_order_status": "CA"},
        )

        self.measurement_ml = MaterialMeasurement.objects.create(code="ml", label="Milliliter", is_active=True)
        self.catalog_syringe = MaterialCatalog.objects.create(
            name="Syringe",
            category="DISPOSABLE",
            default_measurement=self.measurement_ml,
            is_active=True,
        )
        MaterialUsage.objects.create(
            exam=self.exam_cm,
            material_item=self.catalog_syringe,
            material_name="Syringe",
            measurement=self.measurement_ml,
            unit="ml",
            quantity=Decimal("1"),
        )

    def test_contrast_exams_api_returns_in_progress_completed_and_cancelled(self):
        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-api-exams"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 3)
        rows_by_accession = {row["accession_number"]: row for row in payload["results"]}
        self.assertEqual(rows_by_accession["CM-100"]["exam_status"], ExamStatus.COMPLETED)
        self.assertEqual(rows_by_accession["CM-200"]["exam_status"], ExamStatus.IN_PROGRESS)
        self.assertEqual(rows_by_accession["CM-300"]["exam_status"], ExamStatus.CANCELLED)
        self.assertEqual(rows_by_accession["CM-100"]["material_status"], "Documented")
        self.assertEqual(rows_by_accession["CM-200"]["material_status"], "Pending")
        self.assertTrue(rows_by_accession["CM-100"]["can_open"])
        self.assertTrue(rows_by_accession["CM-200"]["can_open"])
        self.assertTrue(rows_by_accession["CM-300"]["can_open"])

    def test_contrast_exams_api_uses_hl7_status_mapping_for_existing_rows(self):
        stale_exam = Exam.objects.create(
            accession_number="CM-400",
            order_id="PEC-CM-400",
            mrn="MRN-CM-400",
            facility=self.facility,
            modality=self.modality,
            procedure_code="CTN",
            procedure_name="CT HAND",
            patient_name="Mapped From HL7",
            status=ExamStatus.IN_PROGRESS,
            metadata={"hl7_order_status": "SC"},
        )

        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-api-exams"))

        self.assertEqual(response.status_code, 200)
        rows_by_accession = {
            row["accession_number"]: row for row in response.json()["results"]
        }
        self.assertIn("CM-400", rows_by_accession)
        self.assertEqual(rows_by_accession["CM-400"]["id"], str(stale_exam.id))
        self.assertEqual(rows_by_accession["CM-400"]["exam_status"], ExamStatus.IN_PROGRESS)
        self.assertEqual(rows_by_accession["CM-400"]["exam_status_label"], "In Progress")

    def test_contrast_exams_api_excludes_order_status_rows(self):
        order_exam = Exam.objects.create(
            accession_number="CM-450",
            order_id="PEC-CM-450",
            mrn="MRN-CM-450",
            facility=self.facility,
            modality=self.modality,
            procedure_code="CTN",
            procedure_name="CT ORDER",
            patient_name="Order Contrast Patient",
            status=ExamStatus.ORDER,
            metadata={"hl7_order_status": "NW"},
        )

        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-api-exams"))

        self.assertEqual(response.status_code, 200)
        result_ids = {row["id"] for row in response.json()["results"]}
        self.assertNotIn(str(order_exam.id), result_ids)

    def test_contrast_exams_api_hides_modalities_with_contrast_disabled(self):
        modality_without_contrast = Modality.objects.create(
            code="XR",
            name="X-Ray",
            is_active=True,
            requires_contrast=False,
        )
        hidden_exam = Exam.objects.create(
            accession_number="CM-999",
            order_id="PEC-CM-999",
            mrn="MRN-CM-999",
            facility=self.facility,
            modality=modality_without_contrast,
            procedure_code="XR-HIDE",
            procedure_name="XR Hidden Exam",
            patient_name="Hidden Contrast Patient",
            status=ExamStatus.IN_PROGRESS,
            metadata={"hl7_order_status": "IP"},
        )

        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-api-exams"))

        self.assertEqual(response.status_code, 200)
        result_ids = {row["id"] for row in response.json()["results"]}
        self.assertIn(str(self.exam_cm.id), result_ids)
        self.assertNotIn(str(hidden_exam.id), result_ids)

    def test_contrast_exams_api_enforces_user_facility_scope(self):
        other_facility = Facility.objects.create(code="CWF2", name="Contrast Workflow Facility 2", is_active=True)
        hidden_exam = Exam.objects.create(
            accession_number="CM-401",
            order_id="PEC-CM-401",
            mrn="MRN-CM-401",
            facility=other_facility,
            modality=self.modality,
            procedure_code="CTA",
            procedure_name="CT CHEST",
            patient_name="Other Facility Contrast Patient",
            status=ExamStatus.IN_PROGRESS,
            metadata={"hl7_order_status": "IP"},
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("contrast-materials-api-exams"))

        self.assertEqual(response.status_code, 200)
        result_ids = {row["id"] for row in response.json()["results"]}
        self.assertIn(str(self.exam_cm.id), result_ids)
        self.assertNotIn(str(hidden_exam.id), result_ids)

    def test_contrast_exams_api_uses_primary_facility_scope_when_user_facilities_empty(self):
        other_facility = Facility.objects.create(code="CWF3", name="Contrast Workflow Facility 3", is_active=True)
        hidden_exam = Exam.objects.create(
            accession_number="CM-402",
            order_id="PEC-CM-402",
            mrn="MRN-CM-402",
            facility=other_facility,
            modality=self.modality,
            procedure_code="CTA",
            procedure_name="CT CHEST",
            patient_name="Other Primary Contrast Patient",
            status=ExamStatus.IN_PROGRESS,
            metadata={"hl7_order_status": "IP"},
        )

        scoped_user = User.objects.create_user(
            email="contrast-primary-scope@example.com",
            password="password123",
            username="contrastprimaryscope",
            first_name="Primary",
            last_name="Contrast Scope",
            role=UserRole.ADMIN,
            primary_facility=self.facility,
        )
        scoped_user.user_permissions.add(
            Permission.objects.get(content_type__app_label='users', codename='contrast_view')
        )

        self.client.force_login(scoped_user)
        response = self.client.get(reverse("contrast-materials-api-exams"))

        self.assertEqual(response.status_code, 200)
        result_ids = {row["id"] for row in response.json()["results"]}
        self.assertIn(str(self.exam_cm.id), result_ids)
        self.assertNotIn(str(hidden_exam.id), result_ids)

    def test_contrast_analytics_page_loads_for_report_user(self):
        self.client.force_login(self.report_user)
        response = self.client.get(reverse("contrast-materials-analytics"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contrast & Materials Analytics")
        self.assertContains(response, "Per Modality Summary")
        self.assertContains(response, "Per Patient Summary")

    def test_contrast_review_page_loads_for_in_progress_exam(self):
        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-review", args=[self.exam_in_progress.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contrast & Materials")
        self.assertContains(response, "Contrast (Catalog)")
        self.assertContains(response, "Material Search")
        self.assertContains(response, "Syringe")

    def test_contrast_review_page_includes_workflow_timeline(self):
        ContrastUsage.objects.create(
            exam=self.exam_in_progress,
            contrast_name="Iohexol",
            concentration_mg_ml=Decimal("300"),
            volume_ml=Decimal("50"),
            route="IV",
        )
        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-review", args=[self.exam_in_progress.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Workflow Timeline")
        self.assertContains(response, "Contrast Documented")

    def test_contrast_session_api_saves_contrast_and_material_entries_for_any_status(self):
        self.client.force_login(self.technologist)
        response = self.client.post(
            reverse("contrast-materials-api-session", args=[self.exam_in_progress.id]),
            data=json.dumps(
                {
                    "contrast_entries": [
                        {
                            "contrast_name": "Iohexol",
                            "concentration_mg_ml": "350",
                            "volume_ml": "60",
                            "route": "IV",
                            "lot_number": "LOT-77",
                        }
                    ],
                    "material_entries": [
                        {
                            "material_item_id": str(self.catalog_syringe.id),
                            "material_name": "Syringe",
                            "measurement_id": str(self.measurement_ml.id),
                            "unit": "",
                            "quantity": "1",
                        }
                    ],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContrastUsage.objects.filter(exam=self.exam_in_progress).count(), 1)
        self.assertEqual(MaterialUsage.objects.filter(exam=self.exam_in_progress).count(), 1)

        contrast = ContrastUsage.objects.filter(exam=self.exam_in_progress).first()
        material = MaterialUsage.objects.filter(exam=self.exam_in_progress).first()
        self.assertEqual(str(contrast.total_mg), "21000.000")
        self.assertEqual(material.unit, "ml")
        self.assertEqual(material.material_item_id, self.catalog_syringe.id)

    def test_contrast_session_api_accepts_blank_optional_numeric_fields(self):
        self.client.force_login(self.technologist)
        response = self.client.post(
            reverse("contrast-materials-api-session", args=[self.exam_in_progress.id]),
            data=json.dumps(
                {
                    "contrast_entries": [
                        {
                            "contrast_name": "Omni",
                            "concentration_mg_ml": "300",
                            "volume_ml": "50",
                            "injection_rate_ml_s": "",
                            "patient_weight_kg": "   ",
                            "route": "IV",
                        }
                    ],
                    "material_entries": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        entry = ContrastUsage.objects.get(exam=self.exam_in_progress, contrast_name="Omni")
        self.assertIsNone(entry.injection_rate_ml_s)
        self.assertIsNone(entry.patient_weight_kg)

    def test_contrast_session_api_returns_json_error_for_invalid_decimal_value(self):
        self.client.force_login(self.technologist)
        response = self.client.post(
            reverse("contrast-materials-api-session", args=[self.exam_in_progress.id]),
            data=json.dumps(
                {
                    "contrast_entries": [
                        {
                            "contrast_name": "Omni",
                            "concentration_mg_ml": "abc",
                            "volume_ml": "50",
                            "route": "IV",
                        }
                    ],
                    "material_entries": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid contrast row #1", response.json().get("error", ""))
        self.assertEqual(ContrastUsage.objects.filter(exam=self.exam_in_progress).count(), 0)

    def test_contrast_session_api_rejects_non_technologist_even_with_admin_permissions(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("contrast-materials-api-session", args=[self.exam_in_progress.id]),
            data=json.dumps(
                {
                    "contrast_entries": [
                        {
                            "contrast_name": "Iohexol",
                            "concentration_mg_ml": "350",
                            "volume_ml": "60",
                            "route": "IV",
                        }
                    ],
                    "material_entries": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Only technologists", response.json().get("error", ""))

    def test_saved_entry_update_api_allows_supervisor_and_admin(self):
        contrast_entry = ContrastUsage.objects.create(
            exam=self.exam_in_progress,
            contrast_name="Omni",
            concentration_mg_ml=Decimal("300"),
            volume_ml=Decimal("20"),
            route="IV",
        )
        material_entry = MaterialUsage.objects.create(
            exam=self.exam_in_progress,
            material_item=self.catalog_syringe,
            material_name="Syringe",
            measurement=self.measurement_ml,
            unit="ml",
            quantity=Decimal("1"),
        )

        self.client.force_login(self.supervisor_user)
        supervisor_response = self.client.post(
            reverse("contrast-materials-api-entry-update", args=[self.exam_in_progress.id]),
            data=json.dumps(
                {
                    "entry_type": "contrast",
                    "entry_id": str(contrast_entry.id),
                    "data": {
                        "contrast_name": "Omnipaque",
                        "concentration_mg_ml": "320",
                        "volume_ml": "25",
                        "route": "IV",
                        "lot_number": "LOT-NEW",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(supervisor_response.status_code, 200)
        contrast_entry.refresh_from_db()
        self.assertEqual(contrast_entry.contrast_name, "Omnipaque")
        self.assertEqual(str(contrast_entry.concentration_mg_ml), "320.000")
        self.assertEqual(str(contrast_entry.volume_ml), "25.000")
        self.assertEqual(contrast_entry.lot_number, "LOT-NEW")

        self.client.force_login(self.admin_user)
        admin_response = self.client.post(
            reverse("contrast-materials-api-entry-update", args=[self.exam_in_progress.id]),
            data=json.dumps(
                {
                    "entry_type": "material",
                    "entry_id": str(material_entry.id),
                    "data": {
                        "material_name": "Gloves",
                        "measurement_id": str(self.measurement_ml.id),
                        "unit": "pcs",
                        "quantity": "2",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(admin_response.status_code, 200)
        material_entry.refresh_from_db()
        self.assertEqual(material_entry.material_name, "Gloves")
        self.assertEqual(material_entry.unit, "ml")
        self.assertEqual(str(material_entry.quantity), "2.000")

    def test_saved_entry_update_api_rejects_technologist(self):
        contrast_entry = ContrastUsage.objects.create(
            exam=self.exam_in_progress,
            contrast_name="Omni",
            concentration_mg_ml=Decimal("300"),
            volume_ml=Decimal("20"),
            route="IV",
        )

        self.client.force_login(self.technologist)
        response = self.client.post(
            reverse("contrast-materials-api-entry-update", args=[self.exam_in_progress.id]),
            data=json.dumps(
                {
                    "entry_type": "contrast",
                    "entry_id": str(contrast_entry.id),
                    "data": {
                        "contrast_name": "Blocked Update",
                        "concentration_mg_ml": "300",
                        "volume_ml": "20",
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Only supervisors and administrators", response.json().get("error", ""))

    def test_review_page_shows_saved_edit_actions_for_supervisor(self):
        ContrastUsage.objects.create(
            exam=self.exam_in_progress,
            contrast_name="Omni",
            concentration_mg_ml=Decimal("300"),
            volume_ml=Decimal("20"),
            route="IV",
        )
        MaterialUsage.objects.create(
            exam=self.exam_in_progress,
            material_item=self.catalog_syringe,
            material_name="Syringe",
            measurement=self.measurement_ml,
            unit="ml",
            quantity=Decimal("1"),
        )

        self.client.force_login(self.supervisor_user)
        response = self.client.get(reverse("contrast-materials-review", args=[self.exam_in_progress.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-saved-entry-edit="contrast"')
        self.assertContains(response, 'data-saved-entry-edit="material"')

    def test_contrast_review_page_handles_legacy_material_rows_without_name_or_item(self):
        entry = MaterialUsage.objects.create(
            exam=self.exam_in_progress,
            material_name="Legacy Item",
            unit="ml",
            quantity=Decimal("1"),
        )
        MaterialUsage.objects.filter(id=entry.id).update(material_name="", material_item=None)

        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-review", args=[self.exam_in_progress.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saved Material Entries")

    def test_contrast_review_page_includes_recommended_procedure_bundle(self):
        bundle = ProcedureMaterialBundle.objects.create(
            procedure_code="CTA",
            procedure_name="CT Angio Brain",
            modality_scope="CT",
            rules_notes="Use preferred CT contrast consumables.",
            is_active=True,
        )
        ProcedureMaterialBundleItem.objects.create(
            bundle=bundle,
            material=self.catalog_syringe,
            material_code="RAD-0033",
            quantity=Decimal("1"),
            sort_order=10,
        )

        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-review", args=[self.exam_cm.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Suggested Bundle")
        self.assertContains(response, "CT Angio Brain")
        self.assertContains(response, "Load Procedure Bundle")

    def test_contrast_review_page_uses_recent_fallback_lists_when_catalog_is_empty(self):
        MaterialCatalog.objects.all().delete()
        ContrastUsage.objects.create(
            exam=self.exam_in_progress,
            contrast_name="Omni 0.021",
            concentration_mg_ml=Decimal("21"),
            volume_ml=Decimal("1"),
            route="IV",
        )
        MaterialUsage.objects.create(
            exam=self.exam_in_progress,
            material_name="IV Cannula 18G",
            unit="pcs",
            quantity=Decimal("1"),
        )

        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-review", args=[self.exam_in_progress.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catalog has no contrast rows. Showing recent contrast choices.")
        self.assertContains(response, "Catalog is empty. Showing bundle/recent material options.")
        self.assertContains(response, "Recent: Omni 0.021")
        self.assertContains(response, "Recent: IV Cannula 18G")

    def test_contrast_review_page_handles_catalog_metadata_without_concentration_keys(self):
        MaterialCatalog.objects.create(
            material_code="RAD039",
            name="Iodinated Contrast",
            category="Contrast Media",
            unit="Bottle",
            is_active=True,
            metadata={"import_source": "import_consumables_catalog"},
        )

        self.client.force_login(self.technologist)
        response = self.client.get(reverse("contrast-materials-review", args=[self.exam_in_progress.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Iodinated Contrast")

    def test_contrast_analytics_export_csv_allows_finance_role(self):
        ContrastUsage.objects.create(
            exam=self.exam_in_progress,
            contrast_name="Iohexol",
            concentration_mg_ml=Decimal("300"),
            volume_ml=Decimal("40"),
            route="IV",
        )
        self.client.force_login(self.finance_user)
        response = self.client.get(
            reverse("contrast-materials-analytics-export"),
            {
                "modality": "CT",
                "entry_kind": "contrast",
                "item_type": "Iohexol",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("contrast_documented_list.csv", response["Content-Disposition"])
        self.assertIn("Iohexol", response.content.decode("utf-8"))

    def test_contrast_analytics_export_csv_denies_view_only_user(self):
        self.client.force_login(self.report_user)
        response = self.client.get(reverse("contrast-materials-analytics-export"))
        self.assertEqual(response.status_code, 403)


class ConsumablesImportCommandTests(TestCase):
    def setUp(self):
        self.modality = Modality.objects.create(
            code="CT",
            name="Computed Tomography",
            is_active=True,
        )
        Procedure.objects.create(
            code="PROC-CT-001",
            name="CT Chest w/ Contrast",
            modality=self.modality,
            is_active=True,
        )

    def _write_temp_csv(self, content: str):
        handle = NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
        handle.write(content)
        handle.flush()
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_import_consumables_catalog_creates_materials_and_bundles(self):
        materials_csv = """MaterialCode,MaterialName,Category,Unit,PackSize,ModalityScope,ProcedureMappingTags,ChargeCode,BillingRef_Example,TypicalCost_SAR,DefaultPrice_SAR,Notes
RAD-0002,IV Cannula 18G,IV Access,PCS,1,CT|MRI|IR,CT_Contrast,CHG-IV-18G,,5,30,Preferred for CT power injection
RAD-0033,CT Injector Syringe 200mL,CT Injector,PCS,1,CT,CT_Contrast,CHG-CT-SYR-200,,55,200,
"""
        bundles_csv = """ProcedureCode,ProcedureName,Modality,BundleItems (MaterialCode:Qty),Rules/Notes
PROC-CT-001,CT Chest w/ Contrast,CT,RAD-0002:1;RAD-0033:1,Use 18G preferred
"""

        materials_path = self._write_temp_csv(materials_csv)
        bundles_path = self._write_temp_csv(bundles_csv)

        call_command(
            "import_consumables_catalog",
            materials_path,
            "--bundles-file",
            bundles_path,
        )

        cannula = MaterialCatalog.objects.get(material_code="RAD-0002")
        syringe = MaterialCatalog.objects.get(material_code="RAD-0033")
        self.assertEqual(cannula.name, "IV Cannula 18G")
        self.assertEqual(str(cannula.category), "IV Access")
        self.assertEqual(cannula.charge_code, "CHG-IV-18G")
        self.assertEqual(str(cannula.typical_cost_sar), "5.000")
        self.assertEqual(str(cannula.default_price_sar), "30.000")
        self.assertEqual(cannula.default_measurement.code, "PCS")
        self.assertEqual(syringe.default_measurement.code, "PCS")

        bundle = ProcedureMaterialBundle.objects.get(procedure_code="PROC-CT-001")
        self.assertEqual(bundle.modality_scope, "CT")
        self.assertEqual(bundle.items.count(), 2)
        first_item = bundle.items.order_by("sort_order").first()
        self.assertEqual(first_item.material.material_code, "RAD-0002")
        self.assertEqual(str(first_item.quantity), "1.000")


class HL7ORMIngestionTests(TestCase):
    SAMPLE_ORM = """MSH|^~\\&|EPIC_RKF^&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.10&ISO|KFMC^&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.1&ISO|RKF_GE2RIS_ORM^&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.27&ISO|RKF^&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.26&ISO|20260228201747|ATAG241422|ORM^O01|870796|P^T|2.3.1
PID|1||1083647^^^MPI&2.16.840.1.113883.3.3731.1.2.2.200.3.1.1.1.11&ISO^PI~412345678^^^RKF-MRNPID&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.11&ISO~1234567890^^^NID&2.16.840.1.113883.3.3731.1.1.100.2&ISO|1234567890^^^NID|ALTEST^HAMAD^TEST HAMAD^^^^L||20150621|M|||^^OTHER^^^SA^P||0512345678^PRN^H^^^051^0512345678^^CP^^^^^^^^^1~051 234 5678^P^M^^^051^2345678|0512345678^WPN^W^^^051^2345678|ARA|Single||536721239^^^AN^AN|1234567890|||^116||||||||N
PV1|1|E|PED ED^A31^A31^10000000001811||||2122235365^ALHAJ^MANAL^JABIR M.^^^^^NID^^^^NID|PED ED\\S\\A31\\S\\A31\\S\\CSH||Eme||||||||38|RKF-536721239|^20260228195700||||||||||000000|||||000000|||||||||||||||536721239||Does patient have Asthma NoDoes patient have contrast allergy No
ORC|NW|RKF-206315379^EPC|RKF-206315379|536721239|NW||1^Once^^20260228201800^20260307^S^^Standing^^^^1||20260228201743|ATAG241422^ALRASHIDI^ABDUALELAH||1093273751^ALRASHIDI^ABDUALELAH^AWAD^^^^^NID^^^^NID|PED ED^^^CSH^^^^^PED EMERGENCY|||Injury, unspecified||WOW2747188^WOW274-7188
OBR|1|RKF-206315379^EPC|RKF-206315379|CSKUH^CT HEAD^IMGEAP||20260228201800||||First Time|O||History:truma in head|||1093273751^ALRASHIDI^ABDUALELAH^AWAD^^^^^NID^^^^NID||, ||||||Imaging|||1^Once^^20260228201800^20260307^S^^Standing^^^^1||||T14.9^T14.9 - ^ICD-10|||||20260228201800"""
    SAMPLE_ORM_WITH_ACCESSION = """Order # RKF-206439661
Acc# 21860732
ORM message
MSH|^~\\&|EPIC_RKF^&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.10&ISO|KFMC^&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.1&ISO|RKF_GE2RIS_ORM^&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.27&ISO|RKF^&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.26&ISO|20260301204652|TT88211|ORM^O01|873674|P^T|2.3.1
PID|1||2389288^^^MPI&2.16.840.1.113883.3.3731.1.2.2.200.3.1.1.1.11&ISO^PI~447121911^^^RKF-MRNPID&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.11&ISO~0000000000^^^NID&2.16.840.1.113883.3.3731.1.1.100.2&ISO|0000000000^^^NID|ALSWAIDANI^ASKARIA^^^^^L||19520210|F|""^""||^^^^^SA^P||0500000000^PRN^M^^^050^0500000000^^CP|||||536744571^^^AN^AN|0000000000|||||||||||N
PV1|1|E|AED ED^C11^C11^10000000001811|||||AED ED\\S\\C11\\S\\C11\\S\\MH||Eme||||||||41|RKF-536744571|^20260301204400||||||||||000000|||||000000|||||||||||||||536744571||Does patient have Asthma UnknownDoes patient have contrast allergy Unknown
ORC|NW|RKF-206439661^EPC|RKF-206439661|536744571|NW||1^Once^^20260301204500^20260308^S^^Standing^^^^1||20260301204649|TT88211^ALAMRI^RAHAF||1098588211^ALAMRI^RAHAF^SALEH^^^^^NID^^^^NID|AED ED^^^MH^^^^^AED EMERGENCY|||stroke||CLISUP^EPIC SUPPORT
OBR|1|RKF-206439661^EPC|RKF-206439661|CACDB^CT ANGIO AORTIC ARCH AND CAROTID BOTH^IMGEAP||20260301204500||||First Time|O||History:stroke|||1098588211^ALAMRI^RAHAF^SALEH^^^^^NID^^^^NID||, ||||||Imaging|||1^Once^^20260301204500^20260308^S^^Standing^^^^1|||||||||20260301204500"""

    def setUp(self):
        self.modality = Modality.objects.create(
            code='CT',
            name='Computed Tomography',
            is_active=True,
        )
        Procedure.objects.create(
            code='CSKUH',
            name='CT Head',
            modality=self.modality,
            body_region='Head',
            is_active=True,
        )

    def test_ingest_orm_message_creates_exam_for_protocoling(self):
        exam, created, parsed = ingest_orm_message(self.SAMPLE_ORM)

        self.assertTrue(created)
        self.assertEqual(exam.accession_number, 'RKF-206315379')
        self.assertEqual(exam.order_id, 'RKF-206315379')
        self.assertEqual(exam.mrn, '412345678')
        self.assertEqual(exam.procedure_code, 'CSKUH')
        self.assertEqual(exam.procedure_name, 'CT HEAD')
        self.assertEqual(exam.modality.code, 'CT')
        self.assertEqual(exam.facility.code, 'RKF')
        self.assertEqual(exam.clinical_history, 'History:truma in head')
        self.assertEqual(exam.reason_for_exam, 'Injury, unspecified')
        self.assertEqual(exam.icd_10_code, 'T14.9')
        self.assertEqual(exam.icd_10_description, 'Injury, unspecified')
        self.assertEqual(exam.metadata['hl7_icd10_code'], 'T14.9')
        self.assertEqual(exam.metadata['hl7_icd10_description'], 'T14.9')
        self.assertEqual(exam.patient_class, 'E')
        self.assertEqual(exam.metadata['hl7_patient_class'], 'E')
        self.assertEqual(exam.status, 'ORDER')
        self.assertEqual(exam.hl7_message_control_id, '870796')
        self.assertIn('History:truma in head', exam.raw_hl7_message)
        self.assertEqual(parsed['message_info']['message_type'], 'ORM^O01')
        self.assertEqual(parsed['observation_request']['diagnosis_code'], 'T14.9')
        self.assertEqual(parsed['observation_request']['diagnosis_description'], 'T14.9')

        self.assertEqual(Facility.objects.filter(code='RKF').count(), 1)
        self.assertEqual(Exam.objects.count(), 1)

    def test_ingest_orm_message_prefers_preface_accession_number(self):
        exam, created, parsed = ingest_orm_message(self.SAMPLE_ORM_WITH_ACCESSION)

        self.assertTrue(created)
        self.assertEqual(exam.accession_number, '21860732')
        self.assertEqual(exam.order_id, 'RKF-206439661')
        self.assertEqual(exam.metadata['hl7_accession_number'], '21860732')
        self.assertEqual(exam.metadata['hl7_order_number'], 'RKF-206439661')
        self.assertEqual(parsed['message_info']['message_control_id'], '873674')

    def test_ingest_orm_message_reuses_existing_order_and_keeps_real_accession(self):
        existing_exam = Exam.objects.create(
            accession_number='21860732',
            order_id='RKF-206315379',
            mrn='412345678',
            facility=Facility.objects.create(
                code='OLD',
                name='Old Facility',
                is_active=True,
            ),
            modality=self.modality,
            procedure_code='CSKUH',
            procedure_name='CT Head',
            patient_name='Existing Patient',
            status='SCHEDULED',
            metadata={
                'hl7_order_number': 'RKF-206315379',
                'hl7_accession_number': '21860732',
                'hl7_order_control': 'SC',
            },
        )

        exam, created, _ = ingest_orm_message(self.SAMPLE_ORM)

        self.assertFalse(created)
        self.assertEqual(exam.pk, existing_exam.pk)
        self.assertEqual(exam.accession_number, '21860732')
        self.assertEqual(exam.order_id, 'RKF-206315379')
        self.assertEqual(exam.metadata['hl7_accession_number'], '21860732')
        self.assertEqual(exam.metadata['hl7_order_request_accession'], 'RKF-206315379')
        self.assertEqual(Exam.objects.filter(order_id='RKF-206315379').count(), 1)


class HL7ORRIngestionTests(TestCase):
    SAMPLE_ORM = """MSH|^~\\&|EPIC|KFMC|AIP|RKF|20260302115900||ORM^O01|ORM172064370|P|2.3.1
PID|1||447121911^^^MPI||Alswaidani^Askaria
ORC|NW|RKF-206453463^EPC|RKF-206453463|123|NW
OBR|1|RKF-206453463^EPC|RKF-206453463|XCHES^XR Chest^IMGEAP||20260302115900||||||History:chest pain"""
    SAMPLE_ORR = """MSH|^~\\&|CRIS|AAML|HIS|RKF|20260302020134||ORM^O01|172064370|P|2.3.1|||AL
PID|||2389288^^^MPI&2.16.840.1.113883.3.3731.1.2.2.200.3.1.1.1.11&ISO~447121911^^^RKF-MRNPID&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001811.11&ISO~447121911^^^RMA-MRNPID&2.16.840.1.113883.3.3731.1.2.2.100.3.1.10000000001132.11&ISO||Alswaidani^Askaria^^^^||19520210|W|||^^Unknown^^00000^SA||0531299987^^^^^^0531299987^CP|||||||||||||Vis||SY|
PV1|||10000000001811^^^||||1021617475||||||||||||RKF-536744571||||||||||||||||||||||||||||||||
ORC|SC|RKF-206453463|21860732^^^21860732||SC||^^^20260302020042||30|^^^^^^^||^^^^^^|RKF|1095061816^AlMugait^Sultan^^^^RadiologyTechnologist^|||AAML|KFMCMOBXR3|2404863553^Ching^Janmar^^^^Radiology Technician|||||||||||||||||
OBR||RKF-206453463^RKF-206453463|21860732|XCHES^XR Chest||||||||||||||||21860732|||||||^^^^|||||^&||2404863553^Ching&Janmar&&&Radiology Technician&&&&||20260302020042"""

    def setUp(self):
        self.facility = Facility.objects.create(
            code='RKF',
            name='Royal Clinic',
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code='XR',
            name='X-Ray',
            is_active=True,
        )
        Procedure.objects.create(
            code='XCHES',
            name='XR Chest',
            modality=self.modality,
            body_region='Chest',
            is_active=True,
        )

    def test_ingest_orr_message_updates_existing_order_with_accession_number(self):
        existing_exam = Exam.objects.create(
            accession_number='RKF-206453463',
            order_id='RKF-206453463',
            mrn='447121911',
            facility=self.facility,
            modality=self.modality,
            procedure_code='XCHES',
            procedure_name='XR Chest',
            patient_name='Askaria Alswaidani',
            status='SCHEDULED',
            raw_hl7_message='ORM PLACEHOLDER',
            metadata={
                'hl7_order_number': 'RKF-206453463',
            },
        )

        exam, created, parsed = ingest_orr_message(self.SAMPLE_ORR)

        self.assertFalse(created)
        self.assertEqual(exam.pk, existing_exam.pk)
        self.assertEqual(exam.accession_number, '21860732')
        self.assertEqual(exam.order_id, 'RKF-206453463')
        self.assertEqual(exam.procedure_code, 'XCHES')
        self.assertEqual(exam.modality.code, 'XR')
        self.assertEqual(exam.metadata['hl7_accession_number'], '21860732')
        self.assertEqual(exam.metadata['hl7_order_control'], 'SC')
        self.assertEqual(exam.metadata['hl7_response_order_status_raw'], 'SC')
        self.assertEqual(exam.status, 'SCHEDULED')
        self.assertEqual(exam.raw_hl7_message, 'ORM PLACEHOLDER')
        self.assertEqual(parsed['message_info']['message_control_id'], '172064370')

        hl7_log = HL7Message.objects.get()
        self.assertEqual(hl7_log.message_control_id, '172064370')
        self.assertEqual(hl7_log.exam, exam)
        self.assertEqual(hl7_log.exam_order_number(), 'RKF-206453463')
        self.assertEqual(hl7_log.exam_accession_number(), '21860732')

    def test_ingest_orr_message_defers_missing_order_request_until_orm_arrives(self):
        exam, created, parsed = ingest_orr_message(self.SAMPLE_ORR)

        self.assertIsNone(exam)
        self.assertFalse(created)
        self.assertEqual(parsed['order']['placer_order_number'], 'RKF-206453463')
        self.assertEqual(Exam.objects.count(), 0)
        self.assertEqual(HL7Message.objects.count(), 1)
        hl7_log = HL7Message.objects.get()
        self.assertEqual(hl7_log.status, 'RECEIVED')
        self.assertEqual(
            hl7_log.error_message,
            'Deferred ORR update waiting for ORM order RKF-206453463.',
        )

    def test_ingest_orr_message_creates_exam_for_actionable_status_when_order_missing(self):
        actionable_orr = self.SAMPLE_ORR.replace('||SC||', '||IP||', 1)

        exam, created, parsed = ingest_orr_message(actionable_orr)

        self.assertTrue(created)
        self.assertIsNotNone(exam)
        self.assertEqual(parsed['order']['placer_order_number'], 'RKF-206453463')
        self.assertEqual(exam.order_id, 'RKF-206453463')
        self.assertEqual(exam.accession_number, '21860732')
        self.assertEqual(exam.status, ExamStatus.IN_PROGRESS)
        self.assertEqual(exam.metadata['hl7_order_status'], 'IP')
        self.assertEqual(HL7Message.objects.count(), 1)
        hl7_log = HL7Message.objects.get()
        self.assertEqual(hl7_log.status, 'PROCESSED')
        self.assertEqual(hl7_log.exam_id, exam.id)

    def test_ingest_orm_replays_deferred_orr_update(self):
        deferred_exam, deferred_created, _ = ingest_orr_message(self.SAMPLE_ORR)
        self.assertIsNone(deferred_exam)
        self.assertFalse(deferred_created)

        deferred_log = HL7Message.objects.get(status='RECEIVED')
        exam, created, _ = ingest_orm_message(self.SAMPLE_ORM)

        self.assertTrue(created)
        exam.refresh_from_db()
        deferred_log.refresh_from_db()

        self.assertEqual(exam.order_id, 'RKF-206453463')
        self.assertEqual(exam.accession_number, '21860732')
        self.assertEqual(exam.status, 'ORDER')
        self.assertEqual(exam.metadata['hl7_order_status'], 'NW')
        self.assertEqual(exam.metadata['hl7_response_order_status_raw'], 'SC')
        self.assertEqual(deferred_log.status, 'PROCESSED')
        self.assertEqual(deferred_log.exam_id, exam.id)
        self.assertEqual(HL7Message.objects.filter(status='PROCESSED').count(), 2)

    def test_ingest_orr_message_maps_ip_to_in_progress(self):
        existing_exam = Exam.objects.create(
            accession_number='RKF-206453463',
            order_id='RKF-206453463',
            mrn='447121911',
            facility=self.facility,
            modality=self.modality,
            procedure_code='XCHES',
            procedure_name='XR Chest',
            patient_name='Askaria Alswaidani',
            status='SCHEDULED',
            raw_hl7_message='ORM PLACEHOLDER',
            metadata={'hl7_order_number': 'RKF-206453463'},
        )

        exam, created, _ = ingest_orr_message(self.SAMPLE_ORR.replace('||SC||', '||IP||', 1))

        self.assertFalse(created)
        self.assertEqual(exam.pk, existing_exam.pk)
        self.assertEqual(exam.status, 'IN_PROGRESS')
        self.assertEqual(exam.metadata['hl7_order_status'], 'IP')

    def test_ingest_orr_message_maps_ca_to_canceled(self):
        existing_exam = Exam.objects.create(
            accession_number='RKF-206453463',
            order_id='RKF-206453463',
            mrn='447121911',
            facility=self.facility,
            modality=self.modality,
            procedure_code='XCHES',
            procedure_name='XR Chest',
            patient_name='Askaria Alswaidani',
            status='SCHEDULED',
            raw_hl7_message='ORM PLACEHOLDER',
            metadata={'hl7_order_number': 'RKF-206453463'},
        )

        exam, created, _ = ingest_orr_message(self.SAMPLE_ORR.replace('||SC||', '||CA||', 1))

        self.assertFalse(created)
        self.assertEqual(exam.pk, existing_exam.pk)
        self.assertEqual(exam.status, 'CANCELLED')
        self.assertEqual(exam.get_status_display(), 'Canceled')
        self.assertEqual(exam.metadata['hl7_order_status'], 'CA')
        self.assertEqual(exam.metadata['hl7_response_order_status_raw'], 'CA')

    def test_ingest_orr_message_marks_completed_without_protocol_as_closed(self):
        existing_exam = Exam.objects.create(
            accession_number='RKF-206453463',
            order_id='RKF-206453463',
            mrn='447121911',
            facility=self.facility,
            modality=self.modality,
            procedure_code='XCHES',
            procedure_name='XR Chest',
            patient_name='Askaria Alswaidani',
            status='SCHEDULED',
            raw_hl7_message='ORM PLACEHOLDER',
            metadata={'hl7_order_number': 'RKF-206453463'},
        )

        exam, created, _ = ingest_orr_message(
            self.SAMPLE_ORR.replace('||SC||', '||CM||', 1)
        )

        self.assertFalse(created)
        self.assertEqual(exam.pk, existing_exam.pk)
        self.assertEqual(exam.status, 'COMPLETED')
        self.assertEqual(exam.protocol_workflow_status, 'CLOSED')
        self.assertTrue(exam.metadata['protocol_completed_without_assignment'])
        self.assertEqual(exam.metadata['protocol_workflow_status'], 'CLOSED')
        self.assertEqual(exam.metadata['hl7_order_status'], 'CM')
        self.assertIsNotNone(exam.exam_datetime)

    def test_ingest_orr_message_marks_assignment_done_when_completed(self):
        existing_exam = Exam.objects.create(
            accession_number='RKF-206453463',
            order_id='RKF-206453463',
            mrn='447121911',
            facility=self.facility,
            modality=self.modality,
            procedure_code='XCHES',
            procedure_name='XR Chest',
            patient_name='Askaria Alswaidani',
            status='SCHEDULED',
            raw_hl7_message='ORM PLACEHOLDER',
            metadata={'hl7_order_number': 'RKF-206453463'},
        )
        protocol = ProtocolTemplate.objects.create(
            code='XR_CHEST_DONE',
            name='XR Chest Done',
            modality=self.modality,
            facility=self.facility,
            body_region='Chest',
            is_active=True,
        )
        assignment = ProtocolAssignment.objects.create(
            exam=existing_exam,
            protocol=protocol,
            status='ACKNOWLEDGED',
        )

        exam, created, _ = ingest_orr_message(
            self.SAMPLE_ORR.replace('||SC||', '||CM||', 1)
        )

        self.assertFalse(created)
        assignment.refresh_from_db()
        self.assertEqual(exam.status, 'COMPLETED')
        self.assertEqual(exam.protocol_workflow_status, 'DONE')
        self.assertEqual(assignment.status, 'DONE')
        self.assertFalse(exam.metadata['protocol_completed_without_assignment'])
        self.assertEqual(exam.metadata['protocol_workflow_status'], 'DONE')


class HL7SIUIngestionTests(TestCase):
    SAMPLE_SIU = """MSH|^~\\&|CRIS|AAML|HIS|DGH|20260327113326|ALBOGMAI|SIU^S12|176459897|P|2.3.1|||AL||||
SCH|DGH-426030007681|21996245|DWMIXR03|10000000000187|DGH|^^^|XKNEL||30^^^min||20260327113236^^^^|||||10000000000187^^^^^||||A0191^ALBOGMAI^MOHAMMED^^^^RADIOLOGY TECHNOLOGIST|||||BOOKED
PID|||1851272^^^MPI&2.16.840.1.113883.3.3731.1.2.2.200.3.1.1.1.11&ISO~40000030260^^^DGH-MRNPID&2.16.840.1.113883.3.3731.1.2.2.100.4.1.10000000000187.11&ISO||Alotaibi^Sharaa^SARAB^Jahaz^^||19661015|W|||^^Unknown^^00000^||0503775659^^^^^^0503775659^CP|||||||||||||SAU||SAU|
PV1||E|^^^10000000000187||||10000000000187^^^^^|10000000000187^^^^^|10000000000187^^^^^||||||||10000000000187^^^^^||DGH-4EM2603012170^^^|UNK|||||||||||||||||||||||||||||||
AIS|||XKNEL|20260327113236|||30^^^min||||||||||"""

    def setUp(self):
        self.facility = Facility.objects.create(
            code='DGH',
            name='DGH',
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code='XR',
            name='X-Ray',
            is_active=True,
        )
        Procedure.objects.create(
            code='XKNEL',
            name='XR Knee Lt',
            modality=self.modality,
            body_region='Lower Extremity',
            is_active=True,
        )

    def test_ingest_siu_message_creates_scheduled_exam(self):
        exam, created, parsed = ingest_siu_message(self.SAMPLE_SIU)

        self.assertTrue(created)
        self.assertEqual(exam.order_id, 'DGH-426030007681')
        self.assertEqual(exam.accession_number, '21996245')
        self.assertEqual(exam.status, ExamStatus.SCHEDULED)
        self.assertEqual(exam.modality.code, 'XR')
        self.assertEqual(exam.procedure_code, 'XKNEL')
        self.assertIsNotNone(exam.scheduled_datetime)
        self.assertEqual(exam.metadata['hl7_order_status'], 'SC')
        self.assertEqual(exam.metadata['hl7_schedule_status'], 'BOOKED')
        self.assertEqual(parsed['message_info']['message_type'], 'SIU^S12')

    def test_ingest_siu_message_updates_existing_order_to_scheduled(self):
        existing_exam = Exam.objects.create(
            accession_number='DGH-426030007681',
            order_id='DGH-426030007681',
            mrn='40000030260',
            facility=self.facility,
            modality=self.modality,
            procedure_code='XKNEL',
            procedure_name='XR Knee Lt',
            patient_name='Existing Patient',
            status=ExamStatus.ORDER,
            metadata={'hl7_order_status': 'NW'},
        )

        exam, created, _ = ingest_siu_message(self.SAMPLE_SIU)

        self.assertFalse(created)
        self.assertEqual(exam.pk, existing_exam.pk)
        self.assertEqual(exam.accession_number, '21996245')
        self.assertEqual(exam.status, ExamStatus.SCHEDULED)
        self.assertEqual(exam.metadata['hl7_order_status'], 'SC')
        self.assertEqual(exam.metadata['hl7_schedule_status'], 'BOOKED')


class ExamsApiWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            email='reviewer@example.com',
            password='password123',
            username='reviewer',
            first_name='Review',
            last_name='User',
            specialty='Neuro',
        )
        self.facility = Facility.objects.create(
            code='API',
            name='API Facility',
            is_active=True,
        )
        self.user.facilities.add(self.facility)
        self.modality = Modality.objects.create(
            code='CT',
            name='Computed Tomography',
            is_active=True,
        )
        self.procedure = Procedure.objects.create(
            code='CTHEAD',
            name='CT Head',
            modality=self.modality,
            body_region='Head',
            is_active=True,
        )
        self.scheduled_datetime = timezone.now().replace(microsecond=0)
        self.exam = Exam.objects.create(
            accession_number='API-100',
            order_id='API-100',
            mrn='55555',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTHEAD',
            procedure_name='CT HEAD',
            patient_name='API Patient',
            status='SCHEDULED',
            scheduled_datetime=self.scheduled_datetime,
            metadata={'hl7_patient_class': 'E'},
        )
        self.protocol = ProtocolTemplate.objects.create(
            code='CT_API_PROTOCOL',
            name='CT API Protocol',
            modality=self.modality,
            facility=self.facility,
            body_region='Head',
            is_active=True,
        )
        ProtocolAssignment.objects.create(
            exam=self.exam,
            protocol=self.protocol,
            assigned_by=self.user,
            assignment_method='MANUAL',
            status='PENDING',
        )

    def test_exams_api_includes_workflow_links(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('exams-api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['viewer']['can_review_protocol'])
        self.assertTrue(payload['viewer']['can_view_protocol'])
        self.assertTrue(payload['viewer']['can_view_contrast'])
        self.assertEqual(payload['viewer']['default_subspeciality'], 'Neuro')
        self.assertEqual(payload['results'][0]['order_id'], self.exam.order_id)
        self.assertIn('order_datetime', payload['results'][0])
        self.assertEqual(payload['results'][0]['patient_class'], 'E')
        self.assertEqual(payload['results'][0]['patient_class_label'], 'Emergency')
        self.assertIn('patient_dob', payload['results'][0])
        self.assertEqual(payload['results'][0]['body_part'], 'Head')
        self.assertEqual(payload['results'][0]['subspeciality'], 'Neuro')
        self.assertEqual(payload['results'][0]['scheduled_datetime'], self.scheduled_datetime.isoformat())
        self.assertEqual(payload['results'][0]['exam_status'], 'SCHEDULED')
        self.assertEqual(payload['results'][0]['exam_status_label'], 'Scheduled')
        self.assertEqual(payload['results'][0]['workflow_status'], 'PENDING')
        self.assertEqual(payload['results'][0]['assignment_status'], 'PENDING')
        self.assertTrue(payload['results'][0]['review_url'].endswith(f'/protocoling/review/{self.exam.id}/'))
        self.assertTrue(payload['results'][0]['technologist_view_url'].endswith(f'/protocoling/technologist/{self.exam.id}/'))
        self.assertTrue(payload['results'][0]['can_open_contrast'])
        self.assertTrue(payload['results'][0]['contrast_review_url'].endswith(f'/contrast-materials/review/{self.exam.id}/'))

    def test_exams_api_hides_non_protocol_workflow_modalities(self):
        xr_modality = Modality.objects.create(
            code='XR',
            name='X-Ray',
            is_active=True,
        )
        hidden_exam = Exam.objects.create(
            accession_number='API-200',
            order_id='API-200',
            mrn='77777',
            facility=self.facility,
            modality=xr_modality,
            procedure_code='XRHIDE',
            procedure_name='XR Hidden',
            patient_name='Hidden Patient',
            status='SCHEDULED',
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('exams-api'))

        self.assertEqual(response.status_code, 200)
        result_ids = {item['id'] for item in response.json()['results']}
        self.assertNotIn(str(hidden_exam.id), result_ids)
        self.assertIn(str(self.exam.id), result_ids)

    def test_exams_api_radiologist_sees_only_radiologist_review_actions(self):
        radiologist = User.objects.create_user(
            email='radiologist-api@example.com',
            password='password123',
            username='radiologistapi',
            first_name='Radio',
            last_name='Logist',
            role=UserRole.RADIOLOGIST,
        )
        radiologist.groups.add(Group.objects.get(name='Radiologist'))
        radiologist.facilities.add(self.facility)

        self.client.force_login(radiologist)

        response = self.client.get(reverse('exams-api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['viewer']['can_assign_protocol'])
        self.assertTrue(payload['viewer']['can_review_protocol'])
        self.assertFalse(payload['viewer']['can_view_protocol'])
        self.assertFalse(payload['viewer']['can_confirm_protocol'])

    def test_exams_api_technologist_sees_only_technologist_review_actions(self):
        technologist = User.objects.create_user(
            email='technologist-api@example.com',
            password='password123',
            username='technologistapi',
            first_name='Tech',
            last_name='Nologist',
            role=UserRole.TECHNOLOGIST,
        )
        technologist.groups.add(Group.objects.get(name='Technologist'))
        technologist.facilities.add(self.facility)

        self.client.force_login(technologist)

        response = self.client.get(reverse('exams-api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload['viewer']['can_assign_protocol'])
        self.assertFalse(payload['viewer']['can_review_protocol'])
        self.assertTrue(payload['viewer']['can_view_protocol'])
        self.assertTrue(payload['viewer']['can_confirm_protocol'])

    def test_exams_api_marks_chest_exam_as_pedia_for_age_14_or_younger(self):
        pediatric_procedure = Procedure.objects.create(
            code='CTCHESTPED',
            name='CT Chest Pediatric',
            modality=self.modality,
            body_region='Chest',
            is_active=True,
        )
        pediatric_exam = Exam.objects.create(
            accession_number='API-300',
            order_id='API-300',
            mrn='88888',
            facility=self.facility,
            modality=self.modality,
            procedure_code=pediatric_procedure.code,
            procedure_name='CT CHEST',
            patient_name='Pediatric Patient',
            patient_dob=date.today() - timedelta(days=12 * 365),
            status='SCHEDULED',
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('exams-api'))

        self.assertEqual(response.status_code, 200)
        rows = {row['id']: row for row in response.json()['results']}
        self.assertIn(str(pediatric_exam.id), rows)
        self.assertEqual(rows[str(pediatric_exam.id)]['subspeciality'], 'Pedia')

    def test_exams_api_only_returns_exams_in_user_facilities(self):
        other_facility = Facility.objects.create(
            code='API2',
            name='API Facility 2',
            is_active=True,
        )
        hidden_exam = Exam.objects.create(
            accession_number='API-400',
            order_id='API-400',
            mrn='99999',
            facility=other_facility,
            modality=self.modality,
            procedure_code='CTHEAD',
            procedure_name='CT HEAD',
            patient_name='Other Facility Patient',
            status='SCHEDULED',
        )

        radiologist = User.objects.create_user(
            email='facility-scope@example.com',
            password='password123',
            username='facilityscope',
            first_name='Facility',
            last_name='Scoped',
            role=UserRole.RADIOLOGIST,
        )
        radiologist.groups.add(Group.objects.get(name='Radiologist'))
        radiologist.facilities.add(self.facility)

        self.client.force_login(radiologist)
        response = self.client.get(reverse('exams-api'))

        self.assertEqual(response.status_code, 200)
        result_ids = {item['id'] for item in response.json()['results']}
        self.assertIn(str(self.exam.id), result_ids)
        self.assertNotIn(str(hidden_exam.id), result_ids)

    def test_exams_api_uses_primary_facility_scope_when_user_facilities_empty(self):
        other_facility = Facility.objects.create(
            code='API3',
            name='API Facility 3',
            is_active=True,
        )
        hidden_exam = Exam.objects.create(
            accession_number='API-410',
            order_id='API-410',
            mrn='99111',
            facility=other_facility,
            modality=self.modality,
            procedure_code='CTHEAD',
            procedure_name='CT HEAD',
            patient_name='Other Primary Facility Patient',
            status='SCHEDULED',
        )

        user = User.objects.create_user(
            email='primary-facility-scope@example.com',
            password='password123',
            username='primaryfacilityscope',
            first_name='Primary',
            last_name='Scoped',
            role=UserRole.RADIOLOGIST,
            primary_facility=self.facility,
        )
        user.groups.add(Group.objects.get(name='Radiologist'))

        self.client.force_login(user)
        response = self.client.get(reverse('exams-api'))

        self.assertEqual(response.status_code, 200)
        result_ids = {item['id'] for item in response.json()['results']}
        self.assertIn(str(self.exam.id), result_ids)
        self.assertNotIn(str(hidden_exam.id), result_ids)

    def test_set_exam_subspeciality_updates_exam_metadata(self):
        unassigned_exam = Exam.objects.create(
            accession_number='API-500',
            order_id='API-500',
            mrn='12345',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTHEAD',
            procedure_name='CT HEAD',
            patient_name='Unassigned Patient',
            status='SCHEDULED',
        )

        radiologist = User.objects.create_user(
            email='subspeciality-editor@example.com',
            password='password123',
            username='subspecialityeditor',
            first_name='Sub',
            last_name='Speciality',
            role=UserRole.RADIOLOGIST,
        )
        radiologist.groups.add(Group.objects.get(name='Radiologist'))
        radiologist.facilities.add(self.facility)

        self.client.force_login(radiologist)
        response = self.client.post(
            reverse('exam-set-subspeciality', args=[unassigned_exam.id]),
            data={'subspeciality': 'MSK'},
        )

        self.assertEqual(response.status_code, 200)
        unassigned_exam.refresh_from_db()
        self.assertEqual(unassigned_exam.metadata.get('subspeciality'), 'MSK')
        self.assertEqual(unassigned_exam.metadata.get('subspecialty'), 'MSK')

    def test_set_exam_subspeciality_rejects_assigned_exam(self):
        radiologist = User.objects.create_user(
            email='subspeciality-assigned@example.com',
            password='password123',
            username='subspecialityassigned',
            first_name='Sub',
            last_name='Assigned',
            role=UserRole.RADIOLOGIST,
        )
        radiologist.groups.add(Group.objects.get(name='Radiologist'))
        radiologist.facilities.add(self.facility)

        self.client.force_login(radiologist)
        response = self.client.post(
            reverse('exam-set-subspeciality', args=[self.exam.id]),
            data={'subspeciality': 'MSK'},
        )

        self.assertEqual(response.status_code, 409)

    def test_set_exam_subspeciality_allows_primary_facility_scope(self):
        unassigned_exam = Exam.objects.create(
            accession_number='API-501',
            order_id='API-501',
            mrn='12346',
            facility=self.facility,
            modality=self.modality,
            procedure_code='CTHEAD',
            procedure_name='CT HEAD',
            patient_name='Primary Scope Patient',
            status='SCHEDULED',
        )

        radiologist = User.objects.create_user(
            email='subspeciality-primary@example.com',
            password='password123',
            username='subspecialityprimary',
            first_name='Primary',
            last_name='Scope',
            role=UserRole.RADIOLOGIST,
            primary_facility=self.facility,
        )
        radiologist.groups.add(Group.objects.get(name='Radiologist'))

        self.client.force_login(radiologist)
        response = self.client.post(
            reverse('exam-set-subspeciality', args=[unassigned_exam.id]),
            data={'subspeciality': 'MSK'},
        )

        self.assertEqual(response.status_code, 200)
        unassigned_exam.refresh_from_db()
        self.assertEqual(unassigned_exam.metadata.get('subspeciality'), 'MSK')


class WorklistFilterPreferencesApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            email='filter-admin@example.com',
            password='password123',
            username='filteradmin',
            first_name='Filter',
            last_name='Admin',
        )
        self.no_permission_user = User.objects.create_user(
            email='filter-viewer@example.com',
            password='password123',
            username='filterviewer',
            first_name='Filter',
            last_name='Viewer',
            role=UserRole.VIEWER,
        )

    def _url(self, context_key):
        return reverse('worklist-filter-preferences-api', args=[context_key])

    def test_get_returns_empty_filters_when_no_preference_exists(self):
        self.client.force_login(self.user)

        response = self.client.get(self._url('protocol'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['has_saved'])
        self.assertEqual(response.json()['filters'], {})

    def test_get_returns_has_saved_true_when_preference_exists_even_if_empty(self):
        self.client.force_login(self.user)
        UserPreference.objects.create(
            user=self.user,
            preference_type='display',
            preference_key='worklist_filters.protocol',
            preference_value={},
        )

        response = self.client.get(self._url('protocol'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['has_saved'])
        self.assertEqual(response.json()['filters'], {})

    def test_post_saves_and_returns_protocol_filters(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self._url('protocol'),
            data=json.dumps(
                {
                    'filters': {
                        'modality': 'MR',
                        'subspeciality': 'Neuro',
                        'query': 'mri spine',
                        'sort_by': 'exam-newest',
                        'unexpected_key': 'drop-me',
                    }
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(
            payload['filters'],
            {
                'modality': ['MR'],
                'subspeciality': ['Neuro'],
                'query': 'mri spine',
                'sort_by': 'exam-newest',
            },
        )

        preference = UserPreference.objects.get(
            user=self.user,
            preference_type='display',
            preference_key='worklist_filters.protocol',
        )
        self.assertEqual(preference.preference_value, payload['filters'])

    def test_post_saves_protocol_date_query_filters(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self._url('protocol'),
            data=json.dumps(
                {
                    'filters': {
                        'order_date_query': '-7',
                        'schedule_date_query': '2026-03-01 to 2026-03-31',
                    }
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload['filters'],
            {
                'order_date_query': '-7',
                'schedule_date_query': '2026-03-01 to 2026-03-31',
            },
        )

    def test_post_allows_updating_existing_filters(self):
        self.client.force_login(self.user)
        UserPreference.objects.create(
            user=self.user,
            preference_type='display',
            preference_key='worklist_filters.qc',
            preference_value={'modality': 'CT'},
        )

        response = self.client.post(
            self._url('qc'),
            data=json.dumps({'filters': {'modality': 'XR', 'query': 'demo'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)

        stored = UserPreference.objects.get(
            user=self.user,
            preference_type='display',
            preference_key='worklist_filters.qc',
        )
        self.assertEqual(stored.preference_value, {'modality': 'XR', 'query': 'demo'})

    def test_context_permission_is_enforced(self):
        self.client.force_login(self.no_permission_user)

        response = self.client.get(self._url('qc'))

        self.assertEqual(response.status_code, 403)

    def test_rejects_unknown_context(self):
        self.client.force_login(self.user)

        response = self.client.get(self._url('unknown'))

        self.assertEqual(response.status_code, 404)
