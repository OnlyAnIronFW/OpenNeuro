# Roxy 音色迁移训练 — MiniCPM-o 4.5 内置 TTS 微调

## 数据集

| 属性 | 值 |
|------|-----|
| 路径 | `F:\OpenNeuro\dataset\tts\Roxy\` |
| 文件数 | 239 WAV |
| 总时长 | ~20 分钟 |
| 格式 | 32000Hz, 16-bit PCM, Mono |
| 语言 | 日语 (ja) |
| 命名规则 | 文件名 = 文本 |

---

## 方案: 直接微调 MiniCPM-o 4.5 内置 TTS

**不是外挂 CosyVoice2/GPT-SoVITS, 而是直接修改 MiniCPM-o 内部的 MiniCPMTTS 模块。**

### MiniCPM-o 4.5 内部 TTS 架构

```
MiniCPM-o 4.5 (9B total)
├── Qwen3-8B (LLM)           — 8B, 生成文本 + hidden states
├── MiniCPMTTS (~300M)       — Llama-based S3 speech token decoder
│   ├── Llama Decoder         — 自回归生成 S3 离散 tokens (25 tokens/sec)
│   └── MLP Projector         — LLM hidden → TTS latent + speaker embedding
├── Token2wav (stepaudio2)   — S3 tokens → 16kHz WAV (Flow-Matching)
└── Whisper-medium (~300M)   — 音频编码 (语音理解, 微调时不加载)
```

### 微调目标

让 `MiniCPMTTS` 模块学会: 给定 **Roxy 参考音频的 speaker embedding** + **任意文本**, 生成 **Roxy 音色的 S3 speech tokens**。

### 训练流程

```
Roxy WAV 文件
    ├─→ Token2wav.encode() → S3 target tokens (训练标签)
    └─→ Whisper + Projection → speaker embedding (音色条件)

文本
    └─→ LLM tokenize → LLM forward → hidden states → MiniCPMTTS

训练:
    MiniCPMTTS(spk_embed, text_hidden) → predicted S3 tokens
    Loss = CrossEntropy(predicted, target)
    ← 梯度只更新 MiniCPMTTS 的 LoRA 权重, LLM 冻结
```

### 使用流程

```bash
# 1. 预处理
python scripts/prepare_voice_dataset.py --format jsonl

# 2. 训练 (~24GB VRAM for LoRA)
python scripts/train_voice_roxy.py

# 3. 推理测试
python scripts/train_voice_roxy.py --inference \
  --text "初めまして、私の名前はロキシーです。" \
  --checkpoint checkpoints/roxy_tts/checkpoint-final
```

### 配置 (train_voice_roxy.py 内)

```python
lora_r = 16           # LoRA rank
lora_alpha = 32        # LoRA alpha
learning_rate = 5e-5   # 学习率
num_epochs = 30        # 训练轮数 (~20min 数据)
batch_size = 2         # 批大小
bf16 = True            # 混合精度
```

### 训练完成后

微调后的模型替代 MiniCPM-o 的默认语音, 所有 `run_live.py` / `start_live.ps1` 中的 TTS 调用自动使用 Roxy 音色, 无需修改任何代码。

### 文件清单

| 文件 | 说明 |
|------|------|
| `prepare_voice_dataset.py` | 数据集预处理 (→ JSONL for training) |
| `train_voice_roxy.py` | MiniCPM-o 内置 TTS LoRA 微调脚本 |

---

## 关键发现

### OpenBMB Issue #895 — 官方训练代码未开源

> "We have not yet released the training code for speech decoder. We will consider open-sourcing training code in the future."

他们给的 DIY 提示:
```
训练窗口: || speaker_embedding | text_tokens | audio_tokens | audio_eos ||
损失: CE loss on audio tokens only
```

本脚本基于此提示实现, 直接操作 MiniCPM-o 的 `MiniCPMTTS` 模块。

### 模型内部结构 (来自 modeling_minicpmo.py)

```python
# MiniCPMO.__init__
self.tts = MiniCPMTTS(config=self.config.tts_config, audio_tokenizer=None)

# TTS 子模块 (从 model.safetensors.index.json 提取)
tts.model                      # 20层 LlamaModel (768 hidden, 12 heads)
tts.model.layers.{0-19}.*      # Q/K/V/O attention + gate/up/down MLP
tts.emb_code                   # Audio code Embedding (6562, 768)
tts.emb_text                   # Text token Embedding (152064, 768)
tts.head_code                  # Linear(768, 6562) with weight_norm
tts.projector_spk              # LLM hidden(4096) → TTS(768) speaker projector
tts.projector_semantic         # LLM hidden(4096) → TTS(768) semantic projector
tts.audio_tokenizer            # Token2wav (stepaudio2) — 推理时加载, 不训练

# LoRA 目标层
lora_target = [
    "tts.model.layers.*.self_attn.{q,k,v,o}_proj",
    "tts.model.layers.*.mlp.{gate,up,down}_proj",
    "tts.projector_spk",
    "tts.projector_semantic",
    "tts.head_code",
]
```

---

## 参考

- [MiniCPM-o 4.5 技术报告](https://arxiv.org/abs/2604.27393)
- [MiniCPM-o HuggingFace](https://huggingface.co/openbmb/MiniCPM-o-4_5)
- [OpenBMB Issue #895](https://github.com/OpenBMB/MiniCPM-V/issues/895)
- [stepaudio2 Token2wav](https://github.com/stepfun-ai/StepAudio)
