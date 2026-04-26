from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from app.services.skill import skill_service

router = APIRouter()

class SkillSettingsUpdate(BaseModel):
    enabled_skills: List[str]
    forced_skills: Optional[List[str]] = Field(default=None)

@router.get("")
@router.get("/")
async def list_skills():
    """獲取所有技能列表及其 Metadata。"""
    return skill_service.get_skill_list()

@router.get("/settings")
async def get_skill_settings():
    """獲取技能設定（技能啟用清單 + 強制執行清單）。"""
    return skill_service.get_settings()

@router.post("/settings")
async def update_skill_settings(settings: SkillSettingsUpdate):
    """更新技能設定。"""
    skill_service.save_settings(settings.enabled_skills, settings.forced_skills)
    return {"status": "success", "settings": skill_service.get_settings()}

@router.get("/{skill_id}/content")
async def get_skill_content(skill_id: str):
    """獲取特定技能的詳細內容。"""
    content = skill_service.get_skill_content(skill_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"skill_id": skill_id, "content": content}
