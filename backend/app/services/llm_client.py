import httpx
import json
from typing import List, Dict, Any, Optional

class LLMClient:
    """
    模組化的 LLM 客戶端，支援自定義 URL、API Key 與模型 ID。
    符合 OpenAI API 格式規範。
    """
    
    async def chat_completion(
        self, 
        base_url: str, 
        api_key: str, 
        model_id: str, 
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        timeout_seconds: float = 120.0
    ) -> Dict[str, Any]:
        
        # 確保 URL 格式正確
        if not base_url.endswith("/chat/completions"):
            base_url = base_url.rstrip("/") + "/chat/completions"
            
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature
        }
        
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            try:
                response = await client.post(
                    base_url,
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as e:
                return {"error": "Timeout Error", "detail": str(e)}
            except httpx.HTTPStatusError as e:
                return {"error": f"HTTP Error: {e.response.status_code}", "detail": e.response.text}
            except Exception as e:
                return {"error": "Connection Error", "detail": str(e)}

llm_client = LLMClient()
