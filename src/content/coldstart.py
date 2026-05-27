"""冷启动 — 种子Skill生成 + 知识导入 + 加速学习"""

import json
import time
from pathlib import Path
from typing import List, Dict, Optional

from src.models.s2_client import DeepSeekClient
from src.prompts.assembler import PromptAssembler


class ColdStartManager:
    """新主播冷启动管理"""

    def __init__(self, prompts: PromptAssembler, s2_client: DeepSeekClient,
                 data_dir: str = "data/memory"):
        self._prompts = prompts
        self._s2 = s2_client
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._skill_dir = self._dir / "skills"
        self._skill_dir.mkdir(exist_ok=True)

    # ── 种子 Skill 生成 ──────────────────────────────

    async def generate_seed_skills(self, count: int = 8) -> List[Dict]:
        """从人设核心 + 领域知识自动生成种子 Skill"""
        persona = self._prompts.persona_core_raw
        knowledge = self._load_knowledge()

        prompt = (
            "基于以下AI主播人设和领域知识, 生成{}个实用的互动Skill。\n\n"
            "人设:\n{}\n\n领域知识:\n{}\n\n"
            "每个Skill包含: \n"
            "1. 名称\n2. 适用场景\n3. 回复模式(一句话概括)\n"
            "4. 2个示例回复(自然口语, 不是模板)\n"
            "输出Markdown, 用##分隔每个Skill。"
        ).format(count, persona[:500], knowledge[:500])

        resp = await self._s2.generate(
            system_prompt="你是AI主播技能设计师。输出Markdown。",
            user_message=prompt,
            first_user_message="用中文输出。",
            s1_confidence=0.7,
            max_tokens=3000,
        )

        skills = self._parse_skills(resp.content)
        for skill in skills:
            self._save_skill(skill)

        return skills

    def _parse_skills(self, markdown: str) -> List[Dict]:
        """从Markdown解析Skill列表"""
        skills = []
        sections = markdown.split("## ")
        for sec in sections[1:]:  # 跳过第一个空段
            lines = sec.strip().split("\n")
            if not lines:
                continue
            name = lines[0].strip()
            content = "\n".join(lines[1:]).strip()
            if name and content:
                skills.append({"name": name, "content": content})
        return skills

    def _save_skill(self, skill: Dict) -> None:
        safe_name = skill["name"].replace(" ", "_").replace("/", "_")[:40]
        path = self._skill_dir / f"{safe_name}.md"
        path.write_text(
            f"# {skill['name']}\n\n{skill['content']}",
            encoding="utf-8",
        )

    def _load_knowledge(self) -> str:
        """加载领域知识"""
        kf = Path("data/knowledge")
        if not kf.exists():
            return "暂无领域知识"
        texts = []
        for f in kf.glob("*.md"):
            try:
                texts.append(f.read_text(encoding="utf-8")[:500])
            except Exception:
                pass
        return "\n\n".join(texts) if texts else "暂无领域知识"

    # ── 学习模式检测 ──────────────────────────────────

    def is_learning_mode(self) -> bool:
        """判断是否需要加速学习"""
        skills = list(self._skill_dir.glob("*.md"))
        if len(skills) < 10:
            return True
        # 检查观众档案
        viewers_path = self._dir / "viewers.json"
        if viewers_path.exists():
            try:
                data = json.loads(viewers_path.read_text(encoding="utf-8"))
                if len(data.get("viewers", {})) < 20:
                    return True
            except Exception:
                pass
        return False

    @property
    def skill_count(self) -> int:
        return len(list(self._skill_dir.glob("*.md")))

    def import_knowledge_file(self, path: str) -> None:
        """导入单个知识文件"""
        src = Path(path)
        if src.exists():
            dest = Path("data/knowledge") / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
