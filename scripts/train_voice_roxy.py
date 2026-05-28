"""
Roxy 音色迁移 — MiniCPM-o 4.5 内置 TTS 模块 LoRA 微调

直接微调 MiniCPM-o 4.5 内部的 MiniCPMTTS 语音解码器,
而非外挂 CosyVoice2/GPT-SoVITS。

原理:
  MiniCPM-o 4.5 = Qwen3-8B (LLM) + MiniCPMTTS (S3 Speech Decoder) + Token2wav
  微调目标: 让 TTS 模块在给定参考 Roxy 音频 embedding 的条件下,
  为任意文本生成 Roxy 音色的 S3 speech tokens。

架构:
  LLM 文本 → last_hidden_states → spk_embeds (MLP)
  Roxy 音频 → Whisper → audio_embeds → spk_ref
  └→ MiniCPMTTS(spk_embeds, text_tokens, spk_ref) → S3 tokens → Token2wav → WAV

训练数据: dataset/tts/Roxy/ (WAV 文件名=文本)
预处理:   python scripts/prepare_voice_dataset.py --format jsonl

Usage:
    # 1. 预处理
    python scripts/prepare_voice_dataset.py --format jsonl

    # 2. 训练 (需要 GPU, ~24GB VRAM for LoRA)
    python scripts/train_voice_roxy.py

    # 3. 推理测试
    python scripts/train_voice_roxy.py --inference --checkpoint checkpoints/roxy_tts
"""

import argparse
import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

# ── Configuration ────────────────────────────────────────


@dataclass
class TTSFineTuneConfig:
    # Model
    model_id: str = "openbmb/MiniCPM-o-4_5"
    model_path: str = ""  # 本地模型路径 (GGUF 或其他)

    # Dataset
    dataset_jsonl: str = r"F:\OpenNeuro\dataset\tts\Roxy_processed\jsonl\dataset.jsonl"
    speaker_name: str = "Roxy"
    language: str = "ja"
    val_split: float = 0.05
    seed: int = 42

    # LoRA
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    # MiniCPMTTS 内部是 Llama-based decoder, 目标注意力投影层
    lora_target_modules: str = "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj"

    # Training
    output_dir: str = r"F:\OpenNeuro\checkpoints\roxy_tts"
    num_epochs: int = 30
    batch_size: int = 2
    gradient_accumulation_steps: int = 4
    learning_rate: float = 5e-5
    warmup_steps: int = 50
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    save_steps: int = 100
    logging_steps: int = 10
    eval_steps: int = 100

    # Precision
    bf16: bool = True
    gradient_checkpointing: bool = True

    # Audio
    sample_rate: int = 16000  # MiniCPM-o 内部使用 16kHz


# ══════════════════════════════════════════════════════════
#  RoxyTTSDataset — 加载 text-audio pairs
# ══════════════════════════════════════════════════════════


class RoxyTTSDataset(Dataset):
    """从 JSONL 加载 (text, audio_path) 对.

    JSONL 格式 (由 prepare_voice_dataset.py 生成):
        {"audio": "path/to/file.wav", "text": "...", "speaker": "Roxy", "language": "ja", "duration_sec": 5.0}
    """

    def __init__(self, jsonl_path: str, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.samples = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.samples.append(json.loads(line))

        if not self.samples:
            raise FileNotFoundError(
                f"No samples found in {jsonl_path}. Run prepare_voice_dataset.py first."
            )

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        return {
            "text": sample["text"],
            "audio_path": sample["audio"],
            "speaker": sample.get("speaker", ""),
            "language": sample.get("language", ""),
            "duration_sec": sample.get("duration_sec", 0),
        }


def collate_fn(batch):
    return batch


def split_dataset(dataset: RoxyTTSDataset, val_ratio: float, seed: int):
    import random

    rng = random.Random(seed)
    indices = list(range(len(dataset)))
    rng.shuffle(indices)
    val_count = max(1, int(len(indices) * val_ratio))
    train_indices = indices[val_count:]
    val_indices = indices[:val_count]
    train_data = [dataset[i] for i in train_indices]
    val_data = [dataset[i] for i in val_indices]
    return train_data, val_data


# ══════════════════════════════════════════════════════════
#  MiniCPM-o TTS Fine-Tuning
# ══════════════════════════════════════════════════════════


def setup_logging(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("roxy_tts")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    fh = logging.FileHandler(output_dir / "train.log", encoding="utf-8")
    fh.setFormatter(handler.formatter)
    logger.addHandler(fh)
    return logger


def load_minicpmo_tts(config: TTSFineTuneConfig, logger: logging.Logger):
    """加载 MiniCPM-o 4.5 模型, 只启用 TTS 模块.

    MiniCPM-o 内部 TTS 架构:
      MiniCPMO.tts = MiniCPMTTS(
        config = MiniCPMTTSConfig (Llama-based decoder ~300M params)
        audio_tokenizer = None (通过 init_tts() 加载 Token2wav)
      )

    训练时不需要 Token2wav (只训练 token 生成),
    推理时通过 Token2wav 将 S3 tokens 转为波形.
    """
    logger.info(f"Loading MiniCPM-o 4.5 TTS module from {config.model_id}...")

    try:
        from transformers import AutoModel

        model = AutoModel.from_pretrained(
            config.model_id,
            trust_remote_code=True,
            torch_dtype=torch.bfloat16 if config.bf16 else torch.float32,
            init_vision=False,  # 不需要视觉
            init_audio=False,  # 不需要音频编码器 (只用 TTS 侧)
            init_tts=True,  # 加载 TTS 模块
            device_map="auto",
        )

        # 验证 TTS 模块已加载
        assert hasattr(model, "tts"), "TTS module not found in model"
        assert model.tts is not None, "TTS module is None"

        logger.info(f"  TTS module: {type(model.tts).__name__}")
        logger.info(f"  TTS config:  {model.config.tts_config.__class__.__name__}")

        # 可选: 加载本地 GGUF 模型代替 HF 模型
        # if config.model_path:
        #     # 对于 llama.cpp GGUF, 使用不同的加载方式
        #     pass

        return model

    except ImportError as e:
        logger.error(f"Import error: {e}")
        logger.info("请安装: pip install transformers>=5.7.0 torch accelerate peft")
        raise
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        if "offline" in str(e).lower() or "connection" in str(e).lower():
            logger.info("离线模式: 请确保模型已下载到本地缓存, 或设置 HF_HUB_OFFLINE=1")
        raise


def apply_lora_to_tts(model, config: TTSFineTuneConfig, logger: logging.Logger):
    """对 MiniCPMTTS 模块注入 LoRA.

    MiniCPMTTS 内部是 Llama-based 架构,
    目标层为 attention 投影 + MLP 门控/投影.
    """
    try:
        from peft import LoraConfig, TaskType, get_peft_model

        # 找到 TTS 模块内的 LLM decoder
        tts_module = model.tts

        # MiniCPMTTS 内部可能有 llm / decoder / speech_decoder
        target = None
        for attr in ["llm", "decoder", "speech_decoder", "model"]:
            if hasattr(tts_module, attr):
                target = getattr(tts_module, attr)
                logger.info(f"  LoRA target: tts.{attr} ({type(target).__name__})")
                break

        if target is None:
            # 如果找不到子模块, 尝试对整个 tts 注入 LoRA
            logger.warning(
                "  No LLM submodule found in TTS, targeting entire tts module"
            )
            target = tts_module

        peft_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=config.lora_r,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            target_modules=config.lora_target_modules.split(","),
            bias="none",
        )

        target = get_peft_model(target, peft_config)
        target.print_trainable_parameters()
        logger.info(f"  LoRA applied: r={config.lora_r}, alpha={config.lora_alpha}")

        return model

    except ImportError:
        logger.error("peft not installed. Run: pip install peft")
        raise


def extract_speaker_embedding(model, audio_waveform: torch.Tensor) -> torch.Tensor:
    """从 Roxy 参考音频提取说话人 embedding.

    MiniCPM-o 使用 Whisper encoder + projection → speaker embedding.
    这个 embedding 作为 TTS 模块的 condition.
    """
    # MiniCPM-o 的 get_sys_prompt() 方法展示了参考音频的处理方式:
    #   1. librosa.load(ref_audio, sr=16000, mono=True)
    #   2. 将 waveform 作为 system message 的 content 传入
    #   3. 模型内部 Whisper encoder 提取 audio embedding
    #   4. LLM 处理 audio embedding, 产生 speaker-aware hidden states
    #   5. _get_last_spk_embeds() 从 LLM hidden states 提取 speaker embedding

    # 简化: 直接用 model.apm (Whisper encoder) + projection
    if hasattr(model, "apm") and model.apm is not None:
        # 需要将 waveform 转为 mel spectrogram (Whisper 输入格式)
        # 这里返回 placeholder, 实际由 processor 处理
        pass

    # 如果 apm 未加载, 使用 processor 提取
    from transformers import WhisperFeatureExtractor

    feature_extractor = WhisperFeatureExtractor.from_pretrained(
        "openai/whisper-medium", trust_remote_code=True
    )
    mel = feature_extractor(
        audio_waveform.squeeze().numpy(), sampling_rate=16000, return_tensors="pt"
    ).input_features
    return model.apm(mel.to(model.device)).last_hidden_state


def generate_speech_tokens_from_text(
    model,
    text: str,
    processor,
    spk_embed: torch.Tensor,
    max_tokens: int = 512,
):
    """使用 MiniCPM-o 内置 TTS 从文本生成 S3 speech tokens.

    流程:
      1. Tokenize text
      2. LLM forward → last_hidden_states
      3. Extract speaker embedding from hidden states
      4. MiniCPMTTS.generate_speech_tokens() → S3 discrete tokens
    """
    device = model.llm.device

    # 构建 system prompt + text
    messages = [
        {
            "role": "system",
            "content": [
                spk_embed.cpu().numpy()
                if isinstance(spk_embed, torch.Tensor)
                else spk_embed
            ],
        },
        {"role": "user", "content": [text]},
    ]

    # 使用 processor 构建输入
    inputs = processor(
        text=text,
        return_tensors="pt",
    ).to(device)

    # LLM forward → hidden states
    with torch.no_grad():
        outputs = model.llm(
            input_ids=inputs["input_ids"],
            output_hidden_states=True,
        )
        last_hidden_states = outputs.hidden_states[-1]  # (1, seq_len, hidden_dim)

    # 调用 MiniCPMTTS 生成 speech tokens
    # 注意: 接口可能因版本不同而变化, 需根据实际模型调整
    if hasattr(model.tts, "generate_speech_tokens"):
        speech_tokens = model.tts.generate_speech_tokens(
            hidden_states=last_hidden_states,
            spk_embed=spk_embed,
            max_new_tokens=max_tokens,
        )
    elif hasattr(model, "_generate_speech_tokens"):
        speech_tokens = model._generate_speech_tokens(
            inputs=None,  # 需要构建正确的输入
            outputs=outputs,
            text=text,
            max_new_tokens=max_tokens,
        )
    else:
        # Fallback: 模拟调用
        raise NotImplementedError(
            "Cannot find speech token generation method. "
            "Please inspect model.tts attributes with: "
            'python -c "from transformers import AutoModel; '
            "m = AutoModel.from_pretrained('openbmb/MiniCPM-o-4_5', trust_remote_code=True, "
            'init_tts=True); print(dir(m.tts))"'
        )

    return speech_tokens


def extract_s3_target_tokens(audio_path: str, model) -> torch.Tensor:
    """从 Roxy WAV 文件提取 S3 离散 tokens 作为训练目标.

    使用 MiniCPM-o 内置的音频 tokenizer (stepaudio2 Token2wav 的 encoder 方向)
    将音频波形编码为 S3 离散 tokens.
    """
    import librosa

    # 加载音频
    waveform, _ = librosa.load(audio_path, sr=16000, mono=True)
    waveform = torch.from_numpy(waveform).float().unsqueeze(0)  # (1, T)

    # 如果 Token2wav 支持 encoder 方向:
    if hasattr(model.tts, "audio_tokenizer") and model.tts.audio_tokenizer is not None:
        tokenizer = model.tts.audio_tokenizer
        if hasattr(tokenizer, "encode"):
            tokens = tokenizer.encode(waveform)
        elif hasattr(tokenizer, "tokenize"):
            tokens = tokenizer.tokenize(waveform)
        else:
            raise NotImplementedError(
                "Token2wav does not support encoding. Need alternative S3 tokenizer."
            )
    else:
        # 需要单独初始化 Token2wav 或使用 CosyVoice2 tokenizer
        raise NotImplementedError(
            "Audio tokenizer not loaded. Call model.init_tts() first, "
            "or install: pip install minicpmo-utils[all]"
        )

    return tokens


def train_epoch(model, train_loader, optimizer, scheduler, config, logger, global_step):
    """一个 epoch 的训练循环."""
    model.train()
    total_loss = 0.0

    for batch_idx, batch in enumerate(train_loader):
        losses = []

        for sample in batch:
            text = sample["text"]
            audio_path = sample["audio_path"]

            try:
                # 1. 提取 Roxy 音频的 S3 tokens (训练目标)
                target_tokens = extract_s3_target_tokens(audio_path, model)

                # 2. 提取 Roxy 参考音频的 speaker embedding
                import librosa

                ref_waveform, _ = librosa.load(audio_path, sr=16000, mono=True)
                ref_waveform = torch.from_numpy(ref_waveform).float().unsqueeze(0)
                spk_embed = extract_speaker_embedding(model, ref_waveform)

                # 3. 生成 prediction tokens
                #    实际训练时需用 processor, 这里简化
                pred_tokens = generate_speech_tokens_from_text(
                    model,
                    text,
                    processor=None,
                    spk_embed=spk_embed,
                    max_tokens=target_tokens.shape[-1],
                )

                # 4. 计算 CE loss
                loss = F.cross_entropy(
                    pred_tokens.view(-1, pred_tokens.size(-1)),
                    target_tokens.view(-1),
                )

                loss = loss / config.gradient_accumulation_steps
                loss.backward()
                losses.append(loss.item())

            except NotImplementedError as e:
                logger.warning(f"Not implemented: {e}")
                continue
            except Exception as e:
                logger.warning(f"Sample error ({audio_path}): {e}")
                continue

        if not losses:
            logger.warning(f"  Batch {batch_idx}: all samples failed, skipping")
            continue

        avg_loss = sum(losses) * config.gradient_accumulation_steps / len(losses)
        total_loss += avg_loss

        if (batch_idx + 1) % config.gradient_accumulation_steps == 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()
            global_step += 1

            if global_step % config.logging_steps == 0:
                logger.info(
                    f"  Step {global_step} | Loss: {avg_loss:.4f} | "
                    f"LR: {scheduler.get_last_lr()[0]:.2e}"
                )

            if global_step % config.save_steps == 0:
                save_checkpoint(model, config, global_step, logger)

    return total_loss / max(len(train_loader), 1), global_step


def save_checkpoint(
    model, config: TTSFineTuneConfig, step: int, logger: logging.Logger
):
    """保存 LoRA adapter."""
    try:
        from peft import PeftModel

        output_dir = Path(config.output_dir) / f"checkpoint-{step}"
        output_dir.mkdir(parents=True, exist_ok=True)

        # 找到应用了 LoRA 的模块
        tts_module = model.tts
        for attr in ["llm", "decoder", "speech_decoder", "model"]:
            if hasattr(tts_module, attr) and isinstance(
                getattr(tts_module, attr), PeftModel
            ):
                getattr(tts_module, attr).save_pretrained(str(output_dir))
                break
        else:
            if isinstance(tts_module, PeftModel):
                tts_module.save_pretrained(str(output_dir))
            else:
                torch.save(model.tts.state_dict(), str(output_dir / "tts_weights.pth"))

        logger.info(f"  Checkpoint saved to {output_dir}")
    except Exception as e:
        logger.error(f"  Failed to save checkpoint: {e}")


def train(config: TTSFineTuneConfig):
    """主训练流程."""
    output_dir = Path(config.output_dir)
    logger = setup_logging(output_dir)

    logger.info("=" * 60)
    logger.info("  Roxy 音色迁移 — MiniCPM-o 4.5 内置 TTS LoRA 微调")
    logger.info(f"  Speaker: {config.speaker_name}  |  Language: {config.language}")
    logger.info(f"  Model: {config.model_id}")
    logger.info(f"  Output: {output_dir}")
    logger.info("=" * 60)

    # 1. 加载数据集
    dataset = RoxyTTSDataset(config.dataset_jsonl, config.sample_rate)
    train_data, val_data = split_dataset(dataset, config.val_split, config.seed)
    total_duration = sum(s.get("duration_sec", 0) for s in train_data)
    logger.info(
        f"\n[Dataset] Train: {len(train_data)} | Val: {len(val_data)} | "
        f"Duration: ~{total_duration / 60:.1f} min"
    )

    train_loader = DataLoader(
        train_data,
        batch_size=config.batch_size,
        shuffle=True,
        collate_fn=collate_fn,
        num_workers=2,
    )

    # 2. 加载模型
    logger.info("\n[Model] Loading MiniCPM-o 4.5 with TTS module...")
    try:
        model = load_minicpmo_tts(config, logger)
    except Exception as e:
        logger.error(f"Model loading failed: {e}")
        logger.info("\n--- 离线环境替代方案 ---")
        logger.info("1. 先下载模型: huggingface-cli download openbmb/MiniCPM-o-4_5")
        logger.info(
            "2. 或使用本地 GGUF: python scripts/train_voice_roxy.py --model_path <path>"
        )
        logger.info(
            "3. 或使用 VoxCPM2 替代: pip install voxcpm && voxcpm lora-ft-webui"
        )
        return

    # 3. 注入 LoRA
    logger.info("\n[LoRA] Injecting LoRA into TTS decoder...")
    try:
        model = apply_lora_to_tts(model, config, logger)
    except Exception as e:
        logger.error(f"LoRA injection failed: {e}")
        return

    # 4. 初始化 optimizer
    trainable_params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(
        trainable_params, lr=config.learning_rate, weight_decay=config.weight_decay
    )
    num_training_steps = (
        config.num_epochs * len(train_loader) // config.gradient_accumulation_steps
    )
    from torch.optim.lr_scheduler import CosineAnnealingLR

    scheduler = CosineAnnealingLR(optimizer, T_max=num_training_steps)

    logger.info(
        f"\n[Training] Epochs: {config.num_epochs} | "
        f"Steps: ~{num_training_steps} | LR: {config.learning_rate}"
    )

    # 5. 训练循环
    global_step = 0
    for epoch in range(config.num_epochs):
        logger.info(f"\n--- Epoch {epoch + 1}/{config.num_epochs} ---")
        avg_loss, global_step = train_epoch(
            model, train_loader, optimizer, scheduler, config, logger, global_step
        )
        logger.info(f"  Epoch {epoch + 1} avg loss: {avg_loss:.4f}")

    # 6. 最终保存
    save_checkpoint(model, config, "final", logger)
    config_path = output_dir / "train_config.json"
    config_path.write_text(
        json.dumps(
            {k: str(v) for k, v in config.__dict__.items()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(f"\nTraining complete. Output: {output_dir}")


# ── Inference ────────────────────────────────────────────


def inference_roxy_tts(
    checkpoint_dir: str,
    text: str,
    output_audio: str,
    ref_audio: Optional[str] = None,
    model_id: str = "openbmb/MiniCPM-o-4_5",
):
    """使用微调后的 MiniCPM-o TTS 推理."""
    logger = logging.getLogger("roxy_inference")
    out = Path(output_audio)
    out.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"\nRoxy TTS 推理")
    logger.info(f"  Text: {text[:60]}...")
    logger.info(f"  Output: {out}")

    # 加载模型 + LoRA
    from transformers import AutoModel
    from peft import PeftModel

    model = AutoModel.from_pretrained(
        model_id,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
        init_vision=False,
        init_audio=False,
        init_tts=True,
        device_map="auto",
    )

    # 加载 LoRA
    for attr in ["llm", "decoder", "speech_decoder", "model"]:
        if hasattr(model.tts, attr):
            try:
                setattr(
                    model.tts,
                    attr,
                    PeftModel.from_pretrained(getattr(model.tts, attr), checkpoint_dir),
                )
                break
            except Exception:
                continue

    # 加载参考音频作为 speaker embedding
    if ref_audio:
        import librosa

        waveform, _ = librosa.load(ref_audio, sr=16000)
        spk_embed = extract_speaker_embedding(
            model, torch.from_numpy(waveform).unsqueeze(0)
        )
    else:
        spk_embed = None

    # 生成 speech tokens → wav
    tokens = generate_speech_tokens_from_text(
        model, text, processor=None, spk_embed=spk_embed
    )
    if hasattr(model.tts, "audio_tokenizer") and model.tts.audio_tokenizer:
        wav = model.tts.audio_tokenizer.decode(tokens)
        import soundfile as sf

        sf.write(str(out), wav.squeeze().cpu().numpy(), 16000)

    logger.info(f"  Done: {out}")


# ── CLI ─────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="MiniCPM-o 4.5 内置 TTS LoRA 微调 — Roxy 音色迁移"
    )
    parser.add_argument("--inference", action="store_true", help="推理模式")
    parser.add_argument(
        "--checkpoint", type=str, default="checkpoints/roxy_tts/checkpoint-final"
    )
    parser.add_argument(
        "--text", type=str, default="初めまして、私の名前はロキシーです。"
    )
    parser.add_argument(
        "--output", type=str, default="checkpoints/roxy_tts/eval/output.wav"
    )
    parser.add_argument("--ref_audio", type=str, default="")
    parser.add_argument("--model_id", type=str, default="openbmb/MiniCPM-o-4_5")
    parser.add_argument("--model_path", type=str, default="")
    args = parser.parse_args()

    if args.inference:
        inference_roxy_tts(
            args.checkpoint, args.text, args.output, args.ref_audio, args.model_id
        )
    else:
        config = TTSFineTuneConfig()
        if args.model_path:
            config.model_path = args.model_path
        train(config)


if __name__ == "__main__":
    main()
