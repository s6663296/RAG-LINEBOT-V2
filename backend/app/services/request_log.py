from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models import LineRequestLog

LineRequestStatus = Literal["received", "processing", "completed", "failed", "ignored"]
ACTIVE_STATUSES = {"received", "processing"}


def _duration_ms(started_at: datetime, completed_at: Optional[datetime]) -> Optional[int]:
    if not started_at:
        return None

    if completed_at:
        end_dt = completed_at
    else:
        end_dt = datetime.now(timezone.utc)

    # Ensure started_at is timezone-aware if it's not
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    delta = end_dt - started_at
    return max(int(delta.total_seconds() * 1000), 0)


class LineRequestLogService:
    """保存 LINE Webhook 請求生命週期，支援併發更新與管理端查詢。"""

    def _to_public(self, log: LineRequestLog) -> Dict[str, Any]:
        return {
            "request_id": log.request_id,
            "webhook_request_id": log.webhook_request_id,
            "line_event_id": log.line_event_id,
            "event_index": log.event_index,
            "event_type": log.event_type,
            "message_type": log.message_type,
            "line_user_id": log.line_user_id,
            "line_group_id": log.line_group_id,
            "line_room_id": log.line_room_id,
            "status": log.status,
            "stage": log.stage,
            "success": log.success,
            "error": log.error or "",
            "user_text_preview": log.user_text_preview or "",
            "reply_text_preview": log.reply_text_preview or "",
            "created_at": log.created_at.isoformat() if log.created_at else "",
            "updated_at": log.updated_at.isoformat() if log.updated_at else "",
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            "duration_ms": _duration_ms(log.created_at, log.completed_at),
            "is_active": log.status in ACTIVE_STATUSES,
            "metadata": log.metadata_json or {},
        }

    async def create_request(
        self,
        *,
        webhook_request_id: str,
        event_index: int,
        event: Dict[str, Any],
        user_text: str,
    ) -> str:
        request_id = uuid4().hex
        
        source = event.get("source") or {}
        message = event.get("message") or {}

        log = LineRequestLog(
            request_id=request_id,
            webhook_request_id=webhook_request_id,
            line_event_id=str(event.get("webhookEventId") or ""),
            event_index=event_index,
            event_type=str(event.get("type") or ""),
            message_type=str(message.get("type") or ""),
            line_user_id=str(source.get("userId") or ""),
            line_group_id=str(source.get("groupId") or ""),
            line_room_id=str(source.get("roomId") or ""),
            status="received",
            stage="received",
            success=None,
            error="",
            user_text_preview=user_text[:120],
            full_user_text=user_text,
            reply_text_preview="",
            full_reply_text="",
            metadata_json={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        async with AsyncSessionLocal() as session:
            session.add(log)
            await session.commit()

        return request_id

    async def update_request(
        self,
        request_id: str,
        *,
        status: Optional[LineRequestStatus] = None,
        stage: Optional[str] = None,
        error: Optional[str] = None,
        reply_text_preview: Optional[str] = None,
        success: Optional[bool] = None,
        finished: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        async with AsyncSessionLocal() as session:
            stmt = select(LineRequestLog).where(LineRequestLog.request_id == request_id)
            result = await session.execute(stmt)
            log = result.scalar_one_or_none()
            
            if not log:
                return False

            if status is not None:
                log.status = status
            if stage is not None:
                log.stage = stage
            if error is not None:
                log.error = error[:1000]
            if reply_text_preview is not None:
                log.reply_text_preview = reply_text_preview[:120]
                log.full_reply_text = reply_text_preview
            if success is not None:
                log.success = bool(success)
            if metadata:
                current_metadata = log.metadata_json or {}
                current_metadata.update(metadata)
                log.metadata_json = current_metadata

            log.updated_at = datetime.now(timezone.utc)

            if finished:
                log.completed_at = datetime.now(timezone.utc)

            await session.commit()
            return True

    async def get_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            stmt = select(LineRequestLog).where(LineRequestLog.request_id == request_id)
            result = await session.execute(stmt)
            log = result.scalar_one_or_none()
            
            if not log:
                return None
            return self._to_public(log)

    async def list_requests(
        self,
        *,
        limit: int = 50,
        status: Optional[LineRequestStatus] = None,
        active_only: bool = False,
    ) -> List[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            stmt = select(LineRequestLog).order_by(LineRequestLog.created_at.desc())
            
            if status:
                stmt = stmt.where(LineRequestLog.status == status)
            if active_only:
                stmt = stmt.where(LineRequestLog.status.in_(ACTIVE_STATUSES))
            
            stmt = stmt.limit(limit)
            
            result = await session.execute(stmt)
            logs = result.scalars().all()
            
            return [self._to_public(log) for log in logs]

    async def summarize(self) -> Dict[str, int]:
        async with AsyncSessionLocal() as session:
            # Get total count
            total_stmt = select(func.count(LineRequestLog.id))
            total_result = await session.execute(total_stmt)
            total = total_result.scalar() or 0

            # Get counts by status
            status_stmt = select(LineRequestLog.status, func.count(LineRequestLog.id)).group_by(LineRequestLog.status)
            status_result = await session.execute(status_stmt)
            status_counts = dict(status_result.all())

            summary: Dict[str, int] = {
                "received": status_counts.get("received", 0),
                "processing": status_counts.get("processing", 0),
                "completed": status_counts.get("completed", 0),
                "failed": status_counts.get("failed", 0),
                "ignored": status_counts.get("ignored", 0),
                "total": total,
            }
            summary["active"] = summary["received"] + summary["processing"]
            return summary

    async def delete_request(self, request_id: str) -> bool:
        async with AsyncSessionLocal() as session:
            stmt = delete(LineRequestLog).where(LineRequestLog.request_id == request_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_requests(self, request_ids: List[str]) -> int:
        if not request_ids:
            return 0
        async with AsyncSessionLocal() as session:
            stmt = delete(LineRequestLog).where(LineRequestLog.request_id.in_(request_ids))
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def get_user_history(self, user_id: str, limit: int = 5) -> List[Dict[str, str]]:
        """獲取特定使用者的對話歷史。回傳格式為 [{'role': 'user', 'content': '...'}, {'role': 'assistant', 'content': '...'}]"""
        if not user_id:
            return []

        async with AsyncSessionLocal() as session:
            # 只選取成功完成且有內容的記錄
            stmt = (
                select(LineRequestLog)
                .where(LineRequestLog.line_user_id == user_id)
                .where(LineRequestLog.status == "completed")
                .order_by(LineRequestLog.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            logs = result.scalars().all()
            
            history = []
            # 因為是按時間倒序排列，所以需要反轉回來
            for log in reversed(logs):
                if log.full_user_text:
                    history.append({"role": "user", "content": log.full_user_text})
                if log.full_reply_text:
                    history.append({"role": "assistant", "content": log.full_reply_text})
            
            return history


line_request_log_service = LineRequestLogService()
