from sqlalchemy import Column, String, Integer, Boolean, DateTime, JSON, Text
from sqlalchemy.sql import func
from app.db.database import Base

class LineRequestLog(Base):
    __tablename__ = "line_request_logs"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(50), unique=True, index=True)
    webhook_request_id = Column(String(50), index=True)
    line_event_id = Column(String(100))
    event_index = Column(Integer)
    event_type = Column(String(50))
    message_type = Column(String(50))
    line_user_id = Column(String(100))
    line_group_id = Column(String(100))
    line_room_id = Column(String(100))
    status = Column(String(20), index=True)
    stage = Column(String(50))
    success = Column(Boolean, nullable=True)
    error = Column(Text)
    user_text_preview = Column(String(120))
    reply_text_preview = Column(String(120))
    full_user_text = Column(Text)
    full_reply_text = Column(Text)
    metadata_json = Column(JSON, name="metadata")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
