"""VCランクカードの画像生成（ProBot 風の横長カード）。

Pillow を使うため同期処理。呼び出し側は asyncio.to_thread 等でスレッドに逃がすこと。
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

# カードのサイズ（VC・テキストの2バーを載せるため縦長め）
WIDTH, HEIGHT = 900, 280

_ICON_DIR = os.path.join(os.path.dirname(__file__), "..", "icon")

_FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
_FONT_CANDIDATES_REGULAR = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]

# テキストバーの色（VC はユーザーのアクセント色、テキストは固定のグリーン）
TEXT_BAR_COLOR = (87, 197, 122)


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for path in (_FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _load_icon(name: str, size: int, color: tuple[int, int, int]) -> Image.Image | None:
    """icon フォルダのアイコンを読み込み、指定色に着色して返す（暗い元画像を明るく塗り直す）。"""
    path = os.path.join(_ICON_DIR, name)
    try:
        icon = Image.open(path).convert("RGBA").resize((size, size))
    except Exception:
        return None
    solid = Image.new("RGBA", (size, size), color + (255,))
    solid.putalpha(icon.getchannel("A"))
    return solid


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: float) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_w:
        text = text[:-1]
    return text + ellipsis


def _draw_bar(
    img, draw, icon_img, level_label, level_color,
    tx, icon_size, bar_x0, bar_x1, cy, progress, fill_color, xp_text,
    level_font, small_font,
):
    if icon_img is not None:
        img.paste(icon_img, (tx, cy - icon_size // 2), icon_img)
    draw.text((tx + icon_size + 10, cy), level_label, font=level_font, fill=level_color, anchor="lm")

    bar_h = 30
    y0, y1 = cy - bar_h // 2, cy + bar_h // 2
    draw.rounded_rectangle([bar_x0, y0, bar_x1, y1], radius=bar_h // 2, fill=(55, 58, 64, 255))
    fill_w = (bar_x1 - bar_x0) * max(0.0, min(1.0, progress))
    if fill_w >= bar_h:
        draw.rounded_rectangle([bar_x0, y0, bar_x0 + fill_w, y1], radius=bar_h // 2, fill=fill_color)
    draw.text(((bar_x0 + bar_x1) / 2, cy), xp_text, font=small_font, fill=(235, 236, 240, 255), anchor="mm")


def render_rank_card(
    avatar_bytes: bytes,
    username: str,
    rank_pos: int,
    vc_level: int,
    vc_cur: int,
    vc_need: int,
    text_level: int,
    text_cur: int,
    text_need: int,
    accent: tuple[int, int, int] = (88, 101, 242),
) -> bytes:
    """ランクカードのPNGバイト列を返す。VC と テキストの2バー構成。"""
    white = (255, 255, 255, 255)
    light = (235, 236, 240)
    gray = (150, 154, 164, 255)

    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))

    # 角丸マスク
    mask = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, WIDTH - 1, HEIGHT - 1], radius=30, fill=255)

    # 背景（単色）
    bg = Image.new("RGBA", (WIDTH, HEIGHT), (30, 33, 36, 255))
    img.paste(bg, (0, 0), mask)

    draw = ImageDraw.Draw(img)

    # アバター（円形＋アクセントのリング）
    d = 180
    ax, ay = 40, (HEIGHT - d) // 2
    ring = 6
    draw.ellipse([ax - ring, ay - ring, ax + d + ring, ay + d + ring], fill=accent + (255,))
    try:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((d, d))
    except Exception:
        avatar = Image.new("RGBA", (d, d), (60, 63, 68, 255))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, d, d], fill=255)
    img.paste(avatar, (ax, ay), mask)

    tx = ax + d + 40
    right = WIDTH - 40

    name_font = _load_font(44, bold=True)
    level_font = _load_font(32, bold=True)
    rank_font = _load_font(26, bold=True)
    small_font = _load_font(24, bold=False)

    # 右上：リーダーボード順位
    rank_text = f"RANK #{rank_pos}"
    rank_w = draw.textlength(rank_text, font=rank_font)
    draw.text((right, 78), rank_text, font=rank_font, fill=gray, anchor="rm")

    # ユーザー名（右上の順位に被らないよう幅制限）
    name = _truncate(draw, username, name_font, (right - rank_w - 24) - tx)
    draw.text((tx, 78), name, font=name_font, fill=white, anchor="lm")

    # 2本のバーは左端を揃える（レベル表記の幅の広い方に合わせる）
    icon_size = 40
    vc_label = f"Lv {vc_level}"
    text_label = f"Lv {text_level}"
    label_w = max(draw.textlength(vc_label, font=level_font), draw.textlength(text_label, font=level_font))
    bar_x0 = tx + icon_size + 10 + label_w + 20
    bar_x1 = right

    mic_icon = _load_icon("mike.png", icon_size, light)
    pen_icon = _load_icon("text.png", icon_size, light)

    vc_pct = int((vc_cur / vc_need) * 100) if vc_need > 0 else 0
    text_pct = int((text_cur / text_need) * 100) if text_need > 0 else 0

    # VCバー（上）
    _draw_bar(
        img, draw, mic_icon, vc_label, accent + (255,),
        tx, icon_size, bar_x0, bar_x1, 170,
        (vc_cur / vc_need) if vc_need > 0 else 0.0, accent + (255,),
        f"{vc_cur} / {vc_need} 分（{vc_pct}%）", level_font, small_font,
    )
    # テキストバー（下）
    _draw_bar(
        img, draw, pen_icon, text_label, TEXT_BAR_COLOR + (255,),
        tx, icon_size, bar_x0, bar_x1, 228,
        (text_cur / text_need) if text_need > 0 else 0.0, TEXT_BAR_COLOR + (255,),
        f"{text_cur} / {text_need} 通（{text_pct}%）", level_font, small_font,
    )

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out.read()
