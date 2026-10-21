"""Generate a small synthetic manuscript workspace (page images + ALTO v4).

The images are fake parchment with jittered "handwritten" lines, but the ALTO
coordinates are exact: every String/TextLine/TextBlock box is computed from the
actual ink, so the overlay and the crop pipeline can be validated end to end.

    python tools/make_sample.py [--out workspace/samples] [--pages 3]
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"

TEXT = [
    "In nomine domini nostri ihesu christi incipit liber iste de uita et moribus sanctorum patrum",
    "et uos karissimi fratres attendite uerba hec que scripta sunt in hoc uolumine paruo",
    "quoniam omnis scriptura diuinitus inspirata utilis est ad docendum et ad arguen dum",
    "sicut dicit apostolus ubi est sapiens ubi scriba ubi disputator huius seculi",
    "nonne stultam fecit deus sapientiam huius mundi et elegit infirma ut confundat fortia",
    "et quia in sapientia dei non cognouit mundus per sapientiam deum placuit deo saluare credentes",
    "ideoque sicut scriptum est qui gloriatur in domino glorietur non in se ipso",
    "Ci commence li romans de la rose ou lart damours est toute enclose et dedens clouse",
    "et dist li uns a lautre biaus dous amis que ferons nous ore mes que uenus est close",
    "car uous auez tant attendu et tant souffert que bien deuez auoir merci de moi",
    "quant il ot ce dit si se leua et prist congie de la dame qui moult fu dolente",
    "et puis sen ala par une uoie estroite ou il auoit moult de espines et de ronces",
    "dominus uobiscum et cum spiritu tuo oremus fratres karissimi ut per intercessionem",
    "beati petri apostoli et omnium sanctorum dignetur nos deus omnipotens liberare",
    "ab omni malo et in sancta ecclesia catholica custodire per eundem christum",
    "factum est autem cum complerentur dies pentecostes erant omnes pariter in eodem loco",
    "et apparuerunt illis dispertitae linguae tamquam ignis seditque supra singulos eorum",
    "et repleti sunt omnes spiritu sancto et ceperunt loqui uariis linguis prout spiritus dabat",
    "apostolus dicit fidem ex auditu et auditum per uerbum christi sedentes in tenebris",
    "lux orta est eis et his qui in regione umbrae mortis habitabant lux uisa est",
    "beatus uir qui non abiit in consilio impiorum et in uia peccatorum non stetit",
    "sed in lege domini uoluntas eius et in lege eius meditabitur die ac nocte",
    "et erit tamquam lignum quod plantatum est secus decursus aquarum quod fructum suum",
    "non ita impii non sic sed tamquam puluis quem proicit uentus a facie terre",
    "propterea non resurgunt impii in iudicio neque peccatores in consilio iustorum",
    "quoniam nouit dominus uiam iustorum et iter impiorum peribit in eternum",
]

RUBRICS = [
    "Incipit prologus sancti hieronimi presbiteri",
    "Capitulum primum de creatione mundi",
    "Sermo beati augustini episcopi de caritate",
]

# first candidate that exists wins
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\constani.ttf",
    r"C:\Windows\Fonts\georgiai.ttf",
    r"C:\Windows\Fonts\constan.ttf",
    r"C:\Windows\Fonts\georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
]

BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\constanb.ttf",
    r"C:\Windows\Fonts\georgiab.ttf",
    r"C:\Windows\Fonts\GOTHICB.TTF",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
]


def load_font(size: int, candidates: list[str] | None = None) -> ImageFont.FreeTypeFont:
    for path in candidates or FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)



def parchment(width: int, height: int, rng: random.Random) -> Image.Image:
    base = Image.new("RGB", (width, height), (234, 223, 200))

    # paper grain
    grain = Image.effect_noise((width, height), 26).convert("L").filter(ImageFilter.GaussianBlur(0.6))
    grain = grain.point(lambda v: 226 + int(v * 0.11))
    base = ImageChops.multiply(base, Image.merge("RGB", (grain, grain, grain)))

    # large tonal blotches
    blotch = Image.new("L", (width // 8, height // 8), 128)
    bd = ImageDraw.Draw(blotch)
    for _ in range(26):
        x, y = rng.randrange(blotch.width), rng.randrange(blotch.height)
        r = rng.randint(6, 30)
        bd.ellipse((x - r, y - r, x + r, y + r), fill=rng.randint(110, 150))
    blotch = blotch.resize((width, height), Image.BICUBIC).filter(ImageFilter.GaussianBlur(40))
    warm = Image.new("RGB", (width, height), (208, 190, 158))
    mask = blotch.point(lambda v: max(0, min(60, int((v - 110) * 0.9))))
    base = Image.composite(ImageChops.multiply(base, warm), base, mask)

    # fibres
    fib = Image.new("L", (width, height), 0)
    fd = ImageDraw.Draw(fib)
    for _ in range(260):
        x0, y0 = rng.randrange(width), rng.randrange(height)
        ln = rng.randint(8, 60)
        ang = rng.uniform(-0.25, 0.25)
        fd.line((x0, y0, x0 + ln * math.cos(ang), y0 + ln * math.sin(ang)), fill=rng.randint(20, 70), width=1)
    fib = fib.filter(ImageFilter.GaussianBlur(0.7))
    base = ImageChops.subtract(base, Image.merge("RGB", (fib, fib, fib)))

    # foxing / stains
    stains = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stains)
    for _ in range(14):
        x, y = rng.randrange(width), rng.randrange(height)
        rx, ry = rng.randint(10, 60), rng.randint(8, 40)
        sd.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(122, 88, 44, rng.randint(10, 26)))
    stains = stains.filter(ImageFilter.GaussianBlur(9))
    base = Image.alpha_composite(base.convert("RGBA"), stains).convert("RGB")

    # vignette
    vign = Image.radial_gradient("L").resize((width, height), Image.BICUBIC)
    vign = vign.point(lambda v: 255 - int(v * 0.30))
    base = ImageChops.multiply(base, Image.merge("RGB", (vign, vign, vign)))
    return base


def wrap_words(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[list[str]]:
    words = text.split()
    lines: list[list[str]] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word])
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(current)
    return lines


def render_page(path: Path, width: int, height: int, seed: int, mm10: bool = False) -> dict:
    rng = random.Random(seed)
    page = parchment(width, height, rng)
    draw = ImageDraw.Draw(page, "RGBA")

    margin_x, margin_top = 128, 138
    max_width = width - 2 * margin_x
    body_size = 36
    font = load_font(body_size)
    font_small = load_font(body_size - 1)
    font_tiny = load_font(body_size + 1)

    blocks: list[list[dict]] = []

    # rubric
    rubric_font = load_font(42, BOLD_CANDIDATES)
    rubric = rng.choice(RUBRICS)
    ry = margin_top
    rx = margin_x + rng.randint(0, 80)
    draw.text((rx, ry), rubric, font=rubric_font, fill=(150, 44, 32, 232))
    rb = draw.textbbox((rx, ry), rubric, font=rubric_font)
    rubric_line = {
        "words": [{"text": rubric, "box": [rb[0], rb[1], rb[2] - rb[0], rb[3] - rb[1]]}],
        "box": [rb[0], rb[1], rb[2] - rb[0], rb[3] - rb[1]],
    }
    blocks.append([rubric_line])
    y = ry + (rb[3] - rb[1]) + 58

    sentences = TEXT[:]
    rng.shuffle(sentences)
    si = 0
    line_gap = 78
    while y + line_gap < height - 120 and si < len(sentences):
        words = sentences[si].split()
        si += 1
        # usually one visual line; occasionally wrap so narrow boxes are tested
        if rng.random() < 0.18:
            wrapped = wrap_words(draw, " ".join(words), font, max_width)
        else:
            fitted: list[str] = []
            for word in words:
                if draw.textlength(" ".join(fitted + [word]), font=font) > max_width:
                    break
                fitted.append(word)
            wrapped = [fitted] if fitted else []
        for words in wrapped:
            if y + line_gap >= height - 110:
                break
            x = margin_x + rng.randint(-4, 10)
            line_words = []
            baseline_jitter = rng.uniform(-1.5, 1.5)
            for wi, word in enumerate(words):
                f = (font, font_small, font_tiny)[rng.randrange(3)]
                wpx = draw.textlength(word, font=f)
                dy = baseline_jitter + rng.uniform(-1.4, 1.4)
                alpha = rng.randint(205, 246)
                ink = (43 + rng.randint(-6, 6), 31 + rng.randint(-4, 4), 22 + rng.randint(-4, 4), alpha)
                bx, by = x, y + dy
                draw.text((bx, by), word, font=f, fill=ink)
                bb = draw.textbbox((bx, by), word, font=f)
                line_words.append(
                    {"text": word, "box": [bb[0], bb[1], max(1, bb[2] - bb[0]), max(1, bb[3] - bb[1])]}
                )
                gap = draw.textlength(" ", font=f) * rng.uniform(0.9, 1.5)
                x += wpx + gap
            if not line_words:
                continue
            x0 = min(w["box"][0] for w in line_words)
            y0 = min(w["box"][1] for w in line_words) - 2
            x1 = max(w["box"][0] + w["box"][2] for w in line_words)
            y1 = max(w["box"][1] + w["box"][3] for w in line_words) + 2
            blocks.append([{"words": line_words, "box": [x0, y0, x1 - x0, y1 - y0]}])
            y += line_gap

    # ink bleed
    page = page.filter(ImageFilter.GaussianBlur(0.35))
    page.save(path, "JPEG", quality=90, optimize=True)

    text_blocks = [[line for line in block] for block in blocks]
    return {"blocks": text_blocks, "size": (width, height), "mm10": mm10}


def write_alto(xml_path: Path, image_name: str, width: int, height: int, blocks, mm10: bool) -> None:
    scale = 2.0 if mm10 else 1.0

    def s(v: float) -> str:
        return str(int(round(v / scale)))

    ET.register_namespace("", ALTO_NS)
    root = ET.Element(f"{{{ALTO_NS}}}alto")
    desc = ET.SubElement(root, f"{{{ALTO_NS}}}Description")
    ET.SubElement(desc, f"{{{ALTO_NS}}}MeasurementUnit").text = "mm10" if mm10 else "pixel"
    source = ET.SubElement(desc, f"{{{ALTO_NS}}}sourceImageInformation")
    ET.SubElement(source, f"{{{ALTO_NS}}}fileName").text = image_name

    layout = ET.SubElement(root, f"{{{ALTO_NS}}}Layout")
    page = ET.SubElement(
        layout,
        f"{{{ALTO_NS}}}Page",
        {
            "ID": f"page_{xml_path.stem}",
            "PHYSICAL_IMG_NR": "1",
            "WIDTH": s(width),
            "HEIGHT": s(height),
        },
    )
    ps = ET.SubElement(
        page,
        f"{{{ALTO_NS}}}PrintSpace",
        {"HPOS": "0", "VPOS": "0", "WIDTH": s(width), "HEIGHT": s(height)},
    )

    block_boxes = [line["box"] for block in blocks for line in block]
    bx0 = min(b[0] for b in block_boxes)
    by0 = min(b[1] for b in block_boxes)
    bx1 = max(b[0] + b[2] for b in block_boxes)
    by1 = max(b[1] + b[3] for b in block_boxes)
    tb = ET.SubElement(
        ps,
        f"{{{ALTO_NS}}}TextBlock",
        {
            "ID": f"TB_{xml_path.stem}",
            "HPOS": s(bx0),
            "VPOS": s(by0),
            "WIDTH": s(bx1 - bx0),
            "HEIGHT": s(by1 - by0),
        },
    )

    for li, block in enumerate(blocks):
        for line in block:
            lb = line["box"]
            tl = ET.SubElement(
                tb,
                f"{{{ALTO_NS}}}TextLine",
                {
                    "ID": f"L{li + 1:04d}",
                    "HPOS": s(lb[0]),
                    "VPOS": s(lb[1]),
                    "WIDTH": s(lb[2]),
                    "HEIGHT": s(lb[3]),
                },
            )
            # slightly slanted quad, like a real hand-drawn text-line polygon
            j1 = ((li * 13) % 9) - 4
            j2 = ((li * 7) % 9) - 4
            quad = [
                (lb[0], lb[1] + j1),
                (lb[0] + lb[2], lb[1] + j2),
                (lb[0] + lb[2], lb[1] + lb[3] - j1),
                (lb[0], lb[1] + lb[3] - j2),
            ]
            shape = ET.SubElement(tl, f"{{{ALTO_NS}}}Shape")
            ET.SubElement(
                shape,
                f"{{{ALTO_NS}}}Polygon",
                {"POINTS": " ".join(f"{s(px)},{s(py)}" for px, py in quad)},
            )
            for wi, word in enumerate(line["words"]):
                wb = word["box"]
                ET.SubElement(
                    tl,
                    f"{{{ALTO_NS}}}String",
                    {
                        "ID": f"L{li + 1:04d}_S{wi + 1:03d}",
                        "CONTENT": word["text"],
                        "HPOS": s(wb[0]),
                        "VPOS": s(wb[1]),
                        "WIDTH": s(wb[2]),
                        "HEIGHT": s(wb[3]),
                    },
                )

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "workspace" / "samples"))
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--width", type=int, default=1500)
    parser.add_argument("--height", type=int, default=2000)
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sizes = [(args.width, args.height), (args.width, args.height), (args.width - 160, args.height)]

    for i in range(args.pages):
        w, h = sizes[min(i, len(sizes) - 1)]
        stem = f"page_{i + 1:02d}"
        img_path = out / f"{stem}.jpg"
        xml_path = out / f"{stem}.xml"
        mm10 = i == 2
        data = render_page(img_path, w, h, seed=1000 + i, mm10=mm10)
        write_alto(xml_path, img_path.name, w, h, data["blocks"], mm10)
        n_lines = sum(len(b) for b in data["blocks"])
        print(f"{img_path.name}: {w}x{h}, {n_lines} lines{' (mm10)' if mm10 else ''}")

    print(f"workspace: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
