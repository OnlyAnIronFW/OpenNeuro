# Roxy 音色迁移训练 — 脚本说明

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

## 三种方案对比

| 方案 | 训练复杂度 | 效果 | 与 OpenNeuro 集成 | 状态 |
|------|-----------|------|-------------------|------|
| **VoxCPM2** | ⭐ 低 | ⭐⭐⭐ 优秀 | 需新增 TTS 后端 | 推荐 |
| **CosyVoice2 LoRA** | ⭐⭐ 中 | ⭐⭐⭐ 优秀 | MiniCPM-o 原生 | 脚本就绪 |
| **GPT-SoVITS** | ⭐ 低 | ⭐⭐ 良好 | 已集成 `tts_provider.py` | 脚本就绪 |

---

### 方案 1: VoxCPM2 (推荐)

OpenBMB 官方 TTS 模型, 专为音色迁移设计。

```bash
# 安装
pip install voxcpm

# LoRA 微调 (WebUI)
voxcpm lora-ft-webui

# 推理
voxcpm clone --text "こんにちは" --reference-audio ref.wav --output out.wav
```

### 方案 2: CosyVoice2 LoRA (MiniCPM-o 原生)

MiniCPM-o 4.5 的 TTS 模块基于 CosyVoice2。微调后可直接替换原生语音。

```bash
# 1. 预处理
python scripts/prepare_voice_dataset.py --format cosyvoice

# 2. 训练
python scripts/train_voice_roxy.py

# 3. 推理
python scripts/train_voice_roxy.py --inference_only \
  --text "初めまして、ロキシーです。" \
  --checkpoint checkpoints/roxy_cosyvoice/lora_adapter
```

### 方案 3: GPT-SoVITS (已集成, 最轻量)

OpenNeuro 的 `live_hub/plugins/bilibili_live_adapter/tts_provider.py` 已集成 GPT-SoVITS。

```bash
# 1. 预处理
python scripts/prepare_voice_dataset.py --format gpt_sovits

# 2. 训练
python scripts/train_voice_roxy_gpt_sovits.py

# 3. 推理
python scripts/train_voice_roxy_gpt_sovits.py --inference_only \
  --text "初めまして、ロキシーです。"
```

---

## MiniCPM-o 4.5 语音模块架构

```
MiniCPM-o 4.5 (9B total)
├── SigLIP2 ViT          (~400M) — 视觉
├── Whisper-medium       (~300M) — 语音识别
├── Qwen3-8B             (~8B)   — LLM 骨干
├── S3 Speech Decoder    (~300M) — 语音 Token 生成 (CosyVoice2-based)
│   └── Flow-Matching    — 波形合成
└── Configurable Voice   — 推理时音色控制 (无需训练)
```

**推理时音色克隆** (已内置, 无需训练):

```python
from transformers import AutoModel
model = AutoModel.from_pretrained("openbmb/MiniCPM-o-4_5", trust_remote_code=True)
audio_prompt = model.get_sys_prompt(ref_audio="roxy_sample.wav")
```

**训练时音色迁移** (等待 OpenBMB 开源):

OpenBMB Issue #895 给的关键提示 — 训练窗口结构:
```
|| speaker_embedding | text_tokens | audio_tokens | audio_eos ||
```
- `speaker_embedding`: LLM last_hidden_states → MLP → TTS latent
- `text_tokens`: 文本 token embedding
- `audio_tokens`: CosyVoice2 S3 discrete tokens (25 tokens/sec)
- Loss: CE loss on audio tokens only

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `prepare_voice_dataset.py` | 数据集预处理 (→ CosyVoice2 / GPT-SoVITS / JSONL) |
| `train_voice_roxy.py` | CosyVoice2 LoRA 微调 (MiniCPM-o 原生路径) |
| `train_voice_roxy_gpt_sovits.py` | GPT-SoVITS 微调 (已集成路径) |

---

## 使用流程

```bash
# 1. 预处理 → 生成训练格式
python scripts/prepare_voice_dataset.py --format all

# 2. 选择方案训练
python scripts/train_voice_roxy.py          # CosyVoice2
# 或
python scripts/train_voice_roxy_gpt_sovits.py  # GPT-SoVITS

# 3. 推理测试
python scripts/train_voice_roxy.py --inference_only --eval
```

---

## 参考

- [MiniCPM-o 4.5 技术报告](https://arxiv.org/abs/2604.27393)
- [OpenBMB Issue #895 — 语音训练代码未开源说明](https://github.com/OpenBMB/MiniCPM-V/issues/895)
- [VoxCPM2 官方仓库](https://github.com/OpenBMB/VoxCPM)
- [CosyVoice 官方仓库](https://github.com/FunAudioLLM/CosyVoice)
- [GPT-SoVITS 官方仓库](https://github.com/RVC-Boss/GPT-SoVITS)
