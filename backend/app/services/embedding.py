import httpx
from typing import List, Dict, Any, Tuple, Optional
from app.core.config import settings

class EmbeddingService:
    """
    負責呼叫外部 API 產生 BGE-M3 的 Dense 與 Sparse 向量。
    """
    
    def __init__(self):
        self.api_url = settings.EMBEDDING_API_URL.strip() if settings.EMBEDDING_API_URL else ""
        self.api_key = settings.EMBEDDING_API_KEY.strip() if settings.EMBEDDING_API_KEY else ""

    async def get_embeddings(self, text: str) -> Tuple[List[float], Dict[int, float]]:
        """
        獲取文字的向量表示。支援 SiliconFlow OpenAI 相容 API。
        回傳: (dense_vector, sparse_vector)
        """
        denses, sparses = await self.get_embeddings_batch([text])
        return denses[0], sparses[0]

    async def get_embeddings_batch(self, texts: List[str]) -> Tuple[List[List[float]], List[Dict[int, float]]]:
        """
        批量獲取文字的向量表示。
        回傳: (list_of_dense_vectors, list_of_sparse_vectors)
        """
        self.refresh_from_settings()
        if not self.api_url or not self.api_key:
            raise RuntimeError("Embedding API 尚未設定，無法進行向量化檢索。")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "input": texts,
            "model": "BAAI/bge-m3"
        }

        async with httpx.AsyncClient(timeout=settings.EMBEDDING_REQUEST_TIMEOUT_SECONDS) as client:
            try:
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json=payload
                )
                if response.status_code != 200:
                    error_msg = response.text
                    try:
                        error_json = response.json()
                        if "message" in error_json:
                            error_msg = error_json["message"]
                    except:
                        pass
                    raise RuntimeError(f"Embedding API Error ({response.status_code}): {error_msg}")
                
                data = response.json()
                
                denses = []
                if "data" in data:
                    # 確保按照輸入順序排序 (通常 OpenAI API 是保證順序的)
                    sorted_data = sorted(data["data"], key=lambda x: x.get("index", 0))
                    for item in sorted_data:
                        denses.append(item["embedding"])
                
                if len(denses) != len(texts):
                    raise ValueError(f"Embedding API returned {len(denses)} vectors for {len(texts)} inputs")
                
                # 目前 API 回傳僅支援 Dense；若未來支援 Sparse，可在此填入 sparse 欄位。
                sparses = [{} for _ in range(len(texts))]
                
                return denses, sparses
                
            except Exception as e:
                raise RuntimeError(f"Embedding Batch API Error: {e}") from e

    def refresh_from_settings(self) -> None:
        self.api_url = settings.EMBEDDING_API_URL.strip() if settings.EMBEDDING_API_URL else ""
        self.api_key = settings.EMBEDDING_API_KEY.strip() if settings.EMBEDDING_API_KEY else ""

embedding_service = EmbeddingService()
