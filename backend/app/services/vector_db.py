from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, 
    VectorParams, 
    SparseVectorParams, 
    PointStruct,
    SparseVector,
    models
)
from typing import List, Dict, Any, Optional
from app.core.config import settings
import datetime

class VectorDBService:
    """
    負責 Qdrant Cloud 的連線與操作。
    """
    
    def __init__(self):
        self.client = None
        self.collection_name = ""
        self.reload_from_settings()

    def reload_from_settings(self):
        """
        依照目前 runtime settings 重建 Qdrant client。
        未設定 Qdrant 時保留為 None，避免無效連線拖慢 API。
        """
        self.collection_name = settings.QDRANT_COLLECTION_NAME
        qdrant_url = settings.QDRANT_URL.strip() if settings.QDRANT_URL else ""

        if not qdrant_url:
            self.client = None
            return

        self.client = QdrantClient(
            url=qdrant_url,
            api_key=settings.QDRANT_API_KEY,
            timeout=settings.QDRANT_REQUEST_TIMEOUT_SECONDS,
            prefer_grpc=False,  # 使用 REST (HTTP) 模式，大幅降低記憶體用量
        )

    def _require_client(self):
        if not self.client:
            raise RuntimeError("Qdrant 尚未設定，請先提供 QDRANT_URL。")
        return self.client

    def init_collection(self):
        """
        初始化 Collection，設定 Dense 與 Sparse 向量參數。
        """
        client = self._require_client()
        # 檢查 Collection 是否已存在
        collections = client.get_collections().collections
        exists = any(c.name == self.collection_name for c in collections)
        
        if not exists:
            self.create_new_collection()
            return f"Collection '{self.collection_name}' created."
        return f"Collection '{self.collection_name}' already exists."

    def clear_collection(self):
        """
        刪除並重新建立 Collection (清空所有資料)。
        """
        client = self._require_client()
        client.delete_collection(collection_name=self.collection_name)
        self.create_new_collection()
        return f"Collection '{self.collection_name}' has been cleared and recreated."

    def create_new_collection(self):
        """
        建立新的 Collection 結構。
        """
        client = self._require_client()
        client.create_collection(
            collection_name=self.collection_name,
            vectors_config={
                "dense": VectorParams(
                    size=1024, 
                    distance=Distance.COSINE
                )
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams()
            }
        )

    def upsert_points(self, points: List[Dict[str, Any]]):
        """
        批次插入數據。
        points 格式: [{id, dense, sparse, payload}]
        """
        client = self._require_client()
        point_structs = []
        for p in points:
            point_structs.append(
                PointStruct(
                    id=p["id"],
                    vector={
                        "dense": p["dense"],
                        "sparse": SparseVector(
                            indices=list(p["sparse"].keys()),
                            values=list(p["sparse"].values())
                        )
                    },
                    payload=p["payload"]
                )
            )
        
        client.upsert(
            collection_name=self.collection_name,
            points=point_structs,
        )

    def search_hybrid(self, dense_vector: List[float], sparse_vector: Dict[int, float], limit: int = 5):
        """
        執行混合檢索 (Hybrid Search)。
        這裡實作 RRF 或簡單的多路檢索合併。
        目前 Qdrant 支援 Prefetch 與 RRF 融合。
        """
        client = self._require_client()
        dense_results = client.search(
            collection_name=self.collection_name,
            query_vector=("dense", dense_vector),
            limit=limit,
            with_payload=True,
            timeout=settings.QDRANT_REQUEST_TIMEOUT_SECONDS,
        )

        if not sparse_vector:
            return dense_results

        # 轉換為 Qdrant 的 SparseVector
        qv_sparse = SparseVector(
            indices=list(sparse_vector.keys()),
            values=list(sparse_vector.values())
        )

        sparse_results = client.search(
            collection_name=self.collection_name,
            query_vector=("sparse", qv_sparse),
            limit=limit,
            with_payload=True,
            timeout=settings.QDRANT_REQUEST_TIMEOUT_SECONDS,
        )

        # Reciprocal Rank Fusion：同時利用 dense 與 sparse 排名，提升關鍵字型問題的穩定度。
        fused = {}
        for source_results in (dense_results, sparse_results):
            for rank, point in enumerate(source_results, start=1):
                point_id = str(point.id)
                if point_id not in fused:
                    fused[point_id] = point
                    fused[point_id].score = 0.0
                fused[point_id].score += 1.0 / (60 + rank)

        return sorted(fused.values(), key=lambda point: point.score, reverse=True)[:limit]

vector_db_service = VectorDBService()
