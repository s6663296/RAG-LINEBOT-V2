import httpx
import logging
from typing import List, Dict, Any, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

class RerankService:
    """
    負責呼叫外部 API 進行 Rerank (重排)。
    預設使用 SiliconFlow 相容介面與 BGE-Reranker-V2-M3 模型。
    """
    
    def __init__(self):
        self.api_url = settings.RERANK_API_URL.strip() if settings.RERANK_API_URL else ""
        self.api_key = settings.RERANK_API_KEY.strip() if settings.RERANK_API_KEY else settings.EMBEDDING_API_KEY.strip()
        self.model_id = settings.RERANK_MODEL_ID.strip() if settings.RERANK_MODEL_ID else "BAAI/bge-reranker-v2-m3"

    async def rerank(self, query: str, documents: List[str], top_n: int = 5) -> List[Dict[str, Any]]:
        """
        對文件清單進行重排。
        回傳格式: [{"index": int, "relevance_score": float}]
        """
        if not settings.RAG_ENABLE_RERANK:
            # 如果未啟用，則回傳原始順序的分數 (雖然這不應該被呼叫)
            return [{"index": i, "relevance_score": 1.0 / (i + 1)} for i in range(len(documents))]

        if not self.api_url or not self.api_key:
            logger.warning("Rerank API URL or Key not set. Skipping rerank.")
            return []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model_id,
            "query": query,
            "documents": documents,
            "top_n": top_n,
            "return_documents": False
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
                
                # SiliconFlow 的回傳通常在 "results" 中
                # 格式範例: {"results": [{"index": 0, "relevance_score": 0.99}, ...]}
                results = data.get("results", [])
                return results
                
            except Exception as e:
                logger.error(f"Rerank API Error: {e}")
                return []

    def refresh_from_settings(self) -> None:
        self.api_url = settings.RERANK_API_URL.strip() if settings.RERANK_API_URL else ""
        self.api_key = settings.RERANK_API_KEY.strip() if settings.RERANK_API_KEY else settings.EMBEDDING_API_KEY.strip()
        self.model_id = settings.RERANK_MODEL_ID.strip() if settings.RERANK_MODEL_ID else "BAAI/bge-reranker-v2-m3"

rerank_service = RerankService()
