"""AI 主播 Electron GUI 后端 — FastAPI"""

import asyncio
import time
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from src.main import AIStreamer
from src.utils.logger import log_manager

load_dotenv()

_tts_engine = None


async def _ensure_tts_engine():
    """延迟加载全局 TTS 引擎."""
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine
    try:
        from src.tts import get_comni_bridge

        _tts_engine = await get_comni_bridge()
        return _tts_engine
    except Exception as e:
        log_manager.get("gui").warning(f"TTS init failed: {e}")
        return None


async def _tts_speak(text: str):
    """TTS 合成 + 播放 (非阻塞, 独立协程)."""
    eng = await _ensure_tts_engine()
    if not eng or not eng.is_ready():
        return
    try:
        await eng.speak(text)
    except Exception as e:
        log_manager.get("gui").warning(f"TTS speak failed: {e}")


app = FastAPI(title="AI Streamer Backend")
streamer: Optional[AIStreamer] = None

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"
PERSONA_PATH = ROOT / "src" / "prompts" / "templates" / "persona_core.md"
S1_PATH = ROOT / "src" / "prompts" / "templates" / "s1_rules.md"
S2_PATH = ROOT / "src" / "prompts" / "templates" / "s2_rules.md"


class MessageRequest(BaseModel):
    text: str
    user: str = "test_user"
    mentioned_bot: bool = True


class ContentRequest(BaseModel):
    content: str


# ── 生命周期 ──────────────────────────────────────────


@app.on_event("startup")
async def startup():
    global streamer
    streamer = AIStreamer(config_path=str(CONFIG_PATH))
    streamer._s1._client._mock_mode = True  # S1 始终mock (MiniCPM需手动启动)
    # S2: load_dotenv() 已加载 .env, Config 自动展开 ${DEEPSEEK_API_KEY}
    api_key = streamer._cfg.current.s2_model.api_key
    streamer._s2._mock_mode = not (api_key and not api_key.startswith("${"))
    await streamer.start()
    print("[GUI Backend] started on :9071")


@app.on_event("shutdown")
async def shutdown():
    if streamer:
        await streamer.stop()


# ── 状态 ──────────────────────────────────────────────


@app.get("/api/status")
async def get_status():
    s = streamer
    if not s:
        return {"running": False}
    return {
        "running": s._running,
        "s1Mode": "mock" if s._s1._client._mock_mode else "real",
        "s2Mode": "mock" if s._s2._mock_mode else "real",
        "replyCount": s.reply_count,
        "cacheHitRate": s.cache_stats.hit_rate,
        "personaName": s._prompts._extract_field("名字") or "NewRoad",
    }


# ── 消息 ──────────────────────────────────────────────


@app.post("/api/send")
async def send_message(req: MessageRequest):
    s = streamer
    if not s or not s._running:
        return JSONResponse({"error": "not running"}, 503)

    t0 = time.perf_counter()
    text = req.text.strip()
    direction, confidence = _gen_mock_s1_direction(text)

    if s._s2._mock_mode:
        # S2 mock 直接调 API, 不走 handle_message (避免mock管道缠绕)
        reply = await _direct_s2_reply(s, text, direction, confidence)
        # TTS 语音输出
        if reply and len(reply) > 1:
            _ = asyncio.create_task(_tts_speak(reply))
        return {
            "reply": reply,
            "s1_token": "Start-Speaking",
            "s1_confidence": confidence,
            "s2_latency_ms": 0,
            "total_latency_ms": (time.perf_counter() - t0) * 1000,
            "cache_hit": False,
            "clean_warnings": [],
            "error": "S2 API call failed (check API key)" if not reply else None,
        }

    # S2 real → 走完整 handle_message 管道
    s._s1._client.set_mock_responses(
        [
            f"<|Start-Speaking confidence={confidence:.2f}|> {direction}",
        ]
    )

    reply = await s.handle_message(
        {
            "user": req.user,
            "text": text,
            "mentioned_bot": req.mentioned_bot,
        },
        bypass_rules=True,
    )

    # TTS 语音输出
    if reply and len(reply) > 1:
        _ = asyncio.create_task(_tts_speak(reply))

    total_ms = (time.perf_counter() - t0) * 1000
    last = s._reply_history[-1] if s._reply_history else None

    return {
        "reply": reply,
        "s1_token": last.s1_token if last else "Start-Speaking",
        "s1_confidence": last.s1_confidence if last else 0.85,
        "s2_latency_ms": last.s2_latency_ms if last else 0,
        "total_latency_ms": total_ms,
        "cache_hit": last.cache_hit if last else False,
        "clean_warnings": last.clean_warnings if last else [],
        "error": "S2 pipeline returned empty reply" if not reply else None,
    }


def _gen_mock_s1_direction(text: str) -> Tuple[str, float]:
    """为 mock S1 生成合理的回复方向文本 + 建议confidence"""
    text_lower = text.lower().strip()
    if any(w in text_lower for w in ("叫什么", "名字", "你是谁")):
        return ("观众问你是谁/叫什么, 简要自我介绍 (10字以内)", 0.4)  # non-think, 快速
    if any(w in text_lower for w in ("介绍", "背景", "设定", "详细")):
        return ("观众想详细了解你的背景设定, 展开介绍一下自己", 0.85)  # think-max, 深度
    if any(w in text_lower for w in ("在播", "在直播", "开播", "直播")):
        return ("观众问是否在直播/播什么, 告诉他现在正在播", 0.4)
    if any(w in text_lower for w in ("延迟", "卡", "慢", "快一点", "快点")):
        return ("观众抱怨延迟/速度, 解释或调侃", 0.4)
    if any(w in text_lower for w in ("段位", "什么段", "排位", "rank")):
        return ("观众问游戏段位/水平, 如实回答或自嘲", 0.5)
    if any(w in text_lower for w in ("装备", "武器", "用什么", "配装")):
        return ("观众问装备/武器配置, 展示当前配装", 0.5)
    if any(w in text_lower for w in ("版本", "更新", "改动", "patch", "哪个强")):
        return ("观众讨论版本/平衡性, 给出自己的看法", 0.7)
    if any(w in text_lower for w in ("菜", "lj", "垃圾", "废物", "辣鸡")):
        return ("观众吐槽主播菜, 用自嘲或怼回去的方式回应", 0.4)
    if any(w in text_lower for w in ("空", "怎么回", "不说话")):
        return ("观众抱怨回复太短或空, 解释或怼回去", 0.4)
    if len(text) <= 5:
        return ("观众发了条很短的消息, 随意回应一下", 0.3)
    return (f"观众说: {text[:30]}, 以主播身份自然回复", 0.5)


async def _direct_s2_reply(
    s, text: str, direction: str, confidence: float = 0.5
) -> Optional[str]:
    """绕过 mock 模式, 强制调真实 DeepSeek API 生成回复"""
    from src.models.s2_client import DeepSeekClient

    cfg = s._cfg.current
    real_client = DeepSeekClient(
        api_key=cfg.s2_model.api_key,
        api_base=cfg.s2_model.api_base,
        model=cfg.s2_model.model,
        temperature=cfg.s2_model.temperature,
        top_p=cfg.s2_model.top_p,
        timeout_ms=12000,
        mock_mode=False,
    )
    await real_client.start()

    try:
        system = s._prompts.build_s2_system()
        first_msg = s._prompts.build_s2_first_user_message("中文")
        user_msg = s._prompts.build_s2_user_message(
            reply_direction=direction,
            triggering_messages=f"[观众] {text}",
            s1_confidence=confidence,
            emotional_state="正常",
        )
        # think-max 长问题需要更大token预算
        tokens = 512 if confidence >= 0.8 else 256
        resp = await real_client.generate(
            system_prompt=system,
            user_message=user_msg,
            first_user_message=first_msg,
            s1_confidence=confidence,
            max_tokens=tokens,
        )
        if resp.error:
            print(f"[S2 direct] error: {resp.error}")
            return None
        content = resp.content
        if not content.strip():
            print(f"[S2 direct] empty content, thinking={len(resp.thinking)}")
            if resp.thinking.strip():
                print("[S2 direct] using thinking as content fallback")
                content = resp.thinking.strip()
            else:
                return None
        clean = s._cleaner.clean(content)
        return clean.text if not clean.is_empty else None
    finally:
        await real_client.stop()


# ── 开关 ──────────────────────────────────────────────


@app.post("/api/toggle_s1")
async def toggle_s1():
    if streamer:
        streamer._s1._client._mock_mode = not streamer._s1._client._mock_mode
        return {"s1Mode": "mock" if streamer._s1._client._mock_mode else "real"}
    return JSONResponse({"error": "not running"}, 503)


@app.post("/api/toggle_s2")
async def toggle_s2():
    if streamer:
        streamer._s2._mock_mode = not streamer._s2._mock_mode
        return {"s2Mode": "mock" if streamer._s2._mock_mode else "real"}
    return JSONResponse({"error": "not running"}, 503)


@app.post("/api/reset")
async def reset():
    if streamer:
        streamer._s1.reset()
        streamer._cache.clear()
        return {"status": "reset"}
    return JSONResponse({"error": "not running"}, 503)


# ── 文件读写 ──────────────────────────────────────────


@app.get("/api/persona")
async def get_persona():
    return {
        "content": PERSONA_PATH.read_text(encoding="utf-8")
        if PERSONA_PATH.exists()
        else ""
    }


@app.post("/api/persona")
async def save_persona(req: ContentRequest):
    PERSONA_PATH.write_text(req.content, encoding="utf-8")
    if streamer:
        streamer._prompts.reload_persona()
    return {"status": "saved", "size": len(req.content)}


@app.get("/api/config")
async def get_config():
    try:
        return {"content": CONFIG_PATH.read_text(encoding="utf-8")}
    except Exception:
        return {"content": "# 配置文件不存在"}


@app.post("/api/config")
async def save_config(req: ContentRequest):
    CONFIG_PATH.write_text(req.content, encoding="utf-8")
    if streamer:
        streamer._cfg.check_and_reload()
        streamer._prompts.reload_all()
    return {"status": "saved", "size": len(req.content)}


@app.get("/api/memory/viewers")
async def get_memory_viewers():
    """返回所有观众档案"""
    if not streamer:
        return {"viewers": []}
    if hasattr(streamer, "_memory") and streamer._memory:
        viewers = streamer._memory.get_all_viewers()
        return {"viewers": viewers, "count": len(viewers)}
    # Fallback
    viewers = []
    if hasattr(streamer, "_memory"):
        for uid, v in streamer._memory._viewers.items():
            viewers.append(
                {
                    "user_id": v.user_id,
                    "display_name": v.display_name,
                    "platform": v.platform,
                    "interaction_count": v.interaction_count,
                    "loyalty_level": v.loyalty_level,
                    "first_seen": v.first_seen,
                    "last_seen": v.last_seen,
                    "topics": v.topics,
                    "known_facts": v.known_facts,
                }
            )
    viewers.sort(key=lambda x: -x["interaction_count"])
    return {"viewers": viewers, "count": len(viewers)}


@app.get("/api/memory/search")
async def search_memory(q: str = "", user_id: str = "", limit: int = 10):
    """语义搜索记忆知识图谱"""
    if not streamer or not q:
        return {"results": [], "query": q}
    if hasattr(streamer, "_graphiti") and streamer._use_graphiti:
        results = await streamer._memory.search_archival(q, user_id=user_id)
        return {"results": results, "query": q, "backend": "graphiti"}
    return {"results": [], "query": q, "backend": "none", "hint": "Graphiti 未启动"}


@app.get("/api/memory/status")
async def get_memory_status():
    """记忆系统状态"""
    if not streamer:
        return {"status": "not_running"}
    return {
        "graphiti_ready": streamer._use_graphiti
        if hasattr(streamer, "_use_graphiti")
        else False,
        "viewer_count": streamer._memory.viewer_count
        if hasattr(streamer, "_memory")
        else 0,
        "interaction_count": streamer._memory.interaction_count
        if hasattr(streamer, "_memory")
        else 0,
        "faq_count": streamer._memory.faq_count if hasattr(streamer, "_memory") else 0,
    }


@app.get("/api/logs/{module}")
async def get_logs(module: str, lines: int = 200):
    """获取模块日志"""
    entries = log_manager.get_recent(module, lines=lines)
    return {"module": module, "entries": entries, "count": len(entries)}


@app.get("/api/logs")
async def list_log_modules():
    """列出所有有日志的模块"""
    log_dir = Path("data/logs")
    if not log_dir.exists():
        return {"modules": []}
    modules = set()
    for f in log_dir.glob("*.log"):
        modules.add(f.stem)
    return {"modules": sorted(modules)}


@app.get("/api/s1_rules")
async def get_s1_rules():
    return {"content": S1_PATH.read_text(encoding="utf-8") if S1_PATH.exists() else ""}


@app.get("/api/s2_rules")
async def get_s2_rules():
    return {"content": S2_PATH.read_text(encoding="utf-8") if S2_PATH.exists() else ""}


# ── 录制 ──────────────────────────────────────────────


@app.get("/api/recordings")
async def list_recordings():
    rec_dir = Path("data/recordings")
    if not rec_dir.exists():
        return {"files": []}
    files = []
    for f in sorted(
        rec_dir.glob("*.rec"), key=lambda x: x.stat().st_mtime, reverse=True
    ):
        files.append(
            {
                "file": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "date": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)
                ),
            }
        )
    return {"files": files[:20]}


# ── 自迭代 ──────────────────────────────────────────


@app.get("/api/iteration/history")
async def iteration_history():
    from src.iteration.injector import Phase4Injector

    inj = Phase4Injector(prompts=streamer._prompts if streamer else None)
    return {"history": inj.history[-20:]}


@app.get("/api/iteration/stats")
async def iteration_stats():
    return {
        "injection_count": 0,
        "training_samples": streamer._trainer.sample_count if streamer else 0,
        "training_corrected": streamer._trainer.stats.get("corrected", 0)
        if streamer
        else 0,
        "training_pending": streamer._trainer.stats.get("pending", 0)
        if streamer
        else 0,
    }


# ── 测试 ──────────────────────────────────────────────


@app.post("/api/test/run/{scenario}")
async def run_test(scenario: str):
    return {"status": "ok", "scenario": scenario, "message": "测试场景已触发"}


# ── 知识库 ───────────────────────────────────────────


@app.get("/api/knowledge/files")
async def knowledge_files():
    kd = Path("data/knowledge")
    if not kd.exists():
        return {"files": []}
    return {"files": [f.name for f in sorted(kd.glob("*.md"))]}


@app.get("/api/knowledge/file/{name}")
async def knowledge_file(name: str):
    if ".." in name or "/" in name or "\\" in name:
        return JSONResponse(content={"error": "invalid filename"}, status_code=400)
    p = Path("data/knowledge") / name
    if p.exists():
        return {"content": p.read_text(encoding="utf-8")}
    return {"content": "文件不存在"}


# ── 技能库 ───────────────────────────────────────────


@app.get("/api/skills/list")
async def skills_list():
    sd = Path("data/memory/skills")
    if not sd.exists():
        return {"skills": []}
    return {"skills": [f.name for f in sorted(sd.glob("*.md"))]}


@app.get("/api/skills/file/{name}")
async def skills_file(name: str):
    if ".." in name or "/" in name or "\\" in name:
        return JSONResponse(content={"error": "invalid filename"}, status_code=400)
    p = Path("data/memory/skills") / name
    if p.exists():
        return {"content": p.read_text(encoding="utf-8")}
    return {"content": "文件不存在"}


# ── 静态文件 ──────────────────────────────────────────

GUI_DIR = ROOT / "gui" / "dist"


@app.get("/")
async def index():
    index_html = GUI_DIR / "index.html"
    if index_html.exists():
        return FileResponse(index_html)
    return {"message": "GUI not built. Run: cd gui && npm run build", "docs": "/docs"}


if (GUI_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=GUI_DIR / "assets"), name="assets")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            if streamer and streamer._running:
                status = {
                    "running": True,
                    "s1Mode": "mock" if streamer._s1._client._mock_mode else "real",
                    "s2Mode": "mock" if streamer._s2._mock_mode else "real",
                    "replyCount": len(streamer._reply_history),
                    "cacheHitRate": streamer._cache.stats.hit_rate
                    if hasattr(streamer, "_cache")
                    else 0,
                    "personaName": getattr(streamer, "_persona_name", "NewRoad"),
                    "connected": True,
                }
            else:
                status = {"running": False, "connected": False}
            await websocket.send_json(status)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
