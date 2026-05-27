<details open>
<summary><b>🇨🇳 中文</b> | <i>点此切换 English ▼</i></summary>

# OpenNeuro — AI 游戏主播

> *一个基于双模型（S1 MiniCPM / S2 DeepSeek）的 AI 直播机器人 —— 实时捕获弹幕、VAD 情绪驱动、角色人设系统。灵感来自 Neuro-sama。*

---

## 目录

- [项目概述](#项目概述)
- [架构](#架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [配置](#配置)
- [Live Hub](#live-hub)
- [人设系统](#人设系统)
- [情感系统](#情感系统)
- [记忆系统](#记忆系统)
- [开发](#开发)

---

## 项目概述

OpenNeuro 是一个 AI 直播机器人，专为 Bilibili 等平台设计。它能实时监听直播间弹幕，通过双模型架构进行决策与回复生成，模拟真实主播的互动风格。

### 核心能力

- **实时弹幕捕获** — 通过内置 Bilibili WebSocket 或 Live Hub 并行采集弹幕
- **双模型架构** — S1（MiniCPM-o 4.5）快速决策 + S2（DeepSeek）深度生成
- **VAD 情绪系统** — 三维情绪模型（Valence-Arousal-Dominance），20+ 事件触发器，自然衰减
- **三层 Prompt 体系** — 规则层 + 人设层 + 动态上下文，人设热重载
- **多级记忆系统** — L1 工作记忆 / L2 短期记忆 / L3 长期记忆（Graphiti 知识图谱）
- **Electron GUI** — 可视化人设编辑器、日志查看、配置管理

---

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      用户输入 (弹幕 / 礼物 / 订阅)                  │
├──────────────┬──────────────────────────────────────────────────┤
│  Live Hub    │  Bilibili 直连 (--platform bilibili)               │
│  :18190/ws   │  wss://broadcastlv.chat.bilibili.com/sub           │
├──────────────┴──────────────────────────────────────────────────┤
│                     Unified Message Queue                         │
├──────────────────────────────────────────────────────────────────┤
│  S1 System (MiniCPM-o 4.5)         │  S2 System (DeepSeek)       │
│  ┌─────────────────────────┐       │  ┌───────────────────────┐  │
│  │ 快速决策: 回/不回/打断     │       │  │ 角色扮演深度回复生成    │  │
│  │ 发言方向 + Confidence     │ ────→ │  │ 1-3句, ≤80字, 口语化 │  │
│  │ ≤15字 Quick Reply        │       │  │ 情感 + 记忆 上下文注入  │  │
│  └─────────────────────────┘       │  └───────────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│  EmotionalState (VAD)  │  PromptAssembler  │  MemoryManager       │
│  valence/arousal/dom   │  Rules+Persona    │  L1→L2→L3            │
├──────────────────────────────────────────────────────────────────┤
│                   GUI Server (Electron + React)                    │
│                   Persona Editor / Logs / Config                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 目录结构

```
OpenNeuro/
├── src/                           # 核心引擎
│   ├── main.py                    # AIStreamer 主控
│   ├── gui_server.py              # FastAPI GUI 后端
│   │
│   ├── prompts/                   # Prompt 三层拼装
│   │   ├── assembler.py           # Rules + Persona + Context
│   │   └── templates/
│   │       ├── persona_core.md    # 唯一人设源 (NewRoad/新露)
│   │       ├── s1_rules.md        # S1 决策规则
│   │       └── s2_rules.md        # S2 生成规则
│   │
│   ├── emotion/                   # VAD 情绪模型
│   ├── memory/                    # L1/L2/L3 记忆
│   │   ├── l1_working.py          # 工作记忆 (滑动窗口)
│   │   ├── l2_short.py            # 短期记忆 (会话级)
│   │   ├── l3_long.py             # 长期记忆 (Graphiti)
│   │   └── graphiti_store.py      # Kuzu 图数据库
│   │
│   ├── content/                   # 内容系统 (冷启动)
│   ├── events/                    # 事件总线
│   ├── config/                    # YAML 配置加载
│   ├── iteration/                 # 自迭代模块
│   ├── models/                    # 模型抽象 (llama.cpp, OpenAI)
│   ├── observability/             # 指标 / 追踪
│   ├── platform/                  # 平台适配器
│   │   ├── maibot_bridge.py       # Live Hub WebSocket 客户端
│   │   ├── bilibili.py            # Bilibili 直连适配器
│   │   ├── twitch.py              # Twitch 适配器
│   │   └── discord.py             # Discord 适配器
│   ├── session/                   # 会话管理
│   ├── threads/                   # 线程并发
│   ├── utils/                     # 工具函数
│   └── vision/                    # 桌面截图
│
├── live_hub/                      # 独立弹幕采集中心
│   ├── src/live_hub/              # Hub Web + WebSocket 服务
│   ├── plugins/bilibili_live_adapter/  # B站直播适配器
│   ├── config/live_hub.toml       # Hub 配置
│   ├── start_live_hub.ps1         # Hub 启动脚本
│   └── start_live_hub_window.cmd  # Hub 窗口启动
│
├── gui/                           # Electron + React 前端
├── data/                          # 运行时数据 (gitignored)
│   ├── memory/                    # 观众记忆数据库
│   ├── sessions/                  # 会话快照
│   ├── events/                    # 事件日志
│   ├── recordings/                # 直播录制
│   └── training/                  # S1 微调样本
│
├── docs/                          # 文档
├── tests/                         # 测试
├── scripts/                       # 工具脚本
├── config.yaml                    # 主配置
├── run_live.py                    # 统一入口
├── start_live.bat                 # Windows 一键启动
├── start_live.ps1                 # PowerShell 全链路启动
└── .env.example                   # API Key 模板
```

---

## 快速开始

### 环境要求

- Python 3.10+ with `pip`
- Node.js 18+ (GUI)
- [llama.cpp](https://github.com/ggerganov/llama.cpp) (S1 MiniCPM)
- DeepSeek API Key (S2)
- Bilibili 直播房间 (可选)

### 安装

```bash
# 1. 克隆
git clone https://github.com/OnlyAnIronFW/OpenNeuro.git
cd OpenNeuro

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API Key
cp .env.example .env
# 编辑 .env: 填入 DEEPSEEK_API_KEY

# 4. 安装 GUI 依赖
cd gui && npm install && cd ..
```

### 配置人设

编辑 `src/prompts/templates/persona_core.md`，修改主播名字、性格、说话风格。
或通过 GUI 的人设编辑器 (`http://127.0.0.1:9071`) 在线编辑，支持热重载。

### 启动

```bash
# Windows: 一键启动 (Live Hub + GUI + AI Streamer)
.\start_live.bat

# PowerShell: 全链路 (Hub + MiniCPM + GUI + B站直连)
.\start_live.ps1

# 手动启动
python run_live.py --platform bilibili    # B站直连
python run_live.py --platform maibot      # 通过 Live Hub
```

---

## 配置

### `config.yaml` 主配置

```yaml
s1:                    # S1 模型 (MiniCPM)
  model_path: ...
  llama_host: "http://127.0.0.1:9060"
s2:                    # S2 模型 (DeepSeek)
  api_key: "${DEEPSEEK_API_KEY}"
  model: "deepseek-chat"

s1_decision:           # S1 决策阈值
  min_confidence: 0.55
  max_per_10s: 3

threads:               # 并发控制
  message_queue: ...
  ai_processing: ...

memory:                # 记忆配置
  l2_max_messages: 50
  l3_enabled: true

platforms:             # 平台适配
  bilibili:
    room_id: 4538234
    cookie_file: "data/bili_cookie.json"
```

### 环境变量 (`.env`)

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key (S2 必需) |
| `SILICONFLOW_API_KEY` | 硅基流动 Key (Embedder，可选) |

---

## Live Hub

独立运行的 Bilibili 弹幕采集中心，支持多实例同时消费同一直播间弹幕流。

### 启动 Hub

```powershell
cd live_hub
.\start_live_hub.ps1
```

- **WebSocket**: `ws://127.0.0.1:18190/ws`
- **HTTP API**: `http://127.0.0.1:18190/api/health`
- **Web UI**: `http://127.0.0.1:18190/` — 实时弹幕流 + 本地注入

### 连接模式

```
模式 A: Live Hub 中间层
  B站 → Hub(:18190) → OpenNeuro (maibot_bridge.py)
  优势: 多实例共享, Web UI, 本地注入

模式 B: B站直连
  B站 WebSocket → OpenNeuro (bilibili.py)
  优势: 零额外进程, 低延迟
```

---

## 人设系统

唯一人设源：**`src/prompts/templates/persona_core.md`**

三层提取：
- `@s1` — S1 快速决策使用的精简人设
- `@s2` — S2 深度生成使用的完整人设
- `@both` — 两层共用

支持热重载：修改文件后通过 GUI 人设编辑器或 API `POST /api/persona/reload` 即可。

示例：当前默认角色为 **NewRoad（新露）** — 嘴损但心不坏的 AI 游戏主播。

## 情感系统

VAD 三维模型（Valence-Arousal-Dominance），20 种事件触发器：

| 事件 | 效价 | 唤醒度 | 支配感 |
|------|------|--------|--------|
| 大礼物 | +0.30 | +0.30 | 0 |
| 胜利 | +0.40 | +0.50 | +0.30 |
| 死亡 | -0.10 | +0.20 | -0.10 |
| 冷场5分钟 | -0.10 | -0.08 | -0.05 |

情绪影响 S1 发言阈值（开心→多说，沮丧→少说）和 S2 回复风格。

## 记忆系统

| 层级 | 类型 | 容量 | 存储 |
|------|------|------|------|
| L1 工作记忆 | 滑动窗口 | 最近 N 条消息 | 内存 |
| L2 短期记忆 | 会话范围 | 最多 50 条 | 会话 JSON |
| L3 长期记忆 | 知识图谱 | 无上限 | Kuzu (Graphiti) |

---

## 开发

### 运行测试

```bash
pytest tests/
```

### 查看 GUI

```bash
python -m uvicorn src.gui_server:app --host 127.0.0.1 --port 9071
# 打开 http://127.0.0.1:9071 或启动 Electron 前端
```

### 提交前检查

- `data/` 目录及其运行时数据已被 `.gitignore` 排除
- 本地配置文件 (`live_hub/plugins/*/config.toml`) 不提交——请参照 `.example` 文件自行配置
- API Key 仅存在 `.env`，不提交

---

## License

MIT

</details>

<details>
<summary><b>🇬🇧 English</b> | <i>Click for Chinese ▼</i></summary>

# OpenNeuro — AI Game Streamer

> *A dual-model (S1 MiniCPM / S2 DeepSeek) AI livestream bot — real-time danmaku capture, VAD-driven emotions, and a full persona system. Inspired by Neuro-sama.*

## Overview

OpenNeuro is an AI streaming bot designed for platforms like Bilibili. It captures live chat in real time, uses a dual-model architecture for fast decision-making and deep response generation, and simulates the personality of a human streamer.

### Core Capabilities

- **Real-time danmaku capture** — via built-in Bilibili WebSocket or Live Hub
- **Dual-model architecture** — S1 (MiniCPM-o 4.5) for fast decisions + S2 (DeepSeek) for deep generation
- **VAD emotion system** — 3D valence-arousal-dominance with 20+ triggers and natural decay
- **Layered prompt system** — Rules + Persona + Dynamic context, hot-reloadable
- **Multi-tier memory** — L1 working / L2 short-term / L3 long-term (Graphiti knowledge graph)
- **Electron GUI** — Visual persona editor, log viewer, config manager

## Quick Start

```bash
# Clone & install
git clone https://github.com/OnlyAnIronFW/OpenNeuro.git
cd OpenNeuro
pip install -r requirements.txt
cp .env.example .env  # edit DEEPSEEK_API_KEY

# Launch
.\start_live.bat       # Windows: Hub + GUI + AI
python run_live.py --platform bilibili  # Direct Bilibili connection
```

## Architecture

```
Danmaku → [Live Hub :18190 | Bilibili direct] → Unified Queue
           ↓
    S1 (MiniCPM): reply/don't/interrupt → direction + confidence
           ↓
    S2 (DeepSeek): persona-driven reply (1-3 sentences, spoken style)
           ↓
    EmotionalState (VAD) ↔ PromptAssembler ↔ MemoryManager (L1→L2→L3)
           ↓
    GUI Server (Electron + React)
```

## Directory Structure

```
OpenNeuro/
├── src/
│   ├── prompts/templates/    # Persona core + S1/S2 rules
│   ├── emotion/              # VAD emotion model
│   ├── memory/               # L1/L2/L3 memory system
│   ├── platform/             # Platform adapters (Bilibili, Twitch, Discord)
│   ├── content/              # Cold-start content system
│   ├── iteration/            # Self-iteration pipeline
│   ├── models/               # Model clients (llama.cpp, OpenAI)
│   ├── session/              # Session management
│   └── vision/               # Desktop screenshot capture
├── live_hub/                 # Independent danmaku hub
│   ├── src/live_hub/         # Hub Web + WebSocket server
│   ├── plugins/              # Bilibili live adapter plugin
│   ├── config/               # Hub configuration
│   └── start_live_hub.ps1    # Hub launcher
├── gui/                      # Electron + React frontend
├── data/                     # Runtime data (gitignored)
├── config.yaml               # Main configuration
├── run_live.py               # Unified entry point
└── .env.example              # API key template
```

## Live Hub

A standalone Bilibili danmaku hub supporting multi-instance consumption of the same live room's chat stream.

```powershell
cd live_hub
.\start_live_hub.ps1
```

- **WebSocket**: `ws://127.0.0.1:18190/ws`
- **HTTP API**: `http://127.0.0.1:18190/api/health`
- **Web UI**: `http://127.0.0.1:18190/`

### Connection Modes

```
Mode A: Live Hub relay
  Bilibili → Hub(:18190) → OpenNeuro
  Pros: Multi-instance, Web UI, local inject

Mode B: Direct Bilibili
  Bilibili WebSocket → OpenNeuro
  Pros: Zero extra processes, low latency
```

## Persona System

Single source of truth: **`src/prompts/templates/persona_core.md`**

Three-tier extraction (`@s1`, `@s2`, `@both` tags). Hot-reloadable via GUI or API. Default persona: **NewRoad** — a sharp-tongued but warm-hearted AI game streamer.

## Emotion System

VAD 3D model (Valence-Arousal-Dominance) with 20 event triggers. Emotions influence S1 speak threshold and S2 reply style.

## Memory System

| Tier | Type | Capacity | Storage |
|------|------|----------|---------|
| L1 Working | Sliding window | Recent N msgs | RAM |
| L2 Short | Session-scoped | Up to 50 | Session JSON |
| L3 Long | Knowledge graph | Unlimited | Kuzu (Graphiti) |

## Development

```bash
pytest tests/
python -m uvicorn src.gui_server:app --host 127.0.0.1 --port 9071
```

## License

MIT

</details>
