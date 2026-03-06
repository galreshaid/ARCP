from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.core.models import Exam, Facility, Modality
from apps.hl7_core.models import HL7Message
from apps.hl7_core.services.inbound_listener import (
    build_hl7_ack,
    dispatch_inbound_hl7_message,
    extract_mllp_messages,
    wrap_mllp_message,
)


class HL7InboundListenerTests(SimpleTestCase):
    SAMPLE_ORM = (
        "MSH|^~\\&|EPIC|KFMC|AIP|RKF|20260302120000||ORM^O01|ORM123|P|2.3.1\r"
        "PID|1||12345^^^MPI||DOE^JANE\r"
        "ORC|NW|RKF-10001^EPC|RKF-10001|123|NW\r"
        "OBR|1|RKF-10001^EPC|RKF-10001|CKIDB^CT RENAL^IMGEAP||20260302115900||||||History:flank pain"
    )
    SAMPLE_ORR = (
        "MSH|^~\\&|CRIS|AAML|AIP|RKF|20260302130000||ORM^O01|ORR123|P|2.3.1\r"
        "PID|1||12345^^^MPI||DOE^JANE\r"
        "ORC|SC|RKF-10001|21860001^^^21860001||CM\r"
        "OBR||RKF-10001^RKF-10001|21860001|CKIDB^CT Renal"
    )

    def test_wrap_and_extract_mllp_message(self):
        framed = wrap_mllp_message(self.SAMPLE_ORM)

        messages, remainder = extract_mllp_messages(framed)

        self.assertEqual(messages, [self.SAMPLE_ORM])
        self.assertEqual(remainder, b"")

    def test_build_hl7_ack_uses_inbound_control_id(self):
        ack = build_hl7_ack(self.SAMPLE_ORM, acknowledgement_code="AA", text_message="Accepted")

        self.assertIn("MSH|^~\\&|AIP|RKF|EPIC|KFMC|", ack)
        self.assertIn("MSA|AA|ORM123|Accepted", ack)

    @patch("apps.hl7_core.services.inbound_listener.ingest_orm_message")
    def test_dispatch_routes_new_orders_to_orm_ingest(self, ingest_mock):
        ingest_mock.return_value = (
            type("ExamStub", (), {"id": "1", "order_id": "RKF-10001", "accession_number": "RKF-10001"})(),
            True,
            {},
        )

        result = dispatch_inbound_hl7_message(self.SAMPLE_ORM)

        ingest_mock.assert_called_once_with(self.SAMPLE_ORM)
        self.assertEqual(result["handler"], "ORM")
        self.assertTrue(result["created"])

    @patch("apps.hl7_core.services.inbound_listener.ingest_orr_message")
    def test_dispatch_routes_status_changes_to_orr_ingest(self, ingest_mock):
        ingest_mock.return_value = (
            type("ExamStub", (), {"id": "1", "order_id": "RKF-10001", "accession_number": "21860001"})(),
            False,
            {},
        )

        result = dispatch_inbound_hl7_message(self.SAMPLE_ORR)

        ingest_mock.assert_called_once_with(self.SAMPLE_ORR)
        self.assertEqual(result["handler"], "ORR")
        self.assertFalse(result["created"])

    @patch("apps.hl7_core.services.inbound_listener.ingest_orr_message")
    def test_dispatch_marks_orr_as_deferred_when_exam_not_found_yet(self, ingest_mock):
        ingest_mock.return_value = (None, False, {})

        result = dispatch_inbound_hl7_message(self.SAMPLE_ORR)

        ingest_mock.assert_called_once_with(self.SAMPLE_ORR)
        self.assertEqual(result["handler"], "ORR_DEFERRED")
        self.assertIsNone(result["exam_id"])
        self.assertEqual(result["order_id"], "RKF-10001")
        self.assertEqual(result["accession_number"], "21860001")

    def test_dispatch_rejects_unknown_order_control(self):
        with self.assertRaisesMessage(ValueError, "Unsupported inbound HL7 flow"):
            dispatch_inbound_hl7_message(
                self.SAMPLE_ORM.replace("ORC|NW|", "ORC|ZZ|")
            )


class HL7HttpEndpointTests(TestCase):
    @patch("apps.hl7_core.views._start_inbound_hl7_processing")
    def test_legacy_http_orm_endpoint_accepts_raw_post_body(self, dispatch_mock):
        response = self.client.post(
            reverse("hl7-http-orm"),
            data=HL7InboundListenerTests.SAMPLE_ORM,
            content_type="text/plain",
        )

        self.assertEqual(response.status_code, 200)
        ack = response.content.decode("utf-8")
        self.assertIn("MSH|^~\\&|AIP|RKF|EPIC|KFMC|", ack)
        self.assertIn("MSA|AA|ORM123|Accepted", ack)
        dispatch_mock.assert_called_once()

    def test_legacy_http_orm_endpoint_rejects_empty_requests(self):
        response = self.client.post(
            reverse("hl7-http-orm"),
            data="",
            content_type="text/plain",
        )

        self.assertEqual(response.status_code, 200)
        ack = response.content.decode("utf-8")
        self.assertIn("MSH|^~\\&|AIP|HOSPITAL|UNKNOWN|UNKNOWN|", ack)
        self.assertIn("MSA|AE||Provide the HL7 message in the request body.", ack)

    @patch("apps.hl7_core.views._start_inbound_hl7_processing")
    def test_legacy_http_orm_endpoint_rejects_duplicate_message_control_id(self, dispatch_mock):
        HL7Message.objects.create(
            direction="INBOUND",
            message_type="ORM^O01",
            message_control_id="ORM123",
            raw_message=HL7InboundListenerTests.SAMPLE_ORM,
            status="PROCESSED",
        )

        response = self.client.post(
            reverse("hl7-http-orm"),
            data=HL7InboundListenerTests.SAMPLE_ORM,
            content_type="text/plain",
        )

        self.assertEqual(response.status_code, 200)
        ack = response.content.decode("utf-8")
        self.assertIn("MSA|AR|ORM123|Duplicate message control ID ORM123.", ack)
        dispatch_mock.assert_not_called()

    @patch("apps.hl7_core.views._start_inbound_hl7_processing")
    def test_legacy_http_orm_endpoint_rejects_duplicate_order_id(self, dispatch_mock):
        facility = Facility.objects.create(code="KFMC", name="King Fahad")
        modality = Modality.objects.create(code="CT", name="CT")
        Exam.objects.create(
            accession_number="ACC-0001",
            order_id="RKF-10001",
            mrn="12345",
            facility=facility,
            modality=modality,
            procedure_name="CT Renal",
            patient_name="Jane Doe",
        )

        response = self.client.post(
            reverse("hl7-http-orm"),
            data=HL7InboundListenerTests.SAMPLE_ORM,
            content_type="text/plain",
        )

        self.assertEqual(response.status_code, 200)
        ack = response.content.decode("utf-8")
        self.assertIn("MSA|AR|ORM123|Duplicate order ID RKF-10001.", ack)
        dispatch_mock.assert_not_called()
