import json
import asyncio
from typing import Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.services.llm_client import llm_client
from app.services.agent import agent_service

router = APIRouter()


class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]


def _is_llm_configured() -> bool:
    return all(
        [
            settings.LLM_BASE_URL,
            settings.LLM_API_KEY,
            settings.LLM_MODEL_ID,
        ]
    )


@router.post("/completions")
async def chat_endpoint(request: ChatRequest):
    """接收測試訊息並使用後端 Agent 流程回覆。"""
    if not _is_llm_configured():
        raise HTTPException(
            status_code=500,
            detail={"error": "LLM is not configured on server"},
        )

    if not request.messages:
        return {"choices": [{"message": {"role": "assistant", "content": "您好，有什麼我可以幫您的嗎？"}}]}

    # 取最後一則訊息作為使用者問題
    user_text = request.messages[-1].get("content", "")
    if not user_text:
         return {"choices": [{"message": {"role": "assistant", "content": "抱歉，我沒有收到您的訊息內容。"}}] }

    # 提取歷史紀錄 (不包含最後一則當前訊息)
    history = []
    if len(request.messages) > 1:
        # 取最近的 N 則訊息作為上下文
        history = request.messages[-(settings.LLM_CONTEXT_WINDOW_SIZE + 1):-1]

    try:
        reply_text = await agent_service.generate_response(user_text, history=history)
        
        # 回傳 OpenAI 相容格式以維持前端正常運作
        return {
            "id": "chat-rag-agent",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": reply_text
                    },
                    "finish_reason": "stop"
                }
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "Agent Error", "detail": str(exc)})


@router.post("/completions/stream")
async def chat_stream_endpoint(request: ChatRequest):
    """串流輸出 RAG 流程狀態與最終回覆。"""
    if not _is_llm_configured():
        raise HTTPException(status_code=500, detail="LLM is not configured")

    user_text = request.messages[-1].get("content", "") if request.messages else ""

    async def event_generator():
        # 用於接收狀態更新的隊列
        queue = asyncio.Queue()

        async def status_callback(status: str):
            await queue.put({"type": "status", "content": status})

        # 提取歷史紀錄
        history = []
        if request.messages and len(request.messages) > 1:
            history = request.messages[-(settings.LLM_CONTEXT_WINDOW_SIZE + 1):-1]

        # 啟動 Agent 任務
        task = asyncio.create_task(agent_service.generate_response(user_text, history=history, status_callback=status_callback))

        # 持續讀取隊列直到任務完成
        while not task.done() or not queue.empty():
            while not queue.empty():
                msg = await queue.get()
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            
            if not task.done():
                await asyncio.sleep(0.05)
        
        # 獲取最終結果
        try:
            final_answer = await task
            yield f"data: {json.dumps({'type': 'answer', 'content': final_answer}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")
