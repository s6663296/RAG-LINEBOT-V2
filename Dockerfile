# 使用輕量級 Python 映像檔
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /app

# 安裝系統依賴 (如果需要的話，目前 requirements.txt 內的套件不需要額外編譯工具)
# RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

# 複製依賴文件並安裝
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製後端與前端代碼
COPY backend ./backend
COPY frontend ./frontend

# 設定環境變數
ENV PYTHONPATH=/app/backend
ENV ENV=production
ENV PORT=8000

# 暴露埠號 (Cloud Run 會自動映射，此處僅作標示)
EXPOSE 8000

# 啟動應用程式
# 使用 python 執行 main.py，內部會調用 uvicorn 並讀取 PORT 環境變數
CMD ["python", "backend/main.py"]
