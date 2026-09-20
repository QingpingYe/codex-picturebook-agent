"""Embed local images into an HTML preview and enforce its size limit."""

import base64
import io
import json
import re
import sys
from pathlib import Path


QUALITY_LADDER = (70, 65, 55, 45)


def _usage() -> str:
    return (
        "usage: embed_images.py <html_path> --limit-mb N --quality Q "
        "[--img-dir DIR] [--max-long-edge PX]"
    )


def load_pillow():
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image


def parse_args(argv: list[str]):
    values = {"html_path": None, "img_dir": None, "limit_mb": None, "quality": None, "max_long_edge": None}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--img-dir":
            values["img_dir"] = argv[index + 1]
            index += 2
        elif arg == "--limit-mb":
            values["limit_mb"] = float(argv[index + 1])
            index += 2
        elif arg == "--quality":
            values["quality"] = int(argv[index + 1])
            index += 2
        elif arg == "--max-long-edge":
            values["max_long_edge"] = int(argv[index + 1])
            index += 2
        elif values["html_path"] is None:
            values["html_path"] = arg
            index += 1
        else:
            return None

    if not values["html_path"] or values["limit_mb"] is None or values["quality"] is None:
        return None
    return values


def find_pool(html: str):
    match = re.search(r"var\s+IMGS\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
    if not match:
        return None
    try:
        pool = json.loads(match.group(1))
    except (TypeError, ValueError):
        return None
    if not isinstance(pool, dict):
        return None
    return match, pool


def resolve_file(html_dir: Path, img_dir: Path, key: str, value: str) -> Path | None:
    candidates = []
    if value and value != key:
        candidates.append(html_dir / value)
    candidates.extend([img_dir / key, html_dir / key])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def to_jpeg_bytes(raw: bytes, quality: int, max_long_edge: int | None, Image):
    image = Image.open(io.BytesIO(raw))
    if image.mode in ("RGBA", "LA"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    if max_long_edge and max(image.size) > max_long_edge:
        scale = max_long_edge / max(image.size)
        image = image.resize(
            (max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale)))
        )

    output = io.BytesIO()
    image.save(output, "JPEG", quality=quality, optimize=True)
    return output.getvalue()


def render(html: str, span: tuple[int, int], pool: dict, raw_map: dict, quality: int, max_long_edge: int | None, Image):
    start, end = span
    data_url_pool = {}
    for key, value in pool.items():
        jpeg = to_jpeg_bytes(raw_map[f"__raw__:{key}"], quality, max_long_edge, Image)
        data_url_pool[key] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    return html[:start] + json.dumps(data_url_pool, ensure_ascii=False) + html[end:]


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args is None:
        print(_usage(), file=sys.stderr)
        return 1

    html_path = Path(args["html_path"])
    if not html_path.is_file():
        print(f"HTML file not found: {html_path}", file=sys.stderr)
        return 1

    html = html_path.read_text(encoding="utf-8")
    pool_info = find_pool(html)
    if pool_info is None:
        print("IMGS pool not found", file=sys.stderr)
        return 1

    match, pool = pool_info
    img_dir = Path(args["img_dir"] or html_path.parent)
    raw_map = {}
    for key, value in pool.items():
        source = resolve_file(html_path.parent, img_dir, key, value)
        if source is None:
            print(f"missing image for IMGS key: {key}", file=sys.stderr)
            return 1
        raw_map[f"__raw__:{key}"] = source.read_bytes()

    Image = load_pillow()
    if Image is None:
        print("Pillow is unavailable; refusing to leave external image paths", file=sys.stderr)
        return 1

    limit_bytes = args["limit_mb"] * 1024 * 1024
    best_html = None
    for quality in [args["quality"], *(q for q in QUALITY_LADDER if q < args["quality"])]:
        candidate = render(html, match.span(1), pool, raw_map, quality, None, Image)
        if len(candidate.encode("utf-8")) < limit_bytes:
            best_html = candidate
            break
        best_html = candidate

    if len(best_html.encode("utf-8")) >= limit_bytes:
        print("HTML exceeds size limit after quality fallback", file=sys.stderr)
        return 2

    html_path.write_text(best_html, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
