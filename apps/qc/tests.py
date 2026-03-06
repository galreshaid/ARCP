import json
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.constants import UserRole
from apps.core.models import Exam, Facility, Modality
from apps.qc.models import QCAnnotation, QCImage, QCResult, QCSession, QCSessionStatus
from apps.users.models import User, UserNotification


ONE_PIXEL_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5WZ6kAAAAASUVORK5CYII="
)


class QCWorkflowTests(TestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        self.override = override_settings(
            MEDIA_ROOT=self.media_dir.name,
            PACS_STUDY_URL_TEMPLATE="https://pacs.example/study/{accession}",
        )
        self.override.enable()

        self.facility = Facility.objects.create(
            code="QC1",
            name="QC Facility",
            is_active=True,
        )
        self.modality = Modality.objects.create(
            code="CT",
            name="Computed Tomography",
            requires_qc=True,
            is_active=True,
            qc_checklist_template={
                "positioning": True,
                "motion": True,
                "artifacts": True,
            },
        )
        self.exam = Exam.objects.create(
            accession_number="QC-ACC-001",
            order_id="QC-ORD-001",
            mrn="MRN-001",
            facility=self.facility,
            modality=self.modality,
            procedure_code="CTHEAD",
            procedure_name="CT HEAD",
            patient_name="QC Patient",
            status="SCHEDULED",
        )
        self.xr_modality = Modality.objects.create(
            code="XR",
            name="X-Ray",
            requires_qc=True,
            is_active=True,
            qc_checklist_template={"positioning": True},
        )
        self.xr_exam = Exam.objects.create(
            accession_number="QC-ACC-002",
            order_id="QC-ORD-002",
            mrn="MRN-002",
            facility=self.facility,
            modality=self.xr_modality,
            procedure_code="XR-PA",
            procedure_name="XR CHEST PA",
            patient_name="QC XR Patient",
            status="SCHEDULED",
        )
        self.radiologist = User.objects.create_user(
            email="qc-radiologist@example.com",
            password="password123",
            username="qcrad",
            first_name="QC",
            last_name="Radiologist",
            role=UserRole.RADIOLOGIST,
        )
        self.xr_supervisor = User.objects.create_user(
            email="qc-supervisor@example.com",
            password="password123",
            username="qcsupervisor",
            first_name="QC",
            last_name="Supervisor",
            role=UserRole.SUPERVISOR,
            preferences={
                "qc_modalities": ["XR"],
            },
        )
        self.xr_supervisor.facilities.add(self.facility)
        self.ct_supervisor = User.objects.create_user(
            email="qc-ct-supervisor@example.com",
            password="password123",
            username="qcctsupervisor",
            first_name="QC",
            last_name="CT Supervisor",
            role=UserRole.SUPERVISOR,
            preferences={
                "qc_modalities": ["CT"],
            },
        )
        self.ct_supervisor.facilities.add(self.facility)
        self.report_viewer = User.objects.create_user(
            email="qc-report@example.com",
            password="password123",
            username="qcreport",
            first_name="QC",
            last_name="Report",
            role=UserRole.VIEWER,
        )
        self.report_viewer.facilities.add(self.facility)

    def tearDown(self):
        self.override.disable()
        self.media_dir.cleanup()

    def test_qc_worklist_api_supervisor_is_modality_scoped(self):
        self.client.force_login(self.xr_supervisor)

        response = self.client.get(reverse("qc:exams-api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 1)
        row = payload["results"][0]
        self.assertEqual(row["accession_number"], "QC-ACC-002")
        self.assertEqual(row["modality"]["code"], "XR")
        self.assertEqual(row["exam_status"], "SCHEDULED")
        self.assertEqual(row["exam_status_label"], "Scheduled")

    def test_qc_worklist_api_radiologist_sees_own_cases_including_non_concern(self):
        QCSession.objects.create(
            exam=self.exam,
            reviewer=self.radiologist,
            accession_number=self.exam.accession_number,
            mrn=self.exam.mrn,
            modality_code=self.exam.modality.code,
            study_name=self.exam.procedure_name,
            notes="Concern about positioning",
            concern_raised=True,
            status=QCSessionStatus.DRAFT,
        )
        QCSession.objects.create(
            exam=self.xr_exam,
            reviewer=self.radiologist,
            accession_number=self.xr_exam.accession_number,
            mrn=self.xr_exam.mrn,
            modality_code=self.xr_exam.modality.code,
            study_name=self.xr_exam.procedure_name,
            notes="",
            concern_raised=False,
            status=QCSessionStatus.DRAFT,
        )

        self.client.force_login(self.radiologist)
        response = self.client.get(reverse("qc:exams-api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 2)
        result_map = {row["accession_number"]: row for row in payload["results"]}
        self.assertIn("QC-ACC-001", result_map)
        self.assertIn("QC-ACC-002", result_map)
        self.assertTrue(result_map["QC-ACC-001"]["concern_raised"])
        self.assertFalse(result_map["QC-ACC-002"]["concern_raised"])

    def test_qc_session_save_saves_png_image_and_annotations(self):
        self.client.force_login(self.radiologist)
        payload = {
            "action": "save",
            "checklist": {
                "positioning": True,
                "motion": False,
            },
            "notes": "Saved QC session",
            "images": [
                {
                    "name": "capture-1.png",
                    "data_url": ONE_PIXEL_PNG_DATA_URL,
                    "annotations": [
                        {
                            "tool": "ARROW",
                            "start": {"x": 1, "y": 1},
                            "end": {"x": 8, "y": 8},
                            "color": "#ff0000",
                            "strokeWidth": 2,
                        }
                    ],
                }
            ],
        }

        response = self.client.post(
            reverse("qc:session-api", args=[self.exam.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(QCSession.objects.count(), 1)
        self.assertEqual(QCImage.objects.count(), 1)
        self.assertEqual(QCAnnotation.objects.count(), 1)

        session = QCSession.objects.get()
        self.assertEqual(session.status, QCSessionStatus.SAVED)
        self.assertEqual(session.accession_number, "QC-ACC-001")

        image = QCImage.objects.get()
        self.assertIn("QC-ACC-001", image.pacs_link)
        self.assertTrue(image.image.name.endswith(".png"))

    def test_supervisor_acknowledge_creates_acknowledged_session(self):
        QCSession.objects.create(
            exam=self.exam,
            reviewer=self.radiologist,
            accession_number=self.exam.accession_number,
            mrn=self.exam.mrn,
            modality_code=self.exam.modality.code,
            study_name=self.exam.procedure_name,
            notes="Need supervisor review",
            concern_raised=True,
            status=QCSessionStatus.SAVED,
        )

        self.client.force_login(self.ct_supervisor)
        payload = {
            "action": "acknowledge",
            "checklist": {"positioning": True},
            "notes": "",
            "images": [],
        }

        response = self.client.post(
            reverse("qc:session-api", args=[self.exam.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        latest = QCSession.objects.filter(exam=self.exam).order_by("-created_at").first()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.status, QCSessionStatus.ACKNOWLEDGED)
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.radiologist,
                category="DIRECT_MESSAGE",
                title__icontains="Acknowledge",
            ).exists()
        )

    def test_supervisor_reply_requires_reply_message(self):
        self.client.force_login(self.ct_supervisor)
        payload = {
            "action": "reply",
            "checklist": {"positioning": True},
            "notes": "",
            "supervisor_reply": "",
            "images": [],
        }

        response = self.client.post(
            reverse("qc:session-api", args=[self.exam.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("reply message", response.json().get("error", "").lower())

    def test_supervisor_reply_sends_direct_message_to_issue_owner(self):
        QCSession.objects.create(
            exam=self.exam,
            reviewer=self.radiologist,
            accession_number=self.exam.accession_number,
            mrn=self.exam.mrn,
            modality_code=self.exam.modality.code,
            study_name=self.exam.procedure_name,
            notes="Need follow-up",
            concern_raised=True,
            status=QCSessionStatus.SAVED,
        )

        self.client.force_login(self.ct_supervisor)
        payload = {
            "action": "reply",
            "checklist": {"positioning": True},
            "notes": "",
            "supervisor_reply": "Please repeat positioning check.",
            "images": [],
        }

        response = self.client.post(
            reverse("qc:session-api", args=[self.exam.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        latest = QCSession.objects.filter(exam=self.exam).order_by("-created_at").first()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.status, QCSessionStatus.REPLIED)
        self.assertIn("Please repeat positioning check.", latest.notes)
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.radiologist,
                category="DIRECT_MESSAGE",
                message__icontains="Please repeat positioning check.",
            ).exists()
        )

    def test_qc_save_can_send_direct_user_message(self):
        self.client.force_login(self.radiologist)
        payload = {
            "action": "save",
            "checklist": {
                "positioning": True,
                "motion": False,
            },
            "notes": "Sending QC update to supervisor",
            "direct_message": {
                "recipient_id": str(self.ct_supervisor.id),
                "title": "QC follow-up needed",
                "message": "Please review this QC issue.",
            },
            "images": [],
        }

        response = self.client.post(
            reverse("qc:session-api", args=[self.exam.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(QCSession.objects.count(), 1)
        session = QCSession.objects.get()
        self.assertEqual(session.status, QCSessionStatus.SAVED)
        self.assertEqual(QCImage.objects.count(), 0)
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.ct_supervisor,
                sender=self.radiologist,
                category="DIRECT_MESSAGE",
                title="QC follow-up needed",
            ).exists()
        )

    def test_qc_launch_by_accession_redirects_to_review(self):
        self.client.force_login(self.radiologist)
        response = self.client.get(
            reverse("qc:launch"),
            {"accession": self.exam.accession_number},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith(f"/quality-control/review/{self.exam.id}/"))

    def test_xr_review_shows_extended_checklist_items(self):
        self.client.force_login(self.radiologist)
        response = self.client.get(reverse("qc:review", args=[self.xr_exam.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Missing Images in PACS")
        self.assertContains(response, "Wrong Tech Markers")

    def test_qc_concern_sends_notification_to_supervisor(self):
        self.client.force_login(self.radiologist)
        payload = {
            "action": "save",
            "checklist": {
                "positioning": False,
            },
            "notes": "Escalate to supervisor",
            "concern_raised": True,
            "images": [],
        }

        response = self.client.post(
            reverse("qc:session-api", args=[self.exam.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.ct_supervisor,
                category="QC_CONCERN",
            ).exists()
        )

    def test_qc_analytics_page_loads_all_sections(self):
        review_session = QCSession.objects.create(
            exam=self.exam,
            reviewer=self.ct_supervisor,
            accession_number=self.exam.accession_number,
            mrn=self.exam.mrn,
            modality_code=self.exam.modality.code,
            study_name=self.exam.procedure_name,
            checklist_state={"positioning": True, "motion": False},
            notes="QC finalized",
            concern_raised=True,
            status=QCSessionStatus.ACKNOWLEDGED,
        )
        QCResult.objects.create(
            exam=self.exam,
            session=review_session,
            decision="APPROVED",
            reviewed_by=self.ct_supervisor,
            checklist_results={"positioning": True},
            summary="Approved after QC review",
        )

        self.client.force_login(self.report_viewer)
        response = self.client.get(reverse("qc:analytics"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "QC Analytics")
        self.assertContains(response, "Latest QC Status Distribution")
        self.assertContains(response, "Modality Summary")
        self.assertContains(response, "Reviewer Activity")
        self.assertContains(response, "Checklist Item Usage")
