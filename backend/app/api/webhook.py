import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import settings
from app.services.line_bot import line_bot_service
from app.services.request_log import line_request_log_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/line")
async def line_webhook(request: Request, x_line_signature: str | None = Header(default=None)):
    """接收 LINE Webhook，驗證簽章並回覆文字訊息。"""
    body = await request.body()

    if settings.LINE_ENABLE_SIGNATURE_VALIDATION and not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    if not line_bot_service.validate_signature(body, x_line_signature or ""):
        raise HTTPException(status_code=401, detail="Invalid LINE signature")

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    events = payload.get("events", [])
    if not isinstance(events, list):
        raise HTTPException(status_code=400, detail="Invalid events format")

    webhook_request_id = uuid4().hex

    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        logger.error("LINE_CHANNEL_ACCESS_TOKEN is not configured.")
        return {
            "status": "accepted",
            "processed": 0,
            "message": "LINE Bot token is not configured",
            "webhook_request_id": webhook_request_id,
        }

    processed = 0

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

        if not line_bot_service.is_configured():
            reply_text = "LINE Bot 尚未完成設定，請聯絡管理員。"
        else:
            try:
                user_id = event.get("source", {}).get("userId", "")
                reply_text = await line_bot_service.generate_reply_text(user_text, user_id=user_id)
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
            stage="sending_reply",
            reply_text_preview=reply_text,
        )

        try:
            await line_bot_service.reply_text(reply_token, reply_text)
            processed += 1
            await line_request_log_service.update_request(
                request_id,
                status="completed",
                stage="completed",
                success=True,
                finished=True,
                reply_text_preview=reply_text,
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

    return {
        "status": "ok",
        "processed": processed,
        "total_events": len(events),
        "webhook_request_id": webhook_request_id,
    }
