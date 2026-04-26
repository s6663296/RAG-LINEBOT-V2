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
        
        # 取得 Skill 列表與設定。
        all_skills = skill_service.get_skill_list()
        skill_settings = skill_service.get_settings()
        enabled_skill_ids = set(skill_settings.get("enabled_skills", []))
        forced_skill_ids = [
            skill_id
            for skill_id in skill_settings.get("forced_skills", [])
            if skill_id in enabled_skill_ids
        ]

        available_skills = [
            skill
            for skill in all_skills
            if skill["skill_id"] in enabled_skill_ids
        ]
        available_skill_ids = {skill["skill_id"] for skill in available_skills}

        global_style_content = ""
        active_style_skill_ids = []

        # 初始系統提示詞 (Router 模式)
        system_prompt = self._get_router_prompt(available_skills)

        # 預先載入強制技能，讓 LLM 後續在既有技能背景下再做路由。
        loaded_skills = {skill_id for skill_id in forced_skill_ids if skill_id in available_skill_ids}
        retrieved_context = ""
        rag_retrieval_done = False
        current_step = 1

        if status_callback:
            # 只回報強制載入的技能
            for skill_id in sorted(loaded_skills):
                await self._report_status(status_callback, current_step, "載入技能", f"正在載入強制技能: {skill_id}")
                current_step += 1

        # 特例：若 rag 被設定為強制技能，先做查詢前處理與檢索，再進入 LLM 決策。
        if "rag" in loaded_skills:
            logger.info("Forced skill 'rag' enabled. Running retrieval before router loop.")
            processed_query = await self._preprocess_query(user_text, status_callback, step=current_step)
            current_step += 1
            
            forced_query = processed_query.rewritten_query or user_text
            rag_context, next_step = await self._execute_rag(forced_query, settings.RAG_TOP_K, status_callback, step=current_step)
            current_step = next_step
            
            rag_retrieval_done = True
            retrieved_context = (
                "[Forced Skill Execution]\n"
                "skill: rag\n"
                "note: RAG retrieval was executed before LLM routing.\n"
                f"{self._format_processed_query_context(processed_query)}\n\n"
                f"{rag_context}"
            )

        max_iterations = max(3, int(getattr(settings, "AGENT_MAX_ITERATIONS", self.MAX_ITERATIONS)))
        last_action_signature = ""
        repeated_action_count = 0

        for i in range(max_iterations):
            logger.info(f"Agent iteration {i+1}...")
            
            # 組裝當前對話訊息
            messages = [{"role": "system", "content": system_prompt}]

            configured_system_prompt = self._get_configured_system_prompt()
            
            combined_system_instructions = ""
            if configured_system_prompt:
                combined_system_instructions += f"User-Defined Style & Personality:\n{configured_system_prompt}\n\n"
            if global_style_content:
                combined_system_instructions += f"Global Reply Rules (MUST STRICTLY FOLLOW):\n{global_style_content}\n\n"
                
            if combined_system_instructions:
                # 注入使用者設定與全域風格，並在此階段強化「必須優先工具」的指令
                messages.append({
                    "role": "system",
                    "content": (
                        f"{combined_system_instructions}"
                        "<instruction_agent_phase>\n"
                        "1. You are currently in the DECISION-MAKING phase. Your goal is to determine IF you need to use tools (like RAG).\n"
                        "2. If a knowledge-base question appears and 'rag' is not loaded yet, call READ_SKILL('rag') first; if 'rag' is already loaded, directly use PREPROCESS_QUERY/CALL_RAG as needed.\n"
                        "3. DO NOT output the fallback message ('Sorry, I cannot answer...') as natural language now. "
                        "You can only use it later in the 'answer' field of ANSWER_DIRECTLY IF AND ONLY IF you have already performed retrieval and found no info.\n"
                        "4. ALWAYS output a single JSON object. DO NOT output plain text.\n"
                        "</instruction_agent_phase>"
                    )
                })
            
            if loaded_skills:
                messages.append({
                    "role": "system",
                    "content": (
                        "Forced/Loaded skills already active before this decision step: "
                        f"{', '.join(sorted(loaded_skills))}. "
                        "Treat them as active context. Do NOT repeatedly call READ_SKILL for the same skill."
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
                    error_detail = result.get("error")
                    logger.error(f"Agent LLM error: {error_detail}")
                    raise Exception(f"系統忙碌中，請稍後再試。(LLM Error: {error_detail})")

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
                        return await self._generate_final_answer(user_text, current_history, retrieved_context, status_callback)
                    raise Exception("I am unable to complete the retrieval process at the moment. Please try a more specific question. (Repeated Action Loop)")
                
                logger.info(f"Step {i+1} Action: {action} (Reason: {reason})")
                if status_callback:
                    await self._report_status(status_callback, current_step, "決策分析", f"{action} - {reason}")
                    current_step += 1

                if action == "ANSWER_DIRECTLY":
                    answer = action_data.get("answer", "I cannot answer this question directly.")

                    if "rag" in loaded_skills and not rag_retrieval_done:
                        if processed_query is None:
                            logger.warning("RAG skill attempted ANSWER_DIRECTLY before preprocessing; forcing PREPROCESS_QUERY.")
                            processed_query = await self._preprocess_query(user_text, status_callback, step=current_step)
                            current_step += 1
                            retrieved_context += self._format_processed_query_context(processed_query)
                            continue
                        if processed_query.need_retrieval:
                            logger.warning("RAG skill attempted ANSWER_DIRECTLY before retrieval; forcing CALL_RAG.")
                            query = processed_query.rewritten_query or user_text
                            rag_context, next_step = await self._execute_rag(query, settings.RAG_TOP_K, status_callback, step=current_step)
                            current_step = next_step
                            retrieved_context = rag_context
                            rag_retrieval_done = True
                            continue
                    return self._sanitize_final_answer(answer)

                elif action == "READ_SKILL":
                    skill_id = action_data.get("skill_id")
                    if skill_id in available_skill_ids:
                        if skill_id not in loaded_skills:
                            loaded_skills.add(skill_id)
                            if status_callback:
                                await self._report_status(status_callback, current_step, "載入技能", f"正在載入技能: {skill_id}")
                                current_step += 1
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
                                await self._report_status(status_callback, current_step, "載入技能", "正在載入技能: rag")
                                current_step += 1
                            continue
                        raise Exception("RAG skill is currently not enabled, cannot perform query preprocessing.")

                    query = action_data.get("query") or user_text
                    processed_query = await self._preprocess_query(query, status_callback, step=current_step)
                    current_step += 1
                    retrieved_context += self._format_processed_query_context(processed_query)
                    continue

                elif action == "CALL_RAG":
                    if "rag" not in loaded_skills:
                        logger.warning("CALL_RAG requested before rag skill was loaded.")
                        if "rag" in available_skill_ids:
                            loaded_skills.add("rag")
                            if status_callback:
                                await self._report_status(status_callback, current_step, "載入技能", "正在載入技能: rag")
                                current_step += 1
                            continue
                        raise Exception("RAG skill is currently not enabled, cannot perform retrieval.")

                    query = action_data.get("query") or (processed_query.rewritten_query if processed_query else user_text)
                    if processed_query is None:
                        logger.warning("CALL_RAG requested before preprocessing; forcing PREPROCESS_QUERY first.")
                        processed_query = await self._preprocess_query(query, status_callback, step=current_step)
                        current_step += 1
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
                    rag_context, next_step = await self._execute_rag(query, top_k, status_callback, step=current_step)
                    current_step = next_step
                    retrieved_context = rag_context
                    rag_retrieval_done = True
                    continue

                elif action == "READ_SKILL_FILE":
                    skill_id = action_data.get("skill_id")
                    file_path = action_data.get("file")
                    if skill_id in available_skill_ids and file_path:
                        file_content = skill_service.get_skill_file_content(skill_id, file_path)
                        if file_content:
                            if status_callback:
                                await self._report_status(status_callback, current_step, "讀取文件", f"正在讀取參考文件: {file_path}")
                                current_step += 1
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
                    raise Exception("I am not sure what the next step is. Please try again later. (Unknown Action)")

            except Exception as e:
                logger.exception(f"Error in agent iteration: {e}")
                raise e

        if rag_retrieval_done and retrieved_context:
            return await self._generate_final_answer(user_text, current_history, retrieved_context, status_callback, step=current_step)

        if "rag" in loaded_skills and processed_query and processed_query.need_retrieval:
            query = processed_query.rewritten_query or user_text
            rag_context, next_step = await self._execute_rag(query, settings.RAG_TOP_K, status_callback, step=current_step)
            current_step = next_step
            return await self._generate_final_answer(user_text, current_history, rag_context, status_callback, step=current_step)

        raise Exception("I apologize, but I've taken too many steps to process your request. Please try a more specific question. (Max Iterations)")

    async def _preprocess_query(self, query: str, status_callback: Optional[Any] = None, step: int = 1) -> ProcessedQuery:
        """
        執行 RAG 查詢前處理並統一送出狀態訊息。
        """
        if status_callback:
            await self._report_status(status_callback, step, "先前處理", "正在分析問題意圖與優化查詢語句")
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

    async def _execute_rag(self, query: str, top_k: int = 5, status_callback: Optional[Any] = None, step: int = 1) -> (str, int):
        """
        執行 RAG 檢索邏輯。回傳 (context_str, next_step_num)
        """
        from app.services.rerank import rerank_service
        from app.services.rag_manager import rag_manager
        current_step = step

        if status_callback:
            await self._report_status(status_callback, current_step, "知識檢索", f"正在進行混合檢索 (Dense + BM25) 搜尋相關資料")
            current_step += 1

        # 第一階段：取回較多候選者 (Candidates)
        candidate_multiplier = int(getattr(settings, "RAG_CANDIDATE_MULTIPLIER", 4))
        candidate_limit = top_k * candidate_multiplier
        
        # 使用 rag_manager.search 進行混合檢索 (已包含 RRF 融合)
        hybrid_results = await rag_manager.search(query, limit=candidate_limit)
        
        if not hybrid_results:
            return "(No relevant information found)", current_step

        # 第二階段：Rerank
        if settings.RAG_ENABLE_RERANK and len(hybrid_results) > 1:
            if status_callback:
                await self._report_status(status_callback, current_step, "精確排序", "正在使用 Reranker 對混合檢索結果進行重排")
                current_step += 1
            
            documents = [res.get("payload", {}).get("text", "") for res in hybrid_results]
            rerank_results = await rerank_service.rerank(query, documents, top_n=top_k)
            
            if rerank_results:
                top_results = []
                for item in rerank_results:
                    idx = item["index"]
                    score = item["relevance_score"]
                    if idx < len(hybrid_results):
                        res = hybrid_results[idx]
                        res["score"] = score  # 更新為 Reranker 的分數
                        top_results.append(res)
            else:
                top_results = hybrid_results[:top_k]
        else:
            top_results = hybrid_results[:top_k]
        
        # 門檻過濾
        score_threshold = float(getattr(settings, "RAG_SCORE_THRESHOLD", 0.0))
        if score_threshold > 0:
            top_results = [res for res in top_results if res.get("score", 0) >= score_threshold]
            
        if not top_results:
            return "(No relevant information found after filtering)", current_step
            
        context_parts = []
        for i, res in enumerate(top_results):
            payload = res.get("payload", {})
            text = payload.get("text", "")
            title = payload.get("title", "No Title")
            score = res.get("score", 0)
            context_parts.append(f"[Reference {i+1}] (Score: {score:.4f}) Title: {title}\nContent: {text}")
        
        return "\n\n".join(context_parts), current_step

    async def _generate_final_answer(self, user_text: str, history: List[Dict[str, str]], retrieved_context: str, status_callback: Optional[Any] = None, step: int = 1) -> str:
        """
        當 Agent 已取得檢索資料但模型未正確輸出 ANSWER_DIRECTLY 時，強制走一次回答生成。
        """
        if status_callback:
            await self._report_status(status_callback, step, "整合回答", "正在整合所有資料並產生最終回覆")
            current_step += 1

        configured_system_prompt = self._get_configured_system_prompt()
        
        # 整合所有已載入技能的內容作為參考
        retrieved_skill_context = ""
        for skill_id in sorted(loaded_skills):
            content = skill_service.get_skill_content(skill_id)
            if content:
                retrieved_skill_context += f"\n\n[Loaded Skill: {skill_id}]\n{content}"
        
        messages = []
        
        combined_system_instructions = ""
        if configured_system_prompt:
            combined_system_instructions += f"User-Defined Style & Personality:\n{configured_system_prompt}\n\n"
        if global_style_content:
            combined_system_instructions += f"Global Reply Rules (MUST STRICTLY FOLLOW):\n{global_style_content}\n\n"
            
        if combined_system_instructions:
            messages.append({
                "role": "system",
                "content": combined_system_instructions.strip()
            })
            
        messages.append({
            "role": "system",
            "content": (
                f"<knowledge_supplement>\n{retrieved_context}\n{retrieved_skill_context}\n</knowledge_supplement>\n\n"
                "<interaction_style>\n"
                "- Respond in Traditional Chinese (zh-TW).\n"
                "- NATURAL PERSONA: You MUST act as a helpful human assistant. NEVER mention 'database', 'reference materials', 'retrieval', 'data', or 'known info' to the user.\n"
                "- Do NOT use phrases like 'According to the data' or 'The information shows'.\n"
                "- Integrate the knowledge naturally into your own response as if you've always known it.\n"
                "</interaction_style>"
            )
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
            error_detail = result.get("error")
            logger.error(f"Final answer LLM error: {error_detail}")
            raise Exception(f"系統忙碌中，請稍後再試。(Final Answer Error: {error_detail})")
        return self._sanitize_final_answer(self._extract_content(result))

    def _get_router_prompt(self, skills: List[Dict[str, Any]]) -> str:
        """
        Generates the initial decision-making prompt.
        """
        skills_json = json.dumps(skills, ensure_ascii=False, indent=2)
        return f"""You are a specialized AI customer service agent running in a RAG-enabled backend.

You help the user by reasoning carefully. You must evaluate the user's intent and read relevant skills to provide the best response.

<interaction_style>
- Respond in Traditional Chinese (zh-TW).
- NATURAL PERSONA: You MUST act as a helpful human assistant. NEVER mention "database", "reference materials", "retrieval", "data", or "known info" to the user.
- Do NOT use phrases like "根據檢索結果" (According to retrieval results) or "資料顯示" (Data shows). Integrate knowledge naturally.
- Be concise but complete.
- Ask clarifying questions ONLY when essential information is missing. Do NOT proactively suggest extended services or ask "if they need anything else".
- Explain important decisions briefly in the "reason" field.
- Do not claim you have completed an action unless you actually did it.
</interaction_style>

<tool_use>
You MUST output a single pure JSON object. DO NOT output Markdown explanatory text.

Available Actions:
1. READ_SKILL: Load the full content of a skill.
{{
  "action": "READ_SKILL",
  "skill_id": "SkillID",
  "reason": "Why this skill was selected"
}}

2. ANSWER_DIRECTLY: Provide the final answer to the user.
{{
  "action": "ANSWER_DIRECTLY",
  "answer": "Content of the reply to the user (zh-TW)",
  "reason": "Why it can be answered directly"
}}

3. ASK_CLARIFICATION: Ask the user for more information.
{{
  "action": "ASK_CLARIFICATION",
  "question": "Question to ask the user (zh-TW)",
  "reason": "Why more information is needed"
}}

4. PREPROCESS_QUERY: (RAG Only) Analyze and rewrite the query.
{{
  "action": "PREPROCESS_QUERY",
  "query": "The rewritten query",
  "reason": "Why preprocessing is needed"
}}

5. CALL_RAG: (RAG Only) Execute knowledge retrieval.
{{
  "action": "CALL_RAG",
  "query": "The search query",
  "top_k": 5,
  "reason": "Why retrieval is needed"
}}

6. READ_SKILL_FILE: Read a specific file within a skill directory.
{{
  "action": "READ_SKILL_FILE",
  "skill_id": "SkillID",
  "file": "relative/path/to/file",
  "reason": "Why this file is needed"
}}

Rules:
- Think about what information or action is needed.
- Use the most specific available action.
- Check results and continue until the task is completed or blocked.
- Once RAG materials are obtained, use ANSWER_DIRECTLY.
- DO NOT output multiple JSON objects at once.
</tool_use>

<skills>
Skills are task-specific instruction bundles.
- Do not assume full skill instructions from descriptions alone.
- When a request matches a skill, use READ_SKILL.
- Once a skill is loaded, follow its instructions exactly.
</skills>

<available_skills>
{skills_json}
</available_skills>

<skill_loading_protocol>
1. Compare request with skill descriptions.
2. If match, READ_SKILL.
3. If already loaded, do NOT repeat READ_SKILL; use skill-specific actions or ANSWER_DIRECTLY.
</skill_loading_protocol>

<safety>
- Do not help bypass security or hide malicious activity.
- Never expose internal secrets, API keys, or private credentials.
- Do not mention internal processes like "retrieval" or "preprocessing" to the user.
</safety>
"""

    def _get_configured_system_prompt(self) -> str:
        """
        取得 UI / .env 設定的全域 system prompt，避免 Agent 流程忽略使用者設定。
        """
        return (getattr(settings, "LLM_SYSTEM_PROMPT", "") or "").strip()

    def _get_global_style_content(self) -> str:
        """
        [DEPRECATED]
        """
        return ""

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

    async def _report_status(self, status_callback: Optional[Any], step: int, action: str, detail: str):
        """
        統一流程顯示格式：【步驟 X】動作名稱 - 詳細描述
        """
        if status_callback:
            await status_callback(f"【步驟 {step}】{action} - {detail}")

agent_service = AgentService()
