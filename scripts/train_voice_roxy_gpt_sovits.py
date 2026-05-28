"""
Roxy 音色迁移 — GPT-SoVITS 微调训练脚本 (备选方案)

OpenNeuro 已集成 GPT-SoVITS TTS (live_hub/plugins/bilibili_live_adapter/tts_provider.py)。
可以直接在现有 GPT-SoVITS 实例上 fine-tune Roxy 音色, 无需额外部署。

优势: 与现有 TTS pipeline 无缝衔接, 训练更快 (~20min 数据, GPU 30min-1h)
劣势: 音色自然度略低于 CosyVoice2

数据集: dataset/tts/Roxy/
预处理: python scripts/prepare_voice_dataset.py --format gpt_sovits

Usage:
    python scripts/train_voice_roxy_gpt_sovits.py
    python scripts/train_voice_roxy_gpt_sovits.py --inference_only
"""

import argparse
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class GPTSovitsTrainConfig:
    # Dataset
    dataset_dir: str = r"F:\OpenNeuro\dataset\tts\Roxy_processed\gpt_sovits\Roxy"
    speaker_name: str = "Roxy"
    language: str = "JA"

    # GPT-SoVITS install path (你的实际安装路径)
    gpt_sovits_root: str = ""

    # Training
    output_dir: str = r"F:\OpenNeuro\checkpoints\roxy_gpt_sovits"
    batch_size: int = 8
    num_epochs: int = 20
    learning_rate: float = 1e-4

    # Audio preprocessing
    sample_rate: int = 32000
    hop_length: int = 320
    win_length: int = 1280
    f0_min: int = 50
    f0_max: int = 1100

    # Model
    ssl_encoder: str = "contentvec"
    vocoder: str = "hifigan"


def setup_logging(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("roxy_gpt_sovits")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


# ══════════════════════════════════════════════════════════
#  GPT-SoVITS Fine-Tuning
# ══════════════════════════════════════════════════════════


def prepare_dataset_gpt_sovits(config: GPTSovitsTrainConfig, logger: logging.Logger):
    """预处理音频为 GPT-SoVITS 所需格式.

    GPT-SoVITS 期望:
    1. 16kHz 或 32kHz WAV
    2. .list 文件: audio_path|speaker|language|text
    3. 已经由 prepare_voice_dataset.py 生成
    """
    list_file = Path(config.dataset_dir) / "esli.list"
    if not list_file.exists():
        logger.error(
            f"esli.list 未找到! 请先运行: python scripts/prepare_voice_dataset.py --format gpt_sovits"
        )
        return False

    logger.info(f"数据集就绪: {list_file}")
    with open(list_file, "r", encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    logger.info(f"  训练样本: {len(lines)} 条")
    return True


def train_gpt_sovits(config: GPTSovitsTrainConfig):
    """GPT-SoVITS 微调主流程.

    GPT-SoVITS 训练分为两个阶段:
    Phase 1: HuBERT/SSL 特征提取
    Phase 2: GPT + SoVITS 联合训练
    """
    logger = setup_logging(Path(config.output_dir))

    logger.info("=" * 60)
    logger.info("  Roxy 音色迁移 — GPT-SoVITS Fine-Tuning (备选)")
    logger.info(f"  Speaker: {config.speaker_name}  |  Language: {config.language}")
    logger.info(f"  Dataset: {config.dataset_dir}")
    logger.info(f"  Output:  {config.output_dir}")
    logger.info("=" * 60)

    # 1. 预处理检查
    if not prepare_dataset_gpt_sovits(config, logger):
        return

    # 2. 确定 GPT-SoVITS 路径
    gs_root = config.gpt_sovits_root or _find_gpt_sovits()
    if not gs_root:
        logger.error(
            "未找到 GPT-SoVITS 安装路径。\n"
            "请设置 --gpt_sovits_root 或安装 GPT-SoVITS:\n"
            "  git clone https://github.com/RVC-Boss/GPT-SoVITS.git"
        )
        return

    logger.info(f"GPT-SoVITS root: {gs_root}")

    # 3. Phase 1: 特征提取
    logger.info("\n[Phase 1] SSL 特征提取...")
    ssl_features_dir = Path(config.output_dir) / "ssl_features"
    ssl_features_dir.mkdir(parents=True, exist_ok=True)

    # ── 实际命令 ──
    # subprocess.run([
    #     "python", f"{gs_root}/GPT_SoVITS/prepare_datasets/1-get-text.py",
    #     "--list", str(Path(config.dataset_dir) / "esli.list"),
    #     "--output", str(Path(config.output_dir) / "texts"),
    # ], check=True)
    #
    # subprocess.run([
    #     "python", f"{gs_root}/GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py",
    #     "--list", str(Path(config.dataset_dir) / "esli.list"),
    #     "--output", str(ssl_features_dir),
    # ], check=True)
    #
    # subprocess.run([
    #     "python", f"{gs_root}/GPT_SoVITS/prepare_datasets/3-get-semantic.py",
    #     "--list", str(Path(config.dataset_dir) / "esli.list"),
    #     "--output", str(ssl_features_dir),
    # ], check=True)

    logger.info("  [stub] hubert + semantic 特征提取 — 待 GPT-SoVITS 接入")

    # 4. Phase 2: 训练
    logger.info("\n[Phase 2] GPT + SoVITS 训练...")

    # ── GPT 训练 (文本→语义) ──
    # subprocess.run([
    #     "python", f"{gs_root}/GPT_SoVITS/s2_train.py",
    #     "--train_list", str(Path(config.dataset_dir) / "esli.list"),
    #     "--output", str(Path(config.output_dir) / "gpt"),
    #     "--epochs", str(config.num_epochs),
    #     "--batch_size", str(config.batch_size),
    #     "--lr", str(config.learning_rate),
    # ], check=True)

    # ── SoVITS 训练 (语义→音频) ──
    # subprocess.run([
    #     "python", f"{gs_root}/GPT_SoVITS/s1_train.py",
    #     "--train_list", str(Path(config.dataset_dir) / "esli.list"),
    #     "--output", str(Path(config.output_dir) / "sovits"),
    #     "--epochs", str(config.num_epochs),
    #     "--batch_size", str(config.batch_size // 2),
    #     "--lr", str(config.learning_rate),
    # ], check=True)

    logger.info("  [stub] GPT + SoVITS 训练 — 待 GPT-SoVITS 接入")

    # 5. 保存配置
    config_path = Path(config.output_dir) / "train_config.json"
    config_path.write_text(
        json.dumps(
            {k: v for k, v in config.__dict__.items()}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    logger.info(f"\nConfig saved to {config_path}")

    # 6. 训练完后的模型文件
    logger.info("\n训练完成后的模型文件:")
    logger.info(f"  GPT weights:  {config.output_dir}/gpt/GPT_SoVITS.pth")
    logger.info(f"  SoVITS weights: {config.output_dir}/sovits/SoVITS.pth")
    logger.info(f"  Reference audio: {config.dataset_dir}/ (用于推理时提供参考音色)")


# ── Inference ────────────────────────────────────────────


def inference_gpt_sovits(
    config: GPTSovitsTrainConfig,
    text: str,
    output_audio: str,
    ref_audio: Optional[str] = None,
):
    """使用训练好的 GPT-SoVITS 模型推理.

    Args:
        config: 训练配置
        text: 合成文本
        output_audio: 输出路径
        ref_audio: 参考音频 (用于 zero-shot 音色控制)
    """
    out = Path(output_audio)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nRoxy GPT-SoVITS 推理")
    print(f"  Text:   {text[:60]}...")
    print(f"  Output: {out}")

    gpt_weights = Path(config.output_dir) / "gpt" / "GPT_SoVITS.pth"
    sovits_weights = Path(config.output_dir) / "sovits" / "SoVITS.pth"

    # ── 实际推理 ──
    # from GPT_SoVITS.inference import GPT_SoVITS
    # model = GPT_SoVITS(gpt_path=str(gpt_weights), sovits_path=str(sovits_weights))
    # audio = model.inference(text=text, ref_audio=ref_audio)
    # torchaudio.save(str(out), audio, 32000)
    print("  [inference stub — 待 GPT-SoVITS 接入]")


# ── Helpers ──────────────────────────────────────────────


def _find_gpt_sovits() -> Optional[str]:
    """尝试自动查找 GPT-SoVITS 安装路径."""
    candidates = [
        r"F:\GPT-SoVITS",
        r"F:\maibot plugin develop\GPT-SoVITS",
        os.path.expandvars(r"%USERPROFILE%\GPT-SoVITS"),
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "GPT_SoVITS")):
            return c
    return None


# ── CLI ─────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Roxy 音色迁移 — GPT-SoVITS Fine-Tuning (备选)"
    )
    parser.add_argument("--inference_only", action="store_true", help="仅推理")
    parser.add_argument(
        "--gpt_sovits_root", type=str, default="", help="GPT-SoVITS 安装路径"
    )
    parser.add_argument("--text", type=str, default="", help="推理文本")
    parser.add_argument("--output", type=str, default="", help="输出音频路径")
    args = parser.parse_args()

    config = GPTSovitsTrainConfig()
    if args.gpt_sovits_root:
        config.gpt_sovits_root = args.gpt_sovits_root

    if args.inference_only:
        test_text = args.text or "初めまして、私の名前はロキシーです。"
        out = args.output or str(Path(config.output_dir) / "eval" / "test.wav")
        inference_gpt_sovits(config, test_text, out)
    else:
        train_gpt_sovits(config)


if __name__ == "__main__":
    main()
