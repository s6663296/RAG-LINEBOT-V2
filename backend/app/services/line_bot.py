import base64
import hashlib
import hmac
import logging
from typing import Any, Dict, List

import httpx

from app.core.config import settings
from app.services.llm_client import llm_client
from app.services.agent import agent_service
from app.services.request_log import line_request_log_service

logger = logging.getLogger(__name__)


class LineBotService:
    """封裝 LINE Bot 的簽章驗證、LLM 回覆與 Reply API 呼叫。"""

    LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"
    LINE_LOADING_API = "https://api.line.me/v2/bot/chat/loading/start"

    def validate_signature(self, body: bytes, signature: str) -> bool:
        """驗證 LINE Webhook 簽章。"""
        if not settings.LINE_ENABLE_SIGNATURE_VALIDATION:
            return True

        if not settings.LINE_CHANNEL_SECRET or not signature:
            return False

        digest = hmac.new(
            settings.LINE_CHANNEL_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected_signature = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected_signature, signature)

    def is_configured(self) -> bool:
        """檢查 LINE Bot 與共用 LLM 所需參數是否齊全。"""
        required_values = [
            settings.LINE_CHANNEL_ACCESS_TOKEN,
            settings.LLM_BASE_URL,
            settings.LLM_API_KEY,
            settings.LLM_MODEL_ID,
        ]
        return all(bool(value) for value in required_values)

    async def show_loading_animation(self, chat_id: str, loading_seconds: int = 20) -> None:
        """呼叫 LINE Loading Animation API 顯示「...」動畫。"""
        if not settings.LINE_CHANNEL_ACCESS_TOKEN or not chat_id:
            return

        headers = {
            "Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "chatId": chat_id,
            "loadingSeconds": loading_seconds,
        }

        try:
            async with httpx.AsyncClient(timeout=settings.LINE_REPLY_TIMEOUT_SECONDS) as client:
                response = await client.post(self.LINE_LOADING_API, headers=headers, json=payload)
                response.raise_for_status()
        except Exception as exc:
            logger.warning(f"Failed to show loading animation: {exc}")

    async def generate_reply_text(self, user_text: str, user_id: str = "", request_id: str = "") -> tuple[str, bool]:
        """使用 AgentService 執行完整 RAG 流程並產生回覆。"""
        try:
            async def status_callback(msg: str):
                """當 Agent 改變狀態時，重新觸發 LINE 動畫維持倒數計時，並記錄當前步驟至 Log。"""
                if user_id:
                    await self.show_loading_animation(user_id)
                if request_id:
                    await line_request_log_service.update_request(
                        request_id,
                        add_step=msg
                    )

            # 獲取對話歷史
            history = await line_request_log_service.get_user_history(
                user_id, limit=settings.LLM_CONTEXT_WINDOW_SIZE
            )
            
            # 開始處理前先觸發一次動畫
            if user_id:
                await self.show_loading_animation(user_id)
            if request_id:
                await line_request_log_service.update_request(
                    request_id,
                    add_step="開始生成回覆 (Agent 啟動)"
                )

            content = await agent_service.generate_response(
                user_text, 
                history=history,
                status_callback=status_callback
            )
            return content[:5000], True
        except Exception as exc:
            logger.exception("Failed to generate LINE reply via AgentService")
            # 優先使用 Exception 中的友善訊息，若無則回傳通用錯誤
            error_msg = str(exc)
            if not error_msg or "Exception" in error_msg:
                error_msg = "目前系統忙碌中，請稍後再試一次。"
            return error_msg[:5000], False

    async def reply_text(self, reply_token: str, text: str) -> None:
        """呼叫 LINE Reply API 回覆文字訊息。"""
        headers = {
            "Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text[:5000]}],
        }

        async with httpx.AsyncClient(timeout=settings.LINE_REPLY_TIMEOUT_SECONDS) as client:
            response = await client.post(self.LINE_REPLY_API, headers=headers, json=payload)
            response.raise_for_status()

    @staticmethod
    def _extract_message_content(result: Dict[str, Any]) -> str:
        """從 OpenAI 相容格式中安全取出第一個回覆內容。"""
        choices = result.get("choices") or []
        if not choices:
            return ""

        message = choices[0].get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join(part for part in text_parts if part).strip()

        return ""


line_bot_service = LineBotService()
