import json
import threading

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.hl7_core.services.inbound_listener import (
    build_hl7_ack,
    evaluate_inbound_hl7_receipt,
    process_inbound_hl7_message,
)


def _extract_raw_hl7_message(request) -> str:
    if request.content_type and "application/json" in request.content_type:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ""

        if isinstance(payload, dict):
            return str(
                payload.get("message")
                or payload.get("raw_message")
                or payload.get("hl7")
                or ""
            ).strip()

        return ""

    try:
        return request.body.decode("utf-8").strip()
    except UnicodeDecodeError:
        return ""


def _run_inbound_hl7_processing(raw_message: str, context):
    process_inbound_hl7_message(raw_message, context=context)


def _start_inbound_hl7_processing(raw_message: str, context):
    worker = threading.Thread(
        target=_run_inbound_hl7_processing,
        args=(raw_message, context),
        daemon=True,
        name="hl7-http-inbound",
    )
    worker.start()


@csrf_exempt
@require_POST
def inbound_hl7_http(request):
    raw_message = _extract_raw_hl7_message(request)
    if not raw_message:
        context = None
        ack_code = "AE"
        ack_text = "Provide the HL7 message in the request body."
        accepted = False
    else:
        context, ack_code, ack_text, accepted = evaluate_inbound_hl7_receipt(raw_message)

    if accepted:
        _start_inbound_hl7_processing(raw_message, context)

    ack_message = build_hl7_ack(
        raw_message,
        acknowledgement_code=ack_code,
        text_message=ack_text,
    )
    return HttpResponse(
        f"{ack_message}\r",
        content_type="text/plain; charset=utf-8",
        status=200,
    )
