"""Graphiti 时序知识图谱存储层 — 替换 L2+L3 JSON 持久化

Kuzu 嵌入式图数据库, 零 Docker, 数据库文件在 data/graphiti/db/.
"""

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from graphiti_core import Graphiti
from graphiti_core.driver.kuzu_driver import KuzuDriver
from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode, EpisodeType

from .graphiti_config import (
    make_deepseek_client,
    make_deepseek_reranker,
    make_siliconflow_embedder,
)


class GraphitiStore:
    """时序知识图谱记忆存储 — 零 Docker, Kuzu 嵌入式数据库"""

    def __init__(self, db_dir: str = "data/graphiti"):
        self._db_dir = Path(db_dir)
        self._db_path: Path | None = None
        self._graphiti: Graphiti | None = None
        self._started = False

    async def start(self) -> None:
        import uuid, shutil

        self._db_dir.mkdir(parents=True, exist_ok=True)
        # 主动清理所有旧的数据库目录 (上次运行残留, 避免 Windows 文件锁)
        for old in sorted(self._db_dir.glob("db*"), reverse=True):
            try:
                shutil.rmtree(str(old), ignore_errors=True)
            except Exception:
                pass
        remaining = list(self._db_dir.glob("db*"))
        if remaining:
            print(
                f"[Graphiti] 警告: {len(remaining)} 个旧目录未能清理: {[r.name for r in remaining]}"
            )
        # 始终使用全新 UUID 路径 (不可能有陈旧锁)
        self._db_path = self._db_dir / f"db_{uuid.uuid4().hex[:8]}"
        driver = KuzuDriver(db=str(self._db_path))
        self._graphiti = Graphiti(
            graph_driver=driver,
            llm_client=make_deepseek_client(),
            embedder=make_siliconflow_embedder(),
            cross_encoder=make_deepseek_reranker(),
            store_raw_episode_content=True,
        )
        await self._graphiti.build_indices_and_constraints()
        self._started = True
        print(f"[Graphiti] Kuzu 启动成功: {self._db_path}")

    async def add_interaction(
        self,
        user_id: str,
        display_name: str,
        message: str,
        reply: str,
        platform: str = "bilibili",
    ) -> bool:
        """记录一次弹幕互动 → LLM 自动提取事实到知识图谱 (异步, 不阻塞)"""
        if not self._started or not self._graphiti:
            return False

        try:
            episode_body = (
                f"观众 [{display_name}] (user_id={user_id}) 发弹幕说: {message}\n"
                f"AI主播回复: {reply}\n"
                f"平台: {platform}"
            )
            await self._graphiti.add_episode(
                name=f"chat:{user_id}:{int(time.time())}",
                episode_body=episode_body,
                source=EpisodeType.message,
                source_description=f"{platform}_live_chat",
                reference_time=datetime.now(timezone.utc),
                group_id=user_id,
            )
            return True
        except Exception as e:
            print(f"[Graphiti] add_interaction 失败: {e}")
            return False

    async def add_knowledge(self, content: str, source: str = "manual") -> int:
        """导入领域知识文档 → 提取实体/关系/事实, 返回提取的实体数"""
        if not self._started or not self._graphiti:
            return 0

        result = await self._graphiti.add_episode(
            name=f"knowledge:{source}:{int(time.time())}",
            episode_body=content,
            source=EpisodeType.text,
            source_description=source,
            reference_time=datetime.now(timezone.utc),
            group_id="knowledge_base",
        )
        return len(result.nodes)

    async def search(self, query: str, user_id: str = "", limit: int = 5) -> list[dict]:
        """混合搜索: 语义 + BM25 + 图遍历 → 返回相关事实"""
        if not self._started or not self._graphiti:
            return []

        try:
            edges = await self._graphiti.search(
                query, group_ids=[user_id] if user_id else None, num_results=limit
            )
            return [
                {
                    "uuid": e.uuid,
                    "name": e.name,
                    "fact": e.fact,
                    "valid_at": e.valid_at.isoformat() if e.valid_at else None,
                    "invalid_at": e.invalid_at.isoformat() if e.invalid_at else None,
                    "source_node_uuid": e.source_node_uuid,
                    "target_node_uuid": e.target_node_uuid,
                    "group_id": e.group_id,
                    "attributes": e.attributes,
                }
                for e in edges
            ]
        except Exception as e:
            print(f"[Graphiti] search 失败: {e}")
            return []

    async def get_viewer_facts(self, user_id: str, limit: int = 10) -> list[dict]:
        """获取关于特定观众的所有已知事实"""
        return await self.search("", user_id=user_id, limit=limit)

    async def get_recent_context(self, user_id: str = "", limit: int = 10) -> str:
        """获取最近互动上下文 → S2 prompt 注入"""
        if not self._started or not self._graphiti:
            return ""

        facts = await self.search("对话 互动 弹幕 聊天", user_id=user_id, limit=limit)
        if not facts:
            return ""

        lines = []
        for f in facts[:limit]:
            fact_text = f.get("fact", "")
            if fact_text:
                lines.append(f"- {fact_text}")
        return "相关记忆:\n" + "\n".join(lines) if lines else ""

    async def get_viewer_profile_text(self, user_id: str) -> str:
        """生成观众档案文本 (替换 L2.get_viewer_context)"""
        facts = await self.get_viewer_facts(user_id, limit=15)
        if not facts:
            return ""

        # 整理为 prompt 可用的自然语言
        fact_lines = [f.get("fact", "") for f in facts if f.get("fact")]
        unique_facts = list(dict.fromkeys(fact_lines))[:8]
        return "观众已知信息:\n" + "\n".join(f"- {f}" for f in unique_facts)

    async def close(self) -> None:
        if self._graphiti:
            await self._graphiti.close()
            self._started = False
            print("[Graphiti] 已关闭")

    @property
    def is_ready(self) -> bool:
        return self._started
