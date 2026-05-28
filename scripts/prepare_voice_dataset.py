"""
Roxy 音色迁移 — 数据集预处理
将 dataset/tts/Roxy/ 内的 WAV 文件 (文件名=文本) 转换为训练格式。

Usage:
    python scripts/prepare_voice_dataset.py
    python scripts/prepare_voice_dataset.py --format cosyvoice
    python scripts/prepare_voice_dataset.py --format gpt_sovits
"""

import argparse
import json
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Constants ────────────────────────────────────────────
ROXY_DIR = Path(r"F:\OpenNeuro\dataset\tts\Roxy")
OUTPUT_DIR = Path(r"F:\OpenNeuro\dataset\tts\Roxy_processed")
SAMPLE_RATE = 32000
MIN_DURATION_SEC = 0.8
MAX_DURATION_SEC = 30.0
AUDIO_EXT = ".wav"


@dataclass
class DatasetStats:
    total: int = 0
    valid: int = 0
    too_short: int = 0
    too_long: int = 0
    total_duration_sec: float = 0.0
    skipped_bad_text: int = 0


def scan_audio_files(root: Path) -> list[tuple[Path, str, float]]:
    """扫描所有 WAV 文件, 提取文件名作为文本.

    Returns:
        list of (path, text, duration_sec)
    """
    entries = []
    for f in sorted(root.glob(f"*{AUDIO_EXT}")):
        if f.name == "desktop.ini":
            continue
        # 文件名去掉 .wav 后缀即为文本
        text = f.stem.strip()
        if not text or len(text) < 1:
            continue
        try:
            with wave.open(str(f), "rb") as w:
                duration = w.getnframes() / w.getframerate()
        except Exception:
            continue
        entries.append((f, text, duration))
    return entries


def filter_and_validate(
    entries: list[tuple[Path, str, float]],
) -> tuple[list[tuple[Path, str, float]], DatasetStats]:
    """过滤无效条目."""
    stats = DatasetStats(total=len(entries))
    valid = []
    for path, text, dur in entries:
        if dur < MIN_DURATION_SEC:
            stats.too_short += 1
            continue
        if dur > MAX_DURATION_SEC:
            stats.too_long += 1
            continue
        # 跳过纯符号/过短文本
        text_clean = text.translate(
            str.maketrans("", "", "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
        )
        if len(text_clean.strip()) < 2:
            stats.skipped_bad_text += 1
            continue
        valid.append((path, text, dur))
        stats.total_duration_sec += dur
    stats.valid = len(valid)
    return valid, stats


# ── CosyVoice2 格式 ─────────────────────────────────────


def prepare_cosyvoice(
    entries: list[tuple[Path, str, float]],
    output_dir: Path,
    speaker_name: str = "Roxy",
) -> Path:
    """生成 CosyVoice2 fine-tune 数据集.

    CosyVoice2 期望格式:
        data/
        ├── metadata.csv       (audio_path | text | speaker | language)
        ├── wavs/               (符号链接或复制)
        └── speaker_info.csv    (speaker_name, speaker_id)
    """
    out = output_dir / "cosyvoice"
    wavs_dir = out / "wavs"
    wavs_dir.mkdir(parents=True, exist_ok=True)

    metadata_lines = ["audio_path|text|speaker|language"]
    for path, text, _dur in entries:
        # 符号链接原文件, 节省磁盘
        link_path = wavs_dir / path.name
        if not link_path.exists():
            os.symlink(path, link_path)
        # CosyVoice2: 日语用 "ja", 中文用 "zh"
        metadata_lines.append(f"wavs/{path.name}|{text}|{speaker_name}|ja")

    metadata_path = out / "metadata.csv"
    metadata_path.write_text("\n".join(metadata_lines), encoding="utf-8")

    # speaker_info.csv
    (out / "speaker_info.csv").write_text(
        f"speaker_name,speaker_id\n{speaker_name},0\n", encoding="utf-8"
    )

    print(f"  CosyVoice2 format → {metadata_path}")
    return out


# ── GPT-SoVITS 格式 ─────────────────────────────────────


def prepare_gpt_sovits(
    entries: list[tuple[Path, str, float]],
    output_dir: Path,
    speaker_name: str = "Roxy",
) -> Path:
    """生成 GPT-SoVITS 训练数据集.

    GPT-SoVITS 期望格式:
        data/{speaker}/
        ├── {text}.wav          (每条一个文件)
        └── esli.list            (audio_path|speaker|language|text)
                                 或 train.list / val.list

    NOTE: 原始文件已是 filename=text 格式, 直接使用.
    """
    out = output_dir / "gpt_sovits" / speaker_name
    out.mkdir(parents=True, exist_ok=True)

    train_lines = []
    for path, text, _dur in entries:
        # 符号链接
        link_path = out / path.name
        if not link_path.exists():
            os.symlink(path, link_path)
        train_lines.append(f"{out.name}/{path.name}|{speaker_name}|JA|{text}")

    list_path = out / "esli.list"
    list_path.write_text("\n".join(train_lines), encoding="utf-8")
    print(f"  GPT-SoVITS format → {list_path}")
    return out


# ── 通用 JSONL (Llama-Factory / 自定义训练) ────────────


def prepare_jsonl(
    entries: list[tuple[Path, str, float]],
    output_dir: Path,
) -> Path:
    """生成通用 JSONL 格式 (兼容多数微调框架).

    每行: {"audio": "path.wav", "text": "...", "speaker": "Roxy", "language": "ja"}
    """
    out = output_dir / "jsonl"
    out.mkdir(parents=True, exist_ok=True)

    data = []
    for path, text, _dur in entries:
        data.append(
            json.dumps(
                {
                    "audio": str(path),
                    "text": text,
                    "speaker": "Roxy",
                    "language": "ja",
                    "duration_sec": round(_dur, 1),
                },
                ensure_ascii=False,
            )
        )

    jsonl_path = out / "dataset.jsonl"
    jsonl_path.write_text("\n".join(data), encoding="utf-8")
    print(f"  JSONL format → {jsonl_path}")
    return out


# ── Main ────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Roxy 音色数据集预处理")
    parser.add_argument(
        "--format",
        type=str,
        default="all",
        choices=["all", "cosyvoice", "gpt_sovits", "jsonl"],
        help="输出格式 (default: all)",
    )
    parser.add_argument(
        "--input_dir",
        type=str,
        default=str(ROXY_DIR),
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=str(OUTPUT_DIR),
    )
    parser.add_argument(
        "--min_dur",
        type=float,
        default=MIN_DURATION_SEC,
    )
    parser.add_argument(
        "--max_dur",
        type=float,
        default=MAX_DURATION_SEC,
    )
    args = parser.parse_args()

    input_path = Path(args.input_dir)
    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 1. 扫描
    print(f"\n{'=' * 60}")
    print(f"  Roxy 音色数据集预处理")
    print(f"  Input:  {input_path}")
    print(f"  Output: {output_path}")
    print(f"{'=' * 60}\n")

    entries = scan_audio_files(input_path)
    print(f"  扫描到: {len(entries)} 个音频文件")

    # 2. 过滤
    valid, stats = filter_and_validate(entries)
    print(f"  有效:   {stats.valid} ({stats.total_duration_sec / 60:.1f} min)")
    if stats.too_short:
        print(f"  过短(<{MIN_DURATION_SEC}s): {stats.too_short}")
    if stats.too_long:
        print(f"  过长(>{MAX_DURATION_SEC}s): {stats.too_long}")
    if stats.skipped_bad_text:
        print(f"  跳过(无意义文本): {stats.skipped_bad_text}")

    # 3. 生成各格式
    fmt = args.format
    if fmt in ("all", "cosyvoice"):
        prepare_cosyvoice(valid, output_path)
    if fmt in ("all", "gpt_sovits"):
        prepare_gpt_sovits(valid, output_path)
    if fmt in ("all", "jsonl"):
        prepare_jsonl(valid, output_path)

    # 4. 统计报告
    report_path = output_path / "dataset_report.json"
    report_path.write_text(
        json.dumps(
            {
                "dataset": "Roxy voice timbre",
                "total_files": stats.total,
                "valid": stats.valid,
                "total_duration_sec": round(stats.total_duration_sec, 1),
                "total_duration_min": round(stats.total_duration_sec / 60, 1),
                "avg_duration_sec": round(stats.total_duration_sec / stats.valid, 1)
                if stats.valid
                else 0,
                "sample_rate": SAMPLE_RATE,
                "language": "ja",
                "filename_text": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  报告: {report_path}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
