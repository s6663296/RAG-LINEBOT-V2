from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import chat, env_settings, request_logs, webhook, skills
from app.api.v1.endpoints import rag
from app.core.config import settings

from fastapi.staticfiles import StaticFiles
import os

from contextlib import asynccontextmanager
from app.db.database import engine, Base

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建立資料庫表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# 設定 CORS，允許前端存取
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 部署時應限制來源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 註冊路由
app.include_router(rag.router, prefix=f"{settings.API_V1_STR}/rag", tags=["rag"])
app.include_router(chat.router, prefix=f"{settings.API_V1_STR}/chat", tags=["chat"])
app.include_router(skills.router, prefix=f"{settings.API_V1_STR}/skills", tags=["skills"])
app.include_router(
    env_settings.router,
    prefix=f"{settings.API_V1_STR}/settings",
    tags=["settings"],
)
app.include_router(webhook.router, prefix="/webhook", tags=["webhook"])
app.include_router(request_logs.router, prefix=f"{settings.API_V1_STR}/logs", tags=["logs"])

# 掛載前端靜態檔案 (假設 frontend 資料夾與 backend 在同一層)
frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

@app.get("/")
async def root():
    return {"message": "Modular LLM API is running", "docs": "/docs"}

if __name__ == "__main__":
    import uvicorn
    import os
    
    # 雲端環境建議優先使用 8080 埠號
    port = int(os.environ.get("PORT", 8080))
    reload = os.environ.get("ENV", "development") == "development"
    
    # proxy_headers=True 確保在 Cloud Run 代理後方能正確處理 HTTPS 標頭
    # forwarded_allow_ips="*" 允許來自代理的轉發
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=port, 
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
