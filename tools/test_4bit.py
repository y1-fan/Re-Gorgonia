"""Smoke test for the 4-bit path used by the 9B model (uses the local 4B weights)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["MEDUSA_4BIT"] = "1"
os.environ["MEDUSA_MODE"] = "model"
os.environ["MEDUSA_MODEL_PATH"] = str(
    (Path(__file__).resolve().parent.parent / "models" / "Medusa0.2Line-4B")
)
os.environ["MEDUSA_WARMUP"] = "0"

from PIL import Image

from inference import DEFAULT_PROMPT, Recognizer

recognizer = Recognizer()
recognizer.probe()
print("status before:", recognizer.status()["fourBit"], recognizer.status()["state"], flush=True)
image = Image.new("RGB", (640, 120), (240, 235, 220))
out = recognizer.recognize([image], DEFAULT_PROMPT, ["test"], max_new_tokens=8)
print("out:", out, flush=True)
status = recognizer.status()
print("status after:", status["state"], "fourBit:", status["fourBit"], "detail:", status["detail"], flush=True)
print("peak VRAM GB:", round(recognizer._torch.cuda.max_memory_allocated() / 1e9, 2), flush=True)
