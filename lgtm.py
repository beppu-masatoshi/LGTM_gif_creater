#!/usr/bin/env python3
"""
lgtm.py — GIF画像に自動で「LGTM」の文字を焼き込むスクリプト
 
使い方:
    python3 lgtm.py 入力.gif 出力.gif
    python3 lgtm.py 入力.gif 出力.gif --text "LGTM" --color white --outline black --x 50 --y 50
    python3 lgtm.py 入力.gif 出力.gif --start 500 --end 2000   # 0.5秒後〜2秒後だけ表示
    python3 lgtm.py 入力.gif 出力.gif --color "255,0,0" --outline "0,0,255"   # RGBで色指定
    python3 lgtm.py 入力.gif 出力.gif --width 200   # 幅200pxにリサイズ (高さは自動計算)
 
アニメーションGIFにも対応しており、各フレームの表示時間・ループ回数・
透過情報を保持したまま、全フレームに文字を重ねて出力します。
テスト
"""
 
import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageSequence, ImageDraw, ImageFont

RGB_PATTERN = re.compile(r"^\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*$")


def parse_color(value: str) -> str | tuple[int, int, int]:
    """"R,G,B" 形式 (例: "255,0,0") をRGBタプルに変換する。
    それ以外 (色名や #rrggbb など) はPillowにそのまま解釈させる。"""
    match = RGB_PATTERN.match(value)
    if not match:
        return value
    r, g, b = (min(255, int(n)) for n in match.groups())
    return (r, g, b)


def find_font(size: int) -> ImageFont.FreeTypeFont:
    """OS内の適当な太字フォントを探す。見つからなければデフォルトフォントを使う。"""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()
 
 
def draw_lgtm(
    frame: Image.Image,
    text: str,
    color: str,
    outline: str,
    x_ratio: float = 0.5,
    y_ratio: float = 0.5,
) -> Image.Image:
    """1フレームにテキストを焼き込む。x_ratio/y_ratio (0.0〜1.0) でテキストの位置を指定する
    (0.5, 0.5 が中央、0,0 が左上、1,1 が右下)。"""
    frame = frame.convert("RGBA")
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 画像の幅の8割くらいに収まるフォントサイズを探索
    font_size = max(10, frame.width // 6)
    font = find_font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=max(1, font_size // 15))
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    while text_w > frame.width * 0.85 and font_size > 10:
        font_size -= 2
        font = find_font(font_size)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=max(1, font_size // 15))
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

    x = (frame.width - text_w) * x_ratio - bbox[0]
    y = (frame.height - text_h) * y_ratio - bbox[1]

    draw.text(
        (x, y),
        text,
        font=font,
        fill=parse_color(color),
        stroke_width=max(1, font_size // 15),
        stroke_fill=parse_color(outline),
    )
 
    return Image.alpha_composite(frame, overlay)
 
 
def process_gif(
    input_path: str,
    output_path: str,
    text: str,
    color: str,
    outline: str,
    x_ratio: float = 0.5,
    y_ratio: float = 0.5,
    start_ms: float = 0,
    end_ms: float | None = None,
    width: int | None = None,
    height: int | None = None,
) -> int:
    """start_ms/end_ms でアニメーション内の文字を表示する時間帯(ミリ秒)を指定できる。
    end_ms が None の場合は最後まで表示する。
    width/height を指定すると、出力GIFのサイズをそのピクセル数にリサイズする
    (両方 None の場合は元のサイズのまま)。"""
    im = Image.open(input_path)

    frames = []
    durations = []
    elapsed_ms = 0
    for frame in ImageSequence.Iterator(im):
        duration = frame.info.get("duration", 100)
        durations.append(duration)
        rgba = frame.convert("RGBA")
        if width is not None and height is not None and (width, height) != rgba.size:
            rgba = rgba.resize((max(1, width), max(1, height)), Image.LANCZOS)
        show_text = elapsed_ms >= start_ms and (end_ms is None or elapsed_ms < end_ms)
        stamped = draw_lgtm(rgba, text, color, outline, x_ratio, y_ratio) if show_text else rgba
        # GIF保存用にパレット変換(透過を維持)
        frames.append(stamped.convert("RGB").convert(
            "P", palette=Image.ADAPTIVE, colors=256
        ))
        elapsed_ms += duration

    loop = im.info.get("loop", 0)
 
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=loop,
        optimize=False,
        disposal=2,
    )
    return len(frames)
 
 
def main():
    parser = argparse.ArgumentParser(description="GIFにLGTMスタンプを焼き込む")
    parser.add_argument("input", help="入力GIFファイルのパス")
    parser.add_argument("output", help="出力GIFファイルのパス")
    parser.add_argument("--text", default="LGTM", help="焼き込む文字列 (デフォルト: LGTM)")
    parser.add_argument(
        "--color", default="white",
        help='文字の塗り色。色名/#rrggbb/"R,G,B" (例: "255,0,0") のいずれかで指定可 (デフォルト: white)',
    )
    parser.add_argument(
        "--outline", default="black",
        help='文字の縁取り色。色名/#rrggbb/"R,G,B" (例: "0,0,255") のいずれかで指定可 (デフォルト: black)',
    )
    parser.add_argument(
        "--x", type=float, default=50, help="文字の水平位置 0(左端)〜100(右端) (デフォルト: 50=中央)"
    )
    parser.add_argument(
        "--y", type=float, default=50, help="文字の垂直位置 0(上端)〜100(下端) (デフォルト: 50=中央)"
    )
    parser.add_argument(
        "--start", type=float, default=0,
        help="文字を表示し始める時間(ミリ秒) (デフォルト: 0=最初から)"
    )
    parser.add_argument(
        "--end", type=float, default=None,
        help="文字の表示を終える時間(ミリ秒) (デフォルト: 指定なし=最後まで表示)"
    )
    parser.add_argument(
        "--width", type=int, default=None,
        help="出力GIFの幅(px) (片方だけ指定した場合、もう片方は元のアスペクト比から自動計算。デフォルト: 元のサイズ)"
    )
    parser.add_argument(
        "--height", type=int, default=None,
        help="出力GIFの高さ(px) (片方だけ指定した場合、もう片方は元のアスペクト比から自動計算。デフォルト: 元のサイズ)"
    )
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"エラー: 入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.end is not None and args.end <= args.start:
        print("エラー: --end は --start より大きい値にしてください", file=sys.stderr)
        sys.exit(1)

    width, height = args.width, args.height
    if (width is not None and width <= 0) or (height is not None and height <= 0):
        print("エラー: --width / --height は正の整数で指定してください", file=sys.stderr)
        sys.exit(1)
    if width is not None and height is None:
        with Image.open(args.input) as probe:
            ow, oh = probe.size
        height = round(width * oh / ow)
    elif height is not None and width is None:
        with Image.open(args.input) as probe:
            ow, oh = probe.size
        width = round(height * ow / oh)

    x_ratio = min(max(args.x, 0), 100) / 100
    y_ratio = min(max(args.y, 0), 100) / 100
    n = process_gif(
        args.input, args.output, args.text, args.color, args.outline,
        x_ratio, y_ratio, args.start, args.end, width, height,
    )
    print(f"[OK] {n} フレームを処理して {args.output} に保存しました")
 
 
if __name__ == "__main__":
    main()