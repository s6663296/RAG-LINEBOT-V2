import os
import pickle
import jieba
import logging
from typing import List, Dict, Any, Optional
from rank_bm25 import BM25Okapi
from app.core.config import settings

logger = logging.getLogger(__name__)

class BM25Service:
    """
    負責關鍵字檢索 (BM25)。
    支援中文斷詞與索引持久化。
    """

    def __init__(self):
        self.index_path = settings.RAG_BM25_INDEX_PATH
        self.bm25 = None
        self.corpus = []  # 存儲原始 chunk 數據: [{"id": str, "text": str, "payload": dict}]
        self.tokenized_corpus = []
        self._load_index()

    def _tokenize(self, text: str) -> List[str]:
        """
        對中文進行斷詞。
        """
        return list(jieba.cut(text))

    def _load_index(self):
        """
        從磁碟載入索引與語料庫。
        """
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "rb") as f:
                    data = pickle.load(f)
                    self.corpus = data.get("corpus", [])
                    self.tokenized_corpus = data.get("tokenized_corpus", [])
                    if self.tokenized_corpus:
                        self.bm25 = BM25Okapi(self.tokenized_corpus)
                logger.info(f"Successfully loaded BM25 index with {len(self.corpus)} documents.")
            except Exception as e:
                logger.error(f"Failed to load BM25 index: {e}")
                self.bm25 = None
                self.corpus = []
                self.tokenized_corpus = []
        else:
            logger.info("BM25 index path does not exist. Starting with empty index.")

    def _save_index(self):
        """
        將索引與語料庫儲存至磁碟。
        """
        try:
            # 確保目錄存在
            os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
            with open(self.index_path, "wb") as f:
                pickle.dump({
                    "corpus": self.corpus,
                    "tokenized_corpus": self.tokenized_corpus
                }, f)
            logger.info(f"BM25 index saved to {self.index_path}.")
        except Exception as e:
            logger.error(f"Failed to save BM25 index: {e}")

    def add_documents(self, documents: List[Dict[str, Any]]):
        """
        批次新增文件到索引。
        documents 格式: [{"id": str, "text": str, "payload": dict}]
        """
        new_tokenized = []
        for doc in documents:
            tokens = self._tokenize(doc["text"])
            self.corpus.append(doc)
            self.tokenized_corpus.append(tokens)
            new_tokenized.append(tokens)
        
        # 重新建立 BM25 實例 (rank_bm25 不支援增量更新，必須重新建立)
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        self._save_index()

    def clear_index(self):
        """
        清空索引。
        """
        self.bm25 = None
        self.corpus = []
        self.tokenized_corpus = []
        if os.path.exists(self.index_path):
            os.remove(self.index_path)
        logger.info("BM25 index cleared.")

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        執行關鍵字搜尋。
        """
        if not self.bm25 or not self.tokenized_corpus:
            return []

        tokenized_query = self._tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)
        
        # 取得 Top-K 索引
        import numpy as np
        top_indices = np.argsort(scores)[::-1][:limit]
        
        results = []
        for idx in top_indices:
            score = scores[idx]
            if score <= 0:
                continue
            
            doc = self.corpus[idx]
            results.append({
                "id": doc["id"],
                "score": float(score),
                "payload": doc["payload"]
            })
            
        return results

bm25_service = BM25Service()
