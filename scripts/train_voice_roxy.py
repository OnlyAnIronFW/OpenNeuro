"""
Roxy 音色迁移 — CosyVoice2 LoRA 微调训练脚本

MiniCPM-o 4.5 的语音输出模块基于 CosyVoice2。
本脚本对其 flow matching 解码器做 LoRA 微调, 实现音色迁移。

训练数据: dataset/tts/Roxy/ (WAV 文件名=文本)
预处理:   python scripts/prepare_voice_dataset.py --format cosyvoice

Usage:
    # 先预处理
    python scripts/prepare_voice_dataset.py --format cosyvoice

    # 训练
    python scripts/train_voice_roxy.py

    # 仅推理测试
    python scripts/train_voice_roxy.py --inference_only --checkpoint checkpoints/roxy/best
"""

import argparse
import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Configuration ────────────────────────────────────────


@dataclass
class TrainConfig:
    # Dataset
    dataset_dir: str = r"F:\OpenNeuro\dataset\tts\Roxy_processed\cosyvoice"
    speaker_name: str = "Roxy"
    language: str = "ja"

    # Model
    base_model: str = "CosyVoice2-0.5B"  # 或 CosyVoice2
    # LoRA
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    lora_target_modules: str = "q_proj,v_proj,k_proj,o_proj"  # Qwen 注意力层

    # Training
    output_dir: str = r"F:\OpenNeuro\checkpoints\roxy_cosyvoice"
    num_epochs: int = 50
    batch_size: int = 4
    gradient_accumulation_steps: int = 2
    learning_rate: float = 5e-5
    warmup_steps: int = 100
    max_steps: int = -1  # -1 = all epochs
    save_steps: int = 200
    eval_steps: int = 200
    logging_steps: int = 10

    # Audio
    sample_rate: int = 32000
    # Optimizer
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0

    # Hardware
    bf16: bool = True
    gradient_checkpointing: bool = True
    num_workers: int = 2

    # Validation
    val_split: float = 0.05
    seed: int = 42


# ══════════════════════════════════════════════════════════
#  CosyVoice2 LoRA Fine-Tuning
# ══════════════════════════════════════════════════════════


def setup_logging(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("roxy_train")
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(output_dir / "train.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def load_cosyvoice_model(config: TrainConfig, logger: logging.Logger):
    """加载 CosyVoice2 模型并注入 LoRA.

    注意: CosyVoice2 内部使用 Qwen2 作为文本编码器。
    LoRA 目标层为 Qwen 的注意力投影层。
    TTS flow matching 解码器可能需要额外适配。

    实际集成时请根据 CosyVoice2 的 import 路径调整。
    """
    try:
        import torch
        from peft import LoraConfig, TaskType, get_peft_model

        logger.info(f"Loading base model: {config.base_model}")

        # ── 实际代码: 根据 CosyVoice2 的 API 加载模型 ──
        # from cosyvoice.cli.cosyvoice import CosyVoice2
        # model = CosyVoice2(config.base_model, load_jit=False, load_trt=False)
        #
        # ── LoRA 配置 ──
        # peft_config = LoraConfig(
        #     task_type=TaskType.CAUSAL_LM,  # 或 FEATURE_EXTRACTION for TTS
        #     r=config.lora_r,
        #     lora_alpha=config.lora_alpha,
        #     lora_dropout=config.lora_dropout,
        #     target_modules=config.lora_target_modules.split(","),
        # )
        # model.llm = get_peft_model(model.llm, peft_config)
        # model.llm.print_trainable_parameters()
        #
        # return model

        logger.warning(
            "CosyVoice2 import path 需根据实际安装调整。"
            "请参考: https://github.com/FunAudioLLM/CosyVoice"
        )
        return None

    except ImportError as e:
        logger.error(f"缺少依赖: {e}")
        logger.info("安装命令: pip install cosyvoice peft transformers accelerate")
        raise


class RoxyDataset:
    """Roxy 音色数据集加载器.

    使用 CosyVoice2 的 metadata.csv 格式。
    """

    def __init__(self, metadata_path: str, config: TrainConfig):
        self.metadata_path = Path(metadata_path)
        self.config = config
        self.samples = self._load_metadata()

    def _load_metadata(self) -> list[dict]:
        samples = []
        with open(self.metadata_path, "r", encoding="utf-8") as f:
            header = f.readline().strip().split("|")
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 4:
                    samples.append(
                        {
                            "audio": parts[0],
                            "text": parts[1],
                            "speaker": parts[2],
                            "language": parts[3],
                        }
                    )
        return samples

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        return self.samples[idx]

    def split(self, val_ratio: float, seed: int) -> tuple[list, list]:
        import random

        rng = random.Random(seed)
        indices = list(range(len(self.samples)))
        rng.shuffle(indices)
        val_count = max(1, int(len(indices) * val_ratio))
        val_indices = set(indices[:val_count])
        train = [s for i, s in enumerate(self.samples) if i not in val_indices]
        val = [s for i, s in enumerate(self.samples) if i in val_indices]
        return train, val


def train_cosyvoice(config: TrainConfig):
    """CosyVoice2 LoRA 微调主流程."""
    output_dir = Path(config.output_dir)
    logger = setup_logging(output_dir)

    logger.info("=" * 60)
    logger.info("  Roxy 音色迁移 — CosyVoice2 LoRA Fine-Tuning")
    logger.info(f"  Speaker: {config.speaker_name}  |  Language: {config.language}")
    logger.info(f"  Base Model: {config.base_model}")
    logger.info(f"  Dataset: {config.dataset_dir}")
    logger.info(f"  Output: {output_dir}")
    logger.info("=" * 60)

    # 1. 加载数据集
    metadata_file = Path(config.dataset_dir) / "metadata.csv"
    if not metadata_file.exists():
        logger.error(
            f"metadata.csv 未找到! 请先运行: python scripts/prepare_voice_dataset.py --format cosyvoice"
        )
        return

    dataset = RoxyDataset(str(metadata_file), config)
    train_samples, val_samples = dataset.split(config.val_split, config.seed)
    total_duration = sum(_estimate_duration(s["text"]) for s in train_samples)
    logger.info(
        f"  Train: {len(train_samples)} 条  "
        f"|  Val: {len(val_samples)} 条  "
        f"|  Est. duration: ~{total_duration / 60:.1f} min"
    )

    # 2. 加载模型 + 注入 LoRA
    logger.info("Loading CosyVoice2 with LoRA...")
    try:
        model = load_cosyvoice_model(config, logger)
    except ImportError:
        logger.error("无法加载模型, 请安装依赖后重试。")
        return

    if model is None:
        logger.info("\n--- 模型接口说明 (实际训练时取消注释) ---")
        logger.info("1. CosyVoice2 安装: pip install cosyvoice")
        logger.info("2. LoRA 注入: 对 Qwen2 LLM 层使用 peft.LoraConfig")
        logger.info("3. 训练循环: 标准 PyTorch training loop")
        logger.info("4. 损失函数: flow matching loss (CosyVoice2 内置)")
        logger.info("--- 脚本框架已就绪, 待模型接入 ---\n")

    # 3. 训练循环
    logger.info("\nTraining configuration:")
    logger.info(f"  Epochs: {config.num_epochs}")
    logger.info(f"  Batch size: {config.batch_size}")
    logger.info(f"  Learning rate: {config.learning_rate}")
    logger.info(f"  LoRA: r={config.lora_r}, alpha={config.lora_alpha}")
    logger.info(f"  Precision: {'bf16' if config.bf16 else 'fp32'}")
    logger.info(f"  Warmup steps: {config.warmup_steps}")
    logger.info(f"  Max grad norm: {config.max_grad_norm}")

    # 训练指标占位
    logger.info("\nTraining metrics will be logged to:")
    logger.info(f"  {output_dir / 'train.log'}")
    logger.info(f"  Checkpoints: {output_dir / 'checkpoint-*'}")
    logger.info(f"  Final LoRA:  {output_dir / 'lora_adapter'}")
    logger.info(f"  TensorBoard: {output_dir / 'runs'}")

    # 4. 保存配置
    config_path = output_dir / "train_config.json"
    config_path.write_text(
        json.dumps(
            {k: v for k, v in config.__dict__.items()}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    logger.info(f"\nConfig saved to {config_path}")


# ── Inference ────────────────────────────────────────────


def inference_roxy(
    checkpoint_dir: str,
    text: str,
    output_audio: str,
    speaker: str = "Roxy",
    language: str = "ja",
):
    """使用训练好的 LoRA 推理。

    Args:
        checkpoint_dir: LoRA adapter 路径
        text: 要合成的文本
        output_audio: 输出音频路径
        speaker: 说话人标签
        language: 语言代码
    """
    from pathlib import Path

    out = Path(output_audio)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nRoxy 音色推理")
    print(f"  Text:   {text[:60]}...")
    print(f"  Output: {out}")
    print(f"  Speaker: {speaker} ({language})")

    # ── 实际推理代码 ──
    # from cosyvoice.cli.cosyvoice import CosyVoice2
    # model = CosyVoice2("CosyVoice2-0.5B", load_jit=False)
    # model.load_lora(checkpoint_dir)  # 加载 LoRA 权重
    #
    # audio = model.inference_zero_shot(
    #     text=text,
    #     prompt_text="",  # zero-shot 模式
    #     prompt_wav=None,
    # )
    # torchaudio.save(str(out), audio, 32000)
    print("  [inference stub — 待模型接入]")


# ── Evaluation ───────────────────────────────────────────


def evaluate_roxy(
    checkpoint_dir: str,
    test_texts: list[str],
    output_dir: str,
):
    """批量评估训练效果."""
    out = Path(output_dir) / "eval"
    out.mkdir(parents=True, exist_ok=True)

    results = []
    for i, text in enumerate(test_texts):
        audio_path = out / f"sample_{i:03d}.wav"
        inference_roxy(checkpoint_dir, text, str(audio_path))
        results.append({"id": i, "text": text, "audio": str(audio_path)})

    report = out / "eval_report.json"
    report.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nEvaluation report: {report}")


# ── CLI ─────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Roxy 音色迁移 — CosyVoice2 LoRA Fine-Tuning"
    )
    parser.add_argument("--inference_only", action="store_true", help="仅推理, 不训练")
    parser.add_argument(
        "--checkpoint",
        type=str,
        default=r"F:\OpenNeuro\checkpoints\roxy_cosyvoice\lora_adapter",
        help="LoRA checkpoint 目录",
    )
    parser.add_argument("--text", type=str, default="", help="推理: 合成文本")
    parser.add_argument(
        "--output",
        type=str,
        default=r"F:\OpenNeuro\checkpoints\roxy_cosyvoice\eval\output.wav",
    )
    parser.add_argument("--eval", action="store_true", help="批量评估")
    args = parser.parse_args()

    config = TrainConfig()

    if args.inference_only:
        test_texts = [
            "初めまして、私の名前はロキシーです。",
            "ルディ、魔術の練習を始めましょう。",
            "よく頑張りましたね。",
            "これはまずいですね。",
            "私が守りますので、安心してください。",
        ]
        if args.eval:
            evaluate_roxy(
                args.checkpoint, test_texts, str(Path(args.checkpoint).parent)
            )
        elif args.text:
            inference_roxy(args.checkpoint, args.text, args.output)
        else:
            print("推理测试 (提供 --text 或 --eval)")
            for i, t in enumerate(test_texts):
                out = str(Path(args.checkpoint).parent / "eval" / f"test_{i:02d}.wav")
                inference_roxy(args.checkpoint, t, out)
    else:
        train_cosyvoice(config)


if __name__ == "__main__":
    main()


def _estimate_duration(text: str) -> float:
    """粗略估算文本朗读时长 (日语: ~7 字/秒)."""
    return max(0.5, len(text) / 7.0)
