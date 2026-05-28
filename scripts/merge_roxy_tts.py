"""
Roxy TTS LoRA → GGUF 合并导出 (Standalone)

训练后将 LoRA adapter 合并回 TTS 权重, 转 GGUF, 替换推理文件。

Usage:
    python scripts/merge_roxy_tts.py
    python scripts/merge_roxy_tts.py --checkpoint checkpoints/roxy_tts_v2/checkpoint-final
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import torch

# Paths
OPENNEURO_ROOT = Path(r"F:\OpenNeuro")
CHECKPOINT_DIR = OPENNEURO_ROOT / "checkpoints" / "roxy_tts_v2"
LLAMA_DIR = Path(r"F:\llm\models\tts")
TTS_GGUF_NAME = "MiniCPM-o-4_5-tts-F16.gguf"
HF_CACHE = os.path.expandvars(
    r"%USERPROFILE%\.cache\huggingface\hub\models--openbmb--MiniCPM-o-4_5\snapshots"
)


def setup_logging():
    logger = logging.getLogger("merge")
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)
    return logger


# ══════════════════════════════════════════════════════════
#  Step 1: Load base TTS + LoRA → merge
# ══════════════════════════════════════════════════════════


def find_model_cache(logger):
    cache = Path(HF_CACHE)
    if not cache.exists():
        logger.error(f"Model cache not found: {cache}")
        sys.exit(1)
    snapshots = sorted(
        [d for d in cache.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return snapshots[0]


def load_base_tts(model_dir, logger):
    """Load base TTS standalone (same as training script)."""
    import shutil

    pkg_dir = Path.cwd() / "_merge_tts"
    pkg_dir.mkdir(exist_ok=True)
    for f in [
        "configuration_minicpmo.py",
        "modeling_navit_siglip.py",
        "modeling_minicpmo.py",
        "utils.py",
    ]:
        src = model_dir / f
        if src.exists():
            content = src.read_text(encoding="utf-8")
            for rel in [
                ".configuration_minicpmo",
                ".modeling_navit_siglip",
                ".utils",
                ".processing_minicpmo",
            ]:
                content = content.replace(f"from {rel}", f"from _merge_tts.{rel[1:]}")
            with open(pkg_dir / f, "w", encoding="utf-8") as fh:
                fh.write(content)
    (pkg_dir / "__init__.py").touch()
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))

    from _merge_tts.configuration_minicpmo import MiniCPMOConfig

    full_config = MiniCPMOConfig.from_pretrained(str(model_dir), local_files_only=True)
    tts_config = full_config.tts_config
    for attr, val in [
        ("top_p", 0.95),
        ("temperature", 1.0),
        ("repetition_penalty", 1.1),
        ("do_sample", True),
        ("num_beams", 1),
        ("max_length", 2048),
        ("top_k", 50),
        ("pad_token_id", 0),
        ("bos_token_id", 1),
        ("eos_token_id", 2),
    ]:
        if not hasattr(tts_config, attr):
            setattr(tts_config, attr, val)

    from _merge_tts.modeling_minicpmo import MiniCPMTTS

    tts = MiniCPMTTS(config=tts_config, audio_tokenizer=None)

    from safetensors.torch import load_file

    all_weights = load_file(str(model_dir / "model-00004-of-00004.safetensors"))
    tts_weights = {k[4:]: v for k, v in all_weights.items() if k.startswith("tts.")}
    tts.load_state_dict(tts_weights, strict=False)
    logger.info(
        f"  Base TTS: {sum(v.numel() for v in tts_weights.values()) / 1e6:.1f}M params"
    )
    return tts


def merge_lora(tts, checkpoint_dir: Path, output_dir: Path, logger):
    """Load LoRA weights directly and merge (LoraModel.save_pretrained already saves merged state)."""
    if not checkpoint_dir.exists():
        logger.error(f"Checkpoint not found: {checkpoint_dir}")
        return False

    # LoraModel.save_pretrained() saves the full model state dict
    # with LoRA weights merged in. Load directly.
    model_file = checkpoint_dir / "model.safetensors"
    if not model_file.exists():
        logger.error(f"model.safetensors not found in {checkpoint_dir}")
        return False

    from safetensors.torch import load_file

    logger.info(f"  Loading merged weights from: {model_file.name}")
    state = load_file(str(model_file))
    tts.model.load_state_dict(state, strict=False)
    logger.info(f"  Loaded {len(state)} tensors")

    # Save
    output_dir.mkdir(parents=True, exist_ok=True)
    merged_state = tts.state_dict()
    from safetensors.torch import save_file

    save_file(merged_state, str(output_dir / "tts_merged.safetensors"))

    param_count = sum(v.numel() for v in merged_state.values())
    size_mb = (output_dir / "tts_merged.safetensors").stat().st_size / 1e6
    logger.info(f"  Saved: {size_mb:.1f} MB, {param_count / 1e6:.1f}M params")

    (output_dir / "merge_report.json").write_text(
        json.dumps(
            {
                "merge_time": datetime.now().isoformat(),
                "checkpoint": str(checkpoint_dir),
                "output": str(output_dir),
                "total_params": param_count,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return True


# ══════════════════════════════════════════════════════════
#  Step 2: safetensors → GGUF
# ══════════════════════════════════════════════════════════


def convert_to_gguf(merged_dir: Path, output_gguf: Path, logger) -> bool:
    """Convert merged safetensors to GGUF using llama.cpp converter."""
    merged_file = merged_dir / "tts_merged.safetensors"
    if not merged_file.exists():
        logger.error(f"Merged weights not found: {merged_file}")
        return False

    # Find llama.cpp convert script
    convert_script = find_llama_convert_script()
    if not convert_script:
        logger.warning("llama.cpp convert_hf_to_gguf.py not found")
        logger.info("\n手动转换步骤:")
        logger.info(f"  cd F:\\llm\\llama.cpp-upstream")
        logger.info(
            f"  python convert_hf_to_gguf.py {merged_dir} --outtype f16 --outfile {output_gguf}"
        )
        return False

    logger.info(f"  Converter: {convert_script}")
    logger.info(f"  Input:     {merged_file}")
    logger.info(f"  Output:    {output_gguf}")

    # Prepare minimal HF-style dir for conversion
    hf_dir = merged_dir / "hf_model"
    hf_dir.mkdir(exist_ok=True)
    shutil.copy2(merged_file, hf_dir / "tts_merged.safetensors")
    (hf_dir / "config.json").write_text(
        json.dumps(
            {
                "model_type": "minicpmo",
                "tts_config": {
                    "backbone_model": "llama",
                    "hidden_size": 768,
                    "num_hidden_layers": 20,
                    "num_attention_heads": 12,
                    "intermediate_size": 3072,
                    "num_audio_tokens": 6562,
                    "num_text_tokens": 152064,
                    "num_vq": 1,
                    "audio_tokenizer_type": "s3tokenizer",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    import subprocess

    result = subprocess.run(
        [
            "python",
            str(convert_script),
            str(hf_dir),
            "--outtype",
            "f16",
            "--outfile",
            str(output_gguf),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode == 0:
        logger.info("  GGUF conversion OK!")
        return True
    else:
        logger.error(f"  GGUF conversion failed:\n{result.stderr[:500]}")
        return False


def find_llama_convert_script() -> Optional[Path]:
    for p in [
        Path(r"F:\llm\llama.cpp-upstream\convert_hf_to_gguf.py"),
        Path(r"F:\llm\llama.cpp\convert_hf_to_gguf.py"),
    ]:
        if p.exists():
            return p
    return None


# ══════════════════════════════════════════════════════════
#  Step 3: Backup + Replace
# ══════════════════════════════════════════════════════════


def replace_gguf(new_gguf: Path, logger) -> bool:
    target = LLAMA_DIR / TTS_GGUF_NAME
    if not new_gguf.exists():
        logger.error(f"New GGUF not found: {new_gguf}")
        return False

    if target.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = LLAMA_DIR / f"MiniCPM-o-4_5-tts-F16.bak_{ts}.gguf"
        shutil.copy2(target, backup)
        logger.info(f"  Backup: {backup.name}")

    shutil.copy2(new_gguf, target)
    logger.info(f"  Replaced: {target}")
    return True


# ══════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="Roxy TTS LoRA → GGUF merge")
    parser.add_argument("--checkpoint", type=str, default="")
    parser.add_argument("--skip-gguf", action="store_true")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("  Roxy TTS — LoRA → GGUF Merge")
    logger.info("=" * 60)

    # Find checkpoint
    if args.checkpoint:
        ckpt = Path(args.checkpoint)
    else:
        ckpts = sorted(
            [
                d
                for d in CHECKPOINT_DIR.iterdir()
                if d.is_dir() and d.name.startswith("checkpoint-")
            ],
            key=lambda d: (
                int(d.name.split("-")[1]) if d.name.split("-")[1] != "final" else 99999
            ),
        )
        ckpt = ckpts[-1] if ckpts else None

    if ckpt is None:
        logger.error("No checkpoint found!")
        sys.exit(1)

    output_dir = CHECKPOINT_DIR / "merged" / ckpt.name
    logger.info(f"Checkpoint: {ckpt}")
    logger.info(f"Output:     {output_dir}")

    # Step 1: Load base + merge LoRA
    model_dir = find_model_cache(logger)
    tts = load_base_tts(model_dir, logger)
    if not merge_lora(tts, ckpt, output_dir, logger):
        sys.exit(1)

    # Step 2: Convert to GGUF
    if not args.skip_gguf:
        new_gguf = output_dir / "MiniCPM-o-4_5-tts-F16-Roxy.gguf"
        if convert_to_gguf(output_dir, new_gguf, logger):
            replace_gguf(new_gguf, logger)
    else:
        logger.info("  Skipping GGUF conversion (--skip-gguf)")

    logger.info(f"\nDone! Restart llama-server to use Roxy voice.")
    # Cleanup
    try:
        shutil.rmtree(Path.cwd() / "_merge_tts", ignore_errors=True)
    except:
        pass


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
