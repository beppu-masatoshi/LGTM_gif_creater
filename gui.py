#!/usr/bin/env python3
"""
lgtm_gui.py — GIFをドラッグ&ドロップするとLGTMを焼き込むGUIアプリ

事前準備 (ローカル環境で1回だけ実行):
    pip install Pillow tkinterdnd2

起動:
    python3 lgtm_gui.py

使い方:
    ウィンドウにGIFファイルをドラッグ&ドロップするか、
    「ファイルを選択...」ボタンから選ぶと、
    同じフォルダに "元のファイル名_LGTM.gif" として自動保存されます。
"""

import bisect
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from PIL import Image, ImageSequence, ImageTk

from lgtm import draw_lgtm, process_gif

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


# ---------------------------- GUI 本体 ----------------------------

PREVIEW_W = 300
PREVIEW_H = 170


class LgtmApp:
    def __init__(self, root):
        self.root = root
        self.root.title("GIF → LGTM ジェネレーター")
        self.root.geometry("480x660")
        self.root.resizable(False, False)

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

        # --- ドロップエリア ---
        self.drop_area = tk.Label(
            root,
            text="ここにGIFファイルを\nドラッグ&ドロップ\n\n(または下のボタンで選択)",
            bg="#f0f0f0",
            fg="#555555",
            font=("Helvetica", 14),
            relief="ridge",
            borderwidth=2,
            justify="center",
        )
        self.drop_area.place(x=20, y=10, width=440, height=110)

        if DND_AVAILABLE:
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind("<<Drop>>", self.on_drop)
        else:
            self.drop_area.config(
                text="ここにGIFファイルを\nドラッグ&ドロップ\n\n(tkinterdnd2 未インストールのため\n現在は無効です。下のボタンから選択してください)"
            )

        # --- ファイル選択ボタン ---
        browse_btn = tk.Button(root, text="ファイルを選択...", command=self.browse_file)
        browse_btn.place(x=20, y=126, width=440, height=30)

        # --- 選択中ファイル表示 ---
        self.file_label = tk.Label(root, text="未選択", fg="#333333", anchor="w")
        self.file_label.place(x=20, y=160, width=440, height=20)

        # --- オプション: テキスト ---
        tk.Label(root, text="文字:").place(x=20, y=188)
        self.text_var = tk.StringVar(value="LGTM")
        tk.Entry(root, textvariable=self.text_var).place(x=70, y=186, width=100)

        # --- オプション: 文字色 ---
        tk.Label(root, text="文字色:").place(x=190, y=188)
        self.color_var = tk.StringVar(value="white")
        tk.Entry(root, textvariable=self.color_var).place(x=250, y=186, width=90)

        # --- オプション: 縁取り色 ---
        tk.Label(root, text="縁色:").place(x=350, y=188)
        self.outline_var = tk.StringVar(value="black")
        tk.Entry(root, textvariable=self.outline_var).place(x=390, y=186, width=70)

        # --- 文字位置プレビュー (GIFがそのまま動きます) ---
        tk.Label(root, text="プレビュー (クリック/ドラッグで文字位置を指定できます):").place(x=20, y=214)
        self.preview_canvas = tk.Canvas(
            root, width=PREVIEW_W, height=PREVIEW_H, bg="#dddddd",
            relief="ridge", borderwidth=2, highlightthickness=0,
        )
        self.preview_canvas.place(x=(480 - PREVIEW_W) // 2, y=234)
        self.preview_canvas.bind("<Button-1>", self.on_preview_click)
        self.preview_canvas.bind("<B1-Motion>", self.on_preview_click)

        reset_pos_btn = tk.Button(root, text="中央に戻す", command=self.reset_position)
        reset_pos_btn.place(x=190, y=234 + PREVIEW_H + 8, width=100, height=26)

        # 文字/色を変更したらプレビューにも反映する
        self.text_var.trace_add("write", lambda *_: self.render_frame(self.current_render_index))
        self.color_var.trace_add("write", lambda *_: self.render_frame(self.current_render_index))
        self.outline_var.trace_add("write", lambda *_: self.render_frame(self.current_render_index))

        # --- 表示タイミング ---
        timing_y = 234 + PREVIEW_H + 8 + 26 + 10
        tk.Label(
            root, text="表示タイミング (スライダーをドラッグすると、その時点の映像で確認できます):"
        ).place(x=20, y=timing_y)

        tk.Label(root, text="開始:").place(x=20, y=timing_y + 24)
        self.start_scale = tk.Scale(
            root, from_=0, to=100, orient="horizontal", showvalue=False,
            command=self.on_start_scale_change,
        )
        self.start_scale.set(0)
        self.start_scale.place(x=70, y=timing_y + 18, width=280, height=26)
        self.start_scale.bind("<Button-1>", lambda e: self.stop_playback())
        self.start_scale.bind("<ButtonRelease-1>", lambda e: self.start_playback())
        self.start_time_label = tk.Label(root, text="0.0秒", width=8, anchor="w")
        self.start_time_label.place(x=360, y=timing_y + 24)

        tk.Label(root, text="終了:").place(x=20, y=timing_y + 54)
        self.end_scale = tk.Scale(
            root, from_=0, to=100, orient="horizontal", showvalue=False,
            command=self.on_end_scale_change,
        )
        self.end_scale.set(100)
        self.end_scale.place(x=70, y=timing_y + 48, width=280, height=26)
        self.end_scale.bind("<Button-1>", lambda e: self.stop_playback())
        self.end_scale.bind("<ButtonRelease-1>", lambda e: self.start_playback())
        self.end_time_label = tk.Label(root, text="0.0秒", width=8, anchor="w")
        self.end_time_label.place(x=360, y=timing_y + 54)

        # --- 変換ボタン ---
        convert_y = timing_y + 24 + 30 + 30 + 10
        self.convert_btn = tk.Button(
            root, text="変換する", command=self.start_convert,
            bg="#4CAF50", fg="white", font=("Helvetica", 12, "bold")
        )
        self.convert_btn.place(x=20, y=convert_y, width=440, height=40)

        # --- 進捗バー ---
        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.place(x=20, y=convert_y + 48, width=440, height=16)

        # --- ステータス表示 ---
        self.status_label = tk.Label(root, text="", fg="#333333")
        self.status_label.place(x=20, y=convert_y + 70, width=440, height=20)

        self.render_frame(None)
        self._sync_timing_labels()

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
            self.status_label.config(text=f"エラー: {error}", fg="red")
            messagebox.showerror("エラー", str(error))
        else:
            self.status_label.config(text=f"完了 ({n_frames}フレーム) → {os.path.basename(output_path)}", fg="green")
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