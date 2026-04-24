# RAG LINE Bot V2 控制台

這是一個基於 FastAPI 與 RAG (Retrieval-Augmented Generation) 技術的 LINE Bot 服務。專案整合了向量資料庫 Qdrant 與 大語言模型 (LLM)，並提供一個現代化的 Web 管理控制台。

## 功能特點
- **RAG 問答系統**：結合 Qdrant 與 Embedding API (SiliconFlow)，提供精準的知識庫檢索與生成。
- **LINE Bot 整合**：完整的 Webhook 處理流程，支援非同步回覆。
- **管理控制台**：
    - **參數設定**：動態調整 LLM、RAG 與 LINE 設定。
    - **日誌監控**：即時查看 LINE Webhook 請求狀態。
    - **技能管理**：視覺化切換 Agent 技能。
    - **知識庫管理**：支援文件上傳、向量化與檢索測試。

## 技術棧
- **Backend**: FastAPI, SQLAlchemy, Pydantic, Uvicorn
- **Frontend**: Vanilla JS, CSS (Modern UI Design)
- **Database**: SQLite (Metadata), Qdrant (Vector Data)
- **LLM/Embedding**: OpenAI API Compatible, SiliconFlow

---

## 部署教學

### 1. 環境變數配置
在部署至 Cloud Run (或 ClawCloud Run) 時，請於控制台設定以下環境變數：

| 變數類別 | 變數名稱 | 說明 |
| :--- | :--- | :--- |
| **LINE** | `LINE_CHANNEL_SECRET` | LINE Channel Secret |
| | `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token |
| **LLM** | `LLM_BASE_URL` | LLM API 接口地址 |
| | `LLM_API_KEY` | LLM API 金鑰 |
| | `LLM_MODEL_ID` | 模型名稱 (如: `gpt-4o-mini`) |
| **Qdrant** | `QDRANT_URL` | Qdrant Cloud URL |
| | `QDRANT_API_KEY` | Qdrant API Key |
| **Embedding** | `EMBEDDING_API_URL` | Embedding API 地址 |
| | `EMBEDDING_API_KEY` | Embedding API Key |
| **系統** | `ENV` | 設為 `production` |
| | `DATABASE_URL` | 資料庫連線 (建議: `sqlite+aiosqlite:////data/app.db`) |

### 2. Docker 部署
本專案已包含 `Dockerfile`，可直接建構映像檔：

```bash
docker build -t rag-linebot .
docker run -p 8000:8000 --env-file .env rag-linebot
```

### 3. Cloud Run 設定建議
- **容器埠號**：8000 (預設)
- **持久化儲存**：建議掛載雲端磁碟至 `/data` 路徑，並將 `DATABASE_URL` 指向該路徑下的檔案。
- **資源限制**：建議至少 512MB RAM。

---

## 本機開發
1. 進入 `backend` 目錄。
2. 安裝依賴：`pip install -r requirements.txt`
3. 複製 `.env.example` 為 `.env` 並填寫必要金鑰。
4. 啟動服務：`python main.py`
5. 訪問 `http://localhost:8001` 查看控制台。

## 開發者
- GitHub: [s6663296](https://github.com/s6663296)
