"""MEDUSA (Qwen3.5-VL) line recogniser.

Runs inside WSL next to the web server.  Model loading is lazy (first job),
generation is batched and serialised, and a deterministic mock backend keeps
the UI usable on machines without CUDA / without the weights.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_PROMPT = (
    "Transcribe the handwritten text in this line image.\n"
    "Output ONLY the transcription."
)

# `official` is the two-line prompt from the model card; the others are
# starting points for experiments (the model is sensitive to wording).
PROMPT_PRESETS = [
    {
        "id": "official",
        "label": "官方推荐",
        "hint": "模型卡里针对训练目标优化的最短版本，通常效果最好。",
        "prompt": DEFAULT_PROMPT,
    },
    {
        "id": "keep_abbrev",
        "label": "保留缩写",
        "hint": "显式要求保持缩写原样，避免模型自行展开。",
        "prompt": (
            "Transcribe the handwritten text in this line image.\n"
            "Keep abbreviations exactly as written; do not expand them.\n"
            "Output ONLY the transcription."
        ),
    },
    {
        "id": "expand_abbrev",
        "label": "展开缩写",
        "hint": "要求把缩写补全为完整字母形式；可按语言补充具体规则。",
        "prompt": (
            "Transcribe the handwritten text in this line image.\n"
            "Expand every abbreviation into its full letter form.\n"
            "Modernize word segmentation (split or join words following modern usage).\n"
            "Use only u and i, never v and j.\n"
            "Do not record allographic variants.\n"
            "Output ONLY the transcription."
        ),
    },
    {
        "id": "catmus",
        "label": "CATMuS 详细",
        "hint": "DocWorkflow 配置里的长版本：保留缩写、统一 u/i、不记录异体字。",
        "prompt": (
            "Transcribe the handwritten text in this line image.\n"
            "Keep abbreviations as written, do not expand them.\n"
            "Modernize word segmentation (split or join words following modern usage).\n"
            "Use only u and i, never v and j, regardless of the original or modern usage.\n"
            "Do not record allographic variants, use standard letter forms.\n"
            "Output ONLY the transcription."
        ),
    },
]


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Recognizer:
    def __init__(self) -> None:
        self.model_path = os.environ.get(
            "MEDUSA_MODEL_PATH",
            str(Path(__file__).resolve().parent / "models" / "Medusa0.2Line-4B"),
        )
        self.device = os.environ.get("MEDUSA_DEVICE", "cuda:0")
        self.max_pixels = _int("MEDUSA_MAX_PIXELS", 401408)
        self.batch_size = max(1, _int("MEDUSA_BATCH", 4))
        self.max_new_tokens = max(1, _int("MEDUSA_MAX_NEW_TOKENS", 160))
        self.four_bit_mode = os.environ.get("MEDUSA_4BIT", "auto").strip().lower()
        self.warmup = _flag("MEDUSA_WARMUP", True)
        self.requested_attn = os.environ.get("MEDUSA_ATTN", "").strip()
        self.requested_mode = os.environ.get("MEDUSA_MODE", "auto").strip().lower()

        self._state = "idle"
        self._detail = "权重尚未加载"
        self._error = ""
        self._backend: Optional[str] = None
        self._torch = None
        self._processor = None
        self._model = None
        self._load_lock = threading.Lock()
        self._gen_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # status / probing
    # ------------------------------------------------------------------ #
    @property
    def backend(self) -> str:
        if self._backend:
            return self._backend
        return "model" if self.requested_mode == "model" else "mock"

    def status(self) -> dict:
        return {
            "backend": self._backend or ("model" if self.requested_mode == "model" else "unknown"),
            "mode": self.requested_mode,
            "state": self._state,
            "detail": self._detail,
            "error": self._error,
            "modelPath": self.model_path,
            "modelName": Path(self.model_path).name,
            "modelPresent": Path(self.model_path, "config.json").is_file(),
            "device": self.device,
            "batchSize": self.batch_size,
            "maxPixels": self.max_pixels,
            "maxNewTokens": self.max_new_tokens,
            "fourBit": self.uses_4bit(),
            "fourBitMode": self.four_bit_mode,
        }

    # ------------------------------------------------------------------ #
    # model selection / quantisation policy
    # ------------------------------------------------------------------ #
    def _weights_bytes(self, path: Optional[str] = None) -> int:
        total = 0
        root = Path(path or self.model_path)
        for pattern in ("*.safetensors", "*.bin"):
            for item in root.glob(pattern):
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
        return total

    def _vram_bytes(self) -> int:
        try:
            import torch

            if torch.cuda.is_available():
                return int(torch.cuda.get_device_properties(0).total_memory)
        except Exception:
            pass
        return 0

    def uses_4bit(self) -> bool:
        mode = self.four_bit_mode
        if mode in {"1", "true", "yes", "on"}:
            return True
        if mode in {"0", "false", "no", "off"}:
            return False
        weights = self._weights_bytes()
        if not weights:
            return False
        vram = self._vram_bytes()
        # 4B (~9GB) stays bf16 on a 12GB card; 9B (~18.8GB) needs NF4.
        return bool(vram and weights > vram * 0.82)

    def set_model_path(self, path: str) -> None:
        target = str(Path(path))
        with self._load_lock:
            self._model = None
            self._processor = None
            if self._torch is not None:
                try:
                    self._torch.cuda.empty_cache()
                except Exception:
                    pass
            self.model_path = target
            self._state = "idle"
            self._detail = "权重未加载"
            self._error = ""
            if self.requested_mode != "mock":
                self._backend = "model"

    def probe(self) -> None:
        """Decide whether the real backend is usable. Cheap-ish (imports torch)."""
        if self.requested_mode == "mock":
            self._set_mock("MEDUSA_MODE=mock")
            return
        if not Path(self.model_path, "config.json").is_file():
            self._set_mock(f"模型目录不存在: {self.model_path}")
            return
        try:
            import torch  # noqa: F401
        except Exception as exc:
            self._set_mock(f"torch 不可用: {exc}")
            return
        if str(self.device).startswith("cuda") and not torch.cuda.is_available():
            self._set_mock("CUDA 不可用")
            return
        self._backend = "model"
        self._state = "idle"
        self._detail = "就绪（权重未加载）"

    def _set_mock(self, reason: str) -> None:
        self._backend = "mock"
        self._state = "ready"
        self._detail = f"模拟后端：{reason}"
        self._error = ""

    # ------------------------------------------------------------------ #
    # loading
    # ------------------------------------------------------------------ #
    def ensure_loaded(self, on_progress=None) -> None:
        if self._backend is None:
            self.probe()
        if self._backend == "mock":
            self._state = "ready"
            return
        if self._state == "ready" and self._model is not None:
            return
        with self._load_lock:
            if self._state == "ready" and self._model is not None:
                return
            self._state = "loading"
            self._detail = "正在加载权重（首次约 30-90 秒）…"
            self._error = ""
            if on_progress:
                on_progress(self._detail)
            t0 = time.time()
            try:
                import torch
                from transformers import AutoModelForImageTextToText, AutoProcessor

                processor = AutoProcessor.from_pretrained(self.model_path)

                # The shipped processor allows 4096x4096 pages; for line crops we
                # want a sane floor so tiny strips are not upscaled.
                ip = getattr(processor, "image_processor", None)
                if ip is not None and hasattr(ip, "size"):
                    try:
                        ip.size = {
                            "shortest_edge": 28 * 28 * 4,
                            "longest_edge": max(self.max_pixels, 28 * 28 * 4),
                        }
                    except Exception:
                        pass

                kwargs: dict = {"device_map": self.device}
                use_4bit = self.uses_4bit()
                if use_4bit:
                    try:
                        import bitsandbytes  # noqa: F401
                    except Exception as exc:
                        raise RuntimeError(
                            "该模型需要 4-bit 量化，但 bitsandbytes 不可用："
                            f"{exc}（在 WSL 里执行 pip install bitsandbytes）"
                        ) from exc
                    from transformers import BitsAndBytesConfig

                    kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=torch.bfloat16,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_use_double_quant=True,
                    )
                    self._detail = "正在以 4-bit 加载权重…"
                else:
                    kwargs["dtype"] = torch.bfloat16
                if self.requested_attn:
                    kwargs["attn_implementation"] = self.requested_attn

                try:
                    model = AutoModelForImageTextToText.from_pretrained(self.model_path, **kwargs)
                except TypeError:
                    # older keyword spelling
                    if "dtype" in kwargs:
                        kwargs["torch_dtype"] = kwargs.pop("dtype")
                    model = AutoModelForImageTextToText.from_pretrained(self.model_path, **kwargs)

                model.eval()
                self._processor = processor
                self._model = model
                self._torch = torch

                if self.warmup:
                    self._detail = "预热中…"
                    if on_progress:
                        on_progress(self._detail)
                    try:
                        from PIL import Image

                        self._generate(
                            [Image.new("RGB", (512, 96), (235, 228, 210))],
                            "Transcribe this line.",
                            1,
                        )
                    except Exception:
                        pass

                self._state = "ready"
                self._detail = f"已加载 · {time.time() - t0:.0f}s"
                if on_progress:
                    on_progress(self._detail)
            except Exception:
                self._state = "error"
                self._error = traceback.format_exc()
                self._detail = "模型加载失败"
                raise

    def unload(self) -> None:
        with self._load_lock:
            self._model = None
            self._processor = None
            if self._torch is not None:
                try:
                    self._torch.cuda.empty_cache()
                except Exception:
                    pass
            self._state = "idle"
            self._detail = "权重已卸载"

    # ------------------------------------------------------------------ #
    # inference
    # ------------------------------------------------------------------ #
    def recognize(
        self,
        images: Sequence,
        prompt: str = DEFAULT_PROMPT,
        ground_truth: Optional[Sequence[Optional[str]]] = None,
        max_new_tokens: Optional[int] = None,
    ) -> list[str]:
        if self._backend is None:
            self.probe()
        if self._backend == "mock":
            out = []
            for i, _ in enumerate(images):
                gt = ground_truth[i] if ground_truth and i < len(ground_truth) else None
                out.append(gt if gt else f"[mock] line {i + 1}")
            return out

        self.ensure_loaded()
        torch = self._torch
        limit = int(max_new_tokens or self.max_new_tokens)
        images = list(images)
        gts = list(ground_truth or [])
        results: list[str] = []
        i = 0
        while i < len(images):
            chunk = images[i : i + self.batch_size]
            try:
                with self._gen_lock:
                    results.extend(self._generate(chunk, prompt, limit))
                i += len(chunk)
            except Exception as exc:
                oom = type(exc).__name__ == "OutOfMemoryError" or "out of memory" in str(exc).lower()
                if oom and len(chunk) > 1:
                    try:
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    self.batch_size = max(1, len(chunk) // 2)
                    continue
                raise
        return results

    # ------------------------------------------------------------------ #
    def _prep_image(self, img):
        from PIL import Image

        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        pixels = w * h
        scale = 1.0
        if pixels > self.max_pixels:
            scale = (self.max_pixels / float(pixels)) ** 0.5
        if scale < 1.0:
            nw, nh = max(32, int(w * scale)), max(32, int(h * scale))
            img = img.resize((nw, nh), Image.LANCZOS)
        return img

    def _generate(self, images: Sequence, prompt: str, max_new_tokens: int) -> list[str]:
        processor = self._processor
        model = self._model
        torch = self._torch

        images = [self._prep_image(img) for img in images]
        texts = []
        for image in images:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "image": image},
                    ],
                }
            ]
            try:
                text = processor.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            except TypeError:
                text = processor.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            texts.append(text)

        inputs = processor(text=texts, images=list(images), padding=True, return_tensors="pt")
        inputs = inputs.to(model.device)

        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
            )

        prompt_len = inputs["input_ids"].shape[1]
        new_tokens = generated[:, prompt_len:]
        decoded = processor.batch_decode(new_tokens, skip_special_tokens=True)
        return [_clean(text) for text in decoded]


def _clean(text: str) -> str:
    text = text.replace("<|im_end|>", "").replace("<|endoftext|>", "")
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()
