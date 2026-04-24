import asyncio
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import settings
from app.services.line_bot import line_bot_service
from app.services.request_log import line_request_log_service

router = APIRouter()
logger = logging.getLogger(__name__)
LINE_EVENT_PROCESS_TIMEOUT_SECONDS = 25.0


async def log_webhook_failure(
    webhook_request_id: str,
    *,
    stage: str,
    error: str,
    body: bytes,
    signature_present: bool,
    metadata: dict | None = None,
) -> None:
    """記錄在事件建立前就失敗的 LINE webhook 請求，避免監控盲區。"""
    try:
        request_id = await line_request_log_service.create_request(
            webhook_request_id=webhook_request_id,
            event_index=0,
            event={"type": "webhook", "message": {"type": "webhook"}},
            user_text="",
        )
        merged_metadata = {
            "body_length": len(body),
            "signature_present": signature_present,
        }
        if metadata:
            merged_metadata.update(metadata)

        await line_request_log_service.update_request(
            request_id,
            status="failed",
            stage=stage,
            success=False,
            finished=True,
            error=error,
            metadata=merged_metadata,
        )
    except Exception:
        logger.exception("Failed to persist LINE webhook failure log")


async def process_line_events(events: list, webhook_request_id: str):
    """同步處理 LINE 傳來的事件，確保 serverless 環境下回覆不會遺失。"""
    for event_index, raw_event in enumerate(events, start=1):
        event = raw_event if isinstance(raw_event, dict) else {}
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        user_text = (message.get("text") or "").strip() if message.get("type") == "text" else ""

        request_id = await line_request_log_service.create_request(
            webhook_request_id=webhook_request_id,
            event_index=event_index,
            event=event,
            user_text=user_text,
        )

        if not isinstance(raw_event, dict):
            await line_request_log_service.update_request(
                request_id,
                status="ignored",
                stage="ignored_invalid_event",
                success=False,
                finished=True,
                error="Event payload is not an object",
            )
            continue

        if event.get("type") != "message":
            await line_request_log_service.update_request(
                request_id,
                status="ignored",
                stage="ignored_non_message_event",
                success=False,
                finished=True,
            )
            continue

        if message.get("type") != "text":
            await line_request_log_service.update_request(
                request_id,
                status="ignored",
                stage="ignored_non_text_message",
                success=False,
                finished=True,
            )
            continue

        reply_token = event.get("replyToken")
        if not reply_token or not user_text:
            await line_request_log_service.update_request(
                request_id,
                status="ignored",
                stage="ignored_missing_reply_token_or_text",
                success=False,
                finished=True,
            )
            continue

        await line_request_log_service.update_request(
            request_id,
            status="processing",
            stage="generating_reply",
            metadata={"user_text_length": len(user_text)},
        )

        reply_generation_timed_out = False

        if not line_bot_service.is_configured():
            reply_text = "LINE Bot 尚未完成設定，請聯絡管理員。"
        else:
            try:
                user_id = event.get("source", {}).get("userId", "")
                reply_text = await asyncio.wait_for(
                    line_bot_service.generate_reply_text(user_text, user_id=user_id),
                    timeout=LINE_EVENT_PROCESS_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                reply_generation_timed_out = True
                logger.warning("LINE reply generation timed out")
                await line_request_log_service.update_request(
                    request_id,
                    status="processing",
                    stage="generate_reply_timeout",
                    metadata={
                        "processing_timeout_seconds": LINE_EVENT_PROCESS_TIMEOUT_SECONDS,
                        "reply_generation_timed_out": True,
                    },
                )
                reply_text = "目前查詢量較大，請稍後再試一次。"
            except Exception as exc:
                logger.exception("Failed to generate LINE reply text")
                await line_request_log_service.update_request(
                    request_id,
                    status="failed",
                    stage="generate_reply_failed",
                    success=False,
                    finished=True,
                    error=str(exc),
                )
                continue

        await line_request_log_service.update_request(
            request_id,
            status="processing",
            stage="sending_timeout_fallback_reply" if reply_generation_timed_out else "sending_reply",
            reply_text_preview=reply_text,
        )

        try:
            await line_bot_service.reply_text(reply_token, reply_text)
            await line_request_log_service.update_request(
                request_id,
                status="completed",
                stage="completed_with_timeout_fallback" if reply_generation_timed_out else "completed",
                success=True,
                finished=True,
                reply_text_preview=reply_text,
                metadata={
                    "reply_generation_timed_out": reply_generation_timed_out,
                } if reply_generation_timed_out else None,
            )
        except Exception as exc:
            logger.exception("Failed to send LINE reply")
            await line_request_log_service.update_request(
                request_id,
                status="failed",
                stage="reply_failed",
                success=False,
                finished=True,
                error=str(exc),
                reply_text_preview=reply_text,
            )


@router.post("/line")
async def line_webhook(
    request: Request,
    x_line_signature: str | None = Header(default=None)
):
    """接收 LINE Webhook，驗證簽章並同步完成回覆，避免 serverless 背景任務遺失。"""
    body = await request.body()
    webhook_request_id = uuid4().hex
    signature_present = bool(x_line_signature)

    if settings.LINE_ENABLE_SIGNATURE_VALIDATION and not x_line_signature:
        await log_webhook_failure(
            webhook_request_id,
            stage="missing_signature",
            error="Missing X-Line-Signature header",
            body=body,
            signature_present=False,
        )
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    if not line_bot_service.validate_signature(body, x_line_signature or ""):
        await log_webhook_failure(
            webhook_request_id,
            stage="invalid_signature",
            error="Invalid LINE signature",
            body=body,
            signature_present=signature_present,
        )
        raise HTTPException(status_code=401, detail="Invalid LINE signature")

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError as exc:
        await log_webhook_failure(
            webhook_request_id,
            stage="invalid_json_payload",
            error=str(exc),
            body=body,
            signature_present=signature_present,
        )
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    events = payload.get("events", [])
    if not isinstance(events, list):
        await log_webhook_failure(
            webhook_request_id,
            stage="invalid_events_format",
            error="Invalid events format",
            body=body,
            signature_present=signature_present,
        )
        raise HTTPException(status_code=400, detail="Invalid events format")

    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        logger.error("LINE_CHANNEL_ACCESS_TOKEN is not configured.")
        await log_webhook_failure(
            webhook_request_id,
            stage="missing_channel_access_token",
            error="LINE_CHANNEL_ACCESS_TOKEN is not configured.",
            body=body,
            signature_present=signature_present,
            metadata={"total_events": len(events)},
        )
        return {
            "status": "accepted",
            "processed": 0,
            "message": "LINE Bot token is not configured",
            "webhook_request_id": webhook_request_id,
        }

    try:
        await process_line_events(events, webhook_request_id)
    except Exception as exc:
        logger.exception("Unexpected failure while processing LINE webhook")
        await log_webhook_failure(
            webhook_request_id,
            stage="process_line_events_failed",
            error=str(exc),
            body=body,
            signature_present=signature_present,
            metadata={"total_events": len(events)},
        )
        raise HTTPException(status_code=500, detail="Failed to process LINE webhook") from exc

    return {
        "status": "ok",
        "processed": len(events),
        "total_events": len(events),
        "webhook_request_id": webhook_request_id,
    }
