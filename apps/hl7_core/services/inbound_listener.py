from __future__ import annotations

import logging
import socketserver
from datetime import datetime
from typing import Any

from django.conf import settings

from apps.core.models import Exam
from apps.core.services.hl7_message_log import create_hl7_message_log
from apps.core.services.hl7_orm import _extract_preface_identifiers, _first_component, ingest_orm_message
from apps.core.services.hl7_orr import ingest_orr_message
from apps.core.services.hl7_siu import ingest_siu_message
from apps.hl7_core.models import HL7Message
from apps.hl7_core.parsers.orm_parser import ORMParser


logger = logging.getLogger(__name__)

MLLP_START_BLOCK = b"\x0b"
MLLP_END_BLOCK = b"\x1c"
MLLP_CARRIAGE_RETURN = b"\x0d"

HL7_RESPONSE_ORDER_CONTROLS = {"SC", "OK", "XO", "IP", "CM", "CA"}


def wrap_mllp_message(raw_message: str) -> bytes:
    payload = (raw_message or "").encode("utf-8")
    return MLLP_START_BLOCK + payload + MLLP_END_BLOCK + MLLP_CARRIAGE_RETURN


def extract_mllp_messages(buffer: bytes) -> tuple[list[str], bytes]:
    messages: list[str] = []
    working = buffer or b""

    while True:
        start = working.find(MLLP_START_BLOCK)
        if start < 0:
            return messages, working

        if start > 0:
            working = working[start:]
            start = 0

        end = working.find(MLLP_END_BLOCK + MLLP_CARRIAGE_RETURN, start + 1)
        if end < 0:
            return messages, working

        payload = working[start + 1:end]
        messages.append(payload.decode("utf-8-sig").strip())
        working = working[end + 2:]


def build_hl7_ack(
    source_message: str,
    *,
    acknowledgement_code: str = "AA",
    text_message: str = "",
) -> str:
    normalized = (source_message or "").replace("\r\n", "\r").replace("\n", "\r")
    msh_line = next(
        (segment for segment in normalized.split("\r") if segment.startswith("MSH|")),
        "",
    )
    fields = msh_line.split("|") if msh_line else []

    sending_application = fields[4] if len(fields) > 4 else "AIP"
    sending_facility = fields[5] if len(fields) > 5 else settings.HL7_SENDING_FACILITY
    receiving_application = fields[2] if len(fields) > 2 else "UNKNOWN"
    receiving_facility = fields[3] if len(fields) > 3 else "UNKNOWN"
    inbound_control_id = fields[9] if len(fields) > 9 else ""
    hl7_version = fields[11] if len(fields) > 11 else "2.3.1"

    ack_control_id = f"ACK{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    escaped_text = (text_message or "").replace("|", "\\F\\").replace("^", "\\S\\")

    segments = [
        (
            f"MSH|^~\\&|{sending_application}|{sending_facility}|"
            f"{receiving_application}|{receiving_facility}|{timestamp}||ACK|"
            f"{ack_control_id}|P|{hl7_version}"
        ),
        f"MSA|{acknowledgement_code}|{inbound_control_id}|{escaped_text}",
    ]
    return "\r".join(segment.rstrip("|") for segment in segments)


def inspect_inbound_hl7_message(raw_message: str) -> dict[str, Any]:
    incoming = (raw_message or "").strip()
    if not incoming:
        raise ValueError("HL7 message is empty.")

    normalized_message, order_hint, accession_hint = _extract_preface_identifiers(incoming)
    if not normalized_message:
        raise ValueError("HL7 message is empty.")

    parsed = ORMParser(normalized_message).parse()
    message_info = parsed.get("message_info") or {}
    order = parsed.get("order") or {}
    observation = parsed.get("observation_request") or {}
    schedule = parsed.get("schedule") or {}

    message_type = (message_info.get("message_type") or "").strip().upper()
    order_control = (
        order.get("order_control")
        or schedule.get("appointment_status")
        or ""
    ).strip().upper()
    message_control_id = (message_info.get("message_control_id") or "").strip()
    order_id = _first_component(
        order_hint
        or order.get("placer_order_number")
        or observation.get("placer_order_number")
        or observation.get("filler_order_number")
        or order.get("filler_order_number")
        or schedule.get("placer_appointment_id")
        or schedule.get("filler_appointment_id")
        or accession_hint
        or ""
    )

    return {
        "raw_message": incoming,
        "normalized_message": normalized_message,
        "parsed": parsed,
        "message_type": message_type,
        "order_control": order_control,
        "message_control_id": message_control_id,
        "order_id": order_id,
        "sending_application": message_info.get("sending_application") or "",
        "sending_facility": _first_component(message_info.get("sending_facility") or ""),
        "receiving_application": message_info.get("receiving_application") or "",
        "receiving_facility": _first_component(message_info.get("receiving_facility") or ""),
    }


def _create_inbound_log(context: dict[str, Any], *, status: str, error_message: str = "", exam=None):
    create_hl7_message_log(
        direction="INBOUND",
        message_type=context.get("message_type") or "UNKNOWN",
        message_control_id=context.get("message_control_id") or "",
        raw_message=context.get("normalized_message") or "",
        parsed_data=context.get("parsed") or {},
        exam=exam,
        status=status,
        error_message=error_message,
        sending_application=context.get("sending_application") or "",
        sending_facility=context.get("sending_facility") or "",
        receiving_application=context.get("receiving_application") or "",
        receiving_facility=context.get("receiving_facility") or "",
    )


def _has_inbound_log_for_message_control_id(message_control_id: str) -> bool:
    normalized = (message_control_id or "").strip()
    if not normalized:
        return False

    logs = HL7Message.objects.filter(
        direction="INBOUND",
        message_control_id=normalized,
    )
    if not logs.exists():
        return False

    # Allow retransmission when the only existing entry is a deferred ORR placeholder.
    # This lets the same message control ID be reprocessed after workflow changes.
    return logs.exclude(
        status="RECEIVED",
        error_message__startswith="Deferred ORR update waiting for ORM order ",
    ).exists()


def evaluate_inbound_hl7_receipt(raw_message: str) -> tuple[dict[str, Any] | None, str, str, bool]:
    try:
        context = inspect_inbound_hl7_message(raw_message)
    except ValueError as exc:
        return None, "AE", str(exc), False
    except Exception:
        logger.exception("HL7 inbound receipt could not be parsed")
        return None, "AE", "Invalid HL7 message.", False

    if context["message_type"] == "ORM^O01" and context["order_control"] == "NW":
        pass
    elif context["message_type"] in {"ORM^O01", "ORR^O02"} and context["order_control"] in HL7_RESPONSE_ORDER_CONTROLS:
        pass
    elif context["message_type"].startswith("SIU^"):
        pass
    else:
        error_message = (
            f"Unsupported inbound HL7 flow for message type {context['message_type'] or 'unknown'} "
            f"and order control {context['order_control'] or 'unknown'}."
        )
        _create_inbound_log(context, status="REJECTED", error_message=error_message)
        return context, "AR", error_message, False

    if _has_inbound_log_for_message_control_id(context["message_control_id"]):
        error_message = f"Duplicate message control ID {context['message_control_id']}."
        existing_exam = Exam.objects.filter(order_id=context["order_id"]).order_by("created_at").first() if context["order_id"] else None
        _create_inbound_log(context, status="REJECTED", error_message=error_message, exam=existing_exam)
        return context, "AR", error_message, False

    if (
        context["message_type"] == "ORM^O01"
        and context["order_control"] == "NW"
        and context["order_id"]
    ):
        existing_exam = Exam.objects.filter(order_id=context["order_id"]).order_by("created_at").first()
        if existing_exam is not None:
            error_message = f"Duplicate order ID {context['order_id']}."
            _create_inbound_log(context, status="REJECTED", error_message=error_message, exam=existing_exam)
            return context, "AR", error_message, False

    return context, "AA", "Accepted", True


def process_inbound_hl7_message(raw_message: str, *, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    active_context = context
    if active_context is None:
        try:
            active_context = inspect_inbound_hl7_message(raw_message)
        except Exception:
            active_context = None

    try:
        outcome = dispatch_inbound_hl7_message(raw_message)
        logger.info("HL7 inbound processed %s", outcome)
        return outcome
    except ValueError as exc:
        logger.warning("HL7 inbound rejected after ACK: %s", exc)
        if active_context and not _has_inbound_log_for_message_control_id(active_context.get("message_control_id") or ""):
            _create_inbound_log(active_context, status="REJECTED", error_message=str(exc))
    except Exception:
        logger.exception("HL7 inbound failed after ACK")
        if active_context and not _has_inbound_log_for_message_control_id(active_context.get("message_control_id") or ""):
            _create_inbound_log(
                active_context,
                status="ERROR",
                error_message="Failed to process inbound HL7 message after ACK.",
            )

    return None


def dispatch_inbound_hl7_message(raw_message: str) -> dict[str, Any]:
    parsed = ORMParser(raw_message).parse()
    message_info = parsed.get("message_info") or {}
    order = parsed.get("order") or {}
    observation = parsed.get("observation_request") or {}

    message_type = (message_info.get("message_type") or "").strip().upper()
    order_control = (order.get("order_control") or "").strip().upper()

    if message_type == "ORM^O01" and order_control == "NW":
        exam, created, _ = ingest_orm_message(raw_message)
        return {
            "handler": "ORM",
            "exam_id": str(exam.id),
            "created": created,
            "order_id": exam.order_id,
            "accession_number": exam.accession_number,
        }

    if message_type in {"ORM^O01", "ORR^O02"} and order_control in HL7_RESPONSE_ORDER_CONTROLS:
        exam, created, _ = ingest_orr_message(raw_message)
        order_id = _first_component(
            order.get("placer_order_number")
            or observation.get("placer_order_number")
            or order.get("filler_order_number")
            or observation.get("filler_order_number")
            or ""
        )
        accession_number = _first_component(
            order.get("filler_order_number")
            or observation.get("filler_order_number")
            or order.get("placer_order_number")
            or observation.get("placer_order_number")
            or ""
        )
        if exam is None:
            return {
                "handler": "ORR_DEFERRED",
                "exam_id": None,
                "created": False,
                "order_id": order_id,
                "accession_number": accession_number,
            }

        return {
            "handler": "ORR",
            "exam_id": str(exam.id),
            "created": created,
            "order_id": exam.order_id or order_id,
            "accession_number": exam.accession_number or accession_number,
        }

    if message_type.startswith("SIU^"):
        exam, created, _ = ingest_siu_message(raw_message)
        return {
            "handler": "SIU",
            "exam_id": str(exam.id),
            "created": created,
            "order_id": exam.order_id,
            "accession_number": exam.accession_number,
        }

    raise ValueError(
        f"Unsupported inbound HL7 flow for message type {message_type or 'unknown'} "
        f"and order control {order_control or 'unknown'}."
    )


class HL7MLLPRequestHandler(socketserver.BaseRequestHandler):
    def handle(self):
        buffer = b""
        client_host, client_port = self.client_address
        logger.info("HL7 listener accepted connection from %s:%s", client_host, client_port)

        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                if buffer.strip():
                    self._process_raw_payload(buffer.decode("utf-8-sig").strip())
                break

            buffer += chunk
            messages, buffer = extract_mllp_messages(buffer)
            for raw_message in messages:
                self._process_raw_payload(raw_message)

    def _process_raw_payload(self, raw_message: str):
        if not raw_message:
            return

        context, ack_code, ack_text, accepted = evaluate_inbound_hl7_receipt(raw_message)

        ack_message = build_hl7_ack(
            raw_message,
            acknowledgement_code=ack_code,
            text_message=ack_text,
        )
        self.request.sendall(wrap_mllp_message(ack_message))

        if accepted:
            process_inbound_hl7_message(raw_message, context=context)


class ThreadedHL7MLLPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_hl7_listener(
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    bind_host = host or settings.HL7_LISTENER_HOST
    bind_port = int(port or settings.HL7_LISTENER_PORT)

    with ThreadedHL7MLLPServer((bind_host, bind_port), HL7MLLPRequestHandler) as server:
        logger.info("HL7 listener started on %s:%s", bind_host, bind_port)
        server.serve_forever()
