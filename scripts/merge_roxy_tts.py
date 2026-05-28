"""
Roxy TTS LoRA → GGUF 合并导出

训练完成后, 将 LoRA adapter 合并回 MiniCPM-o TTS 模块,
然后转换为 GGUF 格式以用于 llama.cpp/llama-server 推理。

流程:
  1. LoRA adapter + base TTS weights → merged safetensors
  2. merged safetensors → GGUF (替换原有的 MiniCPM-o-4_5-tts-F16.gguf)

Usage:
    # 训练后运行
    python scripts/merge_roxy_tts.py

    # 检查合并结果
    python scripts/merge_roxy_tts.py --check

    # 指定 checkpoint
    python scripts/merge_roxy_tts.py --checkpoint checkpoints/roxy_tts/checkpoint-300
"""

import argparse
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import torch

# ── Paths ────────────────────────────────────────────────
OPENNEURO_ROOT = Path(r"F:\OpenNeuro")
CHECKPOINT_DIR = OPENNEURO_ROOT / "checkpoints" / "roxy_tts"
LLAMA_DIR = Path(r"F:\llm\models\tts")  # 原有 TTS GGUF 位置
TTS_GGUF_NAME = "MiniCPM-o-4_5-tts-F16.gguf"

# ── Logging ──────────────────────────────────────────────


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("merge")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


# ══════════════════════════════════════════════════════════
#  Step 1: LoRA + Base → Merged Weights
# ══════════════════════════════════════════════════════════


def find_best_checkpoint(checkpoint_dir: Path) -> Optional[Path]:
    """找到最新的 checkpoint."""
    if not checkpoint_dir.exists():
        return None

    checkpoints = sorted(
        [
            d
            for d in checkpoint_dir.iterdir()
            if d.is_dir() and d.name.startswith("checkpoint-")
        ],
        key=lambda d: int(d.name.split("-")[1]),
    )
    return checkpoints[-1] if checkpoints else None


def merge_lora_to_base(
    model_id: str, checkpoint: Path, output: Path, logger: logging.Logger
) -> bool:
    """将 LoRA adapter 合并到 MiniCPMTTS base weights, 保存为 safetensors."""
    try:
        from transformers import AutoModel
        from peft import PeftModel

        logger.info(f"Loading base model: {model_id}")
        model = AutoModel.from_pretrained(
            model_id,
            trust_remote_code=True,
            local_files_only=True,
            torch_dtype=torch.bfloat16,
            init_vision=False,
            init_audio=False,
            init_tts=True,
        )

        tts_module = model.tts
        logger.info(f"  TTS module: {type(tts_module).__name__}")

        # 找到 TTS 内部的 LLM decoder + 加载 LoRA
        merged = False
        for attr in ["llm", "decoder", "speech_decoder", "model"]:
            if hasattr(tts_module, attr):
                sub = getattr(tts_module, attr)
                try:
                    sub = PeftModel.from_pretrained(sub, str(checkpoint))
                    merged_weights = sub.merge_and_unload()
                    setattr(tts_module, attr, merged_weights)
                    logger.info(f"  Merged LoRA into tts.{attr}")
                    merged = True
                    break
                except Exception as e:
                    logger.debug(f"  tts.{attr}: {e}")
                    continue

        if not merged:
            # 整个 tts 被 PeftModel 包装了, 直接 merge
            try:
                model.tts = PeftModel.from_pretrained(tts_module, str(checkpoint))
                model.tts = model.tts.merge_and_unload()
                logger.info("  Merged LoRA into entire tts module")
                merged = True
            except Exception as e:
                logger.error(f"  Failed to merge: {e}")
                return False

        # 保存所有 TTS 权重
        output.mkdir(parents=True, exist_ok=True)
        tts_state = {}
        for name, param in model.tts.named_parameters():
            tts_state[name] = param.data.cpu().clone()

        # 保存为 safetensors
        from safetensors.torch import save_file

        save_file(tts_state, str(output / "tts_merged.safetensors"))

        # 保存 config.json 用于 GGUF 转换
        tts_config = {
            "hidden_size": model.config.tts_config.hidden_size,
            "num_hidden_layers": model.config.tts_config.num_hidden_layers,
            "num_attention_heads": model.config.tts_config.num_attention_heads,
            "intermediate_size": model.config.tts_config.intermediate_size,
            "num_audio_tokens": model.config.tts_config.num_audio_tokens,
            "num_text_tokens": model.config.tts_config.num_text_tokens,
            "num_vq": model.config.tts_config.num_vq,
            "audio_tokenizer_type": model.config.tts_config.audio_tokenizer_type,
            "condition_type": model.config.tts_config.condition_type,
            "backbone_model": model.config.tts_config.backbone_model,
        }
        (output / "tts_config.json").write_text(
            json.dumps(tts_config, indent=2), encoding="utf-8"
        )

        # 合并报告
        report = {
            "merge_time": datetime.now().isoformat(),
            "base_model": model_id,
            "checkpoint": str(checkpoint),
            "output": str(output),
            "param_count": len(tts_state),
            "total_params": sum(p.numel() for p in tts_state.values()),
        }
        (output / "merge_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )

        logger.info(
            f"  Saved: {output / 'tts_merged.safetensors'} "
            f"({report['total_params'] / 1e6:.1f}M params)"
        )
        return True

    except Exception as e:
        logger.error(f"Merge failed: {e}")
        return False


# ══════════════════════════════════════════════════════════
#  Step 2: Safetensors → GGUF
# ══════════════════════════════════════════════════════════


def convert_to_gguf(
    merged_dir: Path, output_gguf: Path, logger: logging.Logger
) -> bool:
    """将 merged TTS safetensors 转换为 GGUF.

    MiniCPM-o 的 GGUF 文件由 llama.cpp 的转换工具生成。
    TTS 模块作为独立 GGUF 文件被 llama-server 的 --tproj 参数加载。

    方法:
      使用 llama.cpp 的 convert_hf_to_gguf.py 脚本,
      指定只导出 TTS 模块的映射规则。
    """
    merged_weights = merged_dir / "tts_merged.safetensors"
    if not merged_weights.exists():
        logger.error(f"  Merged weights not found: {merged_weights}")
        return False

    logger.info("Converting safetensors → GGUF...")
    logger.info(f"  Input:  {merged_weights}")
    logger.info(f"  Output: {output_gguf}")

    # 寻找 llama.cpp 转换脚本
    convert_script = find_llama_convert_script()
    if not convert_script:
        logger.warning("llama.cpp convert script not found.")
        logger.info("")
        logger.info("=== 手动转换步骤 ===")
        logger.info("1. 进入 llama.cpp 目录:")
        logger.info(r"   cd F:\llm\llama.cpp-upstream")
        logger.info("2. 运行转换 (需要为 MiniCPM-o TTS 模块指定自定义映射):")
        logger.info(f"   python convert_hf_to_gguf.py {merged_dir} \\")
        logger.info("     --outtype f16 \\")
        logger.info(f"     --outfile {output_gguf} \\")
        logger.info("     --model-name 'Roxy-TTS-MiniCPMo'")
        logger.info("3. 替换原有文件:")
        logger.info(f"   cp {output_gguf} {LLAMA_DIR / TTS_GGUF_NAME}")
        logger.info("")
        return False

    # 尝试自动转换
    try:
        import subprocess

        result = subprocess.run(
            [
                "python",
                str(convert_script),
                str(merged_dir),
                "--outtype",
                "f16",
                "--outfile",
                str(output_gguf),
                "--model-name",
                "Roxy-TTS-MiniCPMo",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info("  GGUF conversion successful!")
            return True
        else:
            logger.error(f"  Conversion failed:\n{result.stderr}")
            return False
    except Exception as e:
        logger.warning(f"  Auto conversion failed: {e}")
        return False


def find_llama_convert_script() -> Optional[Path]:
    """查找 llama.cpp 的 HF→GGUF 转换脚本."""
    candidates = [
        Path(r"F:\llm\llama.cpp-upstream\convert_hf_to_gguf.py"),
        Path(r"F:\llm\llama.cpp\convert_hf_to_gguf.py"),
        Path.home() / "llama.cpp" / "convert_hf_to_gguf.py",
        Path.home() / "llama.cpp-upstream" / "convert_hf_to_gguf.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


# ══════════════════════════════════════════════════════════
#  Step 3: Replace GGUF + Backup
# ══════════════════════════════════════════════════════════


def backup_and_replace(
    new_gguf: Path, target_dir: Path, logger: logging.Logger
) -> bool:
    """备份原有 TTS GGUF, 替换为新文件."""
    target = target_dir / TTS_GGUF_NAME

    if not new_gguf.exists():
        logger.error(f"  New GGUF not found: {new_gguf}")
        return False

    if target.exists():
        backup = target.with_suffix(".gguf.bak")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = target_dir / f"MiniCPM-o-4_5-tts-F16.gguf.bak_{ts}"
        shutil.copy2(target, backup)
        logger.info(f"  Backup: {backup.name}")

    shutil.copy2(new_gguf, target)
    logger.info(f"  Replaced: {target}")
    return True


# ══════════════════════════════════════════════════════════
#  Check: Verify merge quality
# ══════════════════════════════════════════════════════════


def check_merge(merged_dir: Path, logger: logging.Logger):
    """检查合并结果."""
    report = merged_dir / "merge_report.json"
    if report.exists():
        data = json.loads(report.read_text())
        logger.info("Merge report:")
        logger.info(f"  Time: {data['merge_time']}")
        logger.info(f"  Checkpoint: {data['checkpoint']}")
        logger.info(f"  Params: {data['total_params'] / 1e6:.1f}M")
        logger.info(f"  Keys: {data['param_count']}")
    else:
        logger.warning("No merge report found.")

    weights = merged_dir / "tts_merged.safetensors"
    if weights.exists():
        size_mb = weights.stat().st_size / 1e6
        logger.info(f"  Merged weights: {size_mb:.1f} MB")

    # 检查原 GGUF
    original = LLAMA_DIR / TTS_GGUF_NAME
    if original.exists():
        logger.info(f"  Original GGUF: {original.stat().st_size / 1e9:.1f} GB")
    backups = sorted(LLAMA_DIR.glob("*.gguf.bak_*"))
    if backups:
        logger.info(f"  Backups: {len(backups)}")


# ══════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="Roxy TTS LoRA → GGUF 合并导出")
    parser.add_argument(
        "--checkpoint", type=str, default="", help="Checkpoint 目录路径"
    )
    parser.add_argument("--check", action="store_true", help="仅检查")
    parser.add_argument(
        "--skip_gguf", action="store_true", help="跳过 GGUF 转换 (仅合并)"
    )
    parser.add_argument(
        "--model_id",
        type=str,
        default="openbmb/MiniCPM-o-4_5",
        help="HF 模型 ID 或本地缓存路径",
    )
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("  Roxy TTS LoRA → GGUF 合并导出")
    logger.info("=" * 60)

    # 找到 checkpoint
    if args.checkpoint:
        checkpoint = Path(args.checkpoint)
    else:
        checkpoint = find_best_checkpoint(CHECKPOINT_DIR)

    if checkpoint is None:
        logger.error("未找到 checkpoint!")
        logger.info("请先运行训练: .\\start_train_roxy.bat")
        return

    merged_dir = CHECKPOINT_DIR / "merged" / checkpoint.name
    logger.info(f"Checkpoint: {checkpoint}")
    logger.info(f"Output:     {merged_dir}")

    if args.check:
        check_merge(merged_dir, logger)
        return

    # Step 1: LoRA → Merged
    logger.info("\n[Step 1] Merging LoRA adapter into base TTS weights...")
    if not merge_lora_to_base(args.model_id, checkpoint, merged_dir, logger):
        logger.error("Merge failed.")
        return

    # Step 2: Merged → GGUF
    if not args.skip_gguf:
        logger.info("\n[Step 2] Converting merged safetensors → GGUF...")
        new_gguf = merged_dir / "MiniCPM-o-4_5-tts-F16-Roxy.gguf"
        if convert_to_gguf(merged_dir, new_gguf, logger):
            # Step 3: Replace
            logger.info("\n[Step 3] Replacing existing TTS GGUF...")
            backup_and_replace(new_gguf, LLAMA_DIR, logger)

    logger.info(f"\nDone! New TTS module: {LLAMA_DIR / TTS_GGUF_NAME}")
    logger.info("Restart llama-server to use the new voice.")


if __name__ == "__main__":
    main()
