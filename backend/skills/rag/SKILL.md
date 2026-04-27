---
name: rag
description: Use when user questions require searching knowledge base, documents, FAQs, product info, or internal data.
---

# RAG Skill

## Goal
Determine if retrieval is necessary based on the user's query and execute an iterative RAG Agent loop when required.

## Allowed Actions
- `PREPROCESS_QUERY`: Execute query preprocessing before RAG. Parameters: `query` (raw query or query to be processed), `reason` (reasoning). Returns `intent`, `need_retrieval`, `rewritten_query`.
- `CALL_RAG`: Execute the internal iterative RAG Agent loop. Parameters: `query` (use `rewritten_query` from `PREPROCESS_QUERY`), `top_k` (default 5), `reason` (reasoning). The backend will search, evaluate sufficiency, rewrite the query if needed, and search again until sufficient or the configured round limit is reached.
- `ANSWER_DIRECTLY`: Reply to the user when retrieval is not needed or after retrieval is completed.
- `READ_SKILL_FILE`: Read reference files in this skill directory.

## When To Use This Skill
- Use ONLY when queries involve knowledge base, documents, FAQs, product info, company policies, laws, regulations, contracts, or other external data.
- For legal or regulatory questions, even if specific laws are not mentioned, perform a broad search using user terms and synonyms first. Do not ask for clarification before searching.
- DO NOT use this skill for greetings, casual chat, robot status checks, programming questions, system commands, or out-of-domain common knowledge. Do not call `PREPROCESS_QUERY` for these.

## Preprocess Tool Usage
1. After loading RAG Skill, if retrieval is deemed necessary, output `PREPROCESS_QUERY`.
2. Keep `PREPROCESS_QUERY.query` close to the user's original intent; for legal queries, add relevant keywords like "clause", "article", "Civil Code", "Maritime Law", etc.
3. After tool returns:
   - If `need_retrieval` is `true`, call `CALL_RAG` using `rewritten_query`.
   - If `need_retrieval` is `false`, call `ANSWER_DIRECTLY` without calling `CALL_RAG`.
4. NEVER call `PREPROCESS_QUERY` for greetings or small talk. Preprocessing is part of the RAG flow, not a mandatory step for all messages.

## Procedure
1. Judge if external knowledge is required.
2. If NOT required, call `ANSWER_DIRECTLY` immediately.
3. If required, call `PREPROCESS_QUERY` to generate optimized search terms.
4. If preprocessing confirms retrieval, call `CALL_RAG` with `rewritten_query`.
5. Treat `CALL_RAG` as a full RAG Agent loop, not a single linear search:
   - ask whether more search is needed;
   - search files/documents;
   - judge whether retrieved context is sufficient;
   - if insufficient, rewrite the query and search again;
   - stop when sufficient or when the configured maximum search rounds is reached.
6. After `CALL_RAG` returns a `[RAG Agent Search Loop]` status, DO NOT call `CALL_RAG` again for the same user question unless the user provides new information.
7. Organize the response based on the retrieved content.
- **NATURAL PERSONA**: DO NOT explicitly mention the retrieval process, "database", "retrieved info", "reference materials", or "data" (e.g., avoid "根據檢索結果", "我的資料庫顯示", "根據資料", "已知資訊"). Provide the information naturally as your own knowledge.
8. Call `ANSWER_DIRECTLY` to provide the final response to the user.

## Output Rules
- Responses MUST be based on the retrieved context.
- If context is insufficient, respond: "抱歉，我目前不太確定這方面的細細節，建議您可以聯絡真人客服為您提供進一步協助。"
- DO NOT hallucinate or pretend to find non-existent data.
- Output ONLY one single JSON object per turn.
- After obtaining context, MUST use `ANSWER_DIRECTLY` for the final natural language response.
- All actions MUST be pure JSON without Markdown or extra text.
- Language: All natural language responses to the user MUST be in Traditional Chinese (zh-TW).
