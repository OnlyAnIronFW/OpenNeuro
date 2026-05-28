"""Patch original GGUF bytes with trained weights."""

import sys, logging, re
from pathlib import Path
from safetensors.torch import load_file
from gguf import GGUFReader
import numpy as np

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("gguf_patch")

ORIGINAL_GGUF = Path(r"F:\llm\models\tts\MiniCPM-o-4_5-tts-F16.gguf")
MERGED_SAFETENSORS = Path(
    r"F:\OpenNeuro\checkpoints\roxy_tts_v2\merged\checkpoint-final\tts_merged.safetensors"
)
OUTPUT_GGUF = Path(
    r"F:\OpenNeuro\checkpoints\roxy_tts_v2\merged\checkpoint-final\MiniCPM-o-4_5-tts-F16-Roxy.gguf"
)

LAYER_RE = re.compile(r"model\.layers\.(\d+)\.(.+)")

GGUF_LAYER_MAP = {
    "self_attn.q_proj.weight": "attn_q.weight",
    "self_attn.k_proj.weight": "attn_k.weight",
    "self_attn.v_proj.weight": "attn_v.weight",
    "self_attn.o_proj.weight": "attn_output.weight",
    "mlp.gate_proj.weight": "ffn_gate.weight",
    "mlp.up_proj.weight": "ffn_up.weight",
    "mlp.down_proj.weight": "ffn_down.weight",
    "input_layernorm.weight": "attn_norm.weight",
    "post_attention_layernorm.weight": "ffn_norm.weight",
}

TOP_LEVEL_MAP = {
    "model.embed_tokens.weight": "token_embd.weight",
    "model.norm.weight": "output_norm.weight",
    "emb_code.0.weight": "emb_code.0.weight",
    "emb_text.weight": "emb_text.weight",
    "head_code.0.parametrizations.weight.original0": "head_code.0.weight",
    "head_code.0.parametrizations.weight.original1": None,
}


def hf_to_gguf_name(hf_name):
    if hf_name in TOP_LEVEL_MAP:
        return TOP_LEVEL_MAP[hf_name]
    if hf_name.startswith("projector_"):
        return hf_name
    m = LAYER_RE.match(hf_name)
    if m:
        idx, suffix = m.group(1), m.group(2)
        if suffix in GGUF_LAYER_MAP:
            return f"blk.{idx}.{GGUF_LAYER_MAP[suffix]}"
    return None


logger.info(f"Loading merged: {MERGED_SAFETENSORS}")
merged = load_file(str(MERGED_SAFETENSORS))

gguf_data = {}
for hf_key, tensor in merged.items():
    gguf_key = hf_to_gguf_name(hf_key)
    if gguf_key:
        gguf_data[gguf_key] = tensor.numpy()

logger.info(f"  Mapped: {len(gguf_data)}/{len(merged)}")

logger.info(f"Loading: {ORIGINAL_GGUF}")
reader = GGUFReader(str(ORIGINAL_GGUF))
logger.info(f"  {len(reader.tensors)} tensors")

# Read raw bytes
with open(ORIGINAL_GGUF, "rb") as f:
    gguf_bytes = bytearray(f.read())

patched = 0
for entry in reader.tensors:
    name = entry.name
    if name not in gguf_data:
        continue
    new_data = gguf_data[name]
    orig_data = entry.data

    # Align shapes (GGUF may store transposed)
    if new_data.shape != orig_data.shape:
        if len(new_data.shape) == 2 and new_data.T.shape == orig_data.shape:
            new_data = new_data.T
    if new_data.shape != orig_data.shape:
        continue

    # Convert to F16 bytes matching original format
    new_bytes = np.ascontiguousarray(new_data.astype(np.float16)).tobytes()
    offset = entry.data_offset

    if len(new_bytes) == entry.n_bytes:
        gguf_bytes[offset : offset + len(new_bytes)] = new_bytes
        patched += 1

logger.info(f"  Patched: {patched}/{len(reader.tensors)}")

with open(OUTPUT_GGUF, "wb") as f:
    f.write(gguf_bytes)

logger.info(
    f"Done: {OUTPUT_GGUF.stat().st_size / 1e9:.2f} GB, {patched} tensors updated"
)
