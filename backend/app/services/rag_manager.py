import uuid
import datetime
import asyncio
from typing import List, Dict, Any
from app.services.embedding import embedding_service
from app.services.vector_db import vector_db_service
from app.services.bm25_service import bm25_service
from app.core.config import settings

class RAGManager:
    """
    協調文件處理流程：切分 -> 向量化 -> 儲存。
    """

    def split_text(self, text: str, chunk_size: int = 600, overlap: int = 100) -> List[str]:
        """
        將文字切分為多個 Chunks。
        優先以段落 (\n\n) 切分，再根據長度細分。
        """
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            if len(current_chunk) + len(para) <= chunk_size:
                current_chunk += para + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                
                # 如果單個段落就超過 chunk_size，則硬切
                if len(para) > chunk_size:
                    start = 0
                    while start < len(para):
                        end = start + chunk_size
                        chunks.append(para[start:end])
                        start += chunk_size - overlap
                    current_chunk = ""
                else:
                    current_chunk = para + "\n\n"
        
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return chunks

    async def add_document(
        self, 
        text: str, 
        title: str, 
        source: str = "upload", 
        lang: str = "zh", 
        section: str = "general",
        chunk_size: int = 600,
        overlap: int = 100
    ):
        """
        處理並索引文件。
        """
        chunks = self.split_text(text, chunk_size=chunk_size, overlap=overlap)
        doc_id = str(uuid.uuid4())
        created_at = datetime.datetime.now().isoformat()
        
        # 批量大小 (降低以避免部分 API 的 Token 限制或 403 錯誤)
        batch_size = 16
        points = []
        
        # 分批處理 Chunks 以提高效率
        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i:i + batch_size]
            
            # 批量獲取向量
            denses, sparses = await embedding_service.get_embeddings_batch(batch_chunks)
            
            # 增加微小延遲，避免過快觸發頻率限制
            await asyncio.sleep(0.1)
            
            for j, (chunk, dense, sparse) in enumerate(zip(batch_chunks, denses, sparses)):
                chunk_index = i + j
                chunk_id = f"{doc_id}_{chunk_index}"
                
                points.append({
                    "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id)),
                    "dense": dense,
                    "sparse": sparse,
                    "payload": {
                        "doc_id": doc_id,
                        "chunk_id": chunk_index,
                        "title": title,
                        "text": chunk,
                        "source": source,
                        "lang": lang,
                        "section": section,
                        "created_at": created_at
                    }
                })
        
        # 批量存入 Qdrant
        if points:
            vector_db_service.upsert_points(points)
            
        # 同步存入 BM25 索引
        if settings.RAG_ENABLE_BM25 and points:
            bm25_docs = [
                {
                    "id": p["id"],
                    "text": p["payload"]["text"],
                    "payload": p["payload"]
                }
                for p in points
            ]
            bm25_service.add_documents(bm25_docs)
            
        return {"doc_id": doc_id, "chunks_count": len(chunks)}

    async def search(self, query: str, limit: int = 5):
        """
        搜尋最相關的 chunks。
        支援 Dense + BM25 混合檢索與 RRF 融合。
        """
        dense_vec, sparse_vec = await embedding_service.get_embeddings(query)
        
        # 1. 取得向量檢索結果 (通常 API 只回 Dense)
        dense_results = vector_db_service.search_hybrid(dense_vec, sparse_vec, limit=limit * 2)
        
        # 2. 取得 BM25 關鍵字檢索結果
        bm25_results = []
        if settings.RAG_ENABLE_BM25:
            bm25_results = bm25_service.search(query, limit=limit * 2)
            
        # 3. RRF (Reciprocal Rank Fusion) 融合
        # 這裡實作簡單的 RRF
        fused_scores = {}
        k = 60  # RRF 常數
        
        # 處理 Dense 結果
        for rank, res in enumerate(dense_results, start=1):
            doc_id = str(res.id)
            if doc_id not in fused_scores:
                fused_scores[doc_id] = {"point": res, "score": 0.0}
            fused_scores[doc_id]["score"] += 1.0 / (k + rank)
            
        # 處理 BM25 結果
        for rank, res in enumerate(bm25_results, start=1):
            doc_id = str(res["id"])
            if doc_id not in fused_scores:
                # 如果不在向量結果中，需要建立一個類型的點
                from qdrant_client.models import ScoredPoint
                point = ScoredPoint(
                    id=res["id"],
                    version=0,
                    score=0.0,
                    payload=res["payload"],
                    vector=None
                )
                fused_scores[doc_id] = {"point": point, "score": 0.0}
            fused_scores[doc_id]["score"] += 1.0 / (k + rank)
            
        # 排序並取 Top-K
        sorted_results = sorted(fused_scores.values(), key=lambda x: x["score"], reverse=True)
        top_results = sorted_results[:limit]
        
        return [
            {
                "score": r["score"],
                "payload": r["point"].payload
            }
            for r in top_results
        ]

rag_manager = RAGManager()
