#!/usr/bin/env python3
"""
lgtm_gui.py — GIFをドラッグ&ドロップするとLGTMを焼き込むGUIアプリ

事前準備 (ローカル環境で1回だけ実行):
    pip install Pillow tkinterdnd2

起動:
    python3 lgtm_gui.py

使い方:
    「ファイル選択」画面でGIFファイルをドラッグ&ドロップ(またはボタンで選択)すると、
    「編集」画面に切り替わります。文字・色・位置・表示タイミングを調整して
    「変換する」を押すと、同じフォルダに "元のファイル名_LGTM.gif" として保存されます。
"""

import bisect
import os
import sys
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from pathlib import Path

from PIL import Image, ImageSequence, ImageTk

from lgtm import draw_lgtm, process_gif

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


# ---------------------------- 見た目の設定 ----------------------------

FONT_FAMILY = "Segoe UI" if sys.platform == "win32" else "Helvetica"

PALETTE = {
    "bg": "#F5F6FA",
    "card_bg": "#FFFFFF",
    "border": "#E2E4EA",
    "text": "#1F2430",
    "muted": "#6B7280",
    "accent": "#4F46E5",
    "accent_active": "#4338CA",
    "accent_disabled": "#A5A6F5",
    "drop_hover": "#EEF0FF",
}

PREVIEW_W = 320
PREVIEW_H = 180


def _configure_style(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    p = PALETTE
    root.configure(bg=p["bg"])

    style.configure("TFrame", background=p["bg"])
    style.configure("Card.TFrame", background=p["card_bg"], relief="solid",
                     borderwidth=1, bordercolor=p["border"])

    style.configure("TLabel", background=p["bg"], foreground=p["text"],
                     font=(FONT_FAMILY, 10))
    style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                     font=(FONT_FAMILY, 18, "bold"))
    style.configure("Sub.TLabel", background=p["bg"], foreground=p["muted"],
                     font=(FONT_FAMILY, 10))
    style.configure("Card.TLabel", background=p["card_bg"], foreground=p["text"],
                     font=(FONT_FAMILY, 10))
    style.configure("CardHeader.TLabel", background=p["card_bg"], foreground=p["text"],
                     font=(FONT_FAMILY, 11, "bold"))
    style.configure("Hint.TLabel", background=p["card_bg"], foreground=p["muted"],
                     font=(FONT_FAMILY, 9))

    style.configure("TEntry", padding=6, fieldbackground="white")

    style.configure("TButton", font=(FONT_FAMILY, 10), padding=8)
    style.configure("Small.TButton", font=(FONT_FAMILY, 9), padding=4)

    style.configure("Accent.TButton", font=(FONT_FAMILY, 12, "bold"),
                     padding=12, background=p["accent"], foreground="white",
                     borderwidth=0)
    style.map(
        "Accent.TButton",
        background=[("disabled", p["accent_disabled"]),
                     ("pressed", p["accent_active"]),
                     ("active", p["accent_active"])],
        foreground=[("disabled", "white")],
    )

    style.configure("Horizontal.TScale", background=p["card_bg"])

    style.configure("Accent.Horizontal.TProgressbar", background=p["accent"],
                     troughcolor=p["card_bg"], bordercolor=p["card_bg"])

    return style


# ---------------------------- GUI 本体 ----------------------------

class LgtmApp:
    def __init__(self, root):
        self.root = root
        self.root.title("GIF → LGTM ジェネレーター")
        self.root.resizable(False, False)

        self.palette = PALETTE
        _configure_style(root)

        self.selected_file = None
        self.preview_frames = []  # 選択中GIFの全フレーム (プレビューサイズに縮小済み, RGBA)
        self.preview_durations = []  # 各フレームの表示時間 (ms)
        self.preview_times = []  # 各フレーム開始時点の累積経過時間 (ms)
        self.preview_photo = None  # ImageTk.PhotoImage への参照保持用
        self.preview_offset = None  # (offset_x, offset_y, disp_w, disp_h)
        self.current_render_index = None  # 現在プレビューに描画中のフレーム番号
        self.preview_after_id = None  # アニメーション再生用の after() ID
        self.preview_index = 0  # 再生中の次フレーム番号
        self.x_ratio = 0.5
        self.y_ratio = 0.5
        self.total_duration_ms = 0  # 選択中GIFの全フレーム合計再生時間

        # --- 画面切り替え用コンテナ ---
        self.container = tk.Frame(root, bg=self.palette["bg"])
        self.container.pack(fill="both", expand=True)
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(0, weight=1)

        self.select_screen = self._build_select_screen()
        self.edit_screen = self._build_edit_screen()
        self.select_screen.grid(row=0, column=0, sticky="nsew")
        self.edit_screen.grid(row=0, column=0, sticky="nsew")

        self.render_frame(None)
        # 両スケールのウィジェットが揃った後でないとcommandコールバックが失敗するため、
        # 画面構築が完全に終わってから終了スケールの初期値(100%)を設定する
        self.end_scale.set(100)
        self._sync_timing_labels()
        self.show_screen("select")

    # ----- 画面構築: ファイル選択画面 -----

    def _build_select_screen(self):
        p = self.palette
        frame = ttk.Frame(self.container, padding=28)

        ttk.Label(frame, text="GIF → LGTM ジェネレーター", style="Header.TLabel").pack(
            anchor="w", pady=(4, 2)
        )
        ttk.Label(
            frame,
            text="GIFにLGTMスタンプを焼き込みます。まずは元になるGIFを選んでください。",
            style="Sub.TLabel", wraplength=420, justify="left",
        ).pack(anchor="w", pady=(0, 20))

        self.drop_area = tk.Label(
            frame,
            text="ここにGIFファイルを\nドラッグ&ドロップ\n\nまたは下のボタンで選択",
            bg=p["card_bg"], fg=p["muted"],
            font=(FONT_FAMILY, 13), justify="center",
            relief="solid", bd=1, highlightthickness=0,
        )
        self.drop_area.pack(fill="both", expand=True, pady=(0, 16))

        if DND_AVAILABLE:
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind("<<Drop>>", self.on_drop)
        else:
            self.drop_area.config(
                text="ここにGIFファイルを\nドラッグ&ドロップ\n\n"
                     "(tkinterdnd2 未インストールのため\n現在は無効です。下のボタンから選択してください)"
            )

        ttk.Button(
            frame, text="ファイルを選択...", style="Accent.TButton", command=self.browse_file
        ).pack(fill="x")

        frame.pack_propagate(True)
        return frame

    # ----- 画面構築: 編集画面 -----

    def _build_edit_screen(self):
        frame = ttk.Frame(self.container, padding=16)

        # --- ツールバー ---
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Button(toolbar, text="← 戻る", command=self.go_back).pack(side="left")
        self.file_label = ttk.Label(toolbar, text="未選択", style="Sub.TLabel")
        self.file_label.pack(side="left", padx=(12, 0))

        # --- 左右2カラムの本体 ---
        content = ttk.Frame(frame)
        content.pack(fill="both", expand=True)
        content.columnconfigure(1, weight=1)

        # --- プレビューカード (左カラム) ---
        preview_card = ttk.Frame(content, style="Card.TFrame", padding=12)
        preview_card.grid(row=0, column=0, sticky="n", padx=(0, 12))
        ttk.Label(preview_card, text="プレビュー", style="CardHeader.TLabel").pack(anchor="w")
        ttk.Label(
            preview_card, text="クリック/ドラッグで文字位置を指定できます",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        canvas_wrap = tk.Frame(preview_card, bg=self.palette["card_bg"])
        canvas_wrap.pack()
        self.preview_canvas = tk.Canvas(
            canvas_wrap, width=PREVIEW_W, height=PREVIEW_H, bg="#E5E7EB",
            highlightthickness=1, highlightbackground=self.palette["border"],
        )
        self.preview_canvas.pack()
        self.preview_canvas.bind("<Button-1>", self.on_preview_click)
        self.preview_canvas.bind("<B1-Motion>", self.on_preview_click)

        ttk.Button(
            preview_card, text="中央に戻す", style="Small.TButton", command=self.reset_position
        ).pack(pady=(8, 0))

        # --- 右カラム (文字スタイル・表示タイミング) ---
        right_col = ttk.Frame(content)
        right_col.grid(row=0, column=1, sticky="nsew")

        # --- 文字スタイルカード ---
        style_card = ttk.Frame(right_col, style="Card.TFrame", padding=12)
        style_card.pack(fill="x", pady=(0, 12))
        style_card.columnconfigure(1, weight=1)

        ttk.Label(style_card, text="文字スタイル", style="CardHeader.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )

        ttk.Label(style_card, text="文字:", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.text_var = tk.StringVar(value="LGTM")
        ttk.Entry(style_card, textvariable=self.text_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=4
        )

        ttk.Label(style_card, text="文字色:", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=4
        )
        self.color_var = tk.StringVar(value="white")
        ttk.Entry(style_card, textvariable=self.color_var).grid(
            row=2, column=1, sticky="ew", pady=4, padx=(0, 6)
        )
        ttk.Button(
            style_card, text="RGBで選択...", style="Small.TButton",
            command=lambda: self.pick_color(self.color_var),
        ).grid(row=2, column=2, pady=4)

        ttk.Label(style_card, text="縁色:", style="Card.TLabel").grid(
            row=3, column=0, sticky="w", pady=4
        )
        self.outline_var = tk.StringVar(value="black")
        ttk.Entry(style_card, textvariable=self.outline_var).grid(
            row=3, column=1, sticky="ew", pady=4, padx=(0, 6)
        )
        ttk.Button(
            style_card, text="RGBで選択...", style="Small.TButton",
            command=lambda: self.pick_color(self.outline_var),
        ).grid(row=3, column=2, pady=4)

        # 文字/色を変更したらプレビューにも反映する
        self.text_var.trace_add("write", lambda *_: self.render_frame(self.current_render_index))
        self.color_var.trace_add("write", lambda *_: self.render_frame(self.current_render_index))
        self.outline_var.trace_add("write", lambda *_: self.render_frame(self.current_render_index))

        # --- 表示タイミングカード ---
        timing_card = ttk.Frame(right_col, style="Card.TFrame", padding=12)
        timing_card.pack(fill="x")
        ttk.Label(timing_card, text="表示タイミング", style="CardHeader.TLabel").pack(anchor="w")
        ttk.Label(
            timing_card, text="スライダーをドラッグすると、その時点の映像で確認できます",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        start_row = ttk.Frame(timing_card, style="Card.TFrame")
        start_row.pack(fill="x", pady=4)
        ttk.Label(start_row, text="開始", style="Card.TLabel", width=6).pack(side="left")
        self.start_scale = ttk.Scale(
            start_row, from_=0, to=100, orient="horizontal", command=self.on_start_scale_change
        )
        self.start_scale.pack(side="left", fill="x", expand=True, padx=8)
        self.start_scale.bind("<Button-1>", lambda e: self.stop_playback())
        self.start_scale.bind("<ButtonRelease-1>", lambda e: self.start_playback())
        self.start_time_label = ttk.Label(start_row, text="0.0秒", style="Card.TLabel", width=8)
        self.start_time_label.pack(side="left")

        end_row = ttk.Frame(timing_card, style="Card.TFrame")
        end_row.pack(fill="x", pady=4)
        ttk.Label(end_row, text="終了", style="Card.TLabel", width=6).pack(side="left")
        self.end_scale = ttk.Scale(
            end_row, from_=0, to=100, orient="horizontal", command=self.on_end_scale_change
        )
        self.end_scale.pack(side="left", fill="x", expand=True, padx=8)
        self.end_scale.bind("<Button-1>", lambda e: self.stop_playback())
        self.end_scale.bind("<ButtonRelease-1>", lambda e: self.start_playback())
        self.end_time_label = ttk.Label(end_row, text="0.0秒", style="Card.TLabel", width=8)
        self.end_time_label.pack(side="left")

        # --- アクションバー ---
        action = ttk.Frame(frame)
        action.pack(fill="x", pady=(12, 0))
        self.convert_btn = ttk.Button(
            action, text="変換する", style="Accent.TButton", command=self.start_convert
        )
        self.convert_btn.pack(fill="x")
        self.progress = ttk.Progressbar(
            action, mode="indeterminate", style="Accent.Horizontal.TProgressbar"
        )
        self.progress.pack(fill="x", pady=(10, 4))
        self.status_label = ttk.Label(action, text="", style="Sub.TLabel")
        self.status_label.pack(anchor="w")

        return frame

    # ----- 画面切り替え -----

    def show_screen(self, name):
        frame = self.select_screen if name == "select" else self.edit_screen
        frame.tkraise()
        self.root.update_idletasks()
        width = frame.winfo_reqwidth()
        height = frame.winfo_reqheight()
        self.root.geometry(f"{width}x{height}")

    def go_back(self):
        self.stop_playback()
        self.selected_file = None
        self.show_screen("select")

    # ----- イベントハンドラ -----

    def on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        if not paths:
            return
        path = paths[0]
        if not path.lower().endswith(".gif"):
            messagebox.showwarning("警告", "GIFファイルをドロップしてください")
            return
        self.set_selected_file(path)

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="GIFファイルを選択", filetypes=[("GIF files", "*.gif")]
        )
        if path:
            self.set_selected_file(path)

    def set_selected_file(self, path):
        self.selected_file = path
        self.file_label.config(text=f"選択中: {os.path.basename(path)}")
        self.status_label.config(text="")

        self.stop_playback()
        try:
            im = Image.open(path)
            frames, durations, times = [], [], []
            disp_w = disp_h = offset_x = offset_y = None
            elapsed = 0
            for frame in ImageSequence.Iterator(im):
                rgba = frame.convert("RGBA")
                if disp_w is None:
                    scale = min(PREVIEW_W / rgba.width, PREVIEW_H / rgba.height)
                    disp_w = max(1, int(rgba.width * scale))
                    disp_h = max(1, int(rgba.height * scale))
                    offset_x = (PREVIEW_W - disp_w) // 2
                    offset_y = (PREVIEW_H - disp_h) // 2
                frames.append(rgba.resize((disp_w, disp_h), Image.LANCZOS))
                duration = frame.info.get("duration", 100)
                durations.append(duration)
                times.append(elapsed)
                elapsed += duration

            self.preview_frames = frames
            self.preview_durations = durations
            self.preview_times = times
            self.total_duration_ms = elapsed
            self.preview_offset = (offset_x, offset_y, disp_w, disp_h)
        except Exception:
            self.preview_frames, self.preview_durations, self.preview_times = [], [], []
            self.total_duration_ms = 0
            self.preview_offset = None

        self.x_ratio = 0.5
        self.y_ratio = 0.5
        self.start_scale.set(0)
        self.end_scale.set(100)
        self._sync_timing_labels()
        self.start_playback()
        self.show_screen("edit")

    def render_frame(self, idx):
        """idx番目のフレームを、現在の文字設定と表示タイミング条件を反映してプレビューに描画する"""
        self.preview_canvas.delete("all")
        if idx is None or not self.preview_frames:
            self.preview_canvas.create_text(
                PREVIEW_W // 2, PREVIEW_H // 2, text="プレビューなし", fill="#888888"
            )
            self.current_render_index = None
            return

        idx = max(0, min(idx, len(self.preview_frames) - 1))
        self.current_render_index = idx

        start_ms = self.total_duration_ms * self.start_scale.get() / 100
        end_ms = self.total_duration_ms * self.end_scale.get() / 100
        elapsed = self.preview_times[idx]
        show_text = start_ms <= elapsed < end_ms

        base = self.preview_frames[idx]
        text = self.text_var.get() or "LGTM"
        color = self.color_var.get() or "white"
        outline = self.outline_var.get() or "black"
        if show_text:
            try:
                stamped = draw_lgtm(base, text, color, outline, self.x_ratio, self.y_ratio)
            except Exception:
                stamped = base
        else:
            stamped = base

        self.preview_photo = ImageTk.PhotoImage(stamped.convert("RGB"))
        offset_x, offset_y, disp_w, disp_h = self.preview_offset
        self.preview_canvas.create_image(offset_x, offset_y, anchor="nw", image=self.preview_photo)

        if not show_text:
            marker_x = offset_x + self.x_ratio * disp_w
            marker_y = offset_y + self.y_ratio * disp_h
            self.preview_canvas.create_oval(
                marker_x - 4, marker_y - 4, marker_x + 4, marker_y + 4, outline="red", width=2
            )

    # ----- アニメーション再生 -----

    def start_playback(self):
        self.stop_playback()
        if not self.preview_frames:
            self.render_frame(None)
            return
        self.preview_index = 0
        self._playback_tick()

    def _playback_tick(self):
        if not self.preview_frames:
            return
        self.render_frame(self.preview_index)
        duration = self.preview_durations[self.preview_index]
        self.preview_index = (self.preview_index + 1) % len(self.preview_frames)
        self.preview_after_id = self.root.after(max(int(duration), 20), self._playback_tick)

    def stop_playback(self):
        if self.preview_after_id is not None:
            try:
                self.root.after_cancel(self.preview_after_id)
            except Exception:
                pass
            self.preview_after_id = None

    def pick_color(self, target_var):
        initial = target_var.get().strip() or None
        try:
            _rgb, hex_color = colorchooser.askcolor(color=initial, title="色を選択 (RGB)")
        except tk.TclError:
            _rgb, hex_color = colorchooser.askcolor(title="色を選択 (RGB)")
        if hex_color:
            target_var.set(hex_color)

    # ----- 文字位置 -----

    def on_preview_click(self, event):
        if self.preview_offset is None:
            return
        offset_x, offset_y, disp_w, disp_h = self.preview_offset
        rel_x = (event.x - offset_x) / disp_w
        rel_y = (event.y - offset_y) / disp_h
        self.x_ratio = min(max(rel_x, 0.0), 1.0)
        self.y_ratio = min(max(rel_y, 0.0), 1.0)
        self.render_frame(self.current_render_index)

    def reset_position(self):
        self.x_ratio = 0.5
        self.y_ratio = 0.5
        self.render_frame(self.current_render_index)

    # ----- 表示タイミング -----

    def _sync_timing_labels(self):
        start_pct = self.start_scale.get()
        end_pct = self.end_scale.get()
        if end_pct < start_pct:
            end_pct = start_pct
            self.end_scale.set(end_pct)

        start_ms = self.total_duration_ms * start_pct / 100
        end_ms = self.total_duration_ms * end_pct / 100
        self.start_time_label.config(text=f"{start_ms / 1000:.1f}秒")
        self.end_time_label.config(text=f"{end_ms / 1000:.1f}秒")

    def scrub_to(self, target_ms):
        """指定した時間(ms)に最も近いフレームを探してプレビューに表示する"""
        if not self.preview_times:
            return
        idx = bisect.bisect_right(self.preview_times, target_ms) - 1
        idx = min(max(idx, 0), len(self.preview_times) - 1)
        self.render_frame(idx)

    def on_start_scale_change(self, _value=None):
        self._sync_timing_labels()
        start_ms = self.total_duration_ms * self.start_scale.get() / 100
        self.scrub_to(start_ms)

    def on_end_scale_change(self, _value=None):
        self._sync_timing_labels()
        end_ms = self.total_duration_ms * self.end_scale.get() / 100
        # 終了時刻ちょうどでは文字は消えるため、直前の時点を確認できるようにする
        self.scrub_to(max(0, end_ms - 1))

    def start_convert(self):
        if not self.selected_file:
            messagebox.showwarning("警告", "先にGIFファイルを選択してください")
            return
        self.convert_btn.config(state="disabled")
        self.progress.start(10)
        self.status_label.config(text="変換中...")
        threading.Thread(target=self.run_convert, daemon=True).start()

    def run_convert(self):
        try:
            src = Path(self.selected_file)
            output_path = str(src.with_name(f"{src.stem}_LGTM.gif"))
            start_ms = self.total_duration_ms * self.start_scale.get() / 100
            end_ms = self.total_duration_ms * self.end_scale.get() / 100
            n = process_gif(
                str(src), output_path,
                self.text_var.get() or "LGTM",
                self.color_var.get() or "white",
                self.outline_var.get() or "black",
                self.x_ratio,
                self.y_ratio,
                start_ms,
                end_ms,
            )
            self.root.after(0, self.on_convert_done, output_path, n, None)
        except Exception as e:
            self.root.after(0, self.on_convert_done, None, 0, e)

    def on_convert_done(self, output_path, n_frames, error):
        self.progress.stop()
        self.convert_btn.config(state="normal")
        if error:
            self.status_label.config(text=f"エラー: {error}", foreground="#DC2626")
            messagebox.showerror("エラー", str(error))
        else:
            self.status_label.config(
                text=f"完了 ({n_frames}フレーム) → {os.path.basename(output_path)}",
                foreground="#16A34A",
            )
            messagebox.showinfo("完了", f"保存しました:\n{output_path}")


def main():
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
        print(
            "注意: tkinterdnd2 が見つかりません。ドラッグ&ドロップは無効です。\n"
            "  pip install tkinterdnd2\n"
            "を実行すると、ドラッグ&ドロップが使えるようになります。",
            file=sys.stderr,
        )
    app = LgtmApp(root)

    def on_close():
        app.stop_playback()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
