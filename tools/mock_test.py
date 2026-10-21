import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from inference import DEFAULT_PROMPT, Recognizer

r = Recognizer()
r.probe()
s = r.status()
print(s["backend"], s["state"], "|", s["detail"])
print(r.recognize([Image.new("RGB", (100, 40))] * 2, DEFAULT_PROMPT, ["abc def", "ghi"]))
