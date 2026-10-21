"""Smoke test: load MEDUSA in WSL and transcribe one sample line."""
import sys
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

HERE = Path(__file__).resolve().parent
MODEL = sys.argv[1] if len(sys.argv) > 1 else str(HERE.parent / "models" / "Medusa0.2Line-4B")
IMAGE = sys.argv[2] if len(sys.argv) > 2 else str(HERE.parent / "workspace" / "samples" / "page_01.jpg")

t0 = time.time()
processor = AutoProcessor.from_pretrained(MODEL)
print(f"processor loaded in {time.time() - t0:.1f}s", flush=True)

t0 = time.time()
model = AutoModelForImageTextToText.from_pretrained(
    MODEL, dtype=torch.bfloat16, device_map="cuda:0"
)
model.eval()
print(f"model loaded in {time.time() - t0:.1f}s", flush=True)

image = Image.open(IMAGE).convert("RGB")
crop = image.crop((120, 220, 1420, 300))

prompt = (
    "Transcribe the handwritten text in this line image.\n"
    "Keep abbreviations as written, do not expand them.\n"
    "Modernize word segmentation (split or join words following modern usage).\n"
    "Use only u and i, never v and j, regardless of the original or modern usage.\n"
    "Do not record allographic variants, use standard letter forms.\n"
    "Output ONLY the transcription."
)

messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image", "image": crop},
        ],
    }
]
text = processor.apply_chat_template(
    messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
)
inputs = processor(text=[text], images=[crop], return_tensors="pt").to("cuda:0")
print("input ids:", inputs["input_ids"].shape, flush=True)

t0 = time.time()
with torch.inference_mode():
    out = model.generate(**inputs, max_new_tokens=80, do_sample=False)
print(f"generate in {time.time() - t0:.1f}s", flush=True)
new = out[:, inputs["input_ids"].shape[1]:]
print("OUT:", repr(processor.batch_decode(new, skip_special_tokens=True)), flush=True)
print(f"peak VRAM {torch.cuda.max_memory_allocated() / 1e9:.2f} GB", flush=True)
