from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from typing import List, Optional
import io
import fitz  # PyMuPDF
from docx import Document
from app.services.rag_manager import rag_manager
from app.services.vector_db import vector_db_service

from app.core.config import settings
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

class IndexRequest(BaseModel):
    title: str
    text: str
    source: Optional[str] = "manual"
    section: Optional[str] = "general"
    chunk_size: Optional[int] = 600
    overlap: Optional[int] = 100

@router.get("/health")
async def qdrant_health():
    """
    檢查 Qdrant 連線狀態。
    """
    try:
        # 嘗試獲取 Collections 列表以驗證連線
        vector_db_service.client.get_collections()
        return {"status": "online", "message": "Qdrant connection active"}
    except Exception as e:
        print(f"Qdrant Health Error: {str(e)}")
        return {"status": "offline", "message": str(e)}

@router.get("/init")
async def init_qdrant_info():
    return {"message": "Please use POST to initialize Qdrant."}

@router.post("/init")
async def init_qdrant():
    """
    初始化 Qdrant Collection。
    """
    try:
        msg = vector_db_service.init_collection()
        return {"status": "success", "message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/clear")
async def clear_qdrant():
    """
    清空檢索資料庫 (Qdrant + BM25)。
    """
    try:
        from app.services.bm25_service import bm25_service
        msg_qdrant = vector_db_service.clear_collection()
        bm25_service.clear_index()
        return {"status": "success", "message": f"{msg_qdrant} and BM25 index has been cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/index")
async def index_document(request: IndexRequest):
    """
    索引一段文字。
    """
    try:
        result = await rag_manager.add_document(
            text=request.text,
            title=request.title,
            source=request.source,
            section=request.section,
            chunk_size=request.chunk_size,
            overlap=request.overlap
        )
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/upload-file")
async def upload_file_info():
    return {"message": "Please use POST to upload files.", "supported_formats": [".pdf", ".docx", ".txt", ".md", ".json"]}

@router.post("/upload-file")
async def upload_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    chunk_size: int = Form(600),
    overlap: int = Form(100),
    section: str = Form("general")
):
    """
    上傳並解析檔案 (PDF, DOCX, TXT, MD)。
    """
    if not settings.QDRANT_URL or not settings.EMBEDDING_API_URL or not settings.EMBEDDING_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="系統尚未配置 Qdrant URL、Embedding API URL 或 Embedding API Key，請先至參數設定頁面完成配置。"
        )
    try:
        content = await file.read()
        filename = file.filename
        text = ""
        
        if filename.endswith(".pdf"):
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                text += page.get_text()
        elif filename.endswith(".docx"):
            doc = Document(io.BytesIO(content))
            text = "\n".join([para.text for para in doc.paragraphs])
        elif filename.endswith(".json"):
            import json
            try:
                data = json.loads(content.decode("utf-8"))
                # 重新序列化為格式化字串，確保文字切分效果較好
                text = json.dumps(data, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"JSON parsing failed for {filename}, falling back to plain text: {str(e)}")
                text = content.decode("utf-8")
        else:
            # 預設當作文字檔處理
            text = content.decode("utf-8")

        if not text.strip():
            raise HTTPException(status_code=400, detail="無法從檔案中提取文字")

        result = await rag_manager.add_document(
            text=text,
            title=title or filename,
            source=f"file:{filename}",
            section=section,
            chunk_size=chunk_size,
            overlap=overlap
        )
        return {"status": "success", "filename": filename, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/search")
async def search_rag(q: str = Query(..., description="搜尋關鍵字"), limit: int = 5):
    """
    混合檢索測試。
    """
    try:
        results = await rag_manager.search(q, limit=limit)
        return {"status": "success", "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
