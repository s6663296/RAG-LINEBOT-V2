import uuid
import datetime
from typing import List, Dict, Any
from app.services.embedding import embedding_service
from app.services.vector_db import vector_db_service

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
        
        # 批量大小
        batch_size = 32
        points = []
        
        # 分批處理 Chunks 以提高效率
        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i:i + batch_size]
            
            # 批量獲取向量
            denses, sparses = await embedding_service.get_embeddings_batch(batch_chunks)
            
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
            
        return {"doc_id": doc_id, "chunks_count": len(chunks)}

    async def search(self, query: str, limit: int = 5):
        """
        搜尋最相關的 chunks。
        """
        dense, sparse = await embedding_service.get_embeddings(query)
        results = vector_db_service.search_hybrid(dense, sparse, limit=limit)
        
        return [
            {
                "score": r.score,
                "payload": r.payload
            }
            for r in results
        ]

rag_manager = RAGManager()
