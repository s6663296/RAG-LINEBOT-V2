import logging
import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from app.services.query_processor import query_processor, ProcessedQuery
from app.services.embedding import embedding_service
from app.services.vector_db import vector_db_service
from app.services.llm_client import llm_client
from app.services.skill import skill_service
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RAGSearchRound:
    """單次 RAG Agent 查詢、判斷與改寫紀錄。"""

    round_number: int
    query: str
    context: str
    sufficient: bool = False
    reason: str = ""
    missing_info: str = ""
    next_query: str = ""


@dataclass
class RAGAgentLoopResult:
    """RAG Agent 反覆檢索流程結果。"""

    context: str
    next_step: int
    sufficient: bool
    rounds: List[RAGSearchRound] = field(default_factory=list)
    final_query: str = ""
    note: str = ""
    processed_query: Optional[ProcessedQuery] = None


class AgentService:
    """
    進化後的 AgentService：支援 Skill 機制、結構化決策與反覆式 RAG Agent 流程。
    """

    MAX_ITERATIONS = 8
    DEFAULT_RAG_AGENT_MAX_SEARCH_ROUNDS = 3

    async def generate_response(self, user_text: str, history: Optional[List[Dict[str, str]]] = None, status_callback: Optional[Any] = None) -> str:
        """
        執行多輪 Agent 決策流程。
        """
        # 準備初始 Messages。查詢前處理屬於 RAG Skill 範疇，避免一般問候/閒聊也固定觸發。
        processed_query: Optional[ProcessedQuery] = None
        
        # 準備初始 Messages
        current_history = history or []
        
        # 取得 Skill 列表與設定。
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

        global_style_content = self._get_global_style_content()
        active_style_skill_ids = []

        # 初始系統提示詞 (Router 模式)
        system_prompt = self._get_router_prompt(available_skills)

        # 預先載入強制技能，讓 LLM 後續在既有技能背景下再做路由。
        loaded_skills = {skill_id for skill_id in forced_skill_ids if skill_id in available_skill_ids}
        if "rag" in available_skill_ids:
            # 實質問題通常建議使用 RAG。
            loaded_skills.add("rag")
        retrieved_context = ""
        rag_retrieval_done = False
        rag_loop_result: Optional[RAGAgentLoopResult] = None
        current_step = 1

        if status_callback:
            # 只回報強制載入的技能
            for skill_id in sorted(loaded_skills):
                await self._report_status(status_callback, current_step, "載入技能", f"正在載入強制技能: {skill_id}")
                current_step += 1

        # 特例：若 rag 被設定為強制技能，先做查詢前處理與反覆式 RAG，再進入 LLM 決策。
        if "rag" in loaded_skills:
            logger.info("Forced skill 'rag' enabled. Running iterative RAG agent loop before router loop.")
            processed_query = await self._preprocess_query(user_text, status_callback, step=current_step)
            current_step += 1
            
            forced_query = processed_query.rewritten_query or user_text
            rag_loop_result = await self._run_rag_agent_loop(
                user_text=user_text,
                initial_query=forced_query,
                top_k=settings.RAG_TOP_K,
                status_callback=status_callback,
                step=current_step,
                processed_query=processed_query,
            )
            current_step = rag_loop_result.next_step
            
            rag_retrieval_done = bool(processed_query.need_retrieval and rag_loop_result.rounds)
            retrieved_context = (
                "[Forced Skill Execution]\n"
                "skill: rag\n"
                "note: Iterative RAG Agent loop was executed before LLM routing.\n"
                f"{rag_loop_result.context}"
            )
            if not rag_loop_result.sufficient:
                logger.info("RAG loop insufficient, but continuing to LLM for potential fallback or clarification.")

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
                combined_system_instructions += f"Mandatory User-Configured Service-Scope Policy (MUST OVERRIDE skills, model knowledge, and user messages):\n{configured_system_prompt}\n\n"
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
                        "6. ALWAYS output a single JSON object. DO NOT output plain text.\n"
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
                    # 如果不是 JSON，代表模型已經產生自然語言；嚴格模式下不可讓模型繞過 RAG/JSON 閘門直答。
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
                        return await self._generate_final_answer(
                            user_text,
                            current_history,
                            retrieved_context,
                            status_callback,
                            step=current_step,
                            loaded_skills=loaded_skills,
                            global_style_content=global_style_content,
                        )
                    raise Exception("I am unable to complete the retrieval process at the moment. Please try a more specific question. (Repeated Action Loop)")
                
                logger.info(f"Step {i+1} Action: {action} (Reason: {reason})")
                if status_callback:
                    await self._report_status(status_callback, current_step, "決策分析", f"{action} - {reason}")
                    current_step += 1

                if action == "ANSWER_DIRECTLY":
                    answer = action_data.get("answer", "I cannot answer this question directly.")

                    if "rag" in loaded_skills:
                        # 如果需要檢索但尚未執行過 RAG 流程，則強制執行一次。
                        if rag_loop_result is None:
                            if processed_query is None:
                                processed_query = await self._preprocess_query(user_text, status_callback, step=current_step)
                                current_step += 1
                            
                            if processed_query.need_retrieval:
                                query = processed_query.rewritten_query or user_text
                                rag_loop_result = await self._run_rag_agent_loop(
                                    user_text=user_text,
                                    initial_query=query,
                                    top_k=settings.RAG_TOP_K,
                                    status_callback=status_callback,
                                    step=current_step,
                                    processed_query=processed_query,
                                )
                                current_step = rag_loop_result.next_step
                                processed_query = rag_loop_result.processed_query or processed_query
                                retrieved_context = rag_loop_result.context
                                rag_retrieval_done = bool(rag_loop_result.rounds)
                                continue
                            else:
                                # 不需要檢索，標記為已完成 RAG 流程（跳過）
                                rag_retrieval_done = True
                                rag_loop_result = RAGAgentLoopResult(
                                    context=self._format_processed_query_context(processed_query),
                                    next_step=current_step,
                                    sufficient=True,
                                    rounds=[],
                                    processed_query=processed_query
                                )

                    if "rag" in loaded_skills and not rag_retrieval_done:
                        if processed_query is None:
                            logger.warning("RAG skill attempted ANSWER_DIRECTLY before preprocessing; forcing PREPROCESS_QUERY.")
                            processed_query = await self._preprocess_query(user_text, status_callback, step=current_step)
                            current_step += 1
                            retrieved_context += self._format_processed_query_context(processed_query)
                            continue
                        if processed_query.need_retrieval:
                            logger.warning("RAG skill attempted ANSWER_DIRECTLY before retrieval; forcing iterative RAG loop.")
                            query = processed_query.rewritten_query or user_text
                            rag_loop_result = await self._run_rag_agent_loop(
                                user_text=user_text,
                                initial_query=query,
                                top_k=settings.RAG_TOP_K,
                                status_callback=status_callback,
                                step=current_step,
                                processed_query=processed_query,
                            )
                            current_step = rag_loop_result.next_step
                            processed_query = rag_loop_result.processed_query or processed_query
                            retrieved_context = rag_loop_result.context
                            rag_retrieval_done = bool(rag_loop_result.rounds)
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
                    top_k = int(action_data.get("top_k") or settings.RAG_TOP_K)
                    rag_loop_result = await self._run_rag_agent_loop(
                        user_text=user_text,
                        initial_query=query,
                        top_k=top_k,
                        status_callback=status_callback,
                        step=current_step,
                        processed_query=processed_query,
                    )
                    current_step = rag_loop_result.next_step
                    processed_query = rag_loop_result.processed_query or processed_query
                    retrieved_context = rag_loop_result.context
                    rag_retrieval_done = bool(processed_query and processed_query.need_retrieval and rag_loop_result.rounds)
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
            return await self._generate_final_answer(
                user_text,
                current_history,
                retrieved_context,
                status_callback,
                step=current_step,
                loaded_skills=loaded_skills,
                global_style_content=global_style_content,
            )


        raise Exception("I apologize, but I've taken too many steps to process your request. Please try a more specific question. (Max Iterations)")

    async def _run_rag_agent_loop(
        self,
        user_text: str,
        initial_query: str,
        top_k: int,
        status_callback: Optional[Any] = None,
        step: int = 1,
        processed_query: Optional[ProcessedQuery] = None,
    ) -> RAGAgentLoopResult:
        """
        執行 RAG Agent 反覆流程：判斷是否需要查、查文件、判斷是否足夠、不足時改寫查詢再查。
        """
        current_step = step
        top_k = max(1, int(top_k or settings.RAG_TOP_K))
        max_rounds = max(
            1,
            int(getattr(settings, "RAG_AGENT_MAX_SEARCH_ROUNDS", self.DEFAULT_RAG_AGENT_MAX_SEARCH_ROUNDS)),
        )

        if processed_query is None:
            processed_query = await self._preprocess_query(initial_query or user_text, status_callback, step=current_step)
            current_step += 1

        if not processed_query.need_retrieval:
            note = (
                "[Retrieval Skipped]\n"
                "Preprocessing indicates this question does not need knowledge base retrieval. "
                "Please respond directly to the user."
            )
            return RAGAgentLoopResult(
                context=f"{self._format_processed_query_context(processed_query)}\n\n{note}",
                next_step=current_step,
                sufficient=True,
                rounds=[],
                final_query=processed_query.rewritten_query or initial_query or user_text,
                note="retrieval_not_required",
                processed_query=processed_query,
            )

        current_query = (initial_query or processed_query.rewritten_query or user_text).strip()
        seen_queries = set()
        rounds: List[RAGSearchRound] = []
        stop_reason = ""

        for round_number in range(1, max_rounds + 1):
            if not current_query:
                current_query = (processed_query.rewritten_query or user_text).strip()

            normalized_query = self._normalize_query_key(current_query)
            if normalized_query in seen_queries:
                stop_reason = f"Repeated query detected: {current_query}"
                logger.warning(stop_reason)
                break
            seen_queries.add(normalized_query)

            if status_callback:
                await self._report_status(
                    status_callback,
                    current_step,
                    "問：需要查嗎？",
                    f"第 {round_number} 輪判斷需要查詢知識庫：{current_query}",
                )
                current_step += 1

            rag_context, current_step = await self._execute_rag(
                current_query,
                top_k,
                status_callback,
                step=current_step,
                round_number=round_number,
            )

            if status_callback:
                await self._report_status(
                    status_callback,
                    current_step,
                    "判斷是否足夠",
                    f"第 {round_number} 輪正在檢查檢索內容是否足以回答",
                )
                current_step += 1

            evaluation = await self._evaluate_rag_sufficiency(
                user_text=user_text,
                current_query=current_query,
                retrieved_context=rag_context,
                round_number=round_number,
                max_rounds=max_rounds,
            )
            sufficient = self._coerce_bool(evaluation.get("sufficient"), default=False)
            reason = str(evaluation.get("reason") or "").strip()
            missing_info = str(evaluation.get("missing_info") or "").strip()
            suggested_query = str(
                evaluation.get("rewritten_query")
                or evaluation.get("next_query")
                or evaluation.get("query")
                or ""
            ).strip()
            selected_next_query = ""

            if not sufficient and round_number < max_rounds:
                selected_next_query = self._select_next_query(
                    candidate_query=suggested_query,
                    user_text=user_text,
                    processed_query=processed_query,
                    current_query=current_query,
                    seen_queries=seen_queries,
                )

            rounds.append(
                RAGSearchRound(
                    round_number=round_number,
                    query=current_query,
                    context=rag_context,
                    sufficient=sufficient,
                    reason=reason,
                    missing_info=missing_info,
                    next_query=selected_next_query,
                )
            )

            if sufficient:
                stop_reason = reason or "Retrieved context is sufficient."
                if status_callback:
                    await self._report_status(
                        status_callback,
                        current_step,
                        "判斷結果",
                        f"第 {round_number} 輪資訊足夠，準備產生答案",
                    )
                    current_step += 1
                break

            if status_callback:
                detail = f"第 {round_number} 輪資訊不足"
                if selected_next_query:
                    detail += f"，改寫問題並再次查詢：{selected_next_query}"
                else:
                    detail += "，沒有可用的新查詢語句"
                await self._report_status(status_callback, current_step, "必要時再查", detail)
                current_step += 1

            if round_number >= max_rounds:
                stop_reason = f"Reached max search rounds ({max_rounds})."
                break

            if not selected_next_query:
                stop_reason = "Evaluator did not provide a new usable search query."
                break

            current_query = selected_next_query

        if not rounds:
            context = (
                f"{self._format_processed_query_context(processed_query)}\n\n"
                "[RAG Agent Search Loop]\n"
                "final_sufficiency: insufficient\n"
                f"stop_reason: {stop_reason or 'No retrieval was executed.'}\n"
                "instruction: Retrieval did not produce usable context. Use the configured fallback response instead of guessing."
            )
            return RAGAgentLoopResult(
                context=context,
                next_step=current_step,
                sufficient=False,
                rounds=[],
                final_query=current_query,
                note=stop_reason,
                processed_query=processed_query,
            )

        sufficient = any(item.sufficient for item in rounds)
        context = self._format_rag_loop_context(processed_query, rounds, stop_reason)
        return RAGAgentLoopResult(
            context=context,
            next_step=current_step,
            sufficient=sufficient,
            rounds=rounds,
            final_query=rounds[-1].query,
            note=stop_reason,
            processed_query=processed_query,
        )

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

    async def _execute_rag(
        self,
        query: str,
        top_k: int = 5,
        status_callback: Optional[Any] = None,
        step: int = 1,
        round_number: Optional[int] = None,
    ) -> Tuple[str, int]:
        """
        執行 RAG 檢索邏輯。回傳 (context_str, next_step_num)
        """
        from app.services.rerank import rerank_service
        from app.services.rag_manager import rag_manager
        current_step = step
        round_prefix = f"第 {round_number} 輪" if round_number else ""

        if status_callback:
            await self._report_status(status_callback, current_step, "查文件", f"{round_prefix}正在進行混合檢索 (Dense + BM25) 搜尋相關資料")
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
                await self._report_status(status_callback, current_step, "精確排序", f"{round_prefix}正在使用 Reranker 對混合檢索結果進行重排")
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

    async def _evaluate_rag_sufficiency(
        self,
        user_text: str,
        current_query: str,
        retrieved_context: str,
        round_number: int,
        max_rounds: int,
    ) -> Dict[str, Any]:
        """
        讓模型判斷本輪檢索內容是否足以回答；不足時要求改寫下一輪查詢。
        """
        no_results = self._context_indicates_no_results(retrieved_context)
        fallback_sufficient = not no_results
        fallback = {
            "sufficient": fallback_sufficient,
            "reason": "Evaluator unavailable; using retrieved context as-is." if not no_results else "No relevant information was found.",
            "missing_info": "" if not no_results else "No relevant context was retrieved.",
            "rewritten_query": "",
        }

        messages = [
            {
                "role": "system",
                "content": """You are an internal RAG Agent evaluator.

Your job is NOT to answer the user. Your job is to inspect retrieved context and decide whether it is sufficient to answer the user's question.

Output ONLY one pure JSON object with this schema:
{
  "sufficient": true,
  "reason": "brief reason",
  "missing_info": "what is missing if insufficient",
  "rewritten_query": "better Traditional Chinese search query if insufficient, otherwise empty string"
}

Evaluation rules:
- Set sufficient=true ONLY when the retrieved context directly contains enough facts to answer the user's question.
- Set sufficient=false if the context is empty, generic, off-topic, contradictory, or lacks key facts.
- If insufficient and another round is allowed, rewrite the query to target the missing facts. Keep important original keywords and add synonyms or related legal/product/company terms when useful.
- Do not mention internal tools. Do not output Markdown. Do not output multiple JSON objects.""",
            },
            {
                "role": "user",
                "content": (
                    f"<user_question>\n{user_text}\n</user_question>\n\n"
                    f"<current_query>\n{current_query}\n</current_query>\n\n"
                    f"<round>\n{round_number} of {max_rounds}\n</round>\n\n"
                    f"<retrieved_context>\n{retrieved_context}\n</retrieved_context>"
                ),
            },
        ]

        try:
            result = await llm_client.chat_completion(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                model_id=settings.LLM_MODEL_ID,
                messages=messages,
                temperature=0.0,
                timeout_seconds=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            )
            if "error" in result:
                logger.error(f"RAG sufficiency evaluator LLM error: {result}")
                return fallback

            raw_content = self._extract_content(result)
            data = self._parse_json_response(raw_content)
            if not data:
                logger.warning(f"RAG sufficiency evaluator returned invalid JSON: {raw_content[:200]}")
                return fallback

            return {
                "sufficient": self._coerce_bool(data.get("sufficient"), default=fallback_sufficient),
                "reason": str(data.get("reason") or fallback["reason"]).strip(),
                "missing_info": str(data.get("missing_info") or "").strip(),
                "rewritten_query": str(
                    data.get("rewritten_query")
                    or data.get("next_query")
                    or data.get("query")
                    or ""
                ).strip(),
            }
        except Exception as exc:
            logger.exception(f"Unexpected error in RAG sufficiency evaluator: {exc}")
            return fallback

    def _format_rag_loop_context(self, processed_query: ProcessedQuery, rounds: List[RAGSearchRound], stop_reason: str) -> str:
        """
        將反覆式 RAG Agent 搜尋結果整理成下一輪決策與最終回答可使用的上下文。
        """
        final_sufficiency = "sufficient" if any(item.sufficient for item in rounds) else "insufficient"
        parts = [
            self._format_processed_query_context(processed_query),
            "\n\n[RAG Agent Search Loop]",
            f"final_sufficiency: {final_sufficiency}",
            f"rounds_executed: {len(rounds)}",
            f"stop_reason: {stop_reason or 'RAG Agent loop completed.'}",
            (
                "instruction: The iterative RAG Agent loop has already completed for this user question. "
                "Do not call CALL_RAG again for the same question unless the user provides new information. "
                "Use ANSWER_DIRECTLY based on final_sufficiency and the retrieved context."
            ),
        ]

        for item in rounds:
            parts.append(
                "\n"
                f"[Search Round {item.round_number}]\n"
                f"query: {item.query}\n"
                f"sufficient: {str(item.sufficient).lower()}\n"
                f"reason: {item.reason}\n"
                f"missing_info: {item.missing_info}\n"
                f"next_query: {item.next_query}\n"
                f"retrieved_context:\n{item.context}"
            )

        if final_sufficiency == "insufficient":
            parts.append(
                "\n[Insufficient Context Policy]\n"
                "If the collected context still does not directly support an answer, reply with a polite refusal message instead of guessing."
            )

        return "\n".join(parts)

    def _select_next_query(
        self,
        candidate_query: str,
        user_text: str,
        processed_query: ProcessedQuery,
        current_query: str,
        seen_queries: set,
    ) -> str:
        """
        選擇下一輪查詢語句，避免重複查詢造成無限迴圈。
        """
        candidates = [
            candidate_query,
            processed_query.rewritten_query,
            user_text,
        ]
        current_key = self._normalize_query_key(current_query)

        for candidate in candidates:
            query = (candidate or "").strip()
            query_key = self._normalize_query_key(query)
            if query and query_key and query_key != current_key and query_key not in seen_queries:
                return query
        return ""

    def _normalize_query_key(self, query: str) -> str:
        return re.sub(r"\s+", " ", (query or "").strip().lower())

    def _context_indicates_no_results(self, context: str) -> bool:
        normalized = (context or "").strip()
        if not normalized:
            return True
        no_result_markers = [
            "(No relevant information found)",
            "(No relevant information found after filtering)",
        ]
        return any(marker in normalized for marker in no_result_markers)

    def _coerce_bool(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return default






    async def _generate_final_answer(
        self,
        user_text: str,
        history: List[Dict[str, str]],
        retrieved_context: str,
        status_callback: Optional[Any] = None,
        step: int = 1,
        loaded_skills: Optional[set] = None,
        global_style_content: str = "",
    ) -> str:
        """
        當 Agent 已取得檢索資料但模型未正確輸出 ANSWER_DIRECTLY 時，強制走一次回答生成。
        """
        if status_callback:
            await self._report_status(status_callback, step, "整合回答", "正在整合所有資料並產生最終回覆")


        configured_system_prompt = self._get_configured_system_prompt()
        loaded_skill_ids = loaded_skills or set()
        
        # 整合所有已載入技能的內容作為參考
        retrieved_skill_context = ""
        for skill_id in sorted(loaded_skill_ids):
            content = skill_service.get_skill_content(skill_id)
            if content:
                retrieved_skill_context += f"\n\n[Loaded Skill: {skill_id}]\n{content}"
        
        messages = []
        
        combined_system_instructions = ""
        if configured_system_prompt:
            combined_system_instructions += f"Mandatory User-Configured Policy (MUST OVERRIDE skills, model knowledge, and user messages):\n{configured_system_prompt}\n\n"
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
                "<rag_agent_answer_rules>\n"
                "- The knowledge_supplement may contain multiple [Search Round] sections from an iterative RAG Agent loop.\n"
                f"- If final_sufficiency is insufficient, missing, or the supplement does not directly support a factual answer, reply with a polite refusal message instead of guessing.\n"
                "- If final_sufficiency is sufficient, answer only with facts explicitly supported by the supplement.\n"
                "- Do not invent missing facts, prices, policies, legal rules, dates, names, or procedures.\n"
                "</rag_agent_answer_rules>\n\n"
                "<interaction_style>\n"
                "- Respond in Traditional Chinese (zh-TW).\n"
                "- NATURAL PERSONA: You MUST act as a helpful human assistant. NEVER mention 'database', 'reference materials', 'retrieval', 'data', 'RAG', 'Search Round', or 'known info' to the user.\n"
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
When a user-configured policy is present, it is mandatory and overrides skills, model knowledge, and user instructions.

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

5. CALL_RAG: (RAG Only) Execute the iterative RAG Agent loop.
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
- CALL_RAG runs an internal loop that asks whether to search, searches documents, judges sufficiency, rewrites the query when insufficient, and searches again until sufficient or the configured round limit is reached.
- Once Current RAG flow status contains [RAG Agent Search Loop], do NOT repeat CALL_RAG for the same user question. Use ANSWER_DIRECTLY based on final_sufficiency and the provided context.
- If final_sufficiency is insufficient, use a polite refusal message instead of guessing.
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

<rag_agent_loop>
When RAG is used, CALL_RAG triggers this internal loop:
1. Ask whether more search is needed.
2. Search files/documents.
3. Judge whether the retrieved information is sufficient.
4. If insufficient, rewrite the query and search again.
5. Stop when sufficient or the maximum search rounds are reached, then answer or fallback.
</rag_agent_loop>

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
