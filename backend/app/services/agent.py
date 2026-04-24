import logging
import json
import re
from typing import List, Dict, Any, Optional
from app.services.query_processor import query_processor, ProcessedQuery
from app.services.embedding import embedding_service
from app.services.vector_db import vector_db_service
from app.services.llm_client import llm_client
from app.services.skill import skill_service
from app.core.config import settings

logger = logging.getLogger(__name__)

class AgentService:
    """
    進化後的 AgentService：支援 Skill 機制與結構化決策。
    """

    MAX_ITERATIONS = 8

    async def generate_response(self, user_text: str, history: Optional[List[Dict[str, str]]] = None, status_callback: Optional[Any] = None) -> str:
        """
        執行多輪 Agent 決策流程。
        """
        # 準備初始 Messages。查詢前處理屬於 RAG Skill 範疇，避免一般問候/閒聊也固定觸發。
        processed_query: Optional[ProcessedQuery] = None
        
        # 準備初始 Messages
        current_history = history or []
        
        # 取得 Skill 列表與啟用設定。未開啟的 skill 不交給 LLM 路由。
        all_skills = skill_service.get_skill_list()
        skill_settings = skill_service.get_settings()
        enabled_skill_ids = set(skill_settings.get("enabled_skills", []))
        available_skills = [skill for skill in all_skills if skill["skill_id"] in enabled_skill_ids]
        available_skill_ids = {skill["skill_id"] for skill in available_skills}
        
        # 初始系統提示詞 (Router 模式)
        system_prompt = self._get_router_prompt(available_skills)
        
        # 執行迴圈：技能是否載入與執行由 LLM 透過 READ_SKILL 決定，不再預先強制載入。
        loaded_skills = set()
        retrieved_context = ""
        rag_retrieval_done = False
        
        max_iterations = max(3, int(getattr(settings, "AGENT_MAX_ITERATIONS", self.MAX_ITERATIONS)))
        last_action_signature = ""
        repeated_action_count = 0

        for i in range(max_iterations):
            logger.info(f"Agent iteration {i+1}...")
            
            # 組裝當前對話訊息
            messages = [{"role": "system", "content": system_prompt}]

            configured_system_prompt = self._get_configured_system_prompt()
            if configured_system_prompt:
                # 注入使用者設定的 System Prompt，但在此階段強化「必須優先工具」的指令
                messages.append({
                    "role": "system",
                    "content": (
                        "User-Defined Style & Personality:\n"
                        f"{configured_system_prompt}\n\n"
                        "--- IMPORTANT INSTRUCTIONS FOR AGENT PHASE ---\n"
                        "1. You are currently in the DECISION-MAKING phase. Your goal is to determine IF you need to use tools (like RAG).\n"
                        "2. If the user question requires information from the knowledge base, you MUST call READ_SKILL and then CALL_RAG.\n"
                        "3. DO NOT output the fallback message ('Sorry, I cannot answer...') as natural language now. "
                        "You can only use it later in the 'answer' field of ANSWER_DIRECTLY IF AND ONLY IF you have already performed retrieval and found no info.\n"
                        "4. ALWAYS output a single JSON object. DO NOT output plain text."
                    )
                })
            
            # 如果有載入的 skill，加入 system prompt 中間
            for skill_id in loaded_skills:
                skill_content = skill_service.get_skill_content(skill_id)
                if skill_content:
                    messages.append({
                        "role": "system", 
                        "content": f"You have loaded the skill [{skill_id}]:\n\n{skill_content}\n\nPlease follow the instructions of this skill."
                    })
            
            # 如果有 RAG 流程狀態或檢索結果，注入給下一輪決策使用。
            if retrieved_context:
                messages.append({
                    "role": "system",
                    "content": f"Current RAG flow status and reference data:\n\n{retrieved_context}"
                })

            # 加入對話歷史與當前請求
            messages.extend(current_history)
            messages.append({"role": "user", "content": user_text})

            # 呼叫 LLM
            try:
                result = await llm_client.chat_completion(
                    base_url=settings.LLM_BASE_URL,
                    api_key=settings.LLM_API_KEY,
                    model_id=settings.LLM_MODEL_ID,
                    messages=messages,
                    temperature=0.2,  # 決策時使用較低溫度
                    timeout_seconds=settings.LLM_REQUEST_TIMEOUT_SECONDS
                )

                if "error" in result:
                    logger.error(f"Agent LLM error: {result}")
                    return "系統忙碌中，請稍後再試。(LLM Error)"

                raw_content = self._extract_content(result)
                action_data = self._parse_json_response(raw_content)

                if not action_data:
                    # 如果不是 JSON，代表模型已經產生自然語言；避免把內部格式洩漏給使用者。
                    logger.warning(f"LLM did not return valid JSON: {raw_content[:100]}...")
                    return self._sanitize_final_answer(raw_content)

                action = action_data.get("action")
                reason = action_data.get("reason", "No reason provided")
                action_signature = json.dumps(action_data, ensure_ascii=False, sort_keys=True)
                if action_signature == last_action_signature:
                    repeated_action_count += 1
                else:
                    repeated_action_count = 0
                last_action_signature = action_signature
                if repeated_action_count >= 2:
                    logger.warning(f"Repeated action detected: {action_signature}")
                    if rag_retrieval_done and retrieved_context:
                        return await self._generate_final_answer(user_text, current_history, retrieved_context)
                    return "I am unable to complete the retrieval process at the moment. Please try a more specific question."
                
                logger.info(f"Step {i+1} Action: {action} (Reason: {reason})")

                if action == "ANSWER_DIRECTLY":
                    answer = action_data.get("answer", "I cannot answer this question directly.")
                    if "rag" in loaded_skills and not rag_retrieval_done:
                        if processed_query is None:
                            logger.warning("RAG skill attempted ANSWER_DIRECTLY before preprocessing; forcing PREPROCESS_QUERY.")
                            processed_query = await self._preprocess_query(user_text, status_callback)
                            retrieved_context += self._format_processed_query_context(processed_query)
                            continue
                        if processed_query.need_retrieval:
                            logger.warning("RAG skill attempted ANSWER_DIRECTLY before retrieval; forcing CALL_RAG.")
                            query = processed_query.rewritten_query or user_text
                            if status_callback:
                                await status_callback(f"正在檢索資料: {query}...")
                            retrieved_context = await self._execute_rag(query, settings.RAG_TOP_K)
                            rag_retrieval_done = True
                            continue
                    return self._sanitize_final_answer(answer)

                elif action == "READ_SKILL":
                    skill_id = action_data.get("skill_id")
                    if skill_id in available_skill_ids:
                        if skill_id not in loaded_skills:
                            loaded_skills.add(skill_id)
                            if status_callback:
                                await status_callback(f"正在載入技能: {skill_id}...")
                            # 進入下一輪，讓 LLM 根據新載入的內容做決定
                            continue
                        else:
                            # 已經載入過了，如果又叫一次，可能模型卡住了；提醒下一輪改用技能內 action。
                            logger.warning(f"Skill {skill_id} already loaded.")
                            retrieved_context += f"\n\n[Flow Reminder]\nSkill {skill_id} is already loaded. In the next step, do NOT repeat READ_SKILL. Please use actions allowed within the skill or ANSWER_DIRECTLY."
                            continue
                    else:
                        logger.error(f"Invalid or disabled skill_id: {skill_id}")

                elif action == "PREPROCESS_QUERY":
                    if "rag" not in loaded_skills:
                        logger.warning("PREPROCESS_QUERY requested before rag skill was loaded.")
                        if "rag" in available_skill_ids:
                            loaded_skills.add("rag")
                            if status_callback:
                                await status_callback("正在載入技能: rag...")
                            continue
                        return "RAG skill is currently not enabled, cannot perform query preprocessing."

                    query = action_data.get("query") or user_text
                    processed_query = await self._preprocess_query(query, status_callback)
                    retrieved_context += self._format_processed_query_context(processed_query)
                    continue

                elif action == "CALL_RAG":
                    if "rag" not in loaded_skills:
                        logger.warning("CALL_RAG requested before rag skill was loaded.")
                        if "rag" in available_skill_ids:
                            loaded_skills.add("rag")
                            if status_callback:
                                await status_callback("正在載入技能: rag...")
                            continue
                        return "RAG skill is currently not enabled, cannot perform retrieval."

                    query = action_data.get("query") or (processed_query.rewritten_query if processed_query else user_text)
                    if processed_query is None:
                        logger.warning("CALL_RAG requested before preprocessing; forcing PREPROCESS_QUERY first.")
                        processed_query = await self._preprocess_query(query, status_callback)
                        retrieved_context += self._format_processed_query_context(processed_query)
                        if not processed_query.need_retrieval:
                            retrieved_context += "\n\n[Retrieval Skipped]\nPreprocessing indicates this question does not need knowledge base retrieval. Please respond directly to the user."
                            continue
                        query = processed_query.rewritten_query or query

                    if not processed_query.need_retrieval:
                        logger.info("CALL_RAG skipped because processed query does not need retrieval.")
                        retrieved_context += "\n\n[Retrieval Skipped]\nPreprocessing indicates this question does not need knowledge base retrieval. Please respond directly to the user."
                        continue

                    top_k = int(action_data.get("top_k") or settings.RAG_TOP_K)
                    if status_callback:
                        await status_callback(f"正在檢索資料: {query}...")
                    
                    retrieved_context = await self._execute_rag(query, top_k)
                    rag_retrieval_done = True
                    continue

                elif action == "READ_SKILL_FILE":
                    skill_id = action_data.get("skill_id")
                    file_path = action_data.get("file")
                    if skill_id in available_skill_ids and file_path:
                        file_content = skill_service.get_skill_file_content(skill_id, file_path)
                        if file_content:
                            if status_callback:
                                await status_callback(f"正在讀取參考文件: {file_path}...")
                            # Add file content as system supplement
                            messages.append({"role": "system", "content": f"File {file_path} content:\n\n{file_content}"})
                            # 更新 system_prompt 或在此處直接 continue 讓 messages 重新組裝
                            # We'll just add it to retrieved_context here
                            retrieved_context += f"\n\n[File Content {file_path}]\n{file_content}"
                            continue
                    else:
                        logger.error(f"Invalid or disabled skill file access: skill_id={skill_id}, file={file_path}")

                elif action == "ASK_CLARIFICATION":
                    question = self._remove_proactive_followups(str(action_data.get("question", "")).strip())
                    return question or "Currently insufficient information to confirm."

                else:
                    # 未知 Action，嘗試直接回答或報錯
                    logger.warning(f"Unknown action: {action}")
                    if "answer" in action_data:
                        return action_data["answer"]
                    return "I am not sure what the next step is. Please try again later."

            except Exception as e:
                logger.exception(f"Error in agent iteration: {e}")
                return f"An error occurred during processing: {str(e)}"

        if rag_retrieval_done and retrieved_context:
            return await self._generate_final_answer(user_text, current_history, retrieved_context)

        if "rag" in loaded_skills and processed_query and processed_query.need_retrieval:
            query = processed_query.rewritten_query or user_text
            if status_callback:
                await status_callback(f"正在檢索資料: {query}...")
            retrieved_context = await self._execute_rag(query, settings.RAG_TOP_K)
            return await self._generate_final_answer(user_text, current_history, retrieved_context)

        return "I apologize, but I've taken too many steps to process your request. Please try a more specific question."

    async def _preprocess_query(self, query: str, status_callback: Optional[Any] = None) -> ProcessedQuery:
        """
        執行 RAG 查詢前處理並統一送出狀態訊息。
        """
        if status_callback:
            await status_callback("正在前處理查詢...")
        return await query_processor.process_query(query)

    def _format_processed_query_context(self, processed_query: ProcessedQuery) -> str:
        """
        將查詢前處理結果格式化為 Agent 下一輪可讀取的流程狀態。
        """
        return (
            "\n\n[Query Preprocessing Results]\n"
            f"intent: {processed_query.intent}\n"
            f"need_retrieval: {processed_query.need_retrieval}\n"
            f"rewritten_query: {processed_query.rewritten_query}"
        )

    async def _execute_rag(self, query: str, top_k: int = 5) -> str:
        """
        執行 RAG 檢索邏輯。
        """
        dense, sparse = await embedding_service.get_embeddings(query)
        candidate_limit = max(top_k, top_k * max(1, int(settings.RAG_CANDIDATE_MULTIPLIER)))
        raw_results = vector_db_service.search_hybrid(dense, sparse, limit=candidate_limit)
        
        # Rerank 與門檻過濾
        sorted_results = sorted(raw_results, key=lambda x: x.score, reverse=True)
        score_threshold = float(getattr(settings, "RAG_SCORE_THRESHOLD", 0.0))
        if score_threshold > 0:
            sorted_results = [res for res in sorted_results if getattr(res, "score", 0) >= score_threshold]
        top_results = sorted_results[:top_k]
        
        if not top_results:
            return "(No relevant information found)"
            
        context_parts = []
        for i, res in enumerate(top_results):
            payload = res.payload or {}
            text = payload.get("text", "")
            title = payload.get("title", "No Title")
            score = getattr(res, 'score', 0)
            context_parts.append(f"[Reference {i+1}] (Score: {score:.4f}) Title: {title}\nContent: {text}")
        
        return "\n\n".join(context_parts)

    async def _generate_final_answer(self, user_text: str, history: List[Dict[str, str]], retrieved_context: str) -> str:
        """
        當 Agent 已取得檢索資料但模型未正確輸出 ANSWER_DIRECTLY 時，強制走一次回答生成。
        """
        configured_system_prompt = self._get_configured_system_prompt()
        messages = []
        if configured_system_prompt:
            messages.append({
                "role": "system",
                "content": configured_system_prompt
            })
        messages.append({
            "role": "system",
            "content": f"Reference Materials:\n\n{retrieved_context}\n\nIMPORTANT: You must respond in Traditional Chinese (zh-TW)."
        })
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        result = await llm_client.chat_completion(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model_id=settings.LLM_MODEL_ID,
            messages=messages,
            temperature=settings.LLM_TEMPERATURE,
            timeout_seconds=settings.LLM_REQUEST_TIMEOUT_SECONDS
        )
        if "error" in result:
            logger.error(f"Final answer LLM error: {result}")
            return "系統忙碌中，請稍後再試。(Final Answer Error)"
        return self._sanitize_final_answer(self._extract_content(result))

    def _get_router_prompt(self, skills: List[Dict[str, Any]]) -> str:
        """
        Generates the initial decision-making prompt.
        """
        skills_json = json.dumps(skills, ensure_ascii=False, indent=2)
        return f"""You are the core decision engine for a backend Agent. You cannot directly answer questions that require external knowledge; you must first determine whether you need to read an enabled skill.

Your task is to decide the next action based on the user message. You MUST output in JSON format.

[Available Skills List]
The following list only contains skills that are enabled in skill management. "Enabled" only means they are available for selection, not that they must be executed. You decide which SKILL to read and execute based on the user question.
{skills_json}

[Allowed JSON Action Formats]

1. Load Skill (READ_SKILL):
{{
  "action": "READ_SKILL",
  "skill_id": "SkillID",
  "reason": "Why this skill was selected"
}}

2. Answer Directly (ANSWER_DIRECTLY):
{{
  "action": "ANSWER_DIRECTLY",
  "answer": "Content of the reply to the user (MUST be in Traditional Chinese)",
  "reason": "Why it can be answered directly (e.g., small talk, simple translation, summarizing known content)"
}}

3. Ask for Clarification (ASK_CLARIFICATION):
{{
  "action": "ASK_CLARIFICATION",
  "question": "Question to ask the user (MUST be in Traditional Chinese)",
  "reason": "Why more information is needed"
}}

[Rules]
- ONLY select skill_ids that exist and are enabled in the list.
- The LLM_SYSTEM_PROMPT from UI settings will be provided as a separate system message and MUST be incorporated into the final response rules. Adhere to any constraints therein (e.g., no follow-up questions).
- The execution of a skill is determined by you based on the user question; do not automatically read a skill just because it is enabled.
- For greetings, small talk, thanks, or questions that do not require external knowledge, use ANSWER_DIRECTLY immediately. RAG preprocessing is FORBIDDEN for these.
- RAG preprocessing and retrieval are part of the 'rag' skill flow. Do NOT output PREPROCESS_QUERY or CALL_RAG before the 'rag' skill is loaded.
- If the question involves the knowledge base, company data, internal policies, etc., you MUST prioritize READ_SKILL. After loading the 'rag' skill, use PREPROCESS_QUERY / CALL_RAG as per the skill's instructions.
- After loading the 'rag' skill, if the question requires external data, you are FORBIDDEN from answering "insufficient data" or "cannot confirm" before PREPROCESS_QUERY and CALL_RAG are completed.
- Once a skill is loaded, you can use the specific actions explicitly listed in that skill's file.
- ASK_CLARIFICATION should only be used when essential user information is missing and you cannot answer or retrieve data directly. Do NOT use it to proactively suggest extended services or ask "if they need anything else".
- Once RAG reference materials are obtained, you MUST use ANSWER_DIRECTLY to output the final answer. Do NOT output READ_SKILL again.
- After answering, do NOT proactively propose extended services, follow-up suggestions, or ask the user if they need more help, unless explicitly requested.
- STRICTLY PROHIBITED: Outputting any Markdown explanatory text. Output ONLY a single pure JSON object.
- DO NOT output multiple JSON objects at once; DO NOT repeat the same action.
- Ensure the JSON structure is complete and correct.
- LANGUAGE: All natural language responses to the user (in 'answer' or 'question' fields) MUST be in Traditional Chinese (zh-TW).
"""

    def _get_configured_system_prompt(self) -> str:
        """
        取得 UI / .env 設定的全域 system prompt，避免 Agent 流程忽略使用者設定。
        """
        return (getattr(settings, "LLM_SYSTEM_PROMPT", "") or "").strip()

    def _extract_content(self, result: Dict[str, Any]) -> str:
        choices = result.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return message.get("content", "").strip()

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """
        嘗試解析 LLM 的 JSON 回傳，處理 Markdown 包裝、前後解釋文字與多段 JSON。
        """
        cleaned = re.sub(r'```(?:json)?\s*|\s*```', '', text).strip()
        if not cleaned:
            return {}

        # 先嘗試整段解析。
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
            if isinstance(data, list) and data and isinstance(data[-1], dict):
                return data[-1]
        except json.JSONDecodeError:
            pass

        # 模型有時會連續輸出多個 JSON 物件；逐一解碼並取最後一個有效 action。
        decoder = json.JSONDecoder()
        candidates: List[Dict[str, Any]] = []
        index = 0
        while index < len(cleaned):
            brace_index = cleaned.find("{", index)
            if brace_index == -1:
                break
            try:
                data, end_index = decoder.raw_decode(cleaned[brace_index:])
                if isinstance(data, dict):
                    candidates.append(data)
                index = brace_index + max(end_index, 1)
            except json.JSONDecodeError:
                index = brace_index + 1

        if candidates:
            for candidate in reversed(candidates):
                if candidate.get("action"):
                    return candidate
            return candidates[-1]

        return {}

    def _remove_proactive_followups(self, text: str) -> str:
        """
        移除模型常見的主動延伸服務或反問句，讓回答結束後直接停止。
        """
        answer = (text or "").strip()
        if not answer:
            return answer

        followup_patterns = [
            r"(?:如果[你您]?(?:要|需要|想)|若[你您]?(?:需要|想)|如[你您]?(?:需要|想)|需要的話|如需)[^。！？!?]*(?:[。！？!?]|$)",
            r"(?:我也可以|我可以|也可以|可以再)(?:幫你|協助你|為你|替你)[^。！？!?]*(?:[。！？!?]|$)",
            r"(?:是否需要|要不要|需要我|需要[我]?再)[^。！？!?]*(?:[。！？!?]|$)",
        ]
        cleaned = answer
        for pattern in followup_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

        lines = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
        cleaned = "\n".join(lines).strip()
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\s+([。！？!?，,；;：:])", r"\1", cleaned)
        return cleaned.strip()

    def _sanitize_final_answer(self, text: str) -> str:
        """
        避免把內部工具 action JSON 直接顯示給使用者。
        """
        answer = (text or "").strip()
        action_data = self._parse_json_response(answer) if "{" in answer and "}" in answer else {}
        if action_data.get("action"):
            if action_data.get("answer"):
                return self._remove_proactive_followups(str(action_data["answer"]).strip()) or "Currently insufficient information to confirm."
            if action_data.get("question"):
                return self._remove_proactive_followups(str(action_data["question"]).strip()) or "Currently insufficient information to confirm."
            return "I cannot generate a reliable answer at the moment. Please try a more specific question."
        answer = self._remove_proactive_followups(answer)
        return answer or "Currently insufficient information to confirm."

agent_service = AgentService()
