import json
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel
from app.services.llm_client import llm_client
from app.core.config import settings

logger = logging.getLogger(__name__)

class ProcessedQuery(BaseModel):
    """
    經過 LLM 處理後的查詢結構。
    """
    intent: str
    need_retrieval: bool
    rewritten_query: str

class QueryProcessor:
    """
    負責訊息前處理（Router 角色），將原始訊息轉為結構化查詢。
    """

    async def process_query(self, user_text: str) -> ProcessedQuery:
        """
        利用 LLM 進行意圖分析與查詢詞優化。
        """
        messages = [
            {"role": "system", "content": settings.LLM_ROUTER_PROMPT},
            {"role": "user", "content": user_text}
        ]

        try:
            result = await llm_client.chat_completion(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                model_id=settings.LLM_MODEL_ID,
                messages=messages,
                temperature=0.1,  # 使用低溫以獲得穩定的 JSON 格式
                timeout_seconds=settings.LLM_REQUEST_TIMEOUT_SECONDS
            )

            if "error" in result:
                logger.error(f"QueryProcessor LLM error: {result}")
                return self._get_fallback_query(user_text)

            content = self._extract_content(result)
            logger.info(f"QueryProcessor Raw Output: {content}")

            if not content:
                logger.warning("QueryProcessor returned empty content")
                return self._get_fallback_query(user_text)

            # 嘗試解析 JSON
            try:
                # 處理可能的 markdown 程式碼塊
                clean_content = content.strip()
                if clean_content.startswith("```"):
                    # 尋找第一個 { 和最後一個 }
                    start_idx = clean_content.find("{")
                    end_idx = clean_content.rfind("}")
                    if start_idx != -1 and end_idx != -1:
                        clean_content = clean_content[start_idx:end_idx+1]
                    else:
                        # 傳統去除 markdown 標籤
                        lines = clean_content.split("\n")
                        clean_content = "\n".join(lines[1:-1]) if len(lines) > 2 else clean_content.strip("`")
                        if clean_content.startswith("json"):
                            clean_content = clean_content[4:]

                data = json.loads(clean_content)
                processed = ProcessedQuery(**data)
                logger.info(f"QueryProcessor Parsed Success: {processed}")
                return processed
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse QueryProcessor JSON: {content}, error: {e}")
                return self._get_fallback_query(user_text)

        except Exception as e:
            logger.exception(f"Unexpected error in QueryProcessor: {e}")
            return self._get_fallback_query(user_text)

    def _get_fallback_query(self, user_text: str) -> ProcessedQuery:
        """
        當 LLM 失敗時的備援方案。
        """
        return ProcessedQuery(
            intent="faq",
            need_retrieval=True,
            rewritten_query=user_text
        )

    def _extract_content(self, result: Dict[str, Any]) -> str:
        """
        從 LLM 回傳結果中取出文字內容。
        """
        choices = result.get("choices") or []
        if not choices:
            return ""
        
        message = choices[0].get("message") or {}
        return message.get("content", "").strip()

query_processor = QueryProcessor()
