import os
from pathlib import Path

from pydantic_settings import BaseSettings

ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILES = [str(ROOT_DIR / ".env")]


class Settings(BaseSettings):
    PROJECT_NAME: str = "控制台"
    API_V1_STR: str = "/api/v1"

    # LINE Bot Webhook 設定
    LINE_ENABLE_SIGNATURE_VALIDATION: bool = True
    LINE_CHANNEL_SECRET: str = ""
    LINE_CHANNEL_ACCESS_TOKEN: str = ""

    # LINE 請求記錄設定
    LINE_REQUEST_LOG_MAX_ENTRIES: int = 1000
    LINE_REPLY_TIMEOUT_SECONDS: float = 30.0

    # 共用 LLM 設定（LINE Bot / 測試聊天 API 共用）
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL_ID: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.7
    LLM_CONTEXT_WINDOW_SIZE: int = 5
    LLM_REQUEST_TIMEOUT_SECONDS: float = 120.0
    AGENT_MAX_ITERATIONS: int = 8
    RAG_AGENT_MAX_SEARCH_ROUNDS: int = 3
    RAG_TOP_K: int = 8
    RAG_CANDIDATE_MULTIPLIER: int = 3
    RAG_SCORE_THRESHOLD: float = 0.0
    LLM_STRICT_SERVICE_SCOPE_MODE: bool = False
    LLM_SYSTEM_PROMPT: str = "You are a professional and friendly customer service assistant. Please respond in Traditional Chinese (zh-TW)."
    LLM_ROUTER_PROMPT: str = """You are a professional RAG system Router.

<interaction_style>
- Be concise and precise.
- Output ONLY pure JSON.
- DO NOT include any explanatory text.
</interaction_style>

<tool_use>
Your task is to analyze the user's input and output the results in pure JSON format.

JSON Field Descriptions:
- intent: String, judgment of the user's intent (e.g., "faq", "greeting", "legal_research", "others").
- need_retrieval: Boolean. Set to true if the question is about the knowledge base, documents, FAQs, products, company info, internal policies, regulations, laws, contracts, cases, or anything requiring data lookup. Set to false for greetings or casual talk.
- rewritten_query: String. Optimized Traditional Chinese search terms for knowledge base retrieval. Retain original keywords and supplement with synonyms, possible jurisdictions, law names, or related concepts.
</tool_use>

[Examples]
- Example 1:
  User: What time do you close?
  Output: {"intent": "faq", "need_retrieval": true, "rewritten_query": "營業時間 門市服務時間 客服時間"}

- Example 2:
  User: What are the relevant laws for a consignee requesting delivery of goods?
  Output: {"intent": "legal_research", "need_retrieval": true, "rewritten_query": "受貨人 請求交付 運送物 交付運送物 相關法條 民法 海商法 運送契約 提單"}
"""

    # 資料庫設定
    DATABASE_URL: str = "sqlite+aiosqlite:///data/app.db"

    # Qdrant 設定
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION_NAME: str = "rag_bge_m3"
    QDRANT_REQUEST_TIMEOUT_SECONDS: int = 30

    # Embedding API 設定
    EMBEDDING_API_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_REQUEST_TIMEOUT_SECONDS: float = 120.0

    # Rerank API 設定
    RAG_ENABLE_RERANK: bool = True
    RERANK_API_URL: str = "https://api.siliconflow.cn/v1/rerank"
    RERANK_API_KEY: str = ""
    RERANK_MODEL_ID: str = "BAAI/bge-reranker-v2-m3"
    
    # BM25 設定
    RAG_ENABLE_BM25: bool = True
    RAG_BM25_INDEX_PATH: str = str(DATA_DIR / "bm25_index.pkl")

    class Config:
        case_sensitive = True
        env_file = tuple(ENV_FILES)


settings = Settings()
