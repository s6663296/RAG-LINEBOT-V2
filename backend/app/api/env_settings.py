from pathlib import Path
from typing import Any, Dict, List

from dotenv import dotenv_values, set_key
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import DATA_DIR, ROOT_DIR, settings

router = APIRouter()
ENV_FILE_PATH = ROOT_DIR / ".env"


class EnvSettingItem(BaseModel):
    key: str
    label: str
    input_type: str
    description: str
    value: str


class EnvSettingsResponse(BaseModel):
    items: List[EnvSettingItem]


class EnvSettingsUpdateRequest(BaseModel):
    values: Dict[str, Any]


class EnvSettingsUpdateResponse(BaseModel):
    updated_keys: List[str]
    items: List[EnvSettingItem]


ENV_SETTING_DEFINITIONS: List[Dict[str, str]] = [
    {
        "key": "LINE_ENABLE_SIGNATURE_VALIDATION",
        "label": "LINE 簽章驗證",
        "input_type": "boolean",
        "description": "是否驗證 LINE Webhook 的 X-Line-Signature。",
    },
    {
        "key": "LINE_CHANNEL_SECRET",
        "label": "LINE Channel Secret",
        "input_type": "password",
        "description": "LINE Developers 提供的 Channel Secret。",
    },
    {
        "key": "LINE_CHANNEL_ACCESS_TOKEN",
        "label": "LINE Channel Access Token",
        "input_type": "password",
        "description": "LINE Developers 的 Messaging API token。",
    },
    {
        "key": "LLM_BASE_URL",
        "label": "LLM Base URL",
        "input_type": "text",
        "description": "OpenAI 相容 API 入口，例如 https://api.openai.com/v1。",
    },
    {
        "key": "LLM_API_KEY",
        "label": "LLM API Key",
        "input_type": "password",
        "description": "LLM 服務的 API Key。",
    },
    {
        "key": "LLM_MODEL_ID",
        "label": "LLM Model ID",
        "input_type": "text",
        "description": "要使用的模型名稱，例如 gpt-4o-mini。",
    },
    {
        "key": "LLM_TEMPERATURE",
        "label": "LLM Temperature",
        "input_type": "number",
        "description": "回覆隨機程度，建議 0 到 2。",
    },
    {
        "key": "LLM_CONTEXT_WINDOW_SIZE",
        "label": "上下文窗口大小",
        "input_type": "number",
        "description": "保留最近的對話訊息數量（預設為 5）。",
    },
    {
        "key": "LLM_REQUEST_TIMEOUT_SECONDS",
        "label": "LLM 請求逾時秒數",
        "input_type": "number",
        "description": "呼叫 LLM API 的等待秒數，RAG 多步驟建議 90 到 180 秒。",
    },
    {
        "key": "AGENT_MAX_ITERATIONS",
        "label": "Agent 最大步驟數",
        "input_type": "number",
        "description": "Agent 可執行的最大決策步驟數，避免 RAG 流程太早結束。",
    },
    {
        "key": "RAG_AGENT_MAX_SEARCH_ROUNDS",
        "label": "RAG Agent 最大查詢輪數",
        "input_type": "number",
        "description": "每次 RAG 回答最多可反覆判斷、改寫問題並重新查詢的輪數，建議 2 到 4。",
    },
    {
        "key": "RAG_TOP_K",
        "label": "RAG 回傳筆數",
        "input_type": "number",
        "description": "最終提供給 LLM 的參考片段數量，數值越大越完整但也越慢。",
    },
    {
        "key": "RAG_CANDIDATE_MULTIPLIER",
        "label": "RAG 候選倍率",
        "input_type": "number",
        "description": "先取更多候選片段再排序，建議 2 到 4。",
    },
    {
        "key": "RAG_SCORE_THRESHOLD",
        "label": "RAG 分數門檻",
        "input_type": "number",
        "description": "過濾低相關度片段；0 表示不過濾。若誤殺答案可維持 0。",
    },
    {
        "key": "RAG_ENABLE_RERANK",
        "label": "啟用 Rerank (重排)",
        "input_type": "boolean",
        "description": "是否啟用兩階段檢索。開啟後會更準確，但會多一次 API 呼叫。",
    },
    {
        "key": "RERANK_API_URL",
        "label": "Rerank API URL",
        "input_type": "text",
        "description": "Rerank API 的 URL，例如 https://api.siliconflow.cn/v1/rerank。",
    },
    {
        "key": "RERANK_API_KEY",
        "label": "Rerank API Key",
        "input_type": "password",
        "description": "Rerank 服務的 API Key (若與 Embedding 相同可留空)。",
    },
    {
        "key": "RERANK_MODEL_ID",
        "label": "Rerank Model ID",
        "input_type": "text",
        "description": "要使用的 Rerank 模型名稱，例如 BAAI/bge-reranker-v2-m3。",
    },
    {
        "key": "LLM_SYSTEM_PROMPT",
        "label": "LLM System Prompt",
        "input_type": "textarea",
        "description": "系統提示詞，會附加在每次對話前。",
    },
    {
        "key": "QDRANT_URL",
        "label": "Qdrant URL",
        "input_type": "text",
        "description": "Qdrant Cloud 的 URL，例如 https://xxx.xxx.cloud.qdrant.io:6333。",
    },
    {
        "key": "QDRANT_API_KEY",
        "label": "Qdrant API Key",
        "input_type": "password",
        "description": "Qdrant Cloud 的 API Key。",
    },
    {
        "key": "QDRANT_COLLECTION_NAME",
        "label": "Qdrant Collection",
        "input_type": "text",
        "description": "要使用的 Collection 名稱。",
    },
    {
        "key": "QDRANT_REQUEST_TIMEOUT_SECONDS",
        "label": "Qdrant 請求逾時秒數",
        "input_type": "number",
        "description": "連線 Qdrant 查詢、寫入與健康檢查的等待秒數。",
    },
    {
        "key": "EMBEDDING_API_URL",
        "label": "Embedding API URL",
        "input_type": "text",
        "description": "Embedding API 的 URL，例如 SiliconFlow 的 https://api.siliconflow.cn/v1/embeddings。",
    },
    {
        "key": "EMBEDDING_API_KEY",
        "label": "Embedding API Key",
        "input_type": "password",
        "description": "Embedding 服務的 API Key。",
    },
    {
        "key": "EMBEDDING_REQUEST_TIMEOUT_SECONDS",
        "label": "Embedding 請求逾時秒數",
        "input_type": "number",
        "description": "呼叫 Embedding API 的等待秒數。",
    },
    {
        "key": "LINE_REPLY_TIMEOUT_SECONDS",
        "label": "LINE Reply API 逾時秒數",
        "input_type": "number",
        "description": "呼叫 LINE 回覆 API 的等待秒數。",
    },
]

EDITABLE_ENV_KEYS = {item["key"] for item in ENV_SETTING_DEFINITIONS}


def _ensure_env_file_exists() -> None:
    ENV_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE_PATH.exists():
        ENV_FILE_PATH.write_text("", encoding="utf-8")


def _load_env_values() -> Dict[str, str]:
    _ensure_env_file_exists()
    values = dotenv_values(str(ENV_FILE_PATH))
    return {key: "" if value is None else str(value) for key, value in values.items()}


def _normalize_boolean(value: Any, key: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return "true"
    if normalized in {"false", "0", "no", "off"}:
        return "false"

    raise HTTPException(status_code=422, detail=f"{key} 必須是布林值")


def _normalize_number(value: Any, key: str) -> str:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{key} 必須是數字") from exc

    if number.is_integer():
        return str(int(number))
    return str(number)


def _normalize_value(key: str, value: Any, input_type: str) -> str:
    if input_type == "boolean":
        return _normalize_boolean(value, key)
    if input_type == "number":
        return _normalize_number(value, key)
    if value is None:
        return ""
    return str(value)


def _runtime_value_from_settings(key: str) -> str:
    value = getattr(settings, key, "")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _build_items(values: Dict[str, str]) -> List[EnvSettingItem]:
    items: List[EnvSettingItem] = []
    for item in ENV_SETTING_DEFINITIONS:
        key = item["key"]
        current_value = values.get(key, _runtime_value_from_settings(key))
        items.append(
            EnvSettingItem(
                key=key,
                label=item["label"],
                input_type=item["input_type"],
                description=item["description"],
                value=current_value,
            )
        )
    return items


def _apply_runtime_settings(updated_values: Dict[str, str]) -> None:
    for key, raw_value in updated_values.items():
        if key == "LINE_ENABLE_SIGNATURE_VALIDATION":
            settings.LINE_ENABLE_SIGNATURE_VALIDATION = raw_value == "true"
        elif key == "RAG_ENABLE_RERANK":
            settings.RAG_ENABLE_RERANK = raw_value == "true"
        elif key == "LLM_TEMPERATURE":
            settings.LLM_TEMPERATURE = float(raw_value)
        elif key in {
            "LLM_REQUEST_TIMEOUT_SECONDS",
            "RAG_SCORE_THRESHOLD",
            "EMBEDDING_REQUEST_TIMEOUT_SECONDS",
            "LINE_REPLY_TIMEOUT_SECONDS",
        }:
            setattr(settings, key, float(raw_value))
        elif key in {
            "LLM_CONTEXT_WINDOW_SIZE",
            "AGENT_MAX_ITERATIONS",
            "RAG_AGENT_MAX_SEARCH_ROUNDS",
            "RAG_TOP_K",
            "RAG_CANDIDATE_MULTIPLIER",
            "QDRANT_REQUEST_TIMEOUT_SECONDS",
        }:
            setattr(settings, key, int(float(raw_value)))
        else:
            setattr(settings, key, raw_value)

    if any(key.startswith("QDRANT_") for key in updated_values):
        from app.services.vector_db import vector_db_service
        vector_db_service.reload_from_settings()

    if any(key.startswith("EMBEDDING_") for key in updated_values):
        from app.services.embedding import embedding_service
        embedding_service.refresh_from_settings()

    if any(key == "RAG_ENABLE_RERANK" or key.startswith("RERANK_") for key in updated_values):
        from app.services.rerank import rerank_service
        rerank_service.refresh_from_settings()


@router.get("/env", response_model=EnvSettingsResponse)
async def get_env_settings() -> EnvSettingsResponse:
    values = _load_env_values()
    return EnvSettingsResponse(items=_build_items(values))


@router.put("/env", response_model=EnvSettingsUpdateResponse)
async def update_env_settings(payload: EnvSettingsUpdateRequest) -> EnvSettingsUpdateResponse:
    if not payload.values:
        raise HTTPException(status_code=400, detail="請提供要更新的參數")

    _ensure_env_file_exists()

    normalized_updates: Dict[str, str] = {}
    definition_map = {item["key"]: item for item in ENV_SETTING_DEFINITIONS}

    for key, value in payload.values.items():
        if key not in EDITABLE_ENV_KEYS:
            continue

        input_type = definition_map[key]["input_type"]
        normalized_updates[key] = _normalize_value(key, value, input_type)

    if not normalized_updates:
        raise HTTPException(status_code=400, detail="沒有可更新的 .env 參數")

    for key, value in normalized_updates.items():
        set_key(str(ENV_FILE_PATH), key, value, quote_mode="auto")

    _apply_runtime_settings(normalized_updates)
    all_values = _load_env_values()

    return EnvSettingsUpdateResponse(
        updated_keys=list(normalized_updates.keys()),
        items=_build_items(all_values),
    )
