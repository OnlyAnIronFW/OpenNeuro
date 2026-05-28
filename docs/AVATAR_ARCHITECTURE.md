# VRM 3D 虚拟形象系统 — 架构与路线图

> 最后更新: 2026-05-28
> 状态: Phase 1 — 基础骨架
> 灵感来源: [SoulLink_Live2D](https://github.com/nanlingyin/SoulLink_Live2D) — LLM驱动的 Live2D 表情控制

---

## 目录

1. [架构总览](#1-架构总览)
2. [设计哲学](#2-设计哲学)
3. [SoulLink 设计模式映射](#3-soullink-设计模式映射)
4. [模块地图](#4-模块地图)
5. [WebSocket 协议规范](#5-websocket-协议规范)
6. [情绪 → 表情管线](#6-情绪--表情管线)
7. [分阶段开发路线图](#7-分阶段开发路线图)
8. [技术参考](#8-技术参考)

---

## 1. 架构总览

OpenNeuro 的 VRM 虚拟形象系统是一套从情绪状态到 3D 面部表情的完整管线。后端（Python）负责情绪感知与 LLM 驱动的表情生成，通过 WebSocket 实时推送 52 个 ARKit BlendShape 权重到前端（浏览器 Three.js + @pixiv/three-vrm），前端负责渲染、缓动、物理模拟与 OBS 透明捕获。

### 1.1 系统架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                        OpenNeuro Python Backend                    │
│                                                                    │
│  ┌─────────────────────┐    ┌──────────────────────────────────┐  │
│  │   AIStreamer 主循环   │    │     AvatarBridge (WS Server)     │  │
│  │   ────────────────   │    │     ═══════════════════════════  │  │
│  │   S1 决策 → S2 生成   │    │     • ws://127.0.0.1:9072/ws/vrm│  │
│  │   EmotionalState(VAD)│───▶│     • 心跳保活 10s interval       │  │
│  │   事件总线订阅        │    │     • 并发: asyncio.gather       │  │
│  └─────────────────────┘    └──────────────┬───────────────────┘  │
│                                            │                       │
│  ┌──────────────────────┐   ┌──────────────▼───────────────────┐  │
│  │  ExpressionGenerator  │   │      MotionPlanner (2-phase)      │  │
│  │  ════════════════════ │   │      ═══════════════════════════  │  │
│  │  • LLM 驱动表情生成    │   │      Phase 1: Plan (规划关键帧)    │  │
│  │  • 52 BlendShape 全量  │   │      Phase 2: Generate (插值帧)   │  │
│  │  • 避免单调重复 Prompt  │   │      • EaseInOutCubic 曲线       │  │
│  │  • 必设所有键值         │   │      • requestAnimationFrame 驱动 │  │
│  └──────────────────────┘   └──────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────────────┐   ┌──────────────────────────────────┐  │
│  │   EmotionMapper       │   │      ProfileLoader               │  │
│  │   ═══════════════════ │   │      ═══════════════════════════ │  │
│  │   • VAD→BlendShape    │   │      • VRM 模型能力档案           │  │
│  │   • 本地快速回退       │   │      • 每模型独立配置              │  │
│  │   • 绕过 LLM, <5ms    │   │      • model_prompt.txt 模式      │  │
│  └──────────────────────┘   └──────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────────────┐   ┌──────────────────────────────────┐  │
│  │   ExpressionCache     │   │      Presets (8 表情)             │  │
│  │   ═══════════════════ │   │      ═══════════════════════════ │  │
│  │   • LRU 缓存          │   │      joy / anger / sorrow /       │  │
│  │   • (VAD, context)    │   │      surprise / fear / disgust /  │  │
│  │     → BlendShape      │   │      neutral / smug              │  │
│  └──────────────────────┘   └──────────────────────────────────┘  │
└─────────────────────────────────┬────────────────────────────────┘
                                  │  WebSocket
                                  │  (JSON)
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                 Browser (Three.js + @pixiv/three-vrm)              │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                    overlay.html                                │ │
│  │                                                               │ │
│  │  ┌───────────────────┐  ┌──────────────────────────────────┐ │ │
│  │  │  VRM Model Loader  │  │  ExpressionProxy (BlendShape)     │ │ │
│  │  │  ════════════════  │  │  ═══════════════════════════════  │ │ │
│  │  │  • @pixiv/three-vrm│  │  • 52 ARKit BlendShape Keys       │ │ │
│  │  │  • GLTFLoader      │  │  • EaseInOutCubic 缓动             │ │ │
│  │  │  • .vrm 文件加载    │  │  • 表达式后自动回 idle             │ │ │
│  │  └───────────────────┘  │  • 权重放大器 (1.25x)               │ │ │
│  │                         └──────────────────────────────────┘ │ │
│  │  ┌───────────────────┐  ┌──────────────────────────────────┐ │ │
│  │  │  SpringBone Physics│  │  Renderer (WebGLRenderer)         │ │ │
│  │  │  ════════════════  │  │  ═══════════════════════════════  │ │ │
│  │  │  • 头发/衣物物理    │  │  • 透明背景 (alpha: true)         │ │ │
│  │  │  • 重力模拟         │  │  • premultipliedAlpha             │ │ │
│  │  └───────────────────┘  │  • OBS 窗口捕获兼容                │ │ │
│  │                         └──────────────────────────────────┘ │ │
│  │  ┌──────────────────────────────────────────────────────────┐ │ │
│  │  │  WebSocket Client (reconnecting)                          │ │ │
│  │  │  • 自动重连 + 指数退避                                     │ │ │
│  │  │  • 消息队列 (帧同步缓冲区)                                  │ │ │
│  │  └──────────────────────────────────────────────────────────┘ │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 数据流概览

```
EmotionalState (VAD 三维)
        │
        ├──► EmotionMapper ──► 本地 BlendShape (快速, <5ms)
        │                           │
        │                           ▼
        │                     AvatarBridge.send_expression()
        │                           │
        ▼                           ▼
AIStreamer (S2 回复文本)     WebSocket → Browser
        │                           │
        ▼                           ▼
ExpressionGenerator (LLM)    VRM ExpressionProxy
        │                    (52 BlendShape keys)
        ▼                           │
  BlendShape JSON                  ▼
  { browsDown_L: 0.2,         EaseInOutCubic 缓动
    mouthSmile: 0.8, ... }          │
        │                           ▼
        └────► AvatarBridge ──► 面部渲染完成
                                  │
                          auto_reset_ms 后
                                  │
                                  ▼
                          平滑回到 neutral
```

---

## 2. 设计哲学

### 2.1 核心原则

1. **情绪驱动表情，不是文本驱动** — VAD 三维模型是表情生成的主信号，文本仅提供上下文微调
2. **LLM 做创意，规则做保障** — LLM 负责生成丰富多变的表情序列，本地 VAD fallback 保证永远有表情可用
3. **权重全量输出** — 强制 LLM 设置全部 52 个 BlendShape 键值，避免遗漏导致表情残缺
4. **缓动在客户端** — 后端发送"目标状态 + 时长"，客户端负责 EaseInOutCubic 插值，减少帧同步开销
5. **表达式 = 瞬态** — 所有表情都有明确的生命周期 (duration_ms)，到期自动回 neutral，不留残影

### 2.2 与 SoulLink 的关系

本系统直接借鉴 SoulLink 的 LLM 表情控制哲学，但将目标从 Live2D (Cubism) 迁移到 VRM (ARKit BlendShape)。SoulLink 用 LLM 理解对话上下文并生成 Live2D 参数，我们改为生成 52 个 ARKit BlendShape 权重。核心差异：

| 维度 | SoulLink (Live2D) | OpenNeuro (VRM) |
|------|-------------------|-----------------|
| 输出参数 | Cubism 参数 (Angle X/Y/Z, Body, Mouth, ...) | 52 ARKit BlendShape (browsDown_L, mouthSmile, ...) |
| 渲染引擎 | Cubism SDK (Native) | @pixiv/three-vrm (WebGL) |
| 物理模拟 | Cubism Physics | VRM SpringBone |
| 模型格式 | .moc3 + .model3.json | .vrm (glTF-based) |
| 平台 | Windows 原生 | 跨平台 (浏览器) |
| OBS 捕获 | Spout2 / 窗口捕获 | 浏览器窗口透明捕获 |

---

## 3. SoulLink 设计模式映射

以下 10 个 SoulLink 核心设计模式已完整映射到 VRM 等效实现：

| # | SoulLink 模式 | VRM 等效实现 | 对应文件 |
|---|--------------|-------------|---------|
| 1 | LLM 接收完整参数列表 → 生成 JSON | LLM 接收 52 ARKit BlendShape 全量列表 → 生成权重 JSON | `expression_llm.py` |
| 2 | 两阶段 TTS 动作规划 (Plan → Generate) | 两阶段 BlendShape 序列生成 (关键帧规划 → 插值填充) | `motion_planner.py` |
| 3 | 每模型独立 model_prompt.txt | 每虚拟形象独立 avatar_prompt.txt (表现力偏好 + BlendShape 倾向) | `profile_loader.py` |
| 4 | EaseInOutCubic + requestAnimationFrame | 完全相同缓动曲线，作用于 VRM ExpressionProxy 权重过渡 | `overlay.html` |
| 5 | generatedMotionLocks Set (状态协调) | generatedExpressionLocks Set：防止 idle 循环与 speaking 表情冲突 | `bridge.py` |
| 6 | Joint 动作幅度 1.25x 增强 | BlendShape 权重放大器 (默认 1.25x)：增强表情感知度 | `expression_llm.py` |
| 7 | 动作后自动回 neutral | 表达式 duration_ms 到期后自动平滑回归 idle (默认 blend duration) | `overlay.html` |
| 8 | Prompt 强制全参数输出 | "你必须设置 ALL 52 BlendShape keys，不得省略任何键" — Prompt 工程 | `expression_llm.py` |
| 9 | 显式多样性提示 ("避免单调重复") | 相同语义注入：记录最近 5 次表情，Prompt 中要求差异化 | `expression_llm.py` |
| 10 | asyncio.gather 并发聊天 + 动作 | asyncio.gather 并发 S2 回复生成 + 表达式生成 + WebSocket 推送 | `bridge.py` |

### 3.1 模式详解

**模式 1 — 全参数输出**: SoulLink 的 LLM prompt 要求生成全部 Cubism 参数，我们同样要求 LLM 输出完整的 52 个 ARKit 键：

```
你必须为以下52个ARKit BlendShape键设置权重值（0-1）：
eyeBlinkLeft, eyeBlinkRight, eyeLookDownLeft, eyeLookDownRight,
eyeLookInLeft, eyeLookInRight, eyeLookOutLeft, eyeLookOutRight,
eyeLookUpLeft, eyeLookUpRight, eyeSquintLeft, eyeSquintRight,
eyeWideLeft, eyeWideRight, browDownLeft, browDownRight,
browInnerUp, browOuterUpLeft, browOuterUpRight, jawForward,
jawLeft, jawRight, jawOpen, mouthClose, mouthFunnel,
mouthPucker, mouthLeft, mouthRight, mouthSmileLeft,
mouthSmileRight, mouthFrownLeft, mouthFrownRight,
mouthDimpleLeft, mouthDimpleRight, mouthStretchLeft,
mouthStretchRight, mouthRollLower, mouthRollUpper,
mouthShrugLower, mouthShrugUpper, mouthPressLeft,
mouthPressRight, mouthLowerDownLeft, mouthLowerDownRight,
mouthUpperUpLeft, mouthUpperUpRight, cheekPuff,
cheekSquintLeft, cheekSquintRight, noseSneerLeft,
noseSneerRight, tongueOut

请输出JSON，不要省略任何键。
```

**模式 4 — 缓动曲线**: 前端使用与 SoulLink 完全一致的 `easeInOutCubic(t) = t < 0.5 ? 4*t³ : 1 - (-2t+2)³/2` 缓动函数，确保从"当前权重"到"目标权重"的过渡在视觉上平滑自然。

**模式 8 — 锁集合**: `generatedExpressionLocks` 是一个 `Set<string>`，记录当前活跃的非 idle 表情 ID。Idle 循环在渲染前检查 `locks.size === 0`，若有说话表情则跳过 idle，确保两层动画不冲突。

---

## 4. 模块地图

### 4.1 模块清单

```
src/avatar/
├── __init__.py              # 包入口, 公共API导出
├── emotion_mapper.py        # VAD→BlendShape 本地映射 (无LLM)
├── expression_llm.py        # LLM驱动的上下文感知表情生成
├── motion_planner.py        # 两阶段TTS动作规划 (Plan→Generate)
├── profile_loader.py        # VRM模型能力档案加载与验证
├── cache.py                 # LRU表情缓存 (VAD三元组→BlendShape)
├── presets.py               # 8个本地预设表情 + 强度调节
├── bridge.py                # WebSocket服务器 + 协议实现
└── overlay.html             # 浏览器端VRM渲染器 (Three.js)
```

### 4.2 各模块职责

#### `emotion_mapper.py` — VAD→BlendShape 本地回退

将三维 VAD 向量直接映射为 BlendShape 权重表，完全在本地计算，延迟 <5ms，不依赖 LLM。

**映射逻辑**:
- `valence > 0` → mouthSmile + cheekSquint (微笑系列)
- `valence < 0` → mouthFrown + browDown (皱眉系列)
- `arousal > 0.6` → eyeWide + browOuterUp (警觉/激动)
- `arousal < 0.15` → eyeSquint + browInnerUp (困倦/无聊)

**接口**: `vad_to_blendshapes(v: float, a: float, d: float) -> dict[str, float]`

**使用场景**:
1. LLM API 超时或不可用时的降级路径
2. 高频游戏事件 (死亡/击杀/掉落) 需要即时表情反馈, 等待 LLM 太慢
3. 所有表情请求的第一层快速响应, 随后 LLM 结果到达后再覆盖

#### `expression_llm.py` — LLM 表情生成器

核心模块。将 S2 回复文本 + 当前情绪 + 对话上下文送入 LLM，通过精心设计的 Prompt 让 LLM 理解语义并生成对应的 BlendShape 权重。

**设计要点**:
- Prompt 包含完整的 52 个 ARKit BlendShape 列表，强制 LLM 全量输出
- 注入 "避免单调重复" 约束，记录最近 N 次表情历史避免千篇一律
- 支持 per-avatar prompt 覆盖 (从 ProfileLoader 加载)
- BlendShape 权重放大系数 (默认 1.25x) 保证即使在视频压缩后表情依然可读
- 与 S2 文本生成并发执行 (asyncio.gather)，不增加端到端延迟

**接口**:
```python
class ExpressionGenerator:
    async def generate(self, text: str, emotion: EmotionalState,
                       context: dict) -> dict[str, float]
```

#### `motion_planner.py` — 两阶段 TTS 动作规划

实现 SoulLink 同款两阶段流程：

1. **Phase 1 (Plan)**: 分析 TTS 音频时长 + 文本内容，规划关键帧 (时间戳 → BlendShape 快照)
2. **Phase 2 (Generate)**: 在关键帧之间做 EaseInOutCubic 插值，生成 60fps 帧序列

**接口**:
```python
class MotionPlanner:
    def plan(self, text: str, audio_duration_ms: int) -> list[Keyframe]
    def generate(self, keyframes: list[Keyframe]) -> list[FrameData]
```

#### `profile_loader.py` — VRM 模型能力档案

每个 VRM 模型的 BlendShape 支持程度不同 (有些模型只有基础表情，有些有完整 52 个)。Profile 记录模型的能力边界和个性化配置。

**AvatarProfile 数据结构**:
- `model_name`: 模型名称
- `supported_blendshapes`: 支持的 BlendShape 键列表
- `default_blendshapes`: 默认 neutral 状态权重
- `idle_animations`: idle 循环动画序列 (呼吸、眨眼)
- `boost_multiplier`: 表情放大系数 (部分模型需要更高倍率)
- `personality_prompt`: 注入到 LLM prompt 的模型特定人格描述
- `avatar_prompt_path`: per-avatar 自定义 prompt 文件路径

**接口**: `load_avatar_profile(model_path: str) -> AvatarProfile`

#### `cache.py` — LRU 表情缓存

对高频情绪状态 (如"收到礼物时的开心") 做 (v, a, d) 三元组粒度的缓存，避免重复调用 LLM。

- 容量: 默认 200 条
- 淘汰策略: LRU
- 键: `(int(v*10), int(a*10), int(d*10))` 量化后的 VAD 三元组
- 过期: 无 (情绪是主观的，同样的 VAD 在 5 秒后仍适用)

**接口**: `ExpressionCache.get(vad: tuple) -> dict | None` / `.set(vad: tuple, blendshapes: dict)`

#### `presets.py` — 本地预设表情

8 个预定义 BlendShape 快照，不依赖 LLM，响应速度 <1ms。主要用于快速回应 (被击杀→立即"sorrow"表情，打出五杀→"surprise")。

| 预设 | BlendShape 特征 | 适用场景 |
|------|----------------|---------|
| `joy` | mouthSmile 0.8, cheekSquint 0.5, eyeSquint 0.3 | 收到礼物、观众夸赞 |
| `anger` | browDown 0.7, mouthFrown 0.5, jawOpen 0.1 | 连败、被嘲讽 |
| `sorrow` | browInnerUp 0.6, mouthFrown 0.3, eyeSquint 0.2 | 角色死亡、冷场 |
| `surprise` | eyeWide 0.8, browOuterUp 0.6, jawOpen 0.3 | 稀有掉落、意外事件 |
| `fear` | eyeWide 0.6, browInnerUp 0.4, mouthStretch 0.2 | BOSS 战、跳脸杀 |
| `disgust` | noseSneer 0.5, mouthFrown 0.4, browDown 0.2 | 遇到脏东西、弹幕恶心 |
| `neutral` | 全部 0 (idle) | 默认状态、自动回退 |
| `smug` | mouthSmileLeft 0.4, browOuterUpLeft 0.3 | 嘲讽、得意 |

每个预设支持 `intensity` 参数 (0.0~1.0)，线性缩放所有权重。

#### `bridge.py` — WebSocket 服务器 + 协议

avatar 子系统的对外接口。在 `:9072` 端口启动 WebSocket 服务，路径 `/ws/vrm`。所有表情、TTS 动作、预设指令均通过此模块编码为 JSON 并推送。

**核心职责**:
- 维护活跃 WebSocket 连接
- 心跳保活 (10s 间隔)
- 消息序列化与帧同步
- `generatedExpressionLocks` 状态管理 (防止 idle/speaking 冲突)
- 降级处理 (无连接时不报错，仅跳过推送)

#### `overlay.html` — 浏览器 VRM 渲染器

独立的 HTML 文件，通过 Three.js + @pixiv/three-vrm 加载 .vrm 模型并渲染。

**技术栈**:
- Three.js r160+ (WebGL 2.0 渲染)
- @pixiv/three-vrm 2.x (VRM 解析 + ExpressionProxy)
- Vanilla JS (无框架依赖，保持最小体积)

**核心功能**:
- `.vrm` 文件加载 (GLTFLoader)
- 52 ARKit BlendShape 权重应用
- EaseInOutCubic 缓动 (当前权重 → 目标权重)
- 自动回 neutral (auto_reset_ms 计时器)
- SpringBone 物理更新 (头发/衣物/尾巴)
- 透明背景 + premultipliedAlpha (OBS 窗口捕获)
- WebSocket 自动重连 (指数退避)

---

## 5. WebSocket 协议规范

### 5.1 连接信息

| 属性 | 值 |
|------|-----|
| 协议 | WS (非加密，本地通信) |
| 地址 | `ws://127.0.0.1:9072/ws/vrm` |
| 心跳间隔 | 10s |
| 心跳超时 | 30s (超时断开) |
| 消息格式 | JSON (UTF-8) |
| 编码 | `json.dumps(ensure_ascii=False)` |

### 5.2 消息格式通用字段

所有消息共享以下外层结构：

```json
{
  "type": "<消息类型>",
  "seq": 0,
  "timestamp": 1715617800000,
  "payload": {}
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 消息类型 (见下表) |
| `seq` | int | 单调递增序号，用于帧排序和丢帧检测 |
| `timestamp` | int | Unix 毫秒时间戳 |
| `payload` | object | 消息特定内容 |

### 5.3 消息类型

#### 5.3.1 `expression` — 表情指令

LLM 或 VAD fallback 生成的完整 BlendShape 权重表。

```json
{
  "type": "expression",
  "seq": 42,
  "timestamp": 1715617800000,
  "payload": {
    "expression_id": "expr_a1b2c3",
    "source": "llm",
    "blendshapes": {
      "browDownLeft": 0.0,
      "browDownRight": 0.0,
      "browInnerUp": 0.15,
      "browOuterUpLeft": 0.1,
      "browOuterUpRight": 0.1,
      "cheekPuff": 0.0,
      "cheekSquintLeft": 0.25,
      "cheekSquintRight": 0.25,
      "eyeBlinkLeft": 0.0,
      "eyeBlinkRight": 0.0,
      "eyeLookDownLeft": 0.0,
      "eyeLookDownRight": 0.0,
      "eyeLookInLeft": 0.0,
      "eyeLookInRight": 0.0,
      "eyeLookOutLeft": 0.0,
      "eyeLookOutRight": 0.0,
      "eyeLookUpLeft": 0.0,
      "eyeLookUpRight": 0.0,
      "eyeSquintLeft": 0.15,
      "eyeSquintRight": 0.15,
      "eyeWideLeft": 0.0,
      "eyeWideRight": 0.0,
      "jawForward": 0.0,
      "jawLeft": 0.0,
      "jawOpen": 0.1,
      "jawRight": 0.0,
      "mouthClose": 0.0,
      "mouthDimpleLeft": 0.1,
      "mouthDimpleRight": 0.1,
      "mouthFrownLeft": 0.0,
      "mouthFrownRight": 0.0,
      "mouthFunnel": 0.0,
      "mouthLeft": 0.0,
      "mouthLowerDownLeft": 0.0,
      "mouthLowerDownRight": 0.0,
      "mouthPressLeft": 0.0,
      "mouthPressRight": 0.0,
      "mouthPucker": 0.0,
      "mouthRight": 0.0,
      "mouthRollLower": 0.0,
      "mouthRollUpper": 0.0,
      "mouthShrugLower": 0.0,
      "mouthShrugUpper": 0.0,
      "mouthSmileLeft": 0.4,
      "mouthSmileRight": 0.4,
      "mouthStretchLeft": 0.0,
      "mouthStretchRight": 0.0,
      "mouthUpperUpLeft": 0.0,
      "mouthUpperUpRight": 0.0,
      "noseSneerLeft": 0.0,
      "noseSneerRight": 0.0,
      "tongueOut": 0.0
    },
    "duration_ms": 2000,
    "auto_reset_ms": 3000,
    "blend_duration_ms": 500,
    "boost_multiplier": 1.25
  }
}
```

`payload` 字段说明:

| 字段 | 类型 | 说明 |
|------|------|------|
| `expression_id` | string | 唯一标识，用于锁管理 |
| `source` | string | `"llm"` / `"vad"` / `"preset"` |
| `blendshapes` | object | 52 个 ARKit BlendShape 键值对 (0.0~1.0) |
| `duration_ms` | int | 表情保持时长 |
| `auto_reset_ms` | int | 自动回 neutral 的延迟 (从消息收到起算) |
| `blend_duration_ms` | int | 缓动过渡时长 (当前→目标) |
| `boost_multiplier` | float | 权重放大器 (默认 1.25) |

#### 5.3.2 `tts_motion_start` / `tts_motion_frame` / `tts_motion_end` — TTS 口型同步

两阶段动作的帧流：

```json
// 开始信号
{
  "type": "tts_motion_start",
  "payload": {
    "motion_id": "mot_x1y2z3",
    "total_frames": 180,
    "frame_duration_ms": 16,
    "audio_duration_ms": 3000
  }
}

// 逐帧数据 (60fps)
{
  "type": "tts_motion_frame",
  "payload": {
    "motion_id": "mot_x1y2z3",
    "frame_index": 0,
    "blendshapes": {
      "jawOpen": 0.35,
      "mouthSmileLeft": 0.1,
      "mouthSmileRight": 0.1
    }
  }
}

// 结束信号
{
  "type": "tts_motion_end",
  "payload": {
    "motion_id": "mot_x1y2z3",
    "auto_reset_ms": 500
  }
}
```

#### 5.3.3 `preset` — 本地预设表情

```json
{
  "type": "preset",
  "payload": {
    "name": "joy",
    "intensity": 0.8,
    "duration_ms": 2000,
    "auto_reset_ms": 3000
  }
}
```

`name` 可选值: `joy`, `anger`, `sorrow`, `surprise`, `fear`, `disgust`, `neutral`, `smug`

#### 5.3.4 `reset` — 重置到 neutral

```json
{
  "type": "reset",
  "payload": {
    "blend_duration_ms": 300
  }
}
```

清除所有活跃表情锁，平滑过渡到 idle 状态。

#### 5.3.5 `heartbeat` — 心跳

```json
// 请求
{ "type": "ping", "seq": 100 }

// 响应
{ "type": "pong", "seq": 100, "timestamp": 1715617800000 }
```

### 5.4 客户端行为规范

1. **BlendShape 应用**: 收到 `expression` / `preset` / `tts_motion_frame` 后，在 `blend_duration_ms` 内使用 EaseInOutCubic 将当前 BlendShape 权重过渡到目标权重
2. **自动回退**: 收到消息后启动定时器，`auto_reset_ms` 毫秒后平滑回到 neutral (时长 = 下次收到的 `blend_duration_ms`，或默认 300ms)
3. **帧丢失处理**: 检查 `seq` 连续性。丢帧 ≤3 → 线性插值补偿。丢帧 >3 → 重置为 recent 目标值
4. **心跳**: 每 10s 发送 `ping`。30s 无 `pong` → 断开重连
5. **SpringBone**: 每帧更新 (渲染循环内)，与 BlendShape 动画并行

---

## 6. 情绪 → 表情管线

### 6.1 主路径: LLM 驱动 (完整管线)

```
Step 1: AIStreamer 生成 S2 回复
        ┌─────────────────────────────────────┐
        │ S2 回复文本: "来了来了~ 小明今天好早"   │
        │ S1 confidence: 0.88                  │
        │ 当前情绪: valence=0.35, arousal=0.4  │
        └─────────────────┬───────────────────┘
                          │
                          ▼
Step 2: ExpressionGenerator 组装 Prompt
        ┌─────────────────────────────────────┐
        │ System Prompt:                      │
        │   "你是VRM表情设计师..."              │
        │   + 52 ARKit BlendShape 完整列表      │
        │   + 当前情绪描述                      │
        │   + 最近5次表情历史(避免重复)          │
        │   + per-avatar 模型特定指令            │
        │                                     │
        │ User Message:                       │
        │   回复文本 + 对话上下文               │
        └─────────────────┬───────────────────┘
                          │
                          ▼
Step 3: LLM 返回 BlendShape JSON
        ┌─────────────────────────────────────┐
        │ {                                    │
        │   "browInnerUp": 0.15,              │
        │   "cheekSquintLeft": 0.25,          │
        │   "cheekSquintRight": 0.25,         │
        │   "mouthSmileLeft": 0.4,            │
        │   "mouthSmileRight": 0.4,           │
        │   ... 全部52个键                      │
        │ }                                    │
        └─────────────────┬───────────────────┘
                          │
                          ▼
Step 4: AvatarBridge.push_expression()
        ┌─────────────────────────────────────┐
        │ 序列化为 WebSocket 消息                │
        │ 类型: "expression"                   │
        │ seq++                               │
        │ 注入 expression_id, 加入 locks      │
        │ 推送到 /ws/vrm 连接的浏览器           │
        └─────────────────┬───────────────────┘
                          │
                          ▼
Step 5: Browser 接收并渲染
        ┌─────────────────────────────────────┐
        │ overlay.html onMessage()            │
        │                                     │
        │ 1. 解析 JSON → blendshapes object    │
        │ 2. 所有 BlendShape 键应用             │
        │    EaseInOutCubic(current, target,   │
        │                    blend_duration)   │
        │ 3. 启动 auto_reset 定时器             │
        │ 4. 渲染循环继续 (SpringBone + 表情)    │
        └─────────────────┬───────────────────┘
                          │
                          ▼
Step 6: auto_reset_ms 后自动回 neutral
        ┌─────────────────────────────────────┐
        │ 所有 BlendShape → 0.0                │
        │ EaseInOutCubic(当前, 0, 300ms)       │
        │ 清除 expression_id 锁                 │
        │ 恢复 idle 动画 (呼吸 + 眨眼)           │
        └─────────────────────────────────────┘
```

### 6.2 回退路径: VAD 本地映射 (无 LLM)

```
Step 1: 游戏事件触发情绪变化
        ┌─────────────────────────────────────┐
        │ EventBus.publish("game_win")         │
        │   → EmotionalState.trigger("game_win") │
        │   valence += 0.40                    │
        │   arousal += 0.50                    │
        │   dominance += 0.30                  │
        └─────────────────┬───────────────────┘
                          │
                          ▼
Step 2: emotion_mapper.vad_to_blendshapes()
        ┌─────────────────────────────────────┐
        │ 输入: (0.50, 0.75, 0.85)             │
        │                                     │
        │ 映射规则:                             │
        │   valence 0.50 → mouthSmile 0.5      │
        │   arousal 0.75 → eyeWide 0.6         │
        │   dominance 0.85 → browOuterUp 0.3   │
        │                                     │
        │ 输出: {...} 52 键完整权重              │
        │ 延迟: <5ms (纯本地计算)               │
        └─────────────────┬───────────────────┘
                          │
                          ▼
Step 3: 绕过 LLM, 直接推送
        ┌─────────────────────────────────────┐
        │ AvatarBridge.push_expression(        │
        │   source="vad",                     │
        │   duration_ms=1500  (比LLM短)         │
        │ )                                    │
        │                                     │
        │ 如同一时刻 LLM 结果到达 →               │
        │   LLM 结果覆盖 VAD fallback (更丰富)    │
        └─────────────────────────────────────┘
```

### 6.3 并发与覆盖逻辑

```
时间轴:

t=0ms    游戏事件触发 → VAD fallback 立即推送 (source=vad, duration=1500ms)
         AIStreamer 开始生成 S2 回复 + 表情 (LLM)

t=800ms  LLM 返回 BlendShape → 推送 (source=llm, duration=3000ms)
         → 覆盖 VAD fallback 表情
         → 延长 auto_reset 定时器

t=3800ms auto_reset 到期 → 平滑回 neutral
```

覆盖规则:
1. 同 `expression_id` → 替换 (更新锁)
2. 不同 `expression_id` → 覆盖 (VAD → LLM)
3. `source=preset` 从不覆盖 LLM 表情 (优先级低)
4. `reset` 消息清除所有锁, 强制回 neutral

---

## 7. 分阶段开发路线图

### Phase 1 — 基础骨架 (当前)

**目标**: 模块定义完成，配置规范确定，架构文档就绪。

| 任务 | 产出 | 状态 |
|------|------|------|
| `emotion_mapper.py` skeleton + docstring | VAD→BlendShape 映射逻辑框架 | ⬜ |
| `expression_llm.py` skeleton + Prompt 模板 | LLM 表情生成器框架 | ⬜ |
| `motion_planner.py` skeleton | 两阶段动作规划框架 | ⬜ |
| `profile_loader.py` + `AvatarProfile` dataclass | 模型能力档案数据结构 | ⬜ |
| `cache.py` + `ExpressionCache` LRU 实现 | 表情缓存完整实现 | ⬜ |
| `presets.py` + 8 组 BlendShape 快照 | 本地预设表情数据 | ⬜ |
| `bridge.py` WebSocket 服务 skeleton | WS 连接管理框架 | ⬜ |
| `config.yaml` 中增加 `avatar:` 配置段 | AvatarConfig schema | ⬜ |
| `docs/AVATAR_ARCHITECTURE.md` | 本文档 | ✅ |

**AvatarConfig (config.yaml 新增段)**:

```yaml
avatar:
  enabled: false
  ws_host: "127.0.0.1"
  ws_port: 9072
  cache:
    max_size: 200
  expression:
    llm_model: "deepseek-chat"
    llm_timeout_ms: 3000
    boost_multiplier: 1.25
    max_history: 5
    vad_fallback_enabled: true
    vad_fallback_duration_ms: 1500
  profile:
    default_model: "default.vrm"
    profiles_dir: "data/avatar_profiles"
  tts_motion:
    frame_rate: 60
    blend_duration_ms: 500
  bridge:
    heartbeat_interval_ms: 10000
    heartbeat_timeout_ms: 30000
```

### Phase 2 — 静态 VRM 渲染

**目标**: 浏览器能加载 .vrm 模型并进行 OBS 透明捕获，Idle 动画循环运行。

| 任务 | 产出 | 验证方式 |
|------|------|---------|
| `overlay.html` — Three.js + @pixiv/three-vrm 脚手架 | 渲染页面 (Canvas) | `live-server` 打开可见模型 |
| GLTFLoader + .vrm 解析 | 模型加载 | 控制台无报错, 模型在画面中 |
| 透明背景 + premultipliedAlpha | OBS 捕获就绪 | OBS 窗口捕获: 背景透明, 模型清晰 |
| Idle 动画循环 (呼吸 4s 周期 + 随机眨眼) | BlendShape 动画 | 模型持续有呼吸起伏 |
| SpringBone 物理更新 | 头发/衣物摆动 | 拖动窗口 → 头发有惯性摆动 |
| OrbitControls (调试用) | 鼠标旋转/缩放 | 右键拖拽旋转, 滚轮缩放 |

**技术验证清单**:
- [ ] `.vrm` 模型在 Chrome/Edge/Firefox 中均能加载
- [ ] OBS "窗口捕获" → 选择浏览器窗口 → 透明背景无黑边
- [ ] `animationFrame` 稳定 60fps (Chrome DevTools Performance)
- [ ] `premultipliedAlpha: true` + WebGLRenderer alpha 通道正确

### Phase 3 — 表情控制

**目标**: LLM 生成表情 + 本地预设 + VAD fallback 全部可用。

| 任务 | 产出 | 验证方式 |
|------|------|---------|
| `ExpressionGenerator` 完整实现 | LLM → BlendShape JSON | 给定文本, 返回有效 BlendShape 权重 |
| LLM Prompt 模板 (包含 52 键全量列表) | `expression_prompt.txt` | Prompt 经 LLM 后输出包含全部 52 键 |
| `presets.py` 8 组预设 → WebSocket 推送 | 预设表情渲染 | 发送 `{"type":"preset","name":"joy"}` → 模型微笑 |
| VAD fallback 管线打通 | `emotion_mapper` 集成 | `trigger("game_win")` → 模型表情变化 |
| 表情覆盖逻辑 (VAD → LLM) | `ExpressionLocks` 状态机 | VAD 先触发, LLM 后到 → LLM 覆盖 |
| 前端 `EaseInOutCubic` 缓动 | 平滑过渡 | 表情变化无跳变, 过渡流畅 |
| `auto_reset` 自动回 neutral | 表情生命周期 | 3s 后表情自动回 idle |

**LLM Prompt 设计要点**:
- 必须内嵌 52 BlendShape 完整列表，每个键附带中文含义注释
- 示例 JSON 输出 (3 种情绪: 开心/惊讶/沮丧) 作为 few-shot
- 明确约束: "所有键必须出现，权重范围 0.0-1.0"
- 情绪 → BlendShape 映射指南 (valence 高→mouthSmile, arousal 高→eyeWide, ...)

### Phase 4 — 口型同步 + TTS 动作

**目标**: 音频驱动的嘴型动画 + 两阶段动作规划。

| 任务 | 产出 | 验证方式 |
|------|------|---------|
| 音频幅度 → jawOpen 映射 | 实时口型 | 说话时嘴巴张开/闭合与音频同步 |
| `MotionPlanner` Phase 1 (Plan) | 关键帧列表 | 给定文本+时长 → 合理的关键帧规划 |
| `MotionPlanner` Phase 2 (Generate) | 60fps 帧序列 | 180 帧 = 3 秒流畅口型动画 |
| `tts_motion_start/frame/end` 帧流推送 | 帧同步 WebSocket | 浏览器按帧率逐帧渲染 |
| 帧流缓冲区 + 丢帧补偿 | 容错处理 | 模拟丢帧 → 不跳变, 线性插值 |
| 口型动画与表情动画叠加 | 混合渲染 | 说话时嘴动 + 眼眉保持表情 |

**口型抖动注意事项**:
- jawOpen 不宜过高 (>0.8), 否则像脱臼
- 配合 `mouthSmile` / `mouthFrown` 表达说话时的情绪基调
- `mouthPucker` 0-0.3 用于 u/ü 元音更自然
- 建议用音频 RMS 能量 + 低通滤波做平滑, 避免帧间抖动

### Phase 5 — 完全集成

**目标**: Avatar 系统接入 AIStreamer 主循环, 在真实直播中运行。

| 任务 | 产出 | 验证方式 |
|------|------|---------|
| AvatarBridge 订阅 EventBus | `reply.sent` → 触发表情生成 | 模拟弹幕 → S2 回复 → 模型表情变化 |
| `asyncio.gather(S2生成, 表情生成)` | 并发不增加延迟 | S2 延迟对比: 有无表情生成无差异 |
| Profile loader 接入配置 | per-avatar 自定义 prompt | 切换不同 .vrm 模型 → 不同表现力 |
| ExpressionCache 接入 | VAD 三元组缓存 | 同一情绪反复触发 → 第二次命中缓存 |
| Electron GUI 嵌入 overlay.html | Web UI 控制面板 | GUI 中可切换表情/预设/模型 |
| 端到端直播测试 | 完整链路验证 | 开播 → 弹幕 → 回复 → 表情 → OBS 输出 |

**EventBus 集成点**:
- `reply.sent` → `ExpressionGenerator.generate()` → `AvatarBridge.push_expression()`
- `platform.gift.received` → 触发预设 `joy` (快速)
- `visual.event.detected` (BOSS/死亡/胜利) → VAD trigger → VAD fallback 表情
- `session.ended` → `reset` 消息 → 清除所有锁

### Phase 6 — 打磨与优化

**目标**: 性能优化、跨模型兼容、视觉保真度提升。

| 任务 | 产出 | 验证方式 |
|------|------|---------|
| BlendShape 权重放大调优 | 最优 boost_multiplier | 对比 1.0x / 1.25x / 1.5x 主观评分 |
| Per-avatar 自定义 prompt 优化 | 每模型独立表现格 | 不同模型有差异化表情风格 |
| GPU 性能剖析与优化 | 稳定 60fps | Chrome DevTools: GPU memory < 200MB |
| 跨浏览器兼容测试 | Chrome/Edge/Firefox 一致 | 三浏览器均通过 OBS 捕获测试 |
| VRM 1.0 + VRM 0.x 兼容 | 双版本加载 | 分别测试 0.x 和 1.0 格式 .vrm |
| SpringBone 参数调优 | 自然物理摆动 | 重力 + 阻尼参数匹配模型体型 |
| ExpressionCache 命中率提升 | >60% 缓存命中 | 统计高频情绪触发 → 缓存覆盖率 |
| 内存泄漏排查 | 无泄漏运行 24h | Chrome Memory 快照: 无持续增长 |

---

## 8. 技术参考

### 8.1 依赖库

| 库 | 用途 | 链接 |
|---|------|------|
| @pixiv/three-vrm | VRM 解析 + BlendShape ExpressionProxy | https://github.com/pixiv/three-vrm |
| Three.js | WebGL 3D 渲染引擎 | https://threejs.org/ |
| FastAPI (WebSocket) | 后端 WS 服务 | 现有项目依赖 |
| Pydantic | 配置 + 消息 schema 校验 | 现有项目依赖 |

### 8.2 参考项目

| 项目 | 说明 | 链接 |
|------|------|------|
| SoulLink_Live2D | LLM 驱动的 Live2D 表情控制系统 | https://github.com/nanlingyin/SoulLink_Live2D |
| SoulLink 表情原理 | LLM 表情生成 Prompt 工程详解 | https://github.com/nanlingyin/SoulLink_Live2D/blob/main/docs/LLM_EXPRESSION_PRINCIPLE.md |
| VRM 规范 | VRM 文件格式官方文档 | https://vrm.dev/ |
| ARKit BlendShape 参考 | Apple ARKit 52 个面部 BlendShape 说明 | https://developer.apple.com/documentation/arkit/arfaceanchor/blendshapelocation |
| three-vrm 示例 | @pixiv/three-vrm 官方 demo | https://pixiv.github.io/three-vrm/packages/three-vrm/examples/basic.html |

### 8.3 52 ARKit BlendShape 完整列表 (带中文注释)

```
# 眼睛
eyeBlinkLeft         # 左眼眨眼
eyeBlinkRight        # 右眼眨眼
eyeLookDownLeft      # 左眼向下看
eyeLookDownRight     # 右眼向下看
eyeLookInLeft        # 左眼向内看
eyeLookInRight       # 右眼向内看
eyeLookOutLeft       # 左眼向外看
eyeLookOutRight      # 右眼向外看
eyeLookUpLeft        # 左眼向上看
eyeLookUpRight       # 右眼向上看
eyeSquintLeft        # 左眼眯眼
eyeSquintRight       # 右眼眯眼
eyeWideLeft          # 左眼睁大
eyeWideRight         # 右眼睁大

# 眉毛
browDownLeft         # 左眉下压
browDownRight        # 右眉下压
browInnerUp          # 眉头抬起
browOuterUpLeft      # 左眉尾抬起
browOuterUpRight     # 右眉尾抬起

# 下巴
jawForward           # 下巴前伸
jawLeft              # 下巴左移
jawRight             # 下巴右移
jawOpen              # 下巴张开 (张嘴)

# 嘴
mouthClose           # 闭嘴
mouthFunnel          # 漏斗嘴 (发"呜")
mouthPucker          # 噘嘴 (发"u"/"ü")
mouthLeft            # 嘴左歪
mouthRight           # 嘴右歪
mouthSmileLeft       # 左嘴角上扬 (微笑)
mouthSmileRight      # 右嘴角上扬 (微笑)
mouthFrownLeft       # 左嘴角下垂
mouthFrownRight      # 右嘴角下垂
mouthDimpleLeft      # 左酒窝
mouthDimpleRight     # 右酒窝
mouthStretchLeft     # 左嘴角拉伸
mouthStretchRight    # 右嘴角拉伸
mouthRollLower       # 下唇外翻
mouthRollUpper       # 上唇外翻
mouthShrugLower      # 下唇耸肩
mouthShrugUpper      # 上唇耸肩
mouthPressLeft       # 左嘴角抿紧
mouthPressRight      # 右嘴角抿紧
mouthLowerDownLeft   # 左下唇下拉
mouthLowerDownRight  # 右下唇下拉
mouthUpperUpLeft     # 左上唇上提
mouthUpperUpRight    # 右上唇上提

# 脸颊 + 鼻子 + 舌头
cheekPuff            # 脸颊鼓起
cheekSquintLeft      # 左脸颊上提
cheekSquintRight     # 右脸颊上提
noseSneerLeft        # 左鼻翼上提 (厌恶)
noseSneerRight       # 右鼻翼上提 (厌恶)
tongueOut            # 吐舌头
```

### 8.4 关键资源

- VRM 模型资源: [VRoid Hub](https://hub.vroid.com/), [Booth](https://booth.pm/)
- OBS 配置指南: 窗口捕获 → 选择浏览器 → 属性中勾选 "透明度"
- WebGL 透明背景关键配置:
  - `renderer = new THREE.WebGLRenderer({ alpha: true, premultipliedAlpha: true })`
  - `renderer.setClearColor(0x000000, 0)`
  - CSS: `body { background: transparent; margin: 0; }`

---

> **文档版本**: v1.0
> **最后更新**: 2026-05-28
> **下一里程碑**: Phase 2 — 静态 VRM 渲染
