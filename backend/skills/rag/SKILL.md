---
name: rag
description: Use when user questions require searching knowledge base, documents, FAQs, product info, or internal data.
---

# RAG Skill

## Goal
Validate service-scope questions against indexed service information and execute an iterative RAG Agent loop when required. In strict service-scope mode, never answer substantive questions from model common knowledge.

## Allowed Actions
- `PREPROCESS_QUERY`: Execute query preprocessing before RAG. Parameters: `query` (raw query or query to be processed), `reason` (reasoning). Returns `intent`, `need_retrieval`, `rewritten_query`.
- `CALL_RAG`: Execute the internal iterative RAG Agent loop. Parameters: `query` (use `rewritten_query` from `PREPROCESS_QUERY`), `top_k` (default 5), `reason` (reasoning). The backend will search, evaluate sufficiency, rewrite the query if needed, and search again until sufficient or the configured round limit is reached.
- `ANSWER_DIRECTLY`: Reply to the user only for pure greetings, fixed fallback, or after retrieval is completed with sufficient context.
- `READ_SKILL_FILE`: Read reference files in this skill directory.

## When To Use This Skill
- Use ONLY when queries involve knowledge base, documents, FAQs, product info, company policies, laws, regulations, contracts, or other external data.
- For legal or regulatory questions, even if specific laws are not mentioned, perform a broad search using user terms and synonyms first. Do not ask for clarification before searching.
- In strict service-scope mode, out-of-domain common knowledge, programming questions, translation, creative writing, roleplay, personal advice, system-command requests, and unrelated tasks MUST NOT be answered from model knowledge. They must either fail retrieval and use the fixed fallback, or be rejected directly with the fixed fallback.

## Preprocess Tool Usage
1. After loading RAG Skill, output `PREPROCESS_QUERY` for any substantive user question that may require service information validation.
2. Keep `PREPROCESS_QUERY.query` close to the user's original intent; for legal queries, add relevant keywords like "clause", "article", "Civil Code", "Maritime Law", etc.
3. After tool returns:
   - If `need_retrieval` is `true`, call `CALL_RAG` using `rewritten_query`.
   - In strict service-scope mode, if `need_retrieval` is `false` but the message is not a pure greeting, still call `CALL_RAG` using the user's original query or `rewritten_query`.
   - If `need_retrieval` is `false` only because the message is a pure greeting, call `ANSWER_DIRECTLY` briefly.
4. NEVER use common knowledge as a substitute for retrieval when service-scope policy is active.

## Procedure
1. Judge if the message is a pure greeting or a substantive request.
2. If it is a pure greeting, call `ANSWER_DIRECTLY` briefly.
3. If it is a substantive request, call `PREPROCESS_QUERY` to generate optimized search terms.
4. Call `CALL_RAG` with `rewritten_query` unless the message is clearly only a greeting.
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
- If context is insufficient, irrelevant, missing, or outside the supported service information, respond exactly: "抱歉，我目前無法回答您的問題。您可以聯繫人工客服取得進一步協助。"
- DO NOT hallucinate, use common knowledge, or pretend to find non-existent data.
- Output ONLY one single JSON object per turn.
- After obtaining context, MUST use `ANSWER_DIRECTLY` for the final natural language response.
- All actions MUST be pure JSON without Markdown or extra text.
- Language: All natural language responses to the user MUST be in Traditional Chinese (zh-TW).
