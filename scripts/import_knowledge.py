"""知识导入脚本 — 将 Markdown/文本 导入 Graphiti 时序知识图谱

用法:
    python scripts/import_knowledge.py                          # 导入 data/knowledge/ 下所有 .md
    python scripts/import_knowledge.py data/knowledge/xxx.md   # 导入单个文件
    python scripts/import_knowledge.py --text "主播PC配置: RTX4090"  # 命令行直接导入
    python scripts/import_knowledge.py --dir data/knowledge    # 指定目录

需要环境变量: DEEPSEEK_API_KEY, SILICONFLOW_API_KEY
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 确保项目根在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.memory.graphiti_store import GraphitiStore


async def import_file(store: GraphitiStore, path: Path) -> int:
    """导入单个文件, 返回提取的实体数"""
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        print(f"  [跳过] {path.name} (空文件)")
        return 0
    n = await store.add_knowledge(content, source=path.stem)
    print(f"  [导入] {path.name} → {n} 个实体")
    return n


async def import_text(store: GraphitiStore, text: str, source: str = "cli") -> int:
    n = await store.add_knowledge(text, source=source)
    print(f"  [导入] {source} → {n} 个实体")
    return n


async def main():
    parser = argparse.ArgumentParser(description="导入领域知识到 Graphiti 时序知识图谱")
    parser.add_argument("files", nargs="*", help="要导入的文件路径")
    parser.add_argument("--text", help="直接从命令行导入文本")
    parser.add_argument("--dir", help="导入指定目录下所有 .md 文件",
                       default="data/knowledge")
    args = parser.parse_args()

    # 检查 API keys
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("[错误] 未设置 DEEPSEEK_API_KEY 环境变量")
        print("  请在 .env 或 start_live.bat 中设置")
        sys.exit(1)
    if not os.environ.get("SILICONFLOW_API_KEY"):
        print("[警告] 未设置 SILICONFLOW_API_KEY, Embedding 可能不可用")
        print("  注册获取免费 API key: https://cloud.siliconflow.cn/account/ak")

    store = GraphitiStore("data/graphiti")
    await store.start()

    total = 0

    if args.text:
        total += await import_text(store, args.text)
    elif args.files:
        for fp in args.files:
            p = Path(fp)
            if p.exists():
                total += await import_file(store, p)
            else:
                print(f"  [未找到] {fp}")
    else:
        # 默认: 导入 data/knowledge/ 下所有 .md
        kd = Path(args.dir)
        if not kd.exists():
            print(f"[信息] {kd} 目录不存在, 创建之")
            kd.mkdir(parents=True, exist_ok=True)
            print(f"  请将 .md 知识文件放入 {kd}/ 后重新运行")
            await store.close()
            return

        md_files = sorted(kd.glob("*.md"))
        if not md_files:
            print(f"[信息] {kd}/ 下没有 .md 文件")
            print(f"  请放入知识文件后重新运行")
            await store.close()
            return

        print(f"找到 {len(md_files)} 个知识文件:")
        for f in md_files:
            total += await import_file(store, f)

    print(f"\n完成: 共导入 {total} 个实体")
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
