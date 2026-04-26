# RAG LINE Bot V2 - 智慧知識庫 Agent 系統

這是一個進化版的 LINE Bot 服務，基於 FastAPI 架構，整合了 **OpenClaw 結構化提示詞**、**多階段 Agent 決策流程**與**混合 RAG (Hybrid RAG)** 技術。專案旨在提供一個具備自然人格、能自主調度技能，且檢索極其精準的智慧助手。

## 系統截圖

<p align="center">
  <img src="assets/settings.png" width="45%" alt="參數設定介面">
  &nbsp;
  <img src="assets/knowledge.png" width="45%" alt="知識庫管理介面">
</p>

---

## 核心功能特點

### 1. 多階段 Agent 決策引擎 (OpenClaw 結構)
採用類 OpenClaw 的結構化提示詞與 XML 標籤管理，Agent 不再只是單純回答，而是具備「思考與執行」循環的智慧體：
- **自主技能調度**：根據使用者意圖，動態決定是否讀取特定技能 (`READ_SKILL`) 或文件 (`READ_SKILL_FILE`)。
- **查詢前處理**：內建 `PREPROCESS_QUERY` 動作，自動進行意圖分析與查詢語句改寫，大幅提升檢索命中率。
- **結構化決策**：強制 LLM 以 JSON 格式進行內部決策，確保邏輯嚴密且可被系統精準解析。

### 2. 強大且精準的混合 RAG 系統
專為高品質問答設計的檢索架構：
- **混合檢索 (Hybrid Search)**：結合向量資料庫 (Dense Retrieval) 與 BM25 關鍵字檢索 (Sparse Retrieval)。
- **RRF 融合演算法**：使用 Reciprocal Rank Fusion (RRF) 自動加權融合不同來源的檢索結果。
- **精確排序 (Reranking)**：整合 Reranker 模型，對初步檢索出的候選內容進行二次精排序，確保最相關的資料排在最前面。

### 3. 模組化技能系統 (Skill System)
支援動態擴展的技能機制：
- **獨立指令集**：每個技能可擁有專屬的 `SKILL.md` 指令與設定。
- **熱插拔管理**：透過控制台即時開啟/關閉或「強制啟用」特定技能。
- **知識隔離**：技能可自帶參考文件，僅在需要時由 Agent 讀取。

### 4. 自然的人格設定 (Natural Persona)
針對對話體驗進行深度優化：
- **去工具化回覆**：Agent 會將檢索到的資料內化，嚴禁提及「根據資料顯示」、「資料庫」或「檢索結果」等生硬詞彙。
- **繁體中文優化**：預設採用台灣用語與親切的人類助手語氣。
- **智慧防呆**：內建自動過濾模型主動反問（如「請問還有什麼需要協助的嗎？」）的功能，保持對話簡潔。

### 5. 全功能管理控制台
- **實時監控**：視覺化查看 Agent 的決策步驟（例如：正在決策、正在檢索、正在排序）。
- **參數調整**：動態修改 LLM 溫度、RAG Top-K、Rerank 門檻等進階設定。
- **知識庫維護**：支援 PDF/文本上傳、自動切片 (Chunking) 與向量化。

---

## 技術棧

| 類別 | 關鍵技術 |
| :--- | :--- |
| **後端核心** | FastAPI (Async), Python 3.10+, Pydantic V2 |
| **Agent 框架** | 自研 OpenClaw-inspired JSON Router |
| **資料庫** | Qdrant (向量 + 稀疏矩陣), SQLite (Metadata), BM25 Index |
| **AI 模型** | OpenAI / SiliconFlow 相容介面 (LLM, Embedding, Rerank) |
| **前端** | Vanilla JS, CSS (現代化 Glassmorphism 設計) |

---

## 專案結構簡述

- `backend/app/services/agent.py`: Agent 決策大腦，負責路由與多輪循環。
- `backend/app/services/rag_manager.py`: 協調混合檢索與 RRF 融合邏輯。
- `backend/app/services/skill.py`: 處理模組化技能的讀取與管理。
- `backend/skills/`: 存放所有技能定義檔案。
- `frontend/`: 獨立的 SPA 網頁，負責管理介面。

---

## 開發者
- GitHub: [s6663296](https://github.com/s6663296)
