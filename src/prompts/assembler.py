"""Prompt 三层拼装引擎 — Rules + Persona + Context"""

from pathlib import Path
from typing import Dict, Optional


class PromptAssembler:
    """
    三层 Prompt 拼装:
      Layer A: 规则层 (rules.md)      — 稳定, 技术性约束
      Layer B: 人设层 (persona_core)  — 常调, 角色性格/风格
      Layer C: 动态上下文             — 每轮调用时拼装

    S1 System = rules + persona (S1 适用部分)
    S2 System = rules + persona (S2 适用部分)
    S2 User   = 动态上下文 (每轮不同)
    """

    def __init__(self, prompts_dir: str = "src/prompts/templates"):
        self._dir = Path(prompts_dir)
        self._s1_rules_path = self._dir / "s1_rules.md"
        self._s2_rules_path = self._dir / "s2_rules.md"
        self._persona_path = self._dir / "persona_core.md"

        if not self._s1_rules_path.exists():
            raise FileNotFoundError(f"S1 规则文件不存在: {self._s1_rules_path}")
        if not self._s2_rules_path.exists():
            raise FileNotFoundError(f"S2 规则文件不存在: {self._s2_rules_path}")
        if not self._persona_path.exists():
            raise FileNotFoundError(f"人设核心文件不存在: {self._persona_path}")

        self._s1_rules = self._s1_rules_path.read_text(encoding="utf-8")
        self._s2_rules = self._s2_rules_path.read_text(encoding="utf-8")
        self._persona_core = self._persona_path.read_text(encoding="utf-8")
        self._s1_persona = self._extract_for_s1()
        self._s2_persona = self._extract_for_s2()

    # ── 公共接口 ──────────────────────────────────────

    def build_s1_system(self) -> str:
        """构建 S1 System Prompt: 规则层 + 人设层(S1)"""
        return f"{self._s1_rules}\n\n---\n\n{self._s1_persona}"

    def build_s2_system(self) -> str:
        """构建 S2 System Prompt: 规则层 + 人设层(S2) + 破限声明"""
        return f"{self._s2_rules}\n\n---\n\n{self._s2_persona}"

    def build_s2_user_message(
        self,
        reply_direction: str = "",
        visual_summary: str = "",
        triggering_messages: str = "",
        recent_chat: str = "",
        retrieved_memories: str = "",
        viewer_profile: str = "",
        relevant_skills: str = "",
        emotional_state: str = "",
        s1_confidence: float = 0.7,
        current_topic: str = "",
        topic_duration: str = "",
        seconds_since_last_reply: float = 0.0,
    ) -> str:
        """构建 S2 User Message (动态上下文层)"""
        parts = []

        if reply_direction:
            parts.append(f"【回复方向】(S1 置信度 {s1_confidence:.2f})\n{reply_direction}")

        if visual_summary:
            parts.append(f"【画面摘要】\n{visual_summary}")

        if current_topic:
            parts.append(f"【当前话题】{current_topic} (已持续 {topic_duration})")

        if seconds_since_last_reply <= 0:
            parts.append("【距上次发言】首次发言")
        else:
            parts.append(f"【距上次发言】{seconds_since_last_reply:.0f} 秒")

        if emotional_state:
            parts.append(f"【情绪状态】{emotional_state}")

        if triggering_messages:
            parts.append(f"【触发消息】\n{triggering_messages}")

        if recent_chat:
            parts.append(f"【最近聊天】\n{recent_chat}")

        if retrieved_memories:
            parts.append(f"【相关记忆】\n{retrieved_memories}")

        if viewer_profile:
            parts.append(f"【观众档案】\n{viewer_profile}")

        if relevant_skills:
            parts.append(f"【可参考 Skill】\n{relevant_skills}")

        parts.append("\n请直接输出回复文本:")
        return "\n\n".join(parts)

    def build_s2_first_user_message(self, language: str = "中文") -> str:
        """DeepSeek 角色扮演控制指令 (first user message)"""
        bot_name = self._extract_field("名字") or "AI主播"
        return f"用{language}思考和表达。你在扮演 {bot_name}，直接以角色身份说话，不要跳出角色。"

    def reload_persona(self) -> None:
        """人设变更后重新提取所有层"""
        self._persona_core = self._persona_path.read_text(encoding="utf-8")
        self._s1_persona = self._extract_for_s1()
        self._s2_persona = self._extract_for_s2()

    def reload_rules(self) -> None:
        """规则变更后重新加载"""
        self._s1_rules = self._s1_rules_path.read_text(encoding="utf-8")
        self._s2_rules = self._s2_rules_path.read_text(encoding="utf-8")

    def reload_all(self) -> None:
        """完全重新加载"""
        self.reload_rules()
        self.reload_persona()

    # ── 注解提取 (Section-based) ────────────────────

    def _extract_for_s1(self) -> str:
        """从 persona_core.md 提取 @s1 和 @both 标记的 section"""
        return self._extract_sections({"@s1", "@both"})

    def _extract_for_s2(self) -> str:
        """从 persona_core.md 提取 @s2 和 @both 标记的 section"""
        return self._extract_sections({"@s2", "@both"})

    def _extract_sections(self, target_tags: set) -> str:
        """
        按 section 提取 persona_core.md:
        - Heading 带目标标记 → 该 section 全部内容纳入
        - Section 内的子行如果自带 @s1/@s2/@both → 仅匹配目标标签的纳入
        - 子行无标记 → 跟随父 section 纳入
        """
        lines = self._persona_core.split("\n")
        result = []
        in_target_section = False

        for line in lines:
            stripped = line.strip()
            is_heading = stripped.startswith("## ") and not stripped.startswith("### ")

            if is_heading:
                in_target_section = any(tag in line for tag in target_tags)
                if in_target_section:
                    clean = line
                    for t in ("@s1", "@s2", "@both"):
                        clean = clean.replace(t, "")
                    result.append(clean.strip())
                continue

            if not in_target_section or not stripped:
                continue

            # 在目标 section 内
            own_tags = {t for t in ("@s1", "@s2", "@both") if t in line}
            if own_tags:
                # 行自带注解 → 检查是否匹配
                if own_tags & target_tags:
                    clean = line
                    for t in ("@s1", "@s2", "@both"):
                        clean = clean.replace(t, "")
                    result.append(clean.strip())
            else:
                # 行无注解 → 跟随 section
                result.append(line)

        return "\n".join(result) if result else "(人设核心未找到匹配标记)"


    def _extract_field(self, field_name: str) -> Optional[str]:
        """从人设核心提取指定字段值"""
        for line in self._persona_core.split("\n"):
            if field_name in line and ":" in line:
                return line.split(":", 1)[-1].strip()
        return None

    # ── 获取完整原始内容 (调试用) ─────────────────────

    @property
    def persona_core_raw(self) -> str:
        return self._persona_core

    @property
    def s1_rules_raw(self) -> str:
        return self._s1_rules

    @property
    def s2_rules_raw(self) -> str:
        return self._s2_rules
