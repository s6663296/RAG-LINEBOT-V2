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

        max_retries = 3
        retry_delay = 2.0  # 初始重試延遲（秒）

        for attempt in range(max_retries + 1):
            async with httpx.AsyncClient(timeout=settings.EMBEDDING_REQUEST_TIMEOUT_SECONDS) as client:
                try:
                    response = await client.post(
                        self.api_url,
                        headers=headers,
                        json=payload
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        denses = []
                        if "data" in data:
                            sorted_data = sorted(data["data"], key=lambda x: x.get("index", 0))
                            for item in sorted_data:
                                denses.append(item["embedding"])
                        
                        if len(denses) != len(texts):
                            raise ValueError(f"Embedding API returned {len(denses)} vectors for {len(texts)} inputs")
                        
                        sparses = [{} for _ in range(len(texts))]
                        return denses, sparses

                    # 處理頻率限制 (429) 或未驗證帳戶的 403 限制
                    error_msg = response.text
                    try:
                        error_json = response.json()
                        if "message" in error_json:
                            error_msg = error_json["message"]
                    except:
                        pass

                    if (response.status_code == 429 or (response.status_code == 403 and "limit exceeded" in error_msg.lower())) and attempt < max_retries:
                        wait_time = retry_delay * (2 ** attempt)
                        print(f"Embedding API {response.status_code} (嘗試 {attempt+1}/{max_retries+1}): {error_msg}. 等待 {wait_time}s 後重試...")
                        import asyncio
                        await asyncio.sleep(wait_time)
                        continue
                    
                    raise RuntimeError(f"Embedding API Error ({response.status_code}): {error_msg}")
                    
                except Exception as e:
                    if attempt < max_retries and not isinstance(e, RuntimeError):
                        wait_time = retry_delay * (2 ** attempt)
                        print(f"Embedding API 請求異常 (嘗試 {attempt+1}/{max_retries+1}): {e}. 等待 {wait_time}s 後重試...")
                        import asyncio
                        await asyncio.sleep(wait_time)
                        continue
                    raise RuntimeError(f"Embedding Batch API Error: {e}") from e

    def refresh_from_settings(self) -> None:
        self.api_url = settings.EMBEDDING_API_URL.strip() if settings.EMBEDDING_API_URL else ""
        self.api_key = settings.EMBEDDING_API_KEY.strip() if settings.EMBEDDING_API_KEY else ""

embedding_service = EmbeddingService()
