"""Small, namespace-agnostic ALTO XML reader.

Only the pieces this app needs:

* the page image (from ``sourceImageInformation/fileName`` or the file stem)
* the page size, so ALTO units can be mapped onto real image pixels
  (works for ``pixel``, ``mm10``, ``inch1200`` ... because we scale by the
  ratio between the ALTO page box and the actual bitmap size)
* the text lines with their bounding boxes (and optional polygons)
* the reference transcription (concatenated ``String`` contents)

Tested shapes: ALTO v2/v3/v4, lines nested in PrintSpace/TextBlock or directly
in PrintSpace, coordinates on the line or only on its ``String`` children.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")

_WORD_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# XML helpers
# --------------------------------------------------------------------------- #
def _ln(tag: object) -> str:
    """Local name of a tag (drops ``{namespace}``)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _iter(root: ET.Element, name: str) -> Iterator[ET.Element]:
    for el in root.iter():
        if _ln(el.tag) == name:
            yield el


def _text(root: ET.Element, name: str) -> Optional[str]:
    el = next(_iter(root, name), None)
    if el is None or el.text is None:
        return None
    return el.text.strip()


def _attr(el: Optional[ET.Element], name: str) -> Optional[str]:
    if el is None:
        return None
    v = el.get(name)
    if v is None:
        v = el.get(name.lower())
    if v is None:
        v = el.get(name.upper())
    return v


def _num(el: Optional[ET.Element], name: str) -> Optional[float]:
    v = _attr(el, name)
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _box(el: Optional[ET.Element]) -> Optional[tuple[float, float, float, float]]:
    x, y = _num(el, "HPOS"), _num(el, "VPOS")
    w, h = _num(el, "WIDTH"), _num(el, "HEIGHT")
    if x is None or y is None or w is None or h is None:
        return None
    return (x, y, w, h)


def _parse_points(pts: str) -> Optional[list[tuple[float, float]]]:
    """Parse POINTS in either ``x,y x,y`` or ``x y x y`` form."""
    cleaned = pts.replace(",", " ").replace(";", " ")
    numbers: list[float] = []
    for token in cleaned.split():
        try:
            numbers.append(float(token))
        except ValueError:
            continue
    if len(numbers) < 6 or len(numbers) % 2:
        return None
    return [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers), 2)]


def _polygon(el: ET.Element) -> Optional[list[tuple[float, float]]]:
    # Look at the TextLine's own <Shape>, not at polygons that may belong to a
    # nested <String> (some exporters attach glyph outlines there).
    for child in el:
        if _ln(child.tag) == "Shape":
            poly = next(_iter(child, "Polygon"), None)
            if poly is not None:
                points = _parse_points(_attr(poly, "POINTS") or "")
                if points:
                    return points
    for child in el:
        if _ln(child.tag) == "Polygon":
            points = _parse_points(_attr(child, "POINTS") or "")
            if points:
                return points
    return None


def _line_text(tl: ET.Element) -> str:
    """Concatenate String/SP/HYP children into a plain transcription.

    ALTO producers are inconsistent: some insert ``<SP/>`` elements, others rely
    on the geometrical gap between ``String`` boxes, others embed the spaces in
    CONTENT.  Handle all three and normalise the whitespace.
    """
    parts: list[str] = []
    last_right: Optional[float] = None
    for child in tl:
        name = _ln(child.tag)
        if name == "String":
            content = _attr(child, "CONTENT")
            box = _box(child)
            if content and parts and last_right is not None and box:
                gap = box[0] - last_right
                height = box[3] or 0.0
                if gap > max(2.0, height * 0.12):
                    parts.append(" ")
            if content:
                parts.append(content)
            if box:
                last_right = box[0] + box[2]
        elif name == "SP":
            parts.append(" ")
            box = _box(child)
            last_right = (box[0] + box[2]) if box else None
        elif name == "HYP":
            parts.append(_attr(child, "CONTENT") or "")
            box = _box(child)
            if box:
                last_right = box[0] + box[2]
        elif name == "TextLine":
            # weird but seen: nested lines -> keep their text as well
            parts.append(" " + _line_text(child))
            last_right = None
    return _WORD_RE.sub(" ", "".join(parts)).strip()


def _require_box(tl: ET.Element) -> Optional[tuple[float, float, float, float]]:
    box = _box(tl)
    if box and box[2] > 0 and box[3] > 0:
        return box
    # some exporters only put coordinates on the String elements
    child_boxes: list[tuple[float, float, float, float]] = []
    for child in tl:
        if _ln(child.tag) in ("String", "SP", "HYP", "Glyph"):
            b = _box(child)
            if b and b[2] >= 0 and b[3] >= 0:
                child_boxes.append(b)
    if not child_boxes:
        # last resort: the polygon itself
        poly = _polygon(tl)
        if poly and len(poly) >= 3:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            if x1 - x0 > 0 and y1 - y0 > 0:
                return (x0, y0, x1 - x0, y1 - y0)
        return None
    x0 = min(b[0] for b in child_boxes)
    y0 = min(b[1] for b in child_boxes)
    x1 = max(b[0] + b[2] for b in child_boxes)
    y1 = max(b[1] + b[3] for b in child_boxes)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


# --------------------------------------------------------------------------- #
# Image discovery
# --------------------------------------------------------------------------- #
def _case_insensitive_lookup(directory: Path, filename: str) -> Optional[Path]:
    if not directory.is_dir():
        return None
    target = filename.lower()
    try:
        for f in directory.iterdir():
            if f.is_file() and f.name.lower() == target:
                return f
    except OSError:
        return None
    return None


def _resolve_relative(base: Path, name: str) -> Optional[Path]:
    name = name.strip().replace("\\", "/")
    if not name:
        return None
    rel = Path(name)
    candidates = [base / rel, base / rel.name, base.parent / rel, base.parent / rel.name]
    try:
        candidates += [base.parent.parent / rel]
    except OSError:
        pass
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    found = _case_insensitive_lookup(base, rel.name) or _case_insensitive_lookup(
        base.parent, rel.name
    )
    if found is None:
        found = _case_insensitive_lookup(base / "images", rel.name)
    return found


def find_image(xml_path: Path, root: Optional[ET.Element] = None) -> Optional[Path]:
    """Locate the bitmap that belongs to an ALTO file."""
    xml_path = Path(xml_path)
    base = xml_path.parent
    if root is None:
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            root = None

    if root is not None:
        for el in _iter(root, "fileName"):
            if not el.text:
                continue
            resolved = _resolve_relative(base, el.text)
            if resolved:
                return resolved

    # same stem, any known image extension
    for ext in IMAGE_EXTS:
        for cand in (base / (xml_path.stem + ext), base / (xml_path.stem + ext.upper())):
            if cand.is_file():
                return cand
        found = _case_insensitive_lookup(base, xml_path.stem + ext)
        if found:
            return found

    # last resort: an image whose name starts with the ALTO stem
    try:
        entries = [f for f in base.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
    except OSError:
        entries = []
    stem = xml_path.stem.lower()
    for f in sorted(entries):
        if f.stem.lower() == stem:
            return f
    for f in sorted(entries):
        if f.stem.lower().startswith(stem):
            return f
    return None


def probe_image(path: Optional[Path]) -> Optional[tuple[int, int]]:
    if not path:
        return None
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Public data model
# --------------------------------------------------------------------------- #
@dataclass
class Line:
    id: str
    order: int
    box: tuple[float, float, float, float]  # image pixels
    polygon: Optional[list[tuple[float, float]]] = None
    gt: str = ""


@dataclass
class AltoPage:
    path: Path
    image: Optional[Path]
    page_width: float
    page_height: float
    unit: str
    image_width: Optional[int]
    image_height: Optional[int]
    lines: list[Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_alto(xml_path: Path | str, image_path: Optional[Path | str] = None) -> AltoPage:
    xml_path = Path(xml_path)
    tree = ET.parse(xml_path)
    root = tree.getroot()

    unit = (_text(root, "MeasurementUnit") or "pixel").strip() or "pixel"
    image = Path(image_path) if image_path else find_image(xml_path, root)
    size = probe_image(image)

    page = next(_iter(root, "Page"), None)
    pw = _num(page, "WIDTH") or 0.0
    ph = _num(page, "HEIGHT") or 0.0

    warnings: list[str] = []
    if image is None:
        warnings.append("未找到与 ALTO 对应的图片")
    elif size is None:
        warnings.append("图片无法读取")
    if not pw or not ph:
        warnings.append("ALTO 缺少 Page 宽高，按 1:1 处理")

    raw: list[tuple[ET.Element, tuple[float, float, float, float]]] = []
    if page is not None:
        for tl in _iter(page, "TextLine"):
            b = _require_box(tl)
            if b is not None:
                raw.append((tl, b))
    if not raw:
        warnings.append("ALTO 中没有带坐标的 TextLine")

    # Some exporters emit coordinates relative to PrintSpace although ALTO
    # specifies page-absolute values. Detect and fix the obvious case.
    if raw and pw and ph:
        ps = next(_iter(page, "PrintSpace"), None)
        psb = _box(ps) if ps is not None else None
        if psb and (psb[0] or psb[1]):
            max_x = max(b[0] + b[2] for _, b in raw)
            max_y = max(b[1] + b[3] for _, b in raw)
            if max_x > pw * 1.02 or max_y > ph * 1.02:
                if max_x <= pw - psb[0] + 2 and max_y <= ph - psb[1] + 2:
                    offset = (psb[0], psb[1])
                    raw = [
                        (el, (b[0] + offset[0], b[1] + offset[1], b[2], b[3]))
                        for el, b in raw
                    ]
                    warnings.append(
                        f"检测到相对 PrintSpace 的坐标，已平移 {offset[0]:g},{offset[1]:g}"
                    )

    iw, ih = size if size else (None, None)
    sx = (iw / pw) if (iw and pw) else 1.0
    sy = (ih / ph) if (ih and ph) else 1.0

    lines: list[Line] = []
    seen: dict[str, int] = {}
    for order, (el, b) in enumerate(raw):
        x, y, w, h = b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy
        if iw and ih:
            x0, y0 = max(0.0, min(x, iw)), max(0.0, min(y, ih))
            x1, y1 = max(0.0, min(x + w, iw)), max(0.0, min(y + h, ih))
            x, y, w, h = x0, y0, x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue

        lid = (_attr(el, "ID") or f"line_{order + 1:04d}").strip() or f"line_{order + 1:04d}"
        if lid in seen:
            seen[lid] += 1
            lid = f"{lid}_{seen[lid]}"
        else:
            seen[lid] = 0

        poly = _polygon(el)
        if poly:
            poly = [(px * sx, py * sy) for px, py in poly]

        lines.append(
            Line(
                id=lid,
                order=order,
                box=(round(x, 2), round(y, 2), round(w, 2), round(h, 2)),
                polygon=poly,
                gt=_line_text(el),
            )
        )

    return AltoPage(
        path=xml_path,
        image=image,
        page_width=pw,
        page_height=ph,
        unit=unit,
        image_width=iw,
        image_height=ih,
        lines=lines,
        warnings=warnings,
    )
