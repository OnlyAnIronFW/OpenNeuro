"""
Roxy 音色迁移 — MiniCPM-o 4.5 内置 TTS 模块 LoRA 微调 (Real Tokenizer)

真实训练: MiniCPMOProcessor (text tokenizer) + Token2wav (S3 audio encoder)
"""

import argparse, json, logging, os, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import torch, torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
import numpy as np


@dataclass
class TTSFineTuneConfig:
    hf_cache_dir: str = os.path.expandvars(
        r"%USERPROFILE%\.cache\huggingface\hub\models--openbmb--MiniCPM-o-4_5\snapshots"
    )
    tts_shard: str = "model-00004-of-00004.safetensors"
    dataset_jsonl: str = r"F:\OpenNeuro\dataset\tts\Roxy_processed\jsonl\dataset.jsonl"
    speaker_name: str = "Roxy"
    language: str = "ja"
    val_split: float = 0.05
    seed: int = 42
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_target_modules: str = "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj"
    output_dir: str = r"F:\OpenNeuro\checkpoints\roxy_tts_v2"
    num_epochs: int = 30
    batch_size: int = 1
    gradient_accumulation_steps: int = 4
    learning_rate: float = 5e-5
    warmup_steps: int = 50
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    save_steps: int = 100
    logging_steps: int = 5
    bf16: bool = True


class RoxyTTSDataset(Dataset):
    def __init__(self, jsonl_path: str):
        self.samples = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    self.samples.append(json.loads(line))
        assert self.samples, f"No samples in {jsonl_path}"

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]
        return {
            "text": s["text"],
            "audio_path": s["audio"],
            "speaker": s.get("speaker", ""),
            "duration_sec": s.get("duration_sec", 0),
        }


def collate_fn(b):
    return b


def split_dataset(d, r, s):
    import random

    rng = random.Random(s)
    idxs = list(range(len(d)))
    rng.shuffle(idxs)
    n = max(1, int(len(idxs) * r))
    return [d[i] for i in idxs[n:]], [d[i] for i in idxs[:n]]


def setup_logging(od: Path) -> logging.Logger:
    od.mkdir(parents=True, exist_ok=True)
    l = logging.getLogger("roxy_tts")
    l.setLevel(logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    l.addHandler(h)
    fh = logging.FileHandler(od / "train.log", encoding="utf-8")
    fh.setFormatter(h.formatter)
    l.addHandler(fh)
    return l


# ══════════════════════════════════════════════════════════
#  Model + Tokenizer Loading
# ══════════════════════════════════════════════════════════


def find_model_cache(config: TTSFineTuneConfig, logger: logging.Logger) -> Path:
    cache = Path(config.hf_cache_dir)
    snapshots = sorted(
        [d for d in cache.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return snapshots[0]


def load_tts_standalone(
    model_dir: Path, config: TTSFineTuneConfig, logger: logging.Logger
):
    """Standalone TTS loading with patched imports."""
    import shutil

    pkg_dir = Path.cwd() / "_minicpm_tts"
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
                content = content.replace(f"from {rel}", f"from _minicpm_tts.{rel[1:]}")
            with open(pkg_dir / f, "w", encoding="utf-8") as fh:
                fh.write(content)
    if (model_dir / "processing_minicpmo.py").exists():
        src = model_dir / "processing_minicpmo.py"
        content = src.read_text(encoding="utf-8")
        for rel in [".configuration_minicpmo", ".tokenization_minicpmo_fast", ".utils"]:
            content = content.replace(f"from {rel}", f"from _minicpm_tts.{rel[1:]}")
        with open(pkg_dir / "processing_minicpmo.py", "w", encoding="utf-8") as fh:
            fh.write(content)
    if (model_dir / "tokenization_minicpmo_fast.py").exists():
        shutil.copy2(
            model_dir / "tokenization_minicpmo_fast.py",
            pkg_dir / "tokenization_minicpmo_fast.py",
        )
    (pkg_dir / "__init__.py").touch()
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))

    from _minicpm_tts.configuration_minicpmo import MiniCPMOConfig

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

    logger.info(
        f"  TTS config: hidden={tts_config.hidden_size}, layers={tts_config.num_hidden_layers}"
    )

    from _minicpm_tts.modeling_minicpmo import MiniCPMTTS

    tts = MiniCPMTTS(config=tts_config, audio_tokenizer=None)

    shard_path = model_dir / config.tts_shard
    from safetensors.torch import load_file

    all_weights = load_file(str(shard_path))
    tts_weights = {k[4:]: v for k, v in all_weights.items() if k.startswith("tts.")}
    tts.load_state_dict(tts_weights, strict=False)
    logger.info(
        f"  TTS loaded: {sum(v.numel() for v in tts_weights.values()) / 1e6:.1f}M params"
    )
    return tts


def load_s3_tokenizer(model_dir: Path, logger: logging.Logger):
    """Load Token2wav S3 encoder."""
    assets = model_dir / "assets" / "token2wav"
    from stepaudio2 import Token2wav

    s3 = Token2wav(str(assets), float16=False)
    logger.info(f"  S3 tokenizer loaded from: {assets}")
    return s3


def load_text_tokenizer(model_dir: Path, logger: logging.Logger):
    """Load tokenizer directly (avoid MiniCPMOProcessor Windows SIGALRM issue)."""
    try:
        from _minicpm_tts.tokenization_minicpmo_fast import MiniCPMOTokenizerFast

        tok = MiniCPMOTokenizerFast.from_pretrained(
            str(model_dir), local_files_only=True
        )
    except Exception:
        from transformers import AutoTokenizer

        tok = AutoTokenizer.from_pretrained(
            str(model_dir), trust_remote_code=True, local_files_only=True
        )
    logger.info(f"  Tokenizer: {type(tok).__name__}")
    return tok


def apply_lora(tts, config: TTSFineTuneConfig, logger: logging.Logger):
    from peft import LoraConfig, LoraModel

    peft_config = LoraConfig(
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=config.lora_target_modules.split(","),
        bias="none",
        task_type="CAUSAL_LM",
    )
    tts.model = LoraModel(tts.model, peft_config, adapter_name="default")
    trainable = sum(p.numel() for p in tts.model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in tts.model.parameters())
    logger.info(f"  LoRA: {trainable / 1e6:.2f}M / {total / 1e6:.1f}M")
    return tts


# ══════════════════════════════════════════════════════════
#  Real Training Loop
# ══════════════════════════════════════════════════════════


def prepare_batch_real(
    sample: dict,
    tts,
    s3_tokenizer,
    text_proc,
    device: torch.device,
    logger: logging.Logger,
) -> Optional[tuple]:
    """Real tokenization: text → token IDs, audio → S3 tokens."""
    text = sample["text"]
    audio_path = sample["audio_path"]

    try:
        # 1. Text → token IDs (text_proc is either MiniCPMOProcessor or tokenizer directly)
        if hasattr(text_proc, "tokenizer"):
            text_tokens = text_proc.tokenizer.encode(text, add_special_tokens=True)
        else:
            text_tokens = text_proc.encode(text, add_special_tokens=True)
        if not text_tokens or len(text_tokens) < 2:
            return None
        text_ids = torch.tensor(text_tokens[:200], device=device)
        text_emb = tts.emb_text(text_ids).unsqueeze(0)  # (1, text_len, 768)

        # 2. Audio → S3 tokens via S3TokenizerV2.quantize(mel, mel_len)
        import librosa

        audio_path = Path(sample["audio_path"])
        if not audio_path.exists():
            logger.debug(f"Audio missing: {audio_path}")
            return None
        wav, sr = librosa.load(str(audio_path), sr=16000, mono=True)
        # Compute mel spectrogram (128 bins, 16kHz, 25ms window, 10ms hop)
        mel_spec = librosa.feature.melspectrogram(
            y=wav, sr=16000, n_mels=128, n_fft=512, hop_length=160, win_length=400
        )
        mel_spec = (
            torch.from_numpy(mel_spec).float().unsqueeze(0).to(device)
        )  # (1, 128, T)
        mel_len = torch.tensor([mel_spec.shape[-1]], device=device)
        s3_codes, s3_code_len = s3_tokenizer.audio_tokenizer.quantize(mel_spec, mel_len)
        target_ids = s3_codes.squeeze(0).long()

        # 3. Speaker embedding: use projectors on zero-input (will learn from data)
        spk_emb = tts.projector_spk(
            torch.zeros(1, 1, 4096, device=device, dtype=text_emb.dtype)
        )
        # Note: real training should extract spk_emb from Whisper encoding of reference audio.
        # For now, the speaker embedding is a trainable zero-input → the model learns
        # to associate specific speaker characteristics through the training data.

        # 4. Construct input: [spk | text]
        combined = torch.cat([spk_emb, text_emb], dim=1)  # (1, 1+text_len, 768)
        return combined, target_ids

    except Exception as e:
        logger.debug(f"Prep error [{text[:30]}...]: {e}")
        return None


def train_epoch(tts, loader, opt, sched, config, logger, global_step, device, s3, proc):
    tts.train()
    total_loss = 0.0
    bc = 0
    for bi, batch in enumerate(loader):
        bls = []
        for sample in batch:
            r = prepare_batch_real(sample, tts, s3, proc, device, logger)
            if r is None:
                continue
            combined, target_ids = r
            hidden = tts.model(inputs_embeds=combined).last_hidden_state
            text_len = combined.shape[1] - 1
            head = (
                tts.head_code[0]
                if isinstance(tts.head_code, torch.nn.ModuleList)
                else tts.head_code
            )
            pred = head(hidden[:, -text_len:, :])
            pred = pred[:, : len(target_ids), :].reshape(-1, pred.size(-1))
            tgt = target_ids[: pred.shape[0]]
            loss = F.cross_entropy(pred, tgt) / config.gradient_accumulation_steps
            loss.backward()
            bls.append(loss.item() * config.gradient_accumulation_steps)
        if not bls:
            continue
        avg = sum(bls) / len(bls)
        total_loss += avg
        bc += 1
        if (bi + 1) % config.gradient_accumulation_steps == 0:
            torch.nn.utils.clip_grad_norm_(tts.parameters(), config.max_grad_norm)
            opt.step()
            sched.step()
            opt.zero_grad()
            global_step += 1
            if global_step % config.logging_steps == 0:
                logger.info(
                    f"  Step {global_step:4d} | Loss: {avg:.4f} | LR: {sched.get_last_lr()[0]:.2e}"
                )
            if global_step % config.save_steps == 0:
                save_ckpt(tts, config, global_step, logger)
    return total_loss / max(bc, 1), global_step


def save_ckpt(tts, config, step, logger):
    try:
        out = Path(config.output_dir) / f"checkpoint-{step}"
        out.mkdir(parents=True, exist_ok=True)
        tts.model.save_pretrained(str(out))
        (out / "tts_config.json").write_text(
            json.dumps(
                {
                    "hidden_size": tts.config.hidden_size,
                    "num_hidden_layers": tts.config.num_hidden_layers,
                    "num_audio_tokens": tts.config.num_audio_tokens,
                    "num_text_tokens": tts.config.num_text_tokens,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(f"  Checkpoint: {out}")
    except Exception as e:
        logger.error(f"Save: {e}")


def train(config: TTSFineTuneConfig):
    od = Path(config.output_dir)
    logger = setup_logging(od)
    logger.info(
        f"{'=' * 60}\n  Roxy TTS — Real Tokenizer Training\n  Speaker: {config.speaker_name}\n{'=' * 60}"
    )

    dataset = RoxyTTSDataset(config.dataset_jsonl)
    train_data, _ = split_dataset(dataset, config.val_split, config.seed)
    dur = sum(s.get("duration_sec", 0) for s in train_data)
    logger.info(f"\n[Dataset] Train: {len(train_data)} | ~{dur / 60:.1f} min")
    loader = DataLoader(
        train_data,
        batch_size=config.batch_size,
        shuffle=True,
        collate_fn=collate_fn,
        num_workers=0,
    )

    model_dir = find_model_cache(config, logger)
    logger.info(f"\n[Model] Cache: {model_dir}")
    tts = load_tts_standalone(model_dir, config, logger)
    s3 = load_s3_tokenizer(model_dir, logger)
    proc = load_text_tokenizer(model_dir, logger)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tts = tts.to(device)
    logger.info(
        f"  Device: {device} ({torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU'})"
    )

    logger.info("\n[LoRA] Injecting...")
    tts = apply_lora(tts, config, logger)

    trainable = [p for p in tts.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(
        trainable, lr=config.learning_rate, weight_decay=config.weight_decay
    )
    total_steps = config.num_epochs * len(loader) // config.gradient_accumulation_steps
    from torch.optim.lr_scheduler import CosineAnnealingLR

    sched = CosineAnnealingLR(opt, T_max=max(1, total_steps))
    logger.info(f"\n[Train] Epochs: {config.num_epochs} | Steps: ~{total_steps}")

    global_step = 0
    for epoch in range(config.num_epochs):
        logger.info(f"\n--- Epoch {epoch + 1}/{config.num_epochs} ---")
        avg_loss, global_step = train_epoch(
            tts, loader, opt, sched, config, logger, global_step, device, s3, proc
        )
        logger.info(f"  Epoch {epoch + 1} avg loss: {avg_loss:.4f}")

    save_ckpt(tts, config, "final", logger)
    (od / "train_config.json").write_text(
        json.dumps(
            {k: str(v) for k, v in config.__dict__.items()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(f"\nDone. Output: {od}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    config = TTSFineTuneConfig()
    if args.check:
        logger = setup_logging(Path(config.output_dir))
        model_dir = find_model_cache(config, logger)
        tts = load_tts_standalone(model_dir, config, logger)
        s3 = load_s3_tokenizer(model_dir, logger)
        proc = load_text_tokenizer(model_dir, logger)
        logger.info(
            f"\nOK! All components loaded: TTS {sum(p.numel() for p in tts.parameters()) / 1e6:.1f}M, S3 ready, Tokenizer ready"
        )
    else:
        train(config)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
