from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.request_log import (
    LineRequestStatus,
    line_request_log_service,
)

router = APIRouter()


class LineRequestLogItem(BaseModel):
    request_id: str
    webhook_request_id: str
    line_event_id: str
    event_index: int
    event_type: str
    message_type: str
    line_user_id: str
    line_group_id: str
    line_room_id: str
    status: LineRequestStatus
    stage: str
    success: bool | None
    error: str
    user_text_preview: str
    reply_text_preview: str
    created_at: str
    updated_at: str
    completed_at: str | None
    duration_ms: int | None
    is_active: bool
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LineRequestLogListResponse(BaseModel):
    summary: Dict[str, int]
    total_returned: int
    items: List[LineRequestLogItem]


class LineRequestLogDetailResponse(BaseModel):
    item: LineRequestLogItem


class LineRequestLogSummaryResponse(BaseModel):
    summary: Dict[str, int]


class DeleteLogsRequest(BaseModel):
    request_ids: List[str]


class DeleteLogsResponse(BaseModel):
    deleted_count: int
    success: bool


@router.get("/line/requests", response_model=LineRequestLogListResponse)
async def list_line_requests(
    limit: int = Query(default=50, ge=1, le=500),
    status: LineRequestStatus | None = Query(default=None),
    active_only: bool = Query(default=False),
) -> LineRequestLogListResponse:
    items = await line_request_log_service.list_requests(
        limit=limit,
        status=status,
        active_only=active_only,
    )
    summary = await line_request_log_service.summarize()

    return LineRequestLogListResponse(
        summary=summary,
        total_returned=len(items),
        items=[LineRequestLogItem(**item) for item in items],
    )


@router.get("/line/requests/{request_id}", response_model=LineRequestLogDetailResponse)
async def get_line_request(request_id: str) -> LineRequestLogDetailResponse:
    item = await line_request_log_service.get_request(request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Request log not found")

    return LineRequestLogDetailResponse(item=LineRequestLogItem(**item))


@router.get("/line/summary", response_model=LineRequestLogSummaryResponse)
async def get_line_request_summary() -> LineRequestLogSummaryResponse:
    summary = await line_request_log_service.summarize()
    return LineRequestLogSummaryResponse(summary=summary)


@router.delete("/line/requests/{request_id}")
async def delete_line_request(request_id: str) -> Dict[str, Any]:
    success = await line_request_log_service.delete_request(request_id)
    if not success:
        raise HTTPException(status_code=404, detail="Request log not found")
    return {"success": True, "message": "Log deleted successfully"}


@router.delete("/line/requests", response_model=DeleteLogsResponse)
async def delete_line_requests(request: DeleteLogsRequest) -> DeleteLogsResponse:
    count = await line_request_log_service.delete_requests(request.request_ids)
    return DeleteLogsResponse(deleted_count=count, success=True)
