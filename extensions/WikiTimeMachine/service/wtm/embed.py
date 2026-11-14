"""Embedding models. Vectors are L2-normalized float32, dimension 384.

- MiniLMEmbedder: sentence-transformers/all-MiniLM-L6-v2, ONNX weights from the official repo at a
  pinned revision, run locally with onnxruntime (CPU). Mean pooling + L2 normalization, max 256
  word pieces (the model's training length).
- HashEmbedder: deterministic feature-hashing embedder for CI/tests (no download).
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import numpy as np

DIM = 384
MINILM_REPO = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


class Embedder:
    model_id: str
    dim: int = DIM

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


class HashEmbedder(Embedder):
    model_id = "hash-384-v1"
    _tok = re.compile(r"\w+", re.UNICODE)

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = [w.lower() for w in self._tok.findall(t)]
            for w in toks:
                h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=8).digest(), "little")
                out[i, h % DIM] += 1.0 if (h >> 63) & 1 else -1.0
            n = np.linalg.norm(out[i])
            if n == 0:
                out[i, 0] = 1.0
            else:
                out[i] /= n
        return out


class MiniLMEmbedder(Embedder):
    model_id = f"{MINILM_REPO}@{MINILM_REVISION[:12]}/onnx-meanpool-l2"

    def __init__(self) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        tok_path = hf_hub_download(MINILM_REPO, "tokenizer.json", revision=MINILM_REVISION)
        onnx_path = hf_hub_download(MINILM_REPO, "onnx/model.onnx", revision=MINILM_REVISION)
        self.tokenizer = Tokenizer.from_file(tok_path)
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.session = ort.InferenceSession(onnx_path, so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}

    def embed(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        outs = []
        for s in range(0, len(texts), batch_size):
            enc = self.tokenizer.encode_batch(texts[s:s + batch_size])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            outs.append(pooled.astype(np.float32))
        return np.vstack(outs) if outs else np.zeros((0, DIM), dtype=np.float32)


@lru_cache(maxsize=4)
def get_embedder(name: str) -> Embedder:
    if name == "hash":
        return HashEmbedder()
    if name == "minilm":
        return MiniLMEmbedder()
    raise ValueError(f"unknown embedder {name!r}")


def to_sql_vector(v: np.ndarray) -> str:
    """Text form accepted by VEC_FromText()."""
    return "[" + ",".join(f"{x:.7g}" for x in v.tolist()) + "]"
