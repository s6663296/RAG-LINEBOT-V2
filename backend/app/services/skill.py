import os
import json
import yaml
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class SkillService:
    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self.settings_path = os.path.join(skills_dir, "settings.json")
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.settings: Dict[str, Any] = {}
        self.load_skills()
        self.load_settings()

    def load_settings(self):
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    loaded_settings = json.load(f)
                    if isinstance(loaded_settings, dict):
                        self.settings.update(loaded_settings)
            except Exception as e:
                logger.error(f"Error loading skill settings: {e}")
        self._normalize_settings()

    def _normalize_settings(self):
        """
        將技能設定正規化為：
        - enabled_skills: 可供 LLM 路由選擇的技能
        - forced_skills: 必須先執行（或先載入）的技能

        相容舊版：若缺少 enabled_skills，預設啟用所有技能；forced_skills 預設為空。
        """
        if "enabled_skills" not in self.settings:
            self.settings["enabled_skills"] = list(self.skills.keys())
        elif not isinstance(self.settings.get("enabled_skills"), list):
            self.settings["enabled_skills"] = []

        if "forced_skills" not in self.settings:
            self.settings["forced_skills"] = []
        elif not isinstance(self.settings.get("forced_skills"), list):
            self.settings["forced_skills"] = []

        valid_skill_ids = set(self.skills.keys())

        enabled_seen = set()
        normalized_enabled = [
            skill_id
            for skill_id in self.settings.get("enabled_skills", [])
            if skill_id in valid_skill_ids and not (skill_id in enabled_seen or enabled_seen.add(skill_id))
        ]

        forced_seen = set()
        normalized_forced = [
            skill_id
            for skill_id in self.settings.get("forced_skills", [])
            if skill_id in valid_skill_ids and not (skill_id in forced_seen or forced_seen.add(skill_id))
        ]

        # 強制技能必須同時是啟用技能，避免無效配置。
        enabled_set = set(normalized_enabled)
        normalized_forced = [skill_id for skill_id in normalized_forced if skill_id in enabled_set]

        self.settings["enabled_skills"] = normalized_enabled
        self.settings["forced_skills"] = normalized_forced
        self.settings.pop("mandatory_skills", None)

    def save_settings(self, enabled_skills: List[str], forced_skills: Optional[List[str]] = None):
        self.load_skills()

        valid_skill_ids = set(self.skills.keys())

        enabled_seen = set()
        normalized_enabled = [
            skill_id
            for skill_id in enabled_skills
            if skill_id in valid_skill_ids and not (skill_id in enabled_seen or enabled_seen.add(skill_id))
        ]

        # 若舊版 API 未帶 forced_skills，保留現有 forced 設定（並在後續做合法化）。
        raw_forced_skills = self.settings.get("forced_skills", []) if forced_skills is None else forced_skills
        if not isinstance(raw_forced_skills, list):
            raw_forced_skills = []

        forced_seen = set()
        normalized_forced = [
            skill_id
            for skill_id in raw_forced_skills
            if skill_id in valid_skill_ids and not (skill_id in forced_seen or forced_seen.add(skill_id))
        ]

        enabled_set = set(normalized_enabled)
        normalized_forced = [skill_id for skill_id in normalized_forced if skill_id in enabled_set]

        self.settings["enabled_skills"] = normalized_enabled
        self.settings["forced_skills"] = normalized_forced
        self.settings.pop("mandatory_skills", None)

        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving skill settings: {e}")

    def get_settings(self) -> Dict[str, Any]:
        self.load_skills()
        self._normalize_settings()
        return {
            **self.settings,
            "enabled_skills": list(self.settings.get("enabled_skills", [])),
            "forced_skills": list(self.settings.get("forced_skills", []))
        }

    def load_skills(self):
        """
        遍歷 skills 目錄，載入所有 skill 的 metadata。
        """
        os.makedirs(self.skills_dir, exist_ok=True)

        loaded_skills: Dict[str, Dict[str, Any]] = {}
        for skill_id in sorted(os.listdir(self.skills_dir)):
            skill_path = os.path.join(self.skills_dir, skill_id)
            if not os.path.isdir(skill_path):
                continue

            skill_md_path = os.path.join(skill_path, "SKILL.md")
            if not os.path.exists(skill_md_path):
                logger.warning("Skill directory %s skipped because SKILL.md is missing", skill_path)
                continue

            metadata = self._extract_metadata(skill_md_path) or {}
            loaded_skills[skill_id] = {
                "skill_id": skill_id,
                "name": metadata.get("name", skill_id),
                "description": metadata.get("description", ""),
                "metadata": metadata,
                "path": skill_md_path,
                "dir": skill_path
            }

        self.skills = loaded_skills
        logger.info(f"Loaded {len(self.skills)} skills: {list(self.skills.keys())}")

    def _extract_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        從 .md 檔案的 frontmatter 提取 metadata。
        """
        metadata = {}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        metadata = yaml.safe_load(parts[1])
        except Exception as e:
            logger.error(f"Error extracting metadata from {file_path}: {e}")
        return metadata

    def get_skill_list(self) -> List[Dict[str, Any]]:
        """
        回傳可用技能列表（不包含完整內容）。
        """
        self.load_skills()
        return [
            {
                "skill_id": s["skill_id"],
                "name": s["name"],
                "description": s["description"],
                **s.get("metadata", {})
            }
            for s in self.skills.values()
        ]

    def get_skill_content(self, skill_id: str) -> Optional[str]:
        """
        讀取特定技能的 SKILL.md 內容（不包含 frontmatter）。
        """
        if skill_id not in self.skills:
            self.load_skills()
        if skill_id not in self.skills:
            return None
        
        path = self.skills[skill_id]["path"]
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        return parts[2].strip()
                return content.strip()
        except Exception as e:
            logger.error(f"Error reading skill {skill_id}: {e}")
            return None

    def get_skill_file_content(self, skill_id: str, relative_path: str) -> Optional[str]:
        """
        讀取技能目錄下的其他檔案（白名單檢查）。
        """
        if skill_id not in self.skills:
            self.load_skills()
        if skill_id not in self.skills:
            return None
        
        # 安全檢查：防止 path traversal
        if ".." in relative_path or relative_path.startswith("/") or relative_path.startswith("\\"):
            logger.warning(f"Rejected insecure path access: {relative_path}")
            return None

        full_path = os.path.join(self.skills[skill_id]["dir"], relative_path)
        if not os.path.exists(full_path):
            return None

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading skill file {skill_id}/{relative_path}: {e}")
            return None

# 初始化實例
# 假設 skills 目錄在 backend/skills
base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
skill_service = SkillService(os.path.join(base_dir, "skills"))
