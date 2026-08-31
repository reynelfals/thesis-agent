#!/usr/bin/env python3
"""Render the Thesis judge deck, one-page PDF, and narrated demo video."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "submission_assets"
SLIDES_DIR = ASSETS / "slides"
AUDIO_DIR = ASSETS / "audio"
PARTS_DIR = ASSETS / "video_parts"
AUDIO_TEMPO = 1.00

WIDTH = 1920
HEIGHT = 1080

BG = "#F4F3EC"
INK = "#0A2B27"
TEAL = "#006B62"
MINT = "#D9F5E9"
LIME = "#D8FF4E"
CORAL = "#E45C3F"
WHITE = "#FFFFFF"
MUTED = "#55706B"
LINE = "#9BB0AA"
SOFT = "#E8E8DE"
DARK = "#0C312C"

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_MONO if mono else FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(path, size)


def wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    text_font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textlength(candidate, font=text_font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    text_font: ImageFont.FreeTypeFont,
    *,
    fill: str = INK,
    max_width: int,
    line_gap: int = 10,
    max_lines: int | None = None,
) -> int:
    x, y = xy
    lines = wrap_lines(draw, text, text_font, max_width)
    if max_lines is not None:
        lines = lines[:max_lines]
    bbox = draw.textbbox((0, 0), "Ag", font=text_font)
    line_height = bbox[3] - bbox[1] + line_gap
    for line in lines:
        draw.text((x, y), line, font=text_font, fill=fill)
        y += line_height
    return y


def hexagon(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int, fill: str) -> None:
    cx, cy = center
    points = [
        (
            cx + radius * math.cos(math.radians(60 * index - 30)),
            cy + radius * math.sin(math.radians(60 * index - 30)),
        )
        for index in range(6)
    ]
    draw.polygon(points, fill=fill)


def base_slide(number: int, kicker: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    for x in range(0, WIDTH, 96):
        draw.line((x, 0, x, HEIGHT), fill="#EEEEE6", width=1)
    for y in range(0, HEIGHT, 96):
        draw.line((0, y, WIDTH, y), fill="#EEEEE6", width=1)

    hexagon(draw, (94, 68), 24, TEAL)
    draw.text((132, 42), "THESIS", font=font(34, bold=True), fill=INK)
    draw.text((291, 55), "/ audit-first autonomy", font=font(16, mono=True), fill=MUTED)
    draw.text((1390, 52), kicker.upper(), font=font(15, mono=True), fill=TEAL)
    draw.text((1765, 48), f"{number:02d} / 08", font=font(17, mono=True), fill=MUTED)
    draw.line((72, 105, 1848, 105), fill=INK, width=2)
    draw.line((72, 1024, 1848, 1024), fill=INK, width=2)
    draw.text(
        (72, 1039),
        "PAPER TRADING ONLY  ·  HACKATHON DEMONSTRATION  ·  NOT INVESTMENT ADVICE",
        font=font(14, mono=True),
        fill=MUTED,
    )
    draw.text(
        (1518, 1039),
        "thesis-agent.replit.app",
        font=font(14, mono=True),
        fill=TEAL,
    )
    return image, draw


def slide_title(draw: ImageDraw.ImageDraw, title: str, subtitle: str = "") -> int:
    y = draw_wrapped(
        draw,
        (72, 142),
        title,
        font(66, bold=True),
        max_width=1500,
        line_gap=4,
    )
    if subtitle:
        y = draw_wrapped(
            draw,
            (76, y + 14),
            subtitle,
            font(24),
            fill=MUTED,
            max_width=1520,
            line_gap=8,
        )
    return y


def card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    fill: str = WHITE,
    outline: str = LINE,
    radius: int = 18,
    width: int = 2,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    fill: str = LIME,
    text_fill: str = INK,
) -> int:
    x, y = xy
    text_font = font(16, bold=True, mono=True)
    text_width = int(draw.textlength(text, font=text_font))
    width = text_width + 32
    draw.rounded_rectangle((x, y, x + width, y + 36), radius=18, fill=fill)
    draw.text((x + 16, y + 8), text, font=text_font, fill=text_fill)
    return width


def bullet_list(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    items: Iterable[str],
    *,
    max_width: int,
    text_size: int = 24,
    bullet_fill: str = TEAL,
    text_fill: str = INK,
    gap: int = 18,
) -> int:
    x, y = xy
    text_font = font(text_size)
    for item in items:
        draw.ellipse((x, y + 10, x + 10, y + 20), fill=bullet_fill)
        y = draw_wrapped(
            draw,
            (x + 28, y),
            item,
            text_font,
            max_width=max_width - 28,
            fill=text_fill,
            line_gap=7,
        )
        y += gap
    return y


def render_slide_1() -> Image.Image:
    image, draw = base_slide(1, "Options Alpha Agents")
    pill(draw, (76, 146), "AUTONOMOUS · DEFINED RISK · AUDITABLE")
    draw.text((72, 236), "Propose.", font=font(116, bold=True), fill=INK)
    headline_font = font(116, bold=True)
    prove = "Prove."
    draw.text((72, 357), prove, font=headline_font, fill=TEAL)
    execute_x = 72 + int(draw.textlength(prove, font=headline_font)) + 26
    draw.text((execute_x, 357), "Execute.", font=headline_font, fill=INK)
    draw_wrapped(
        draw,
        (78, 516),
        "An options agent that cannot risk a dollar until it writes why the trade exists, when it is wrong, and the maximum amount it can lose.",
        font(31),
        fill=MUTED,
        max_width=1050,
        line_gap=14,
    )
    card(draw, (1235, 250, 1800, 716), fill=LIME, outline=INK, radius=0)
    draw.text((1290, 306), "THE TRUST\nBOUNDARY", font=font(45, bold=True), fill=INK, spacing=8)
    draw.line((1290, 444, 1740, 444), fill=INK, width=2)
    draw.text((1290, 481), "GROK", font=font(19, mono=True), fill=TEAL)
    draw.text((1515, 481), "PROPOSES", font=font(19, bold=True), fill=INK)
    draw.text((1290, 542), "CODE", font=font(19, mono=True), fill=TEAL)
    draw.text((1515, 542), "PROVES", font=font(19, bold=True), fill=INK)
    draw.text((1290, 603), "ALPACA", font=font(19, mono=True), fill=TEAL)
    draw.text((1515, 603), "EXECUTES", font=font(19, bold=True), fill=INK)
    draw.text((78, 903), "Official Alpaca MCP · Grok · FastAPI · SQLite", font=font(19, mono=True), fill=TEAL)
    return image


def render_slide_2() -> Image.Image:
    image, draw = base_slide(2, "Architecture")
    slide_title(draw, "One agent. Three trust zones.", "Language is creative. Authority is deterministic. Evidence comes from the broker.")
    zones = [
        ("01", "GROK", "Proposes", "Setup · invalidation · horizon · expected move · conviction", MINT),
        ("02", "DETERMINISTIC CODE", "Proves", "Contracts · liquidity · risk · sizing · clock agreement · execution gates", WHITE),
        ("03", "ALPACA PAPER", "Executes", "Official MCP write path · orders · fills · positions · portfolio history", LIME),
    ]
    x_positions = [72, 660, 1248]
    for x, (number, name, verb, body, fill_color) in zip(x_positions, zones):
        card(draw, (x, 365, x + 530, 787), fill=fill_color, outline=INK)
        draw.text((x + 36, 401), number, font=font(20, mono=True), fill=TEAL)
        draw.text((x + 36, 454), name, font=font(25, bold=True, mono=True), fill=INK)
        draw.text((x + 36, 512), verb, font=font(52, bold=True), fill=TEAL if number != "03" else INK)
        draw_wrapped(draw, (x + 36, 602), body, font(24), fill=MUTED, max_width=452, line_gap=9)
    for x in (620, 1208):
        draw.line((x, 565, x + 26, 565), fill=CORAL, width=4)
        draw.polygon([(x + 26, 555), (x + 44, 565), (x + 26, 575)], fill=CORAL)
    card(draw, (244, 842, 1676, 950), fill=DARK, outline=DARK)
    draw.text((294, 872), "PUBLIC AUDIT CONSOLE", font=font(22, bold=True, mono=True), fill=LIME)
    draw.text(
        (650, 870),
        "Read-only reconciliation across all three zones",
        font=font(29, bold=True),
        fill=WHITE,
    )
    return image


def render_slide_3() -> Image.Image:
    image, draw = base_slide(3, "Deterministic scout")
    slide_title(draw, "Scout wide. Ask narrowly.", "The model never receives an unrestricted market search.")
    funnel = [
        (72, 344, 1848, 455, "29 liquid stocks + sector ETFs", "FULL UNIVERSE"),
        (268, 491, 1652, 602, "Top five ranked underlyings", "STOCK EVIDENCE"),
        (484, 638, 1436, 749, "No more than three option-feasible candidates", "LIQUIDITY FILTER"),
        (700, 785, 1220, 896, "One structured Grok thesis", "MODEL CONTEXT"),
    ]
    colors = [WHITE, "#E5F4EF", MINT, LIME]
    for (x1, y1, x2, y2, main, label), fill_color in zip(funnel, colors):
        draw.polygon(
            [(x1, y1), (x2, y1), (x2 - 62, y2), (x1 + 62, y2)],
            fill=fill_color,
            outline=INK,
        )
        draw.text((x1 + 92, y1 + 22), label, font=font(16, mono=True), fill=TEAL)
        main_font = font(31 if y1 < 700 else 28, bold=True)
        text_width = draw.textlength(main, font=main_font)
        draw.text(((x1 + x2 - text_width) / 2, y1 + 56), main, font=main_font, fill=INK)
    draw.text((75, 933), "Stock ranking first · option chains only for finalists · failed liquidity is evidence", font=font(20, mono=True), fill=MUTED)
    return image


def render_slide_4() -> Image.Image:
    image, draw = base_slide(4, "Risk engine")
    slide_title(draw, "A model request is not an order.", "Code independently rebuilds the spread and can always refuse.")
    card(draw, (72, 339, 818, 934), fill=DARK, outline=DARK)
    draw.text((112, 379), "GROK RETURNS", font=font(18, mono=True), fill=LIME)
    draw.text((112, 431), "A falsifiable thesis", font=font(43, bold=True), fill=WHITE)
    bullet_list(
        draw,
        (116, 529),
        [
            "direction + regime",
            "setup + invalidation",
            "horizon + expected move",
            "volatility note + conviction",
        ],
        max_width=600,
        text_size=27,
        bullet_fill=LIME,
        text_fill=WHITE,
        gap=20,
    )
    pill(draw, (112, 858), "REQUEST ONLY", fill=CORAL, text_fill=WHITE)

    card(draw, (862, 339, 1848, 934), fill=WHITE, outline=INK)
    draw.text((906, 379), "CODE ENFORCES", font=font(18, mono=True), fill=TEAL)
    draw.text((906, 431), "Hard gates before risk", font=font(43, bold=True), fill=INK)
    items = [
        "Paper endpoint + active account",
        "Options level 3 + clock agreement",
        "14–45 DTE + two-sided quote quality",
        "Defined debit + contract identity",
        "≤ 2% equity per thesis",
        "≤ 6% aggregate risk + position cap",
        "Market open + explicit execution flag",
    ]
    y = 520
    for index, item in enumerate(items):
        fill_color = MINT if index % 2 == 0 else BG
        draw.rounded_rectangle((906, y, 1802, y + 48), radius=8, fill=fill_color)
        draw.rectangle((920, y + 14, 940, y + 34), fill=TEAL)
        draw.line((925, y + 24, 931, y + 30), fill=WHITE, width=3)
        draw.line((931, y + 30, 938, y + 18), fill=WHITE, width=3)
        draw.text((962, y + 10), item, font=font(20), fill=INK)
        y += 55
    return image


def render_slide_5() -> Image.Image:
    image, draw = base_slide(5, "Official Alpaca MCP")
    slide_title(draw, "One write path. Zero ambiguous retries.", "The execution boundary is enforced in code—not delegated to the prompt.")
    card(draw, (72, 340, 620, 913), fill=WHITE, outline=INK)
    draw.text((112, 381), "READ EVIDENCE", font=font(18, mono=True), fill=TEAL)
    bullet_list(
        draw,
        (114, 449),
        [
            "Account + positions",
            "Market clock agreement",
            "Stock + option data",
            "Orders + fills",
            "Portfolio history",
        ],
        max_width=442,
        text_size=24,
    )
    pill(draw, (112, 823), "ALLOW-LISTED + SANITIZED", fill=MINT)

    card(draw, (674, 340, 1246, 913), fill=LIME, outline=INK, radius=0)
    draw.text((718, 381), "ONLY WRITE", font=font(18, mono=True), fill=TEAL)
    draw.text((718, 449), "place_option_\norder", font=font(46, bold=True, mono=True), fill=INK, spacing=8)
    draw.line((718, 596, 1196, 596), fill=INK, width=2)
    draw.text((718, 636), "DAY LIMIT · MLEG · 1:1", font=font(21, bold=True, mono=True), fill=INK)
    draw_wrapped(
        draw,
        (718, 700),
        "No CLI write. No SDK write. No silent fallback.",
        font(27, bold=True),
        fill=INK,
        max_width=450,
        line_gap=11,
    )

    card(draw, (1300, 340, 1848, 913), fill=DARK, outline=DARK)
    draw.text((1340, 381), "TERMINAL OUTCOMES", font=font(18, mono=True), fill=LIME)
    bullet_list(
        draw,
        (1342, 449),
        [
            "Timeout",
            "Malformed response",
            "Missing order ID",
            "Ambiguous result",
            "Broker rejection",
        ],
        max_width=424,
        text_size=24,
        bullet_fill=CORAL,
        text_fill=WHITE,
    )
    draw.text((1342, 822), "NEVER RETRIED", font=font(31, bold=True), fill=WHITE)
    return image


def _dashboard_path() -> Path:
    preferred = ASSETS / "audit-console-live.png"
    if preferred.exists():
        return preferred
    return ASSETS / "audit-console.jpg"


def render_slide_6() -> Image.Image:
    image, draw = base_slide(6, "Judge-facing evidence")
    slide_title(draw, "One read-only screen proves what happened.", "The page cannot submit, replace, cancel, or close an order.")
    frame = (72, 321, 1848, 929)
    card(draw, frame, fill=WHITE, outline=INK, radius=12)
    source = Image.open(_dashboard_path()).convert("RGB")
    fitted = ImageOps.fit(source, (1736, 548), method=Image.Resampling.LANCZOS)
    image.paste(fitted, (92, 341))
    draw.rounded_rectangle((1218, 758, 1808, 907), radius=10, fill=LIME, outline=INK, width=2)
    draw.text((1252, 782), "AUDIT, NOT CONTROL", font=font(18, mono=True), fill=TEAL)
    draw_wrapped(
        draw,
        (1252, 822),
        "Thesis · gates · MCP trace · broker state · P&L",
        font(24, bold=True),
        fill=INK,
        max_width=510,
        line_gap=7,
    )
    return image


def render_slide_7() -> Image.Image:
    image, draw = base_slide(7, "Production readiness")
    slide_title(
        draw,
        "The proof includes a safe refusal.",
        "Live paper-account and MCP checks passed. Deterministic scouting stopped the cycle.",
    )
    checks = [
        ("PAPER", "Paper endpoint", "PASS"),
        ("ACCOUNT", "Fresh $100,000 baseline", "PASS"),
        ("OPTIONS", "Trading level 3", "PASS"),
        ("MCP", "Official tools discovered", "PASS"),
        ("SCOUT", "Option-feasible candidates", "ZERO"),
        ("OUTCOME", "Broker order submitted", "NO TRADE"),
    ]
    positions = [(72, 360), (660, 360), (1248, 360), (72, 620), (660, 620), (1248, 620)]
    for (label, title, status), (x, y) in zip(checks, positions):
        fill_color = LIME if status in {"PASS", "ARMED"} else MINT
        card(draw, (x, y, x + 530, y + 214), fill=fill_color, outline=INK)
        draw.text((x + 34, y + 30), label, font=font(16, mono=True), fill=TEAL)
        draw_wrapped(draw, (x + 34, y + 72), title, font(27, bold=True), fill=INK, max_width=452, line_gap=7)
        draw.text((x + 34, y + 157), status, font=font(19, bold=True, mono=True), fill=INK)
    draw.text(
        (75, 916),
        "No candidate passed deterministic scouting; no broker order or fill is claimed.",
        font=font(23, bold=True),
        fill=CORAL,
    )
    return image


def render_slide_8() -> Image.Image:
    image, draw = base_slide(8, "Closing")
    pill(draw, (76, 146), "EVIDENCE OVER ASSERTION")
    draw.text((72, 245), "Every trade—and every\nrefusal—is explainable.", font=font(76, bold=True), fill=INK, spacing=10)
    draw.text((76, 483), "GROK", font=font(20, mono=True), fill=TEAL)
    draw.text((76, 522), "PROPOSES", font=font(36, bold=True), fill=INK)
    draw.line((306, 548, 420, 548), fill=CORAL, width=4)
    draw.text((477, 483), "DETERMINISTIC CODE", font=font(20, mono=True), fill=TEAL)
    draw.text((477, 522), "PROVES", font=font(36, bold=True), fill=INK)
    draw.line((686, 548, 800, 548), fill=CORAL, width=4)
    draw.text((857, 483), "ALPACA PAPER", font=font(20, mono=True), fill=TEAL)
    draw.text((857, 522), "EXECUTES", font=font(36, bold=True), fill=INK)
    card(draw, (72, 662, 1848, 916), fill=DARK, outline=DARK)
    draw.text((118, 706), "LIVE AUDIT CONSOLE", font=font(18, mono=True), fill=LIME)
    draw.text((118, 752), "https://thesis-agent.replit.app", font=font(34, bold=True, mono=True), fill=WHITE)
    draw.text((118, 819), "PUBLIC SOURCE", font=font(18, mono=True), fill=LIME)
    draw.text((408, 813), "github.com/reynelfals/thesis-agent", font=font(29, bold=True, mono=True), fill=WHITE)
    return image


def render_slides() -> list[Path]:
    SLIDES_DIR.mkdir(parents=True, exist_ok=True)
    renderers = [
        render_slide_1,
        render_slide_2,
        render_slide_3,
        render_slide_4,
        render_slide_5,
        render_slide_6,
        render_slide_7,
        render_slide_8,
    ]
    paths: list[Path] = []
    images: list[Image.Image] = []
    for index, renderer in enumerate(renderers, 1):
        image = renderer()
        path = SLIDES_DIR / f"slide-{index:02d}.png"
        image.save(path, optimize=True)
        paths.append(path)
        images.append(image)

    pdf_path = ASSETS / "thesis-slides.pdf"
    images[0].save(
        pdf_path,
        "PDF",
        save_all=True,
        append_images=images[1:],
        resolution=144.0,
        title="Thesis — Propose. Prove. Execute.",
        author="Thesis contributors",
        subject="Alpaca AI Trading Agents Hackathon presentation",
    )
    images[0].save(ASSETS / "thesis-demo-poster.png", optimize=True)
    return paths


def one_page_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    heading: str,
    body: str,
    *,
    fill: str = WHITE,
) -> None:
    card(draw, box, fill=fill, outline=INK, radius=12)
    x1, y1, x2, _ = box
    draw.text((x1 + 28, y1 + 24), heading.upper(), font=font(17, bold=True, mono=True), fill=TEAL)
    draw.line((x1 + 28, y1 + 61, x2 - 28, y1 + 61), fill=LINE, width=1)
    draw_wrapped(
        draw,
        (x1 + 28, y1 + 84),
        body,
        font(24),
        fill=INK,
        max_width=x2 - x1 - 56,
        line_gap=9,
    )


def render_one_page() -> Path:
    width, height = 1275, 1650
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 75):
        draw.line((x, 0, x, height), fill="#EEEEE6", width=1)
    for y in range(0, height, 75):
        draw.line((0, y, width, y), fill="#EEEEE6", width=1)

    hexagon(draw, (79, 72), 25, TEAL)
    draw.text((120, 43), "THESIS", font=font(37, bold=True), fill=INK)
    draw.text((952, 57), "OPTIONS ALPHA AGENTS", font=font(15, mono=True), fill=TEAL)
    draw.line((54, 112, 1221, 112), fill=INK, width=2)

    draw.text((54, 151), "Propose. Prove. Execute.", font=font(64, bold=True), fill=INK)
    draw_wrapped(
        draw,
        (58, 238),
        "An autonomous options agent that cannot risk a dollar until it writes why the trade exists, when it is wrong, and the maximum amount it can lose.",
        font(26),
        fill=MUTED,
        max_width=1110,
        line_gap=10,
    )

    one_page_card(
        draw,
        (54, 348, 621, 735),
        "AI logic",
        "A 29-symbol deterministic scout ranks liquid stocks and sector ETFs, probes options only for the top five, and advances at most three candidates. Grok returns a structured, falsifiable thesis through a request tool. It never selects final contracts, sizes risk, reads the account clock, or submits an order.",
        fill=MINT,
    )
    one_page_card(
        draw,
        (654, 348, 1221, 735),
        "Deterministic risk",
        "Code rebuilds a call or put debit vertical, refreshes both leg quotes, and enforces paper-only access, active account, options level 3, 14–45 DTE, quote quality, contract identity, market hours, position limits, ≤2% equity risk per thesis, ≤6% aggregate risk, and explicit execution enablement.",
    )
    one_page_card(
        draw,
        (54, 768, 621, 1188),
        "Official Alpaca MCP",
        "The official Alpaca MCP server is the agent tool boundary. place_option_order is the only write path; there is no CLI or SDK write fallback. Timeout, malformed response, missing order ID, rejection, or an ambiguous result is terminal and never retried. Trading and Market Data APIs provide account, quotes, orders, fills, positions, and portfolio history.",
    )
    one_page_card(
        draw,
        (654, 768, 1221, 1188),
        "Judge evidence",
        "The public FastAPI console is read-only. It reconciles an append-only SQLite decision ledger with live Alpaca paper data and exposes the thesis, every gate, sanitized MCP trace, exact multi-leg intent, broker state, fills, linked positions, realized and unrealized P&L, reconciliation delta, and equity curve. A no-trade remains auditable evidence.",
        fill=LIME,
    )

    card(draw, (54, 1222, 1221, 1488), fill=DARK, outline=DARK, radius=10)
    draw.text((88, 1254), "WHY THESIS", font=font(17, bold=True, mono=True), fill=LIME)
    draw_wrapped(
        draw,
        (88, 1300),
        "Evidence over assertion. Grok is creative where language is useful and powerless where deterministic controls are safer. Every trade—and every refusal to trade—is explainable from one public, read-only screen.",
        font(25, bold=True),
        fill=WHITE,
        max_width=1080,
        line_gap=8,
    )
    draw.text((88, 1450), "thesis-agent.replit.app", font=font(20, mono=True), fill=LIME)

    draw.line((54, 1531, 1221, 1531), fill=INK, width=2)
    draw.text(
        (54, 1561),
        "PAPER TRADING ONLY · HACKATHON DEMONSTRATION · NOT INVESTMENT ADVICE",
        font=font(15, mono=True),
        fill=MUTED,
    )
    draw.text((1027, 1557), "AUG 2026", font=font(16, bold=True, mono=True), fill=TEAL)

    preview_path = ASSETS / "one-page-preview.png"
    image.save(preview_path, optimize=True)
    pdf_path = ASSETS / "thesis-one-page.pdf"
    image.save(
        pdf_path,
        "PDF",
        resolution=150.0,
        title="Thesis — Submission Brief",
        author="Thesis contributors",
        subject="Alpaca AI Trading Agents Hackathon one-page write-up",
    )
    return pdf_path


def command(args: Sequence[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def media_duration(path: Path) -> float:
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    )
    return float(output.strip())


def build_video(slides: Sequence[Path]) -> Path:
    audio_paths = [AUDIO_DIR / f"slide-{index:02d}.mp3" for index in range(1, 9)]
    missing = [str(path) for path in audio_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing narration files: {', '.join(missing)}")

    shutil.rmtree(PARTS_DIR, ignore_errors=True)
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    segment_metadata: list[dict[str, float | str]] = []

    for index, (slide, audio) in enumerate(zip(slides, audio_paths), 1):
        voice_duration = media_duration(audio) / AUDIO_TEMPO
        total_duration = voice_duration + 0.4
        fade_out = max(0.0, total_duration - 0.35)
        audio_fade_out = max(0.0, voice_duration - 0.25)
        part = PARTS_DIR / f"part-{index:02d}.mp4"
        video_filter = (
            "scale=1920:1080,"
            "zoompan=z='min(zoom+0.00010,1.025)':d=1:s=1920x1080:fps=30,"
            f"fade=t=in:st=0:d=0.25,fade=t=out:st={fade_out:.3f}:d=0.35,"
            "format=yuv420p"
        )
        audio_filter = (
            f"atempo={AUDIO_TEMPO:.2f},"
            "afade=t=in:st=0:d=0.12,"
            f"afade=t=out:st={audio_fade_out:.3f}:d=0.25,"
            "apad=pad_dur=0.4"
        )
        command(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-framerate",
                "30",
                "-i",
                str(slide),
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                video_filter,
                "-af",
                audio_filter,
                "-t",
                f"{total_duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "19",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(part),
            ]
        )
        parts.append(part)
        segment_metadata.append(
            {
                "slide": slide.name,
                "narration": audio.name,
                "duration_seconds": round(total_duration, 3),
            }
        )

    concat_file = PARTS_DIR / "concat.txt"
    concat_file.write_text(
        "".join(f"file '{part.resolve()}'\n" for part in parts),
        encoding="utf-8",
    )
    voice_master = PARTS_DIR / "voice-master.mp4"
    command(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(voice_master),
        ]
    )

    output = ASSETS / "thesis-demo.mp4"
    music = AUDIO_DIR / "thesis-demo-bed.mp3"
    if music.exists():
        command(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(voice_master),
                "-stream_loop",
                "-1",
                "-i",
                str(music),
                "-filter_complex",
                "[1:a]volume=0.055[bed];"
                "[0:a][bed]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(output),
            ]
        )
    else:
        shutil.copy2(voice_master, output)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    metadata = {
        "file": output.name,
        "duration_seconds": round(media_duration(output), 3),
        "sha256": digest,
        "resolution": "1920x1080",
        "video_codec": "H.264",
        "audio_codec": "AAC",
        "segments": segment_metadata,
    }
    (ASSETS / "thesis-demo-metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--video",
        action="store_true",
        help="Also render thesis-demo.mp4 from generated narration clips.",
    )
    args = parser.parse_args()

    ASSETS.mkdir(parents=True, exist_ok=True)
    slides = render_slides()
    one_page = render_one_page()
    print(f"Rendered {len(slides)} slide images")
    print(f"Rendered {ASSETS / 'thesis-slides.pdf'}")
    print(f"Rendered {one_page}")
    if args.video:
        print(f"Rendered {build_video(slides)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())