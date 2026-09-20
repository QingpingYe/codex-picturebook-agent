"""Local, offline self-contained HTML exporter."""

import html
import json
from dataclasses import dataclass
from pathlib import Path


REQUIRED_METADATA = (
    "project_id",
    "series_id",
    "title",
    "episode",
    "target_age",
    "total_pages",
    "generated_at",
)
REQUIRED_PAGE_FIELDS = (
    "page_number",
    "english_text",
    "chinese_translation",
    "illustration_description",
    "image_path",
)


@dataclass(frozen=True)
class ExportResult:
    html_path: str
    page_count: int
    missing_images: list[int]
    sections_rendered: list[str]
    sections_skipped: list[dict]


def _require(payload: dict, field: str) -> None:
    if field not in payload:
        raise ValueError(f"missing required field: {field}")


def _json_for_script(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )


def _valid_cards(value, required: tuple[str, str]) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get(required[0]), str)
        and item[required[0]].strip()
        and isinstance(item.get(required[1]), str)
        and item[required[1]].strip()
    ]


def _skipped(section: str, reason: str) -> dict:
    return {"section": section, "reason": reason}


def _render_cards(cards: list[dict], title_field: str) -> str:
    return "".join(
        f"""
        <article class="card">
          <h2>{html.escape(str(card[title_field]))}</h2>
          <p>{html.escape(str(card['description']))}</p>
        </article>
        """
        for card in cards
    )


def build_html(payload: dict, output_dir: Path) -> ExportResult:
    _require(payload, "project_metadata")
    _require(payload, "pages")
    metadata = payload["project_metadata"]
    pages = payload["pages"]
    if not isinstance(metadata, dict):
        raise ValueError("project_metadata must be an object")
    if not isinstance(pages, list) or not pages:
        raise ValueError("pages must be a non-empty list")

    for field in REQUIRED_METADATA:
        _require(metadata, field)
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ValueError(f"page {index} must be an object")
        for field in REQUIRED_PAGE_FIELDS:
            _require(page, field)

    missing_images: list[int] = []
    image_pool: dict[str, str] = {}
    script_pages: list[dict] = []
    for page in pages:
        image_path = Path(page["image_path"])
        if image_path.exists():
            image_name = image_path.name
            image_pool[image_name] = image_name
        else:
            image_name = "placeholder.svg"
            missing_images.append(page["page_number"])
        script_pages.append(
            {
                "page": page["page_number"],
                "english_text": page["english_text"],
                "chinese_translation": page["chinese_translation"],
                "illustration_description": page["illustration_description"],
                "image": image_name,
            }
        )

    rendered = ["header"]
    skipped: list[dict] = []
    overview_parts: list[str] = []
    if metadata.get("logline"):
        overview_parts.append(f'<p class="lead">{html.escape(str(metadata["logline"]))}</p>')
    if metadata.get("synopsis"):
        overview_parts.append(f'<p>{html.escape(str(metadata["synopsis"]))}</p>')
    if overview_parts:
        rendered.append("overview")
    else:
        skipped.append(_skipped("overview", "logline and synopsis are absent"))

    characters = _valid_cards(payload.get("characters"), ("name", "description"))
    locations = _valid_cards(payload.get("locations"), ("name", "description"))
    themes = _valid_cards(payload.get("themes"), ("title", "description"))
    for section, cards in (
        ("characters", characters),
        ("locations", locations),
        ("themes", themes),
    ):
        if cards:
            rendered.append(section)
        else:
            skipped.append(_skipped(section, f"{section}[] is absent or invalid"))

    rendered.append("storyboard")
    emotion_pages = [
        page
        for page in pages
        if isinstance(page.get("emotion_valence"), int)
        and -2 <= page["emotion_valence"] <= 2
    ]
    if len(emotion_pages) >= 2:
        rendered.append("emotion_arc")
    else:
        skipped.append(_skipped("emotion_arc", "fewer than two valid emotion values"))

    storyboard_cards = "\n".join(
        f"""
        <article class="page">
          <h3>{html.escape(str(page['english_text']))}</h3>
          <p>{html.escape(str(page['chinese_translation']))}</p>
          <p>{html.escape(str(page['illustration_description']))}</p>
        </article>
        """
        for page in pages
    )
    optional_sections = []
    if characters:
        optional_sections.append(
            f'<section aria-label="characters">{_render_cards(characters, "name")}</section>'
        )
    if locations:
        optional_sections.append(
            f'<section aria-label="locations">{_render_cards(locations, "name")}</section>'
        )
    if themes:
        optional_sections.append(
            f'<section aria-label="themes">{_render_cards(themes, "title")}</section>'
        )
    output_path = Path(output_dir).resolve() / (
        f"{metadata['project_id']}_ep{metadata['episode']}_storyboard.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(str(metadata['title']))} · 绘本预览</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; color: #17212b; background: #f7f5f0; }}
    main {{ max-width: 1080px; margin: 0 auto; padding: 24px 20px; }}
    .page {{ background: #fff; border: 1px solid #d9d3c7; margin: 0 0 16px; padding: 18px; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(str(metadata['title']))}</h1>
      <p>{html.escape(str(metadata['target_age']))} · Episode {html.escape(str(metadata['episode']))}</p>
    </header>
    {''.join(overview_parts)}
    {''.join(optional_sections)}
    <section aria-label="storyboard">{storyboard_cards}</section>
  </main>
  <script>
    const IMGS = {_json_for_script(image_pool)};
    const PAGES = {_json_for_script(script_pages)};
    document.title = document.title;
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )

    return ExportResult(
        html_path=str(output_path),
        page_count=len(pages),
        missing_images=missing_images,
        sections_rendered=rendered,
        sections_skipped=skipped,
    )
