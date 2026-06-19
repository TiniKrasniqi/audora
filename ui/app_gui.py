# -*- coding: utf-8 -*-
import io
import json
import math
import os
import shutil
import sys
import tkinter as tk
import threading
import queue
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List
from urllib.request import Request, urlopen

import customtkinter as ctk
from tkinter import filedialog, messagebox

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageTk

from core.utils import ensure_ffmpeg_or_die, default_download_dir, human_time, DEFAULT_BITRATE
from core.downloader import DownloadProgress
from core.queue import DownloadManager
from core.media import MediaInfo, format_media_time, import_vlc_binding, probe_media, vlc_runtime_status


# Color theme
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

ACCENT = "#ececec"
ACCENT_HOVER = "#f4f4f4"
ACCENT_TEXT = "#0d0d0d"
BG = "#212121"
SURFACE = "#2f2f2f"
SURFACE_ALT = "#303030"
SURFACE_HOVER = "#3a3a3a"
BORDER = "#4a4a4a"
ROW_BG = "#2b2b2b"
ROW_ACTIVE_BG = "#3a3a3a"
ROW_ERROR_BG = "#3a2729"
THUMB_BG = "#3a3a3a"
TEXT = "#ececec"
TEXT_SOFT = "#d6d6d6"
MUTED = "#b4b4b4"
MUTED_DARK = "#8f8f8f"
DANGER = "#e25555"
DANGER_HOVER = "#ef6666"
WARNING = "#f0b45d"
WARNING_HOVER = "#f4c278"
LOADER_SPEED = 0.32
HISTORY_RENDER_BATCH_SIZE = 10
DELETED_HOLD_MS = 3000

AUDIO_QUALITIES = {
    "128 kbps": "128",
    "192 kbps": "192",
    "256 kbps": "256",
    "320 kbps": "320",
}

VIDEO_QUALITIES = ["480p", "720p", "1080p", "1440p", "2160p (4K)"]

HISTORY_EXTENSIONS = (".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".webm")
SETTINGS_FILE_NAME = "settings.json"


try:
    _RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9 fallback
    _RESAMPLE = Image.LANCZOS


def _make_line_icon(kind: str, color: str = TEXT, size: int = 20) -> ctk.CTkImage:
    scale = 4
    canvas_size = size * scale
    stroke = max(scale, int(round(size * scale * 0.06)))
    icon_color = ImageColor.getrgb(color) + (255,)
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def p(value: float) -> int:
        return int(round(value * scale))

    if kind == "history":
        outline = [
            (p(6.2), p(3.4)),
            (p(12.7), p(3.4)),
            (p(15.8), p(6.6)),
            (p(15.8), p(16.6)),
            (p(6.2), p(16.6)),
            (p(6.2), p(3.4)),
        ]
        draw.line(outline, fill=icon_color, width=stroke, joint="curve")
        draw.line([(p(12.7), p(3.4)), (p(12.7), p(6.6)), (p(15.8), p(6.6))], fill=icon_color, width=stroke)
        for y in (8.7, 11.2, 13.7):
            draw.line([(p(8.3), p(y)), (p(13.7), p(y))], fill=icon_color, width=stroke)
    elif kind == "settings":
        center = (size / 2, size / 2)
        points = []
        for index in range(24):
            angle = (math.pi * 2 * index / 24) - (math.pi / 2)
            radius = 8.0 if index % 4 in (0, 1) else 6.7
            points.append((p(center[0] + math.cos(angle) * radius), p(center[1] + math.sin(angle) * radius)))
        draw.line(points + [points[0]], fill=icon_color, width=stroke, joint="curve")
        draw.ellipse([p(7.25), p(7.25), p(12.75), p(12.75)], outline=icon_color, width=stroke)
    elif kind == "close":
        inset = 6
        draw.line([(p(inset), p(inset)), (p(size - inset), p(size - inset))], fill=icon_color, width=stroke)
        draw.line([(p(size - inset), p(inset)), (p(inset), p(size - inset))], fill=icon_color, width=stroke)
    elif kind == "back":
        draw.polygon(
            [
                (p(5.0), p(10.0)),
                (p(10.8), p(4.6)),
                (p(10.8), p(7.7)),
                (p(16.0), p(7.7)),
                (p(16.0), p(12.3)),
                (p(10.8), p(12.3)),
                (p(10.8), p(15.4)),
            ],
            fill=icon_color,
        )
    elif kind == "play":
        draw.polygon([(p(6.8), p(4.7)), (p(6.8), p(15.3)), (p(15.0), p(10.0))], fill=icon_color)
    elif kind == "pause":
        draw.rounded_rectangle([p(6.3), p(5.2), p(8.7), p(14.8)], radius=p(0.7), fill=icon_color)
        draw.rounded_rectangle([p(11.3), p(5.2), p(13.7), p(14.8)], radius=p(0.7), fill=icon_color)
    elif kind == "remove":
        icon_stroke = max(stroke + scale, p(2.0))
        draw.line([(p(6.0), p(6.0)), (p(14.0), p(14.0))], fill=icon_color, width=icon_stroke)
        draw.line([(p(14.0), p(6.0)), (p(6.0), p(14.0))], fill=icon_color, width=icon_stroke)
    elif kind == "volume":
        draw.polygon(
            [(p(4.2), p(8.1)), (p(7.0), p(8.1)), (p(10.2), p(5.3)), (p(10.2), p(14.7)), (p(7.0), p(11.9)), (p(4.2), p(11.9))],
            fill=icon_color,
        )
        draw.arc([p(9.8), p(6.0), p(15.0), p(14.0)], start=-42, end=42, fill=icon_color, width=stroke)
        draw.arc([p(8.8), p(3.8), p(17.4), p(16.2)], start=-38, end=38, fill=icon_color, width=stroke)

    image = image.resize((size, size), _RESAMPLE)
    return ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))


_THUMBNAIL_CACHE: Dict[str, Optional[bytes]] = {}
_THUMBNAIL_CACHE_LOCK = threading.Lock()


def _fetch_thumbnail_bytes(url: str) -> Optional[bytes]:
    if not url:
        return None

    with _THUMBNAIL_CACHE_LOCK:
        if url in _THUMBNAIL_CACHE:
            return _THUMBNAIL_CACHE[url]

    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urlopen(request, timeout=8) as response:
            data = response.read()
    except Exception:  # pylint: disable=broad-except
        data = None

    with _THUMBNAIL_CACHE_LOCK:
        _THUMBNAIL_CACHE[url] = data
    return data


def _settings_path() -> str:
    if sys.platform.startswith("win"):
        base_dir = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base_dir = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")

    settings_dir = os.path.join(base_dir, "YouTubeDownloader")
    os.makedirs(settings_dir, exist_ok=True)
    return os.path.join(settings_dir, SETTINGS_FILE_NAME)


def _default_settings() -> Dict[str, str]:
    return {
        "audio_quality": "192 kbps",
        "video_quality": "720p",
        "parallel_downloads": "3",
        "download_dir": default_download_dir(),
    }


def _clean_settings(raw_settings: Optional[Dict]) -> Dict[str, str]:
    defaults = _default_settings()
    settings = dict(defaults)
    if isinstance(raw_settings, dict):
        settings.update({key: str(value) for key, value in raw_settings.items() if value is not None})

    if settings.get("audio_quality") not in AUDIO_QUALITIES:
        settings["audio_quality"] = defaults["audio_quality"]
    if settings.get("video_quality") not in VIDEO_QUALITIES:
        settings["video_quality"] = defaults["video_quality"]

    try:
        parallel = int(settings.get("parallel_downloads", defaults["parallel_downloads"]))
    except (TypeError, ValueError):
        parallel = int(defaults["parallel_downloads"])
    settings["parallel_downloads"] = str(max(1, min(4, parallel)))

    download_dir = (settings.get("download_dir") or "").strip()
    settings["download_dir"] = download_dir or defaults["download_dir"]
    return settings


def _load_settings() -> Dict[str, str]:
    try:
        with open(_settings_path(), "r", encoding="utf-8") as settings_file:
            raw_settings = json.load(settings_file)
    except (OSError, ValueError, json.JSONDecodeError):
        raw_settings = None
    return _clean_settings(raw_settings)


def _save_settings(settings: Dict[str, str]):
    with open(_settings_path(), "w", encoding="utf-8") as settings_file:
        json.dump(_clean_settings(settings), settings_file, indent=2)


class InAppTooltip:
    def __init__(self, root, widget, text: str):
        self.root = root
        self.widget = widget
        self.text = text
        self._after_id: Optional[str] = None
        self._label: Optional[ctk.CTkLabel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._destroy, add="+")

    def _widget_exists(self, widget) -> bool:
        try:
            return bool(widget.winfo_exists())
        except Exception:
            return False

    def _schedule(self, _event=None):
        if not self._widget_exists(self.widget):
            return
        self._cancel()
        self._after_id = self.root.after(350, self._show)

    def _cancel(self):
        if self._after_id is None:
            return
        try:
            self.root.after_cancel(self._after_id)
        except Exception:
            pass
        self._after_id = None

    def _show(self):
        self._after_id = None
        if not self._widget_exists(self.root) or not self._widget_exists(self.widget):
            return
        if self._label is None:
            self._label = ctk.CTkLabel(
                self.root,
                text=self.text,
                fg_color=SURFACE_HOVER,
                text_color=TEXT,
                corner_radius=6,
                font=("Segoe UI", 11),
                height=26,
            )

        try:
            self.root.update_idletasks()
            root_x = self.root.winfo_rootx()
            root_y = self.root.winfo_rooty()
            x = self.widget.winfo_rootx() - root_x + (self.widget.winfo_width() // 2)
            y = self.widget.winfo_rooty() - root_y + self.widget.winfo_height() + 6
            self._label.place(x=x, y=y, anchor="n")
            self._label.lift()
        except Exception:
            self._hide()

    def _hide(self, _event=None):
        self._cancel()
        if self._label is not None and self._widget_exists(self._label):
            self._label.place_forget()

    def _destroy(self, _event=None):
        self._cancel()
        if self._label is not None and self._widget_exists(self._label):
            self._label.destroy()
        self._label = None


class ComposerStartProxy:
    def __init__(self, shell):
        self.shell = shell

    def configure(self, **kwargs):
        self.shell.configure_start_button(**kwargs)

    config = configure

    def cget(self, attribute_name: str):
        return self.shell.cget_start_button(attribute_name)


class ComposerShell(tk.Canvas):
    def __init__(
        self,
        master,
        command,
        height: int,
        corner_radius: int,
        fg_color: str,
        border_color: str,
        bg_color: str,
    ):
        self._height_value = height
        self._corner_radius = corner_radius
        self._fg_color_value = fg_color
        self._border_color = border_color
        self._bg_color = bg_color
        self._button_text = "Start"
        self._button_width = 78
        self._button_height = 38
        self._button_radius = 19
        self._button_right_inset = 5
        self._button_fg = ACCENT
        self._button_hover = ACCENT_HOVER
        self._button_text_color = ACCENT_TEXT
        self._button_state = "normal"
        self._button_hovered = False
        self._command = command
        self._photo_image = None
        self.start_button = ComposerStartProxy(self)

        super().__init__(
            master=master,
            height=height,
            bg=bg_color,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Motion>", self._on_motion, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Button-1>", self._on_click, add="+")
        self._apply_cursor()

    def _button_bounds(self, width: Optional[int] = None):
        width = self.winfo_width() if width is None else width
        height = self._height_value
        x2 = max(1, width - self._button_right_inset)
        x1 = max(0, x2 - self._button_width)
        y1 = max(0, (height - self._button_height) // 2)
        return x1, y1, x2, y1 + self._button_height

    def _point_in_button(self, x: int, y: int) -> bool:
        x1, y1, x2, y2 = self._button_bounds()
        return x1 <= x <= x2 and y1 <= y <= y2

    def _current_button_fill(self) -> str:
        if self._button_state == "normal" and self._button_hovered:
            return self._button_hover
        return self._button_fg

    def _load_font(self, scale: int, max_width: int):
        font_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf")
        font_size = 12 * scale
        while font_size >= 9 * scale:
            try:
                font = ImageFont.truetype(font_path, font_size)
            except OSError:
                try:
                    font = ImageFont.truetype("arial.ttf", font_size)
                except OSError:
                    return ImageFont.load_default()

            bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), self._button_text, font=font)
            if bbox[2] - bbox[0] <= max_width:
                return font
            font_size -= scale
        return font

    def _render_image(self, width: Optional[int] = None) -> Image.Image:
        width = max(1, self.winfo_width() if width is None else width)
        scale = 4
        scaled_width = width * scale
        scaled_height = self._height_value * scale
        bg = ImageColor.getrgb(self._bg_color)
        shell = ImageColor.getrgb(self._fg_color_value)
        border = ImageColor.getrgb(self._border_color)
        button = ImageColor.getrgb(self._current_button_fill())
        text_color = ImageColor.getrgb(self._button_text_color)

        image = Image.new("RGB", (scaled_width, scaled_height), bg)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            [0, 0, scaled_width - 1, scaled_height - 1],
            radius=self._corner_radius * scale,
            fill=shell,
            outline=border,
            width=scale,
        )

        x1, y1, x2, y2 = self._button_bounds(width)
        button_box = [x1 * scale, y1 * scale, x2 * scale - 1, y2 * scale - 1]
        draw.rounded_rectangle(
            button_box,
            radius=self._button_radius * scale,
            fill=button,
            outline=None,
        )

        font = self._load_font(scale, max_width=(self._button_width - 12) * scale)
        text_bbox = draw.textbbox((0, 0), self._button_text, font=font)
        text_x = ((x1 + x2) * scale - (text_bbox[2] - text_bbox[0])) / 2 - text_bbox[0]
        text_y = ((y1 + y2) * scale - (text_bbox[3] - text_bbox[1])) / 2 - text_bbox[1] - (0.5 * scale)
        draw.text((text_x, text_y), self._button_text, fill=text_color, font=font)

        return image.resize((width, self._height_value), _RESAMPLE)

    def _draw(self):
        width = max(1, self.winfo_width())
        self.delete("all")
        self._photo_image = ImageTk.PhotoImage(self._render_image(width))
        self.create_image(0, 0, image=self._photo_image, anchor="nw")
        self.tag_lower("all")

    def _on_configure(self, _event=None):
        self._draw()

    def _on_motion(self, event):
        hovered = self._point_in_button(event.x, event.y)
        if hovered != self._button_hovered:
            self._button_hovered = hovered
            self._draw()
            self._apply_cursor()

    def _on_leave(self, _event=None):
        if self._button_hovered:
            self._button_hovered = False
            self._draw()
            self._apply_cursor()

    def _on_click(self, event):
        if self._button_state == "normal" and self._point_in_button(event.x, event.y) and self._command:
            self._command()
            return "break"
        return None

    def _apply_cursor(self):
        cursor = "hand2" if self._button_state == "normal" and self._button_hovered else ""
        super().configure(cursor=cursor)

    def configure_start_button(self, **kwargs):
        if "text" in kwargs:
            self._button_text = kwargs.pop("text")
        if "state" in kwargs:
            self._button_state = kwargs.pop("state")
            self._apply_cursor()
        if "fg_color" in kwargs:
            self._button_fg = kwargs.pop("fg_color")
        if "hover_color" in kwargs:
            self._button_hover = kwargs.pop("hover_color")
        if "text_color" in kwargs:
            self._button_text_color = kwargs.pop("text_color")
        if "width" in kwargs:
            self._button_width = int(kwargs.pop("width"))
        if "height" in kwargs:
            self._button_height = int(kwargs.pop("height"))
        if "corner_radius" in kwargs:
            self._button_radius = int(kwargs.pop("corner_radius"))
        self._draw()

    def cget_start_button(self, attribute_name: str):
        if attribute_name == "text":
            return self._button_text
        if attribute_name == "state":
            return self._button_state
        if attribute_name == "fg_color":
            return self._button_fg
        if attribute_name == "hover_color":
            return self._button_hover
        if attribute_name == "text_color":
            return self._button_text_color
        if attribute_name == "width":
            return self._button_width
        if attribute_name == "height":
            return self._button_height
        if attribute_name == "corner_radius":
            return self._button_radius
        if attribute_name == "right_inset":
            return self._button_right_inset
        if attribute_name == "bg_color":
            return self._fg_color_value
        raise ValueError(f"unknown option '{attribute_name}'")


class SmoothScrollableFrame(ctk.CTkScrollableFrame):
    def __init__(self, master, **kwargs):
        self._smooth_scroll_after_id: Optional[str] = None
        self._smooth_scroll_target_px = 0.0
        super().__init__(master, **kwargs)
        self.bind_all("<Button-4>", self._mouse_wheel_all, add="+")
        self.bind_all("<Button-5>", self._mouse_wheel_all, add="+")

    def destroy(self):
        self._cancel_smooth_wheel_scroll()
        super().destroy()

    def _cancel_smooth_wheel_scroll(self):
        if self._smooth_scroll_after_id is None:
            return
        try:
            self.after_cancel(self._smooth_scroll_after_id)
        except Exception:
            pass
        self._smooth_scroll_after_id = None

    def _vertical_scroll_span(self) -> float:
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is None:
            return 0.0
        try:
            scrollregion = canvas.bbox("all")
            if not scrollregion:
                return 0.0
            content_height = float(scrollregion[3] - scrollregion[1])
            visible_height = float(max(canvas.winfo_height(), 1))
        except Exception:
            return 0.0
        return max(0.0, content_height - visible_height)

    def _current_scroll_px(self, span: Optional[float] = None) -> float:
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is None:
            return 0.0
        span = self._vertical_scroll_span() if span is None else span
        if span <= 0:
            return 0.0
        try:
            return float(canvas.yview()[0]) * span
        except Exception:
            return 0.0

    def _scroll_to_px(self, offset: float, span: Optional[float] = None):
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is None:
            return
        span = self._vertical_scroll_span() if span is None else span
        if span <= 0:
            return
        clamped = max(0.0, min(float(offset), span))
        try:
            canvas.yview_moveto(clamped / span)
        except Exception:
            self._smooth_scroll_after_id = None

    def _wheel_delta_pixels(self, event) -> float:
        if sys.platform.startswith("win"):
            return -(float(getattr(event, "delta", 0)) / 120.0) * 92.0
        if sys.platform == "darwin":
            return -float(getattr(event, "delta", 0)) * 12.0

        event_num = getattr(event, "num", None)
        if event_num == 4:
            return -86.0
        if event_num == 5:
            return 86.0
        return -float(getattr(event, "delta", 0)) * 86.0

    def _smooth_scroll_by(self, delta_px: float):
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is None or not delta_px:
            return

        try:
            if canvas.yview() == (0.0, 1.0):
                return
        except Exception:
            return

        span = self._vertical_scroll_span()
        if span <= 0:
            return

        current = self._current_scroll_px(span)
        if self._smooth_scroll_after_id is None:
            self._smooth_scroll_target_px = current

        self._smooth_scroll_target_px = max(0.0, min(self._smooth_scroll_target_px + delta_px, span))
        if self._smooth_scroll_after_id is None:
            self._animate_smooth_scroll()

    def _animate_smooth_scroll(self):
        span = self._vertical_scroll_span()
        if span <= 0:
            self._smooth_scroll_after_id = None
            return

        self._smooth_scroll_target_px = max(0.0, min(self._smooth_scroll_target_px, span))
        current = self._current_scroll_px(span)
        distance = self._smooth_scroll_target_px - current

        if abs(distance) < 0.8:
            self._scroll_to_px(self._smooth_scroll_target_px, span)
            self._smooth_scroll_after_id = None
            return

        self._scroll_to_px(current + distance * 0.32, span)
        self._smooth_scroll_after_id = self.after(10, self._animate_smooth_scroll)

    def _mouse_wheel_all(self, event):
        if not self.check_if_master_is_canvas(event.widget):
            return None
        if self._shift_pressed:
            return super()._mouse_wheel_all(event)

        self._smooth_scroll_by(self._wheel_delta_pixels(event))
        return "break"


class LoadingPlaceholderFrame(ctk.CTkFrame):
    def __init__(
        self,
        master,
        message: str,
        detail: str = "",
        row_count: int = 3,
        compact: bool = False,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self._bars: List[ctk.CTkProgressBar] = []
        _ = row_count, compact

        label = ctk.CTkLabel(
            self,
            text=message,
            font=("Segoe UI", 13, "bold"),
            text_color=TEXT_SOFT,
            anchor="w",
        )
        label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))

        if detail:
            detail_label = ctk.CTkLabel(
                self,
                text=detail,
                font=("Segoe UI", 12),
                text_color=MUTED_DARK,
                anchor="w",
                wraplength=520,
            )
            detail_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))

        bar_row = 2 if detail else 1
        bar_pady = (0, 16) if detail else (8, 16)
        main_bar = ctk.CTkProgressBar(
            self,
            height=5,
            mode="indeterminate",
            indeterminate_speed=LOADER_SPEED,
            fg_color=BORDER,
            progress_color=ACCENT,
        )
        main_bar.grid(row=bar_row, column=0, sticky="ew", padx=14, pady=bar_pady)
        self._bars.append(main_bar)

        self.start()

    def start(self):
        for bar in self._bars:
            bar.start()

    def stop(self):
        for bar in self._bars:
            try:
                bar.stop()
            except Exception:
                pass

    def destroy(self):
        self.stop()
        super().destroy()


class DownloadRow(ctk.CTkFrame):
    def __init__(self, master, title: str, item_index: Optional[int] = None, item_count: Optional[int] = None):
        super().__init__(master, corner_radius=10, fg_color=ROW_BG)
        self.columnconfigure(1, weight=1)

        self._item_index = item_index
        self._item_count = item_count
        self._title = title or "Preparing…"
        self._active = False
        self._progress_loading = False
        self._thumbnail_url: Optional[str] = None

        self.thumb_container = ctk.CTkFrame(self, width=72, height=72, fg_color=THUMB_BG, corner_radius=8)
        self.thumb_container.grid(row=0, column=0, rowspan=2, padx=(10, 10), pady=8, sticky="n")
        self.thumb_container.grid_propagate(False)

        self.thumbnail_label = ctk.CTkLabel(
            self.thumb_container,
            text="🎬",
            font=("Segoe UI Emoji", 26),
            text_color=MUTED,
        )
        self.thumbnail_label.place(relx=0.5, rely=0.5, anchor="center")
        self.thumbnail_label.image = None

        self.progress_var = ctk.DoubleVar(value=0)

        self.meta_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.meta_frame.grid(row=0, column=1, padx=(0, 10), pady=8, sticky="nsew")
        self.meta_frame.columnconfigure(0, weight=1)
        self.meta_frame.columnconfigure(1, weight=0)

        index_text = self._format_index()
        self.index_label = ctk.CTkLabel(
            self.meta_frame,
            text=index_text,
            width=48,
            anchor="e",
            font=("Segoe UI", 13, "bold"),
            text_color=MUTED,
        )
        self.index_label.grid(row=0, column=1, padx=(12, 0), sticky="e")

        self.title_label = ctk.CTkLabel(
            self.meta_frame,
            text=self._title,
            anchor="w",
            font=("Segoe UI", 15, "bold"),
            text_color=TEXT,
        )
        self.title_label.grid(row=0, column=0, sticky="ew")

        self.percent_label = ctk.CTkLabel(
            self.meta_frame,
            text="",
            anchor="e",
            font=("Segoe UI", 13, "bold"),
            text_color=TEXT_SOFT,
        )
        self.percent_label.grid(row=1, column=1, sticky="e", padx=(12, 0), pady=(6, 0))

        self.status_label = ctk.CTkLabel(
            self.meta_frame,
            text="Waiting…",
            anchor="w",
            font=("Segoe UI", 12),
            text_color=MUTED,
            wraplength=520,
        )
        self.status_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.progress_bar = ctk.CTkProgressBar(
            self.meta_frame,
            variable=self.progress_var,
            height=7,
            progress_color=ACCENT,
        )
        self.progress_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))

    def _set_progress_loading(self, loading: bool):
        if self._progress_loading == loading:
            return
        self._progress_loading = loading
        if loading:
            self.progress_bar.configure(mode="indeterminate", indeterminate_speed=LOADER_SPEED)
            self.progress_bar.start()
        else:
            try:
                self.progress_bar.stop()
            except Exception:
                pass
            self.progress_bar.configure(mode="determinate")

    def _set_thumbnail_placeholder(self):
        self.thumbnail_label.configure(text="🎬", image=None)
        self.thumbnail_label.image = None

    def set_thumbnail_url(self, url: Optional[str]):
        if not url:
            self._thumbnail_url = None
            self._set_thumbnail_placeholder()
            return
        if url == self._thumbnail_url:
            return

        self._thumbnail_url = url
        self._set_thumbnail_placeholder()

        def worker():
            data = _fetch_thumbnail_bytes(url)

            def apply():
                if self._thumbnail_url != url:
                    return
                if not data:
                    self._set_thumbnail_placeholder()
                    return
                try:
                    pil_img = Image.open(io.BytesIO(data)).convert("RGBA")
                    pil_img.thumbnail((72, 72), _RESAMPLE)
                except Exception:  # pylint: disable=broad-except
                    self._set_thumbnail_placeholder()
                    return

                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(64, 64))
                self.thumbnail_label.configure(image=ctk_img, text="")
                self.thumbnail_label.image = ctk_img

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------
    def _format_index(self) -> str:
        if self._item_index is None:
            return "•"
        if self._item_count:
            return f"{self._item_index}/{self._item_count}"
        return f"#{self._item_index}"

    def update_meta(self, item_index: Optional[int], item_count: Optional[int]):
        if item_index is not None:
            self._item_index = item_index
        if item_count:
            self._item_count = item_count
        self.index_label.configure(text=self._format_index())

    def set_title(self, title: str):
        if title and title != self._title:
            self._title = title
            self.title_label.configure(text=title)

    def set_active(self, active: bool):
        if self._active == active:
            return
        self._active = active
        self.configure(fg_color=ROW_ACTIVE_BG if active else ROW_BG)

    def mark_error(self):
        self._set_progress_loading(False)
        self.configure(fg_color=ROW_ERROR_BG)
        self.status_label.configure(text_color=DANGER_HOVER)

    def mark_complete(self, label: str = "Complete"):
        self._set_progress_loading(False)
        self.set_active(False)
        self.progress_var.set(1.0)
        self.percent_label.configure(text="100%", text_color=ACCENT)
        self.status_label.configure(text=label, text_color=ACCENT)

    def update_progress(self, prog: DownloadProgress):
        if prog.item_index is not None or prog.item_count:
            self.update_meta(prog.item_index, prog.item_count)
        if prog.title:
            self.set_title(prog.title)
        if prog.thumbnail_url:
            self.set_thumbnail_url(prog.thumbnail_url)

        is_preparing = prog.status == "downloading" and prog.message in ("preparing", "resolving")
        is_processing = prog.status == "finished" and prog.message in ("postprocessing", "processing")
        has_known_download_progress = (
            prog.status == "downloading"
            and not is_preparing
            and (bool(prog.total) or float(prog.percent or 0.0) > 0)
        )
        self._set_progress_loading(False)

        if has_known_download_progress:
            clamped = max(0.0, min(100.0, float(prog.percent)))
            self.progress_var.set(clamped / 100.0)
            self.percent_label.configure(text=f"{clamped:.1f}%", text_color=TEXT_SOFT)
        elif is_processing:
            self.progress_var.set(1.0)
            self.percent_label.configure(text="100%", text_color=TEXT_SOFT)
        elif prog.status in ("queued", "downloading") or is_processing:
            if prog.status != "queued":
                self.progress_var.set(0.0)
            self.percent_label.configure(text="", text_color=TEXT_SOFT)

        status_text = ""
        text_color = MUTED

        if prog.status == "queued":
            status_text = "Queued"
            text_color = MUTED_DARK
        elif prog.status == "downloading":
            if is_preparing:
                status_text = "Preparing..."
            elif not has_known_download_progress:
                status_text = "Downloading..."
            else:
                status_parts = []
                if prog.eta:
                    status_parts.append(f"ETA {int(prog.eta)}s")
                if prog.speed:
                    kb = prog.speed / 1024
                    status_parts.append(f"{kb:,.0f} KB/s")
                status_text = " • ".join(status_parts) or "Downloading..."
            text_color = TEXT_SOFT

        elif prog.status == "finished":
            if prog.message == "postprocessing":
                status_text = "Converting..."
                text_color = TEXT_SOFT
            elif prog.message == "processing":
                status_text = "Processing..."
                text_color = TEXT_SOFT
            elif prog.message == "all_done":
                self.mark_complete("Complete")
                return
            else:
                self.mark_complete("Finished")
                return

        elif prog.status == "stopped":
            self._set_progress_loading(False)
            status_text = prog.message or "Stopped"
            text_color = WARNING

        elif prog.status == "error":
            self._set_progress_loading(False)
            status_text = prog.message or "Error"
            text_color = DANGER_HOVER
            self.mark_error()

        if status_text:
            self.status_label.configure(text=status_text, text_color=text_color)


class DownloadList(SmoothScrollableFrame):
    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            corner_radius=12,
            fg_color=SURFACE,
            **kwargs,
        )
        self.grid_columnconfigure(0, weight=1)
        self._rows: Dict[str, DownloadRow] = {}
        self._row_status: Dict[str, str] = {}
        self._row_order: Dict[str, int] = {}
        self._next_row_order = 0
        self._scroll_animation_after_id: Optional[str] = None
        self._loading_frame: Optional[LoadingPlaceholderFrame] = None
        self._placeholder_text = "No downloads yet. Paste a link to begin."
        self._empty_label = ctk.CTkLabel(
            self,
            text=self._placeholder_text,
            text_color=MUTED_DARK,
            font=("Segoe UI", 13),
            anchor="w",
        )
        self._empty_label.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 12))

    def reset(self):
        for row in self._rows.values():
            row.destroy()
        self._rows.clear()
        self._row_status.clear()
        self._row_order.clear()
        self._next_row_order = 0
        self._cancel_scroll_animation()
        self._cancel_loading_placeholder()
        self._show_placeholder()

    def set_placeholder_text(self, text: str, show: bool = False):
        self._placeholder_text = text
        self._empty_label.configure(text=text)
        if show:
            self._show_placeholder()

    def show_loading(self, message: str, detail: str = ""):
        self._empty_label.grid_forget()
        self._cancel_loading_placeholder()
        self._loading_frame = LoadingPlaceholderFrame(
            self,
            message=message,
            detail=detail,
            row_count=3,
        )
        self._loading_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 6))

    def _cancel_loading_placeholder(self):
        if self._loading_frame is None:
            return
        self._loading_frame.destroy()
        self._loading_frame = None

    def _show_placeholder(self):
        self._cancel_loading_placeholder()
        self._empty_label.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 12))

    def _hide_placeholder(self):
        self._empty_label.grid_forget()

    def _key_for(self, prog: DownloadProgress) -> str:
        if prog.job_id:
            return prog.job_id
        if prog.item_index is not None:
            return f"item-{prog.item_index:04d}"
        return "single"

    def has_rows(self) -> bool:
        return bool(self._rows)

    def _status_group(self, status: str) -> int:
        if status in ("finished", "stopped", "error"):
            return 1
        return 0

    def _reflow_rows(self):
        def sort_key(item):
            key, row = item
            return (
                self._status_group(self._row_status.get(key, "queued")),
                row._item_index if row._item_index is not None else 10**9,
                self._row_order.get(key, 0),
            )

        for row_index, (_key, row) in enumerate(sorted(self._rows.items(), key=sort_key)):
            row.grid_configure(row=row_index)

    def _cancel_scroll_animation(self):
        if self._scroll_animation_after_id is None:
            return
        try:
            self.after_cancel(self._scroll_animation_after_id)
        except Exception:
            pass
        self._scroll_animation_after_id = None

    def _scroll_to_top(self):
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is None:
            return

        self._cancel_scroll_animation()
        self._cancel_smooth_wheel_scroll()

        def start_animation():
            try:
                start = float(canvas.yview()[0])
            except Exception:
                self._scroll_animation_after_id = None
                return

            if start <= 0.001:
                self._scroll_animation_after_id = None
                return

            total_steps = 30
            interval_ms = 9

            def animate(step=1):
                progress = min(1.0, step / total_steps)
                if progress < 0.5:
                    eased = 8 * progress**4
                else:
                    eased = 1 - ((-2 * progress + 2) ** 4) / 2
                position = max(0.0, start * (1 - eased))

                try:
                    canvas.yview_moveto(position)
                except Exception:
                    self._scroll_animation_after_id = None
                    return

                if progress < 1.0:
                    self._scroll_animation_after_id = self.after(interval_ms, lambda: animate(step + 1))
                else:
                    self._scroll_animation_after_id = None

            animate()

        self._scroll_animation_after_id = self.after(40, start_animation)

    def _ensure_row(self, prog: DownloadProgress) -> DownloadRow:
        key = self._key_for(prog)
        row = self._rows.get(key)
        if row is None:
            self._cancel_loading_placeholder()
            self._hide_placeholder()
            display_index = prog.item_index
            row = DownloadRow(self, prog.title or "Preparing…", display_index, prog.item_count)
            row.grid(row=len(self._rows), column=0, padx=10, pady=8, sticky="ew")
            self._rows[key] = row
            self._row_status[key] = prog.status or "queued"
            self._row_order[key] = self._next_row_order
            self._next_row_order += 1
            self._scroll_to_top()
        return row

    def _set_row_status(self, prog: DownloadProgress):
        key = self._key_for(prog)
        status = prog.status or self._row_status.get(key, "queued")
        if status == "finished" and prog.message in ("postprocessing", "processing"):
            status = "downloading"
        self._row_status[key] = status

    def update_from_progress(self, prog: DownloadProgress):
        if not prog.job_id:
            if prog.message == "all_done":
                if not self._rows:
                    self.set_placeholder_text("Download completed.", show=True)
                    return
                for key, row in self._rows.items():
                    row.mark_complete("Complete")
                    self._row_status[key] = "finished"
                self._reflow_rows()
                return

            if prog.status == "error":
                if not self._rows:
                    self.set_placeholder_text("Could not start the download. Check the link and try again.", show=True)
                    return
                for key, row in self._rows.items():
                    row.mark_error()
                    row.set_active(False)
                    self._row_status[key] = "error"
                self._reflow_rows()
                return

            if prog.status == "stopped":
                if not self._rows:
                    self.set_placeholder_text("Download stopped before any items were queued.", show=True)
                    return
                for key, status in list(self._row_status.items()):
                    if status not in ("finished", "error"):
                        self._row_status[key] = "stopped"
                self.mark_all_inactive()
                self._reflow_rows()
                return

        row = self._ensure_row(prog)
        self._set_row_status(prog)
        if prog.status == "downloading":
            row.set_active(True)
        elif prog.status == "queued":
            row.set_active(False)
        elif prog.status in ("error", "stopped", "finished"):
            row.set_active(False)
        row.update_progress(prog)
        self._reflow_rows()

    def mark_all_inactive(self):
        for row in self._rows.values():
            row.set_active(False)




class HistoryMediaPlayer(ctk.CTkFrame):
    AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg")
    VIDEO_EXTENSIONS = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v")

    def __init__(self, master, on_close, **kwargs):
        super().__init__(
            master,
            corner_radius=12,
            fg_color=SURFACE,
            **kwargs,
        )
        self.on_close = on_close
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.file_path: Optional[str] = None
        self.media_info = MediaInfo()
        self.duration = 0.0
        self.playing = False
        self._ui_after_id: Optional[str] = None
        self._display_photo = None
        self._artwork_image: Optional[Image.Image] = None
        self._last_visual: Optional[Image.Image] = None
        self._slider_dragging = False
        self._syncing_slider = False
        self._closed = False
        self._event_attached = False
        self._player_event_queue = queue.Queue()

        self._vlc = None
        self._vlc_instance = None
        self._vlc_player = None
        self._vlc_media = None
        self.play_btn = None
        self.player_icons = {
            "play": _make_line_icon("play", TEXT, 18),
            "pause": _make_line_icon("pause", TEXT, 18),
            "volume": _make_line_icon("volume", MUTED, 18),
        }

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        header.grid_columnconfigure(0, weight=1)

        self.player_title_var = ctk.StringVar(value="Now playing")
        title_label = ctk.CTkLabel(
            header,
            textvariable=self.player_title_var,
            font=("Segoe UI", 17, "bold"),
            text_color=TEXT,
            anchor="w",
        )
        title_label.grid(row=0, column=0, sticky="ew")

        close_btn = ctk.CTkButton(
            header,
            text="Close",
            width=72,
            height=30,
            corner_radius=8,
            command=self._close_requested,
            fg_color=SURFACE_ALT,
            hover_color=SURFACE_HOVER,
            text_color=TEXT,
        )
        close_btn.grid(row=0, column=1, padx=(12, 0))

        self.player_meta_var = ctk.StringVar(value="")
        meta_label = ctk.CTkLabel(
            self,
            textvariable=self.player_meta_var,
            font=("Segoe UI", 12),
            text_color=MUTED,
            anchor="w",
        )
        meta_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        self.video_surface = tk.Frame(
            self,
            bg=ROW_BG,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            height=300,
        )
        self.video_surface.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 12))
        self.video_surface.bind("<Configure>", self._on_surface_resize, add="+")

        self.art_canvas = tk.Canvas(
            self.video_surface,
            bg=ROW_BG,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self.art_canvas.place(x=0, y=0, relwidth=1, relheight=1)

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        controls.grid_columnconfigure(4, weight=1)

        self.play_btn = ctk.CTkButton(
            controls,
            text="",
            image=self.player_icons["pause"],
            width=30,
            height=32,
            corner_radius=0,
            command=self._toggle_playback,
            fg_color="transparent",
            hover=False,
            text_color=TEXT,
        )
        self.play_btn.grid(row=0, column=0, padx=(0, 8))

        volume_icon = ctk.CTkLabel(
            controls,
            text="",
            image=self.player_icons["volume"],
            width=22,
        )
        volume_icon.grid(row=0, column=1, padx=(0, 4))

        self.volume_slider = ctk.CTkSlider(
            controls,
            from_=0,
            to=100,
            number_of_steps=100,
            width=82,
            command=self._on_volume_change,
            fg_color=BORDER,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
        )
        self.volume_slider.grid(row=0, column=2, padx=(0, 12))
        self.volume_slider.set(100)

        self.elapsed_var = ctk.StringVar(value="0:00")
        elapsed_label = ctk.CTkLabel(
            controls,
            textvariable=self.elapsed_var,
            width=48,
            text_color=MUTED,
            font=("Segoe UI", 12),
        )
        elapsed_label.grid(row=0, column=3, padx=(0, 8))

        self.progress_slider = ctk.CTkSlider(
            controls,
            from_=0,
            to=1000,
            number_of_steps=1000,
            command=self._on_progress_drag,
            fg_color=BORDER,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
        )
        self.progress_slider.grid(row=0, column=4, sticky="ew")
        self.progress_slider.set(0)
        self.progress_slider.bind("<ButtonPress-1>", self._begin_seek, add="+")
        self.progress_slider.bind("<ButtonRelease-1>", self._finish_seek, add="+")

        self.total_var = ctk.StringVar(value="0:00")
        total_label = ctk.CTkLabel(
            controls,
            textvariable=self.total_var,
            width=48,
            text_color=MUTED,
            font=("Segoe UI", 12),
        )
        total_label.grid(row=0, column=5, padx=(8, 10))

        self.player_status_var = ctk.StringVar(value="")
        status_label = ctk.CTkLabel(
            self,
            textvariable=self.player_status_var,
            font=("Segoe UI", 12),
            text_color=MUTED_DARK,
            anchor="w",
        )
        status_label.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 14))

    def destroy(self):
        self.close_media()
        self._release_vlc()
        super().destroy()

    def open_media(self, file_path: str, artwork_path: Optional[str] = None):
        self.close_media(clear_visual=False)
        self._closed = False
        self.file_path = file_path
        self.player_title_var.set(os.path.basename(file_path) or "Now playing")
        self.player_meta_var.set("Opening media...")
        self.player_status_var.set("Loading playback...")
        self.elapsed_var.set("0:00")
        self.total_var.set("0:00")
        self.progress_slider.set(0)
        self.playing = False
        self._update_play_button_icon()
        self.play_btn.configure(state="disabled")
        self._draw_message("Loading media...")
        self.update_idletasks()

        self.media_info = probe_media(file_path)
        self.duration = self.media_info.duration
        self.total_var.set(format_media_time(self.duration))
        self._load_artwork(artwork_path)
        self._clear_player_events()

        if not os.path.exists(file_path):
            self.player_meta_var.set("Missing file")
            self._draw_message("File not found")
            self.playing = False
            self._update_play_button_icon()
            self.play_btn.configure(state="disabled")
            return

        self.player_meta_var.set(self._media_description())
        self.player_status_var.set("Preparing playback...")

        if self._is_video_media():
            self._show_video_surface()
        else:
            self._show_art_surface()
            self._draw_audio_visual()

        try:
            self._ensure_vlc_backend()
        except Exception as exc:  # pylint: disable=broad-except
            self.player_status_var.set(str(exc))
            self._draw_message("Playback runtime missing")
            self.playing = False
            self._update_play_button_icon()
            self.play_btn.configure(state="disabled")
            return

        try:
            self._load_vlc_media(file_path)
            self._set_playing(True)
            self.play_btn.configure(state="normal")
        except Exception as exc:  # pylint: disable=broad-except
            self.player_status_var.set(f"Playback failed: {exc}")
            self.playing = False
            self._update_play_button_icon()
            self.play_btn.configure(state="disabled")
            return

        self.player_status_var.set("")
        self._schedule_ui_update(50)

    def close_media(self, clear_visual: bool = True):
        self._closed = True
        self._cancel_after_jobs()
        self.playing = False
        self.file_path = None
        self._clear_player_events()

        if self._vlc_player is not None:
            try:
                self._vlc_player.stop()
            except Exception:
                pass
        if self._vlc_media is not None:
            try:
                self._vlc_media.release()
            except Exception:
                pass
        self._vlc_media = None

        if clear_visual:
            self.art_canvas.delete("all")
            self._display_photo = None
            self._last_visual = None

    def _ensure_vlc_backend(self):
        if self._vlc_player is not None:
            return

        try:
            vlc, runtime_dir = import_vlc_binding()
        except Exception as exc:
            raise RuntimeError(f"Could not load VLC playback engine. {vlc_runtime_status()}. {exc}") from exc

        instance = vlc.Instance("--quiet", "--no-video-title-show")
        if instance is None:
            raise RuntimeError("Could not start the VLC playback engine.")

        player = instance.media_player_new()
        if player is None:
            raise RuntimeError("Could not create the VLC media player.")

        self._vlc = vlc
        self._vlc_instance = instance
        self._vlc_player = player
        self._attach_vlc_events()

    def _attach_vlc_events(self):
        if self._event_attached or self._vlc_player is None or self._vlc is None:
            return
        event_manager = self._vlc_player.event_manager()
        event_manager.event_attach(self._vlc.EventType.MediaPlayerEndReached, self._threadsafe_vlc_event, "eof")
        event_manager.event_attach(
            self._vlc.EventType.MediaPlayerEncounteredError,
            self._threadsafe_vlc_event,
            "error",
        )
        event_manager.event_attach(
            self._vlc.EventType.MediaPlayerLengthChanged,
            self._threadsafe_vlc_event,
            "length_changed",
        )
        self._event_attached = True

    def _release_vlc(self):
        if self._vlc_player is not None:
            try:
                self._vlc_player.release()
            except Exception:
                pass
        if self._vlc_instance is not None:
            try:
                self._vlc_instance.release()
            except Exception:
                pass
        self._vlc_player = None
        self._vlc_instance = None
        self._vlc = None
        self._event_attached = False

    def _load_vlc_media(self, file_path: str):
        if self._vlc_instance is None or self._vlc_player is None:
            raise RuntimeError("VLC playback engine is not ready.")

        if self._vlc_media is not None:
            try:
                self._vlc_media.release()
            except Exception:
                pass
            self._vlc_media = None

        if self._is_video_media():
            self._set_vlc_window()

        media_uri = Path(file_path).resolve().as_uri()
        self._vlc_media = self._vlc_instance.media_new(media_uri)
        self._vlc_player.set_media(self._vlc_media)
        self._vlc_player.audio_set_volume(int(float(self.volume_slider.get())))

    def _set_vlc_window(self, surface=None):
        if self._vlc_player is None:
            return
        surface = surface or self.video_surface
        surface.update_idletasks()
        handle = surface.winfo_id()
        if sys.platform.startswith("win"):
            self._vlc_player.set_hwnd(handle)
        elif sys.platform == "darwin":
            self._vlc_player.set_nsobject(handle)
        else:
            self._vlc_player.set_xwindow(handle)

    def _load_artwork(self, artwork_path: Optional[str]):
        self._artwork_image = None
        if not artwork_path or not os.path.exists(artwork_path):
            return
        try:
            with Image.open(artwork_path) as image:
                self._artwork_image = image.convert("RGB").copy()
        except Exception:
            self._artwork_image = None

    def _media_description(self) -> str:
        meta_parts = []
        if self._is_video_media():
            if self.media_info.width and self.media_info.height:
                meta_parts.append(f"Video {self.media_info.width}x{self.media_info.height}")
            else:
                meta_parts.append("Video")
        elif self.media_info.has_audio:
            meta_parts.append("Audio")
        else:
            meta_parts.append("Media")
        if self.media_info.has_audio and self._is_video_media():
            meta_parts.append("Audio")
        if self.duration:
            meta_parts.append(format_media_time(self.duration))
        return " | ".join(meta_parts)

    def _is_video_media(self) -> bool:
        extension = os.path.splitext(self.file_path or "")[1].lower()
        if extension in self.AUDIO_EXTENSIONS:
            return False
        if extension in self.VIDEO_EXTENSIONS:
            return True
        if self.media_info.has_video:
            return True
        return extension in self.VIDEO_EXTENSIONS

    def _set_playing(self, playing: bool):
        if self._vlc_player is None:
            return
        if playing:
            result = self._vlc_player.play()
            if result == -1:
                raise RuntimeError("VLC could not start playback.")
        else:
            self._vlc_player.pause()
        self.playing = playing
        self._update_play_button_icon()

    def _update_play_button_icon(self):
        if self.play_btn is None:
            return
        icon_name = "pause" if self.playing else "play"
        self.play_btn.configure(text="", image=self.player_icons[icon_name])

    def _toggle_playback(self):
        if self.duration and self._current_position() >= self.duration - 0.15 and not self.playing:
            self._seek_to(0.0)
        try:
            self._set_playing(not self.playing)
        except Exception as exc:  # pylint: disable=broad-except
            self.player_status_var.set(f"Playback failed: {exc}")
        self._schedule_ui_update(50)

    def _begin_seek(self, _event=None):
        self._slider_dragging = True

    def _on_progress_drag(self, value):
        if self._syncing_slider or not self._slider_dragging:
            return
        position = self._slider_to_seconds(float(value))
        self.elapsed_var.set(format_media_time(position))

    def _finish_seek(self, _event=None):
        target = self._slider_to_seconds(self.progress_slider.get())
        self._slider_dragging = False
        self._seek_to(target)

    def _on_volume_change(self, value):
        if self._vlc_player is not None:
            self._vlc_player.audio_set_volume(max(0, min(100, int(float(value)))))

    def _seek_to(self, seconds: float):
        if self._vlc_player is None:
            return
        target = max(0.0, float(seconds or 0.0))
        if self.duration > 0:
            target = min(target, self.duration)
        self._vlc_player.set_time(int(target * 1000))
        self._update_time_ui(target)

    def _slider_to_seconds(self, value: float) -> float:
        if self.duration <= 0:
            return 0.0
        return (max(0.0, min(1000.0, value)) / 1000.0) * self.duration

    def _current_position(self) -> float:
        if self._vlc_player is None:
            return 0.0
        try:
            position_ms = self._vlc_player.get_time()
        except Exception:
            return 0.0
        if position_ms is None or position_ms < 0:
            return 0.0
        return position_ms / 1000.0

    def _refresh_duration_from_vlc(self):
        if self._vlc_player is None:
            return
        try:
            length_ms = self._vlc_player.get_length()
        except Exception:
            return
        if length_ms and length_ms > 0:
            self.duration = length_ms / 1000.0
            self.total_var.set(format_media_time(self.duration))

    def _schedule_ui_update(self, delay_ms: int):
        if self._ui_after_id is not None:
            return
        self._ui_after_id = self.after(max(1, delay_ms), self._ui_loop)

    def _ui_loop(self):
        self._ui_after_id = None
        if self._closed:
            return

        self._drain_player_events()
        self._refresh_duration_from_vlc()
        position = self._current_position()
        self._update_time_ui(position)
        self._ui_after_id = self.after(250, self._ui_loop)

    def _update_time_ui(self, position: float):
        position = max(0.0, position)
        if self.duration > 0:
            position = min(position, self.duration)
        self.elapsed_var.set(format_media_time(position))
        self.total_var.set(format_media_time(self.duration))
        if not self._slider_dragging:
            slider_value = 0 if self.duration <= 0 else (position / self.duration) * 1000
            self._syncing_slider = True
            try:
                self.progress_slider.set(max(0, min(1000, slider_value)))
            finally:
                self._syncing_slider = False

    def _show_video_surface(self):
        self.art_canvas.place_forget()
        self.video_surface.configure(bg=ROW_BG)

    def _show_art_surface(self):
        self.art_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.art_canvas.tk.call("raise", self.art_canvas._w)

    def _draw_audio_visual(self):
        if self._artwork_image is not None:
            self._draw_pil_image(self._artwork_image)
        else:
            self._draw_message("Audio")

    def _on_surface_resize(self, _event=None):
        if self.art_canvas.winfo_ismapped():
            self._redraw_last_visual()

    def _redraw_last_visual(self):
        if self._last_visual is not None:
            self._draw_pil_image(self._last_visual)
        elif self.file_path and not self._is_video_media():
            self._draw_audio_visual()

    def _draw_pil_image(self, pil_image: Image.Image):
        self._show_art_surface()
        self._last_visual = pil_image
        self.art_canvas.update_idletasks()
        canvas_width = max(1, self.art_canvas.winfo_width())
        canvas_height = max(1, self.art_canvas.winfo_height())
        image_width, image_height = pil_image.size
        if image_width <= 0 or image_height <= 0:
            return

        scale = min(canvas_width / image_width, canvas_height / image_height)
        target_width = max(1, int(image_width * scale))
        target_height = max(1, int(image_height * scale))
        resized = pil_image.resize((target_width, target_height), _RESAMPLE)
        self._display_photo = ImageTk.PhotoImage(resized)
        self.art_canvas.delete("all")
        self.art_canvas.create_rectangle(0, 0, canvas_width, canvas_height, fill=ROW_BG, outline="")
        self.art_canvas.create_image(
            canvas_width // 2,
            canvas_height // 2,
            image=self._display_photo,
            anchor="center",
        )

    def _draw_message(self, message: str):
        self._show_art_surface()
        self._last_visual = None
        self.art_canvas.update_idletasks()
        canvas_width = max(1, self.art_canvas.winfo_width())
        canvas_height = max(1, self.art_canvas.winfo_height())
        self.art_canvas.delete("all")
        self.art_canvas.create_rectangle(0, 0, canvas_width, canvas_height, fill=ROW_BG, outline="")
        self.art_canvas.create_text(
            canvas_width // 2,
            canvas_height // 2,
            text=message,
            fill=MUTED,
            font=("Segoe UI", 18, "bold"),
        )

    def _threadsafe_vlc_event(self, _event, event_name: str):
        self._player_event_queue.put((event_name, None))

    def _drain_player_events(self):
        try:
            while True:
                event_name, _payload = self._player_event_queue.get_nowait()
                if event_name == "eof":
                    self._handle_eof()
                elif event_name == "error":
                    self._handle_playback_error()
                elif event_name == "length_changed":
                    self._refresh_duration_from_vlc()
        except queue.Empty:
            pass

    def _clear_player_events(self):
        try:
            while True:
                self._player_event_queue.get_nowait()
        except queue.Empty:
            pass

    def _handle_playback_error(self):
        self.playing = False
        self._update_play_button_icon()
        self.player_status_var.set("Playback error. The file may be incomplete or unsupported.")
        if not self._is_video_media():
            self._draw_message("Playback error")

    def _handle_eof(self):
        self.playing = False
        self._update_play_button_icon()
        if self.duration:
            self._update_time_ui(self.duration)

    def _close_requested(self):
        self.close_media()
        self.on_close()

    def _cancel_after_jobs(self):
        if self._ui_after_id is not None:
            try:
                self.after_cancel(self._ui_after_id)
            except Exception:
                pass
        self._ui_after_id = None


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.configure(fg_color=BG)
        self.title("Audora")
        self.geometry("880x620")
        self.minsize(780, 540)

        ensure_ffmpeg_or_die(self)

        self.settings = _load_settings()

        # Threading / queue manager
        self.manager = DownloadManager(
            self._enqueue_log,
            self._enqueue_progress,
            max_workers=int(self.settings["parallel_downloads"]),
        )
        self.audio_quality_var = ctk.StringVar(value=self.settings["audio_quality"])
        self.video_quality_var = ctk.StringVar(value=self.settings["video_quality"])
        self.out_dir_var = ctk.StringVar(value=self.settings["download_dir"])
        self.settings_btn: Optional[ctk.CTkButton] = None
        self.history_btn: Optional[ctk.CTkButton] = None
        self.clear_downloads_btn: Optional[ctk.CTkButton] = None
        self.settings_panel = None
        self.format_dropdown_panel = None
        self.icons = {
            "history": _make_line_icon("history", TEXT, 21),
            "settings": _make_line_icon("settings", TEXT, 20),
            "close": _make_line_icon("close", TEXT, 24),
            "back": _make_line_icon("back", TEXT, 22),
            "back_muted": _make_line_icon("back", MUTED_DARK, 22),
            "play": _make_line_icon("play", TEXT, 18),
            "play_muted": _make_line_icon("play", MUTED_DARK, 18),
            "remove": _make_line_icon("remove", DANGER_HOVER, 18),
            "remove_muted": _make_line_icon("remove", MUTED_DARK, 18),
        }
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue()
        self.activity_history = []
        self.history_panel = None
        self.history_player: Optional[HistoryMediaPlayer] = None
        self.history_images: List[ctk.CTkImage] = []
        self.history_context_stack: List[Dict[str, object]] = []
        self._history_load_token = 0
        self._history_render_after_id: Optional[str] = None
        self._download_start_token = 0
        self._download_start_after_id: Optional[str] = None
        self._current_total_items: Optional[int] = None
        self._cancel_requested = False
        self._downloads_visible = False

        # UI build
        self._build_ui()
        self._clear_activity()

        # Poll queues
        self.after(80, self._drain_log_queue)
        self.after(80, self._drain_progress_queue)
        self.after(220, self._ensure_history_panel)

    # ------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------
    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(pady=(18, 8), padx=30, fill="x")
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        title = ctk.CTkLabel(header, text="Audora", font=("Segoe UI", 22, "bold"), text_color=TEXT)
        title.grid(row=0, column=0, sticky="w")

        header_actions = ctk.CTkFrame(header, fg_color="transparent")
        header_actions.grid(row=0, column=1, sticky="e")

        self.history_btn = ctk.CTkButton(
            header_actions,
            text="",
            image=self.icons["history"],
            width=32,
            height=32,
            corner_radius=0,
            fg_color="transparent",
            hover_color=BG,
            command=self._show_history,
        )
        self.history_btn.pack(side="left", padx=(0, 8))
        InAppTooltip(self, self.history_btn, "History")

        self.settings_btn = ctk.CTkButton(
            header_actions,
            text="",
            image=self.icons["settings"],
            width=32,
            height=32,
            corner_radius=0,
            fg_color="transparent",
            hover_color=BG,
            command=self._open_settings,
        )
        self.settings_btn.pack(side="left")
        InAppTooltip(self, self.settings_btn, "Settings")

        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True, padx=30, pady=(0, 0))

        self.input_section = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self.input_section.pack(fill="x", expand=True, padx=54)

        self.launch_intro_frame = ctk.CTkFrame(self.input_section, fg_color="transparent")
        self.launch_intro_frame.pack(fill="x", pady=(0, 22))

        self.launch_title = ctk.CTkLabel(
            self.launch_intro_frame,
            text="Audora",
            font=("Segoe UI", 36, "bold"),
            text_color=TEXT,
        )
        self.launch_title.pack(pady=(0, 8))

        self.launch_subtitle = ctk.CTkLabel(
            self.launch_intro_frame,
            text="Download audio, video, and playlists from a YouTube link.",
            font=("Segoe UI", 14),
            text_color=MUTED,
            wraplength=620,
            justify="center",
        )
        self.launch_subtitle.pack()

        self.input_shell = ComposerShell(
            self.input_section,
            command=self._toggle_download,
            height=46,
            corner_radius=23,
            fg_color=SURFACE_ALT,
            border_color=BORDER,
            bg_color=BG,
        )
        self.input_shell.pack(fill="x")
        self.input_shell.bind("<Configure>", self._layout_input_shell_children, add="+")

        self.format_var = ctk.StringVar(value="Audio")

        self.url_entry = ctk.CTkEntry(
            self.input_shell,
            placeholder_text="Paste YouTube URL here...",
            width=240,
            height=34,
            corner_radius=0,
            bg_color=SURFACE_ALT,
            fg_color=SURFACE_ALT,
            border_width=0,
            text_color=TEXT,
            placeholder_text_color=MUTED_DARK,
        )
        self.url_entry.place(x=18, y=6)
        self.url_entry.bind("<Return>", self._on_url_submit)

        self.format_menu = tk.Label(
            self.input_shell,
            text=self._format_dropdown_label(),
            bg=SURFACE_ALT,
            fg=TEXT_SOFT,
            activebackground=SURFACE_ALT,
            activeforeground=TEXT,
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.format_menu.bind("<Button-1>", lambda _event: self._toggle_format_dropdown(), add="+")
        self.format_menu.place(x=260, y=7, width=72, height=32)

        self.start_btn = self.input_shell.start_button

        self.input_hint_var = ctk.StringVar(value="")
        self.input_hint_label = ctk.CTkLabel(
            self.input_section,
            textvariable=self.input_hint_var,
            text_color=MUTED,
            font=("Segoe UI", 12),
            anchor="center",
        )
        self.input_hint_label.pack(pady=(8, 0))

        # Activity + download list
        self.downloads_card = ctk.CTkFrame(self.content_frame, fg_color=SURFACE, corner_radius=14)

        header_row = ctk.CTkFrame(self.downloads_card, fg_color="transparent")
        header_row.pack(fill="x", padx=16, pady=(16, 8))

        self.jobs_title_var = ctk.StringVar(value="Waiting for downloads")
        jobs_title = ctk.CTkLabel(
            header_row,
            textvariable=self.jobs_title_var,
            font=("Segoe UI", 17, "bold"),
            text_color=TEXT,
        )
        jobs_title.pack(side="left")

        self.clear_downloads_btn = ctk.CTkButton(
            header_row,
            text="Clear",
            width=64,
            height=30,
            corner_radius=8,
            command=self._reset_to_start_state,
            fg_color=SURFACE_ALT,
            hover_color=SURFACE_HOVER,
            text_color=TEXT,
        )
        self.clear_downloads_btn.pack(side="right")
        self.clear_downloads_btn.pack_forget()

        header_divider = ctk.CTkFrame(self.downloads_card, height=2, fg_color=BORDER)
        header_divider.pack(fill="x", padx=16, pady=(0, 12))

        self.download_list = DownloadList(self.downloads_card, width=780, height=260)
        self.download_list.pack(fill="both", expand=True, padx=(2, 16), pady=(0, 18))

        self.status_var = ctk.StringVar(value="Ready")
        self.status_label = ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color=MUTED, font=("Segoe UI", 12)
        )

        self._update_input_hint()
        self._update_window_title()




    # ------------------------------------------------------------
    # UI events
    # ------------------------------------------------------------
    def _layout_input_shell_children(self, _event=None):
        if not hasattr(self, "input_shell") or not hasattr(self, "format_menu") or not hasattr(self, "url_entry"):
            return
        width = max(self.input_shell.winfo_width(), 1)
        button_width = int(self.start_btn.cget("width"))
        button_right_inset = int(self.start_btn.cget("right_inset"))
        format_width = 72
        format_gap = 5
        left_pad = 18
        entry_gap = 8
        format_x = max(left_pad + 90, width - button_right_inset - button_width - format_gap - format_width)
        entry_width = max(80, format_x - left_pad - entry_gap)

        self.url_entry.configure(width=entry_width)
        self.url_entry.place_configure(x=left_pad, y=6)
        self.format_menu.place_configure(x=format_x, y=7, width=format_width, height=32)

    def _set_format_menu_state(self, state: str):
        if not hasattr(self, "format_menu"):
            return
        self.format_menu.configure(
            state=state,
            fg=TEXT_SOFT if state == "normal" else MUTED_DARK,
            cursor="hand2" if state == "normal" else "",
        )

    def _format_dropdown_label(self) -> str:
        return f"{self.format_var.get()} ▾"

    def _hide_format_dropdown(self):
        if self.format_dropdown_panel is None:
            return
        self.format_dropdown_panel.destroy()
        self.format_dropdown_panel = None

    def _toggle_format_dropdown(self):
        if self.format_menu.cget("state") == "disabled":
            return
        if self.format_dropdown_panel is not None:
            self._hide_format_dropdown()
            return

        panel = ctk.CTkFrame(
            self,
            fg_color=SURFACE,
            corner_radius=10,
            border_color=BORDER,
            border_width=1,
        )
        self.format_dropdown_panel = panel

        for option in ("Audio", "Video"):
            selected = option == self.format_var.get()
            option_btn = ctk.CTkButton(
                panel,
                text=option,
                width=92,
                height=30,
                corner_radius=8,
                fg_color=SURFACE_HOVER if selected else "transparent",
                hover_color=SURFACE_HOVER,
                text_color=TEXT,
                command=lambda value=option: self._select_format(value),
            )
            option_btn.pack(fill="x", padx=4, pady=(4 if option == "Audio" else 0, 4))

        self.update_idletasks()
        root_x = self.winfo_rootx()
        root_y = self.winfo_rooty()
        x = self.format_menu.winfo_rootx() - root_x
        y = self.format_menu.winfo_rooty() - root_y + self.format_menu.winfo_height() + 6
        panel.place(x=x, y=y)
        panel.lift()

    def _select_format(self, choice: str):
        self.format_var.set(choice)
        self._hide_format_dropdown()
        self._on_format_change(choice)

    def _update_input_hint(self):
        if not hasattr(self, "input_hint_var"):
            return
        if self.format_var.get() == "Audio":
            quality_label = self.audio_quality_var.get()
            self.input_hint_var.set(f"Downloading audio in {quality_label}")
        else:
            quality_label = self.video_quality_var.get()
            self.input_hint_var.set(f"Downloading video in {quality_label}")

    def _show_launch_intro(self):
        if not hasattr(self, "launch_intro_frame"):
            return
        if self.launch_intro_frame.winfo_manager():
            return
        self.launch_intro_frame.pack(fill="x", pady=(0, 22), before=self.input_shell)

    def _hide_launch_intro(self):
        if not hasattr(self, "launch_intro_frame"):
            return
        if self.launch_intro_frame.winfo_manager():
            self.launch_intro_frame.pack_forget()

    def _set_downloads_clear_visible(self, visible: bool):
        if not self.clear_downloads_btn:
            return
        if visible:
            if not self.clear_downloads_btn.winfo_manager():
                self.clear_downloads_btn.pack(side="right")
        else:
            if self.clear_downloads_btn.winfo_manager():
                self.clear_downloads_btn.pack_forget()

    def _reveal_downloads_area(self):
        if self._downloads_visible:
            return
        self._downloads_visible = True
        self._hide_launch_intro()
        self._set_downloads_clear_visible(False)
        self.input_section.pack_forget()
        self.input_section.pack(fill="x", padx=0, pady=(28, 10))
        self.downloads_card.pack(fill="both", expand=True, pady=(8, 12))

    def _reset_to_start_state(self):
        if self.manager.has_active_jobs():
            messagebox.showinfo("Busy", "Downloads are still running. Please wait before clearing the list.")
            return

        self._hide_format_dropdown()
        self._set_ui_running(False)
        self._set_downloads_clear_visible(False)
        self.downloads_card.pack_forget()
        self._downloads_visible = False
        self.download_list.reset()
        self.download_list.set_placeholder_text("No downloads yet. Paste a link to begin.", show=True)
        self.jobs_title_var.set("Waiting for downloads")
        self.status_var.set("Ready")
        self._clear_activity()
        self._current_total_items = None
        self._cancel_requested = False

        self.url_entry.configure(state="normal")
        self.url_entry.delete(0, "end")
        self.start_btn.configure(
            text="Start",
            state="normal",
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT,
        )
        self.input_section.pack_forget()
        self.input_section.pack(fill="x", expand=True, padx=54)
        self._show_launch_intro()
        self._update_input_hint()
        self.after_idle(self._layout_input_shell_children)
        self.after_idle(lambda: self.url_entry.focus_set())

    def _on_url_submit(self, _event=None):
        if self.start_btn.cget("text") == "Start":
            self._toggle_download()
        return "break"


    def _update_window_title(self):
        """Reflect the current format/quality in the window title."""
        format_choice = self.format_var.get()
        if format_choice == "Audio":
            quality_label = self.audio_quality_var.get()
            if quality_label not in AUDIO_QUALITIES:
                quality_label = f"{DEFAULT_BITRATE} kbps"
            self.title(f"YouTube → MP3 ({quality_label})")
        else:
            quality_label = self.video_quality_var.get()
            if quality_label not in VIDEO_QUALITIES:
                quality_label = "720p"
            self.title(f"YouTube → MP4 ({quality_label})")


    def _choose_dir(self, target_var: Optional[ctk.StringVar] = None):
        target = target_var or self.out_dir_var
        chosen = filedialog.askdirectory(initialdir=target.get() or default_download_dir())
        if chosen:
            target.set(chosen)

    def _close_settings_window(self):
        if self.settings_panel is None:
            return
        self.settings_panel.destroy()
        self.settings_panel = None

    def _setting_row(self, parent, row_index: int, label_text: str, widget):
        label = ctk.CTkLabel(parent, text=label_text, font=("Segoe UI", 13), anchor="w", text_color=TEXT_SOFT)
        label.grid(row=row_index, column=0, sticky="w", padx=(18, 14), pady=8)
        widget.grid(row=row_index, column=1, sticky="ew", padx=(0, 18), pady=8)

    def _open_settings(self):
        self._hide_format_dropdown()
        if self.manager.has_active_jobs():
            messagebox.showinfo("Busy", "Settings can be changed after the current downloads finish.")
            return
        if self.settings_panel is not None:
            self.settings_panel.lift()
            return

        self.settings_audio_var = ctk.StringVar(value=self.audio_quality_var.get())
        self.settings_video_var = ctk.StringVar(value=self.video_quality_var.get())
        self.settings_parallel_var = ctk.StringVar(value=str(self.manager.max_workers))
        self.settings_dir_var = ctk.StringVar(value=self.out_dir_var.get())

        panel = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.settings_panel = panel

        body = ctk.CTkFrame(panel, fg_color=SURFACE, corner_radius=12, width=540, height=340)
        body.grid_propagate(False)
        body.grid_columnconfigure(1, weight=1)

        settings_header = ctk.CTkFrame(body, fg_color="transparent")
        settings_header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(16, 10))
        settings_header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(settings_header, text="Settings", font=("Segoe UI", 20, "bold"), anchor="w", text_color=TEXT)
        title.grid(row=0, column=0, sticky="w")

        close_btn = ctk.CTkButton(
            settings_header,
            text="",
            image=self.icons["close"],
            width=38,
            height=38,
            corner_radius=0,
            fg_color="transparent",
            hover_color=SURFACE,
            command=self._close_settings_window,
        )
        close_btn.grid(row=0, column=1, sticky="e")

        audio_menu = ctk.CTkOptionMenu(
            body,
            values=list(AUDIO_QUALITIES.keys()),
            variable=self.settings_audio_var,
            corner_radius=8,
            fg_color=SURFACE_ALT,
            button_color=BORDER,
            button_hover_color=SURFACE_HOVER,
            text_color=TEXT,
        )
        self._setting_row(body, 1, "Default audio bitrate", audio_menu)

        video_menu = ctk.CTkOptionMenu(
            body,
            values=VIDEO_QUALITIES,
            variable=self.settings_video_var,
            corner_radius=8,
            fg_color=SURFACE_ALT,
            button_color=BORDER,
            button_hover_color=SURFACE_HOVER,
            text_color=TEXT,
        )
        self._setting_row(body, 2, "Default video resolution", video_menu)

        parallel_menu = ctk.CTkOptionMenu(
            body,
            values=[str(i) for i in range(1, 5)],
            variable=self.settings_parallel_var,
            corner_radius=8,
            fg_color=SURFACE_ALT,
            button_color=BORDER,
            button_hover_color=SURFACE_HOVER,
            text_color=TEXT,
        )
        self._setting_row(body, 3, "Parallel downloads", parallel_menu)

        location_frame = ctk.CTkFrame(body, fg_color="transparent")
        location_frame.grid_columnconfigure(0, weight=1)
        location_entry = ctk.CTkEntry(
            location_frame,
            textvariable=self.settings_dir_var,
            height=36,
            corner_radius=8,
            fg_color=SURFACE_ALT,
            border_color=BORDER,
            text_color=TEXT,
        )
        location_entry.grid(row=0, column=0, sticky="ew")
        browse_btn = ctk.CTkButton(
            location_frame,
            text="Browse",
            width=84,
            height=36,
            corner_radius=8,
            fg_color=SURFACE_ALT,
            hover_color=SURFACE_HOVER,
            text_color=TEXT,
            command=lambda: self._choose_dir(self.settings_dir_var),
        )
        browse_btn.grid(row=0, column=1, padx=(8, 0))
        self._setting_row(body, 4, "Download location", location_frame)

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=2, sticky="e", padx=18, pady=(16, 18))
        save_btn = ctk.CTkButton(
            actions,
            text="Save",
            width=90,
            height=34,
            corner_radius=8,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT,
            command=self._save_settings_from_modal,
        )
        save_btn.pack(side="left")

        panel.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        body.place(relx=0.5, rely=0.5, anchor="center")
        panel.lift()

    def _save_settings_from_modal(self):
        download_dir = (self.settings_dir_var.get() or "").strip()
        if not download_dir:
            messagebox.showwarning("Settings", "Please choose a download location.")
            return

        try:
            os.makedirs(download_dir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Settings", f"Could not create the download folder.\n\n{exc}")
            return

        settings = _clean_settings(
            {
                "audio_quality": self.settings_audio_var.get(),
                "video_quality": self.settings_video_var.get(),
                "parallel_downloads": self.settings_parallel_var.get(),
                "download_dir": download_dir,
            }
        )

        if not self.manager.set_max_workers(int(settings["parallel_downloads"])):
            messagebox.showerror("Settings", "Could not update the parallel download limit.")
            return

        self.settings = settings
        self.audio_quality_var.set(settings["audio_quality"])
        self.video_quality_var.set(settings["video_quality"])
        self.out_dir_var.set(settings["download_dir"])
        self._update_input_hint()

        try:
            _save_settings(settings)
        except OSError as exc:
            messagebox.showerror("Settings", f"Could not save settings.\n\n{exc}")
            return

        self._update_window_title()
        self._log(f"[{human_time()}] Settings saved.")
        self._close_settings_window()

    def _find_thumbnail(self, file_path: str) -> Optional[str]:
        base, _ = os.path.splitext(file_path)
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = base + ext
            if os.path.exists(candidate):
                return candidate
        return None

    def _gather_history(self, directory: str, include_folders: bool = True):
        entries = []
        try:
            for entry in os.scandir(directory):
                path = entry.path
                try:
                    if entry.is_file() and entry.name.lower().endswith(HISTORY_EXTENSIONS):
                        try:
                            mtime = entry.stat().st_mtime
                        except OSError:
                            mtime = 0
                        entries.append(
                            {
                                "type": "file",
                                "name": entry.name,
                                "path": path,
                                "mtime": mtime,
                                "thumbnail": self._find_thumbnail(path),
                            }
                        )
                    elif include_folders and entry.is_dir():
                        children = self._gather_history(path, include_folders=False)
                        if not children:
                            continue
                        try:
                            folder_mtime = entry.stat().st_mtime
                        except OSError:
                            folder_mtime = 0
                        latest_child_mtime = max((child.get("mtime") or 0) for child in children) if children else 0
                        entries.append(
                            {
                                "type": "folder",
                                "name": entry.name,
                                "path": path,
                                "mtime": max(folder_mtime, latest_child_mtime),
                                "count": len(children),
                            }
                        )
                except OSError:
                    continue
        except OSError:
            return []
        entries.sort(key=lambda item: item.get("mtime") or 0, reverse=True)
        return entries

    def _format_timestamp(self, mtime: Optional[float]) -> str:
        if not mtime:
            return ""
        try:
            return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return ""

    def _render_history_entries(self, parent: ctk.CTkScrollableFrame, entries: List[Dict], allow_folders: bool, image_store: List[ctk.CTkImage]):
        for entry in entries:
            row = ctk.CTkFrame(parent, fg_color=ROW_BG, corner_radius=10)
            row.grid_columnconfigure(1, weight=1)
            row.pack(fill="x", padx=4, pady=6)

            thumb_label = None
            thumb_container = None
            thumb_path = entry.get("thumbnail")
            if thumb_path:
                try:
                    with Image.open(thumb_path) as img:
                        pil_img = img.convert("RGBA").copy()
                    ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(60, 60))
                    image_store.append(ctk_img)
                    thumb_label = ctk.CTkLabel(row, image=ctk_img, text="")
                    thumb_label.image = ctk_img
                    thumb_label.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
                except Exception:
                    thumb_label = None

            if thumb_label is None:
                thumb_container = ctk.CTkFrame(row, width=60, height=60, fg_color=THUMB_BG, corner_radius=8)
                thumb_container.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
                thumb_container.grid_propagate(False)
                icon_text = "📁" if entry.get("type") == "folder" else "🎵"
                icon_label = ctk.CTkLabel(thumb_container, text=icon_text, font=("Segoe UI Emoji", 28), text_color=MUTED)
                icon_label.place(relx=0.5, rely=0.5, anchor="center")

            title_prefix = "📁 " if entry.get("type") == "folder" else ""
            title_label = ctk.CTkLabel(
                row,
                text=f"{title_prefix}{entry.get('name', '')}",
                font=("Segoe UI", 14, "bold"),
                anchor="w",
                text_color=TEXT,
            )
            title_label.grid(row=0, column=1, sticky="w", padx=(0, 6), pady=(10, 0))

            meta_parts = []
            timestamp = self._format_timestamp(entry.get("mtime"))
            if timestamp:
                meta_parts.append(timestamp)
            if entry.get("type") == "folder" and entry.get("count"):
                meta_parts.append(f"{entry['count']} tracks")
            meta_text = " • ".join(meta_parts) if meta_parts else ""
            meta_label = ctk.CTkLabel(
                row,
                text=meta_text,
                font=("Segoe UI", 12),
                text_color=MUTED_DARK,
                anchor="w",
            )
            meta_label.grid(row=1, column=1, sticky="w", padx=(0, 6), pady=(0, 10))

            action_frame = ctk.CTkFrame(row, fg_color="transparent")
            action_frame.grid(row=0, column=2, rowspan=2, padx=(6, 12), pady=12, sticky="e")
            row_buttons = []

            if entry.get("type") == "file":
                play_btn = ctk.CTkButton(
                    action_frame,
                    text="",
                    image=self.icons["play"],
                    width=32,
                    height=32,
                    corner_radius=0,
                    command=lambda p=entry.get("path"): self._play_history_entry(p),
                    fg_color="transparent",
                    hover_color=SURFACE_HOVER,
                    text_color=TEXT,
                )
                play_btn.pack(side="left", padx=(0, 6))
                play_btn._normal_image = self.icons["play"]
                play_btn._disabled_image = self.icons["play_muted"]
                InAppTooltip(self, play_btn, "Play")
                row_buttons.append(play_btn)

                delete_btn = ctk.CTkButton(
                    action_frame,
                    text="",
                    image=self.icons["remove"],
                    width=32,
                    height=32,
                    corner_radius=0,
                    command=lambda e=entry: self._delete_history_entry(e),
                    fg_color="transparent",
                    hover_color=SURFACE_HOVER,
                    text_color=DANGER_HOVER,
                )
                delete_btn.pack(side="left")
                delete_btn._normal_image = self.icons["remove"]
                delete_btn._disabled_image = self.icons["remove_muted"]
                InAppTooltip(self, delete_btn, "Delete")
                row_buttons.append(delete_btn)

            if entry.get("type") == "folder" and allow_folders:
                delete_btn = ctk.CTkButton(
                    action_frame,
                    text="",
                    image=self.icons["remove"],
                    width=32,
                    height=32,
                    corner_radius=0,
                    command=lambda e=entry: self._delete_history_entry(e),
                    fg_color="transparent",
                    hover_color=SURFACE_HOVER,
                    text_color=DANGER_HOVER,
                )
                delete_btn.pack(side="left")
                delete_btn._normal_image = self.icons["remove"]
                delete_btn._disabled_image = self.icons["remove_muted"]
                InAppTooltip(self, delete_btn, "Delete")
                row_buttons.append(delete_btn)

                def open_folder(_event, p=entry.get("path"), n=entry.get("name")):
                    if getattr(row, "_history_delete_state", ""):
                        return
                    self._open_history_folder(p, n)

                clickable_widgets = [row, title_label, meta_label]
                if thumb_label is not None:
                    clickable_widgets.append(thumb_label)
                elif thumb_container is not None:
                    clickable_widgets.extend([thumb_container])

                for widget in clickable_widgets:
                    widget.bind("<Button-1>", open_folder)
                    try:
                        widget.configure(cursor="hand2")
                    except Exception:
                        pass

                def on_enter(_event, target=row):
                    if getattr(target, "_history_delete_state", ""):
                        return
                    target.configure(fg_color=ROW_ACTIVE_BG)

                def on_leave(_event, target=row):
                    if getattr(target, "_history_delete_state", ""):
                        return
                    target.configure(fg_color=ROW_BG)

                row.bind("<Enter>", on_enter)
                row.bind("<Leave>", on_leave)

            entry["_history_row_widgets"] = {
                "row": row,
                "title_label": title_label,
                "meta_label": meta_label,
                "thumb_label": thumb_label,
                "thumb_container": thumb_container,
                "action_frame": action_frame,
                "buttons": row_buttons,
                "meta_text": meta_text,
                "allow_folders": allow_folders,
            }

    def _play_history_entry(self, file_path: str):
        if not file_path:
            return
        if not os.path.exists(file_path):
            messagebox.showerror("Play file", "This file no longer exists.")
            self._refresh_history_after_delete()
            return
        self._show_history_player(file_path)

    def _history_root_dir(self) -> str:
        if self.history_context_stack:
            root = str(self.history_context_stack[0].get("dir") or "").strip()
        else:
            root = (self.out_dir_var.get() or "").strip()
        return os.path.abspath(root or default_download_dir())

    def _is_within_history_root(self, path: str) -> bool:
        try:
            root = os.path.normcase(self._history_root_dir())
            target = os.path.normcase(os.path.abspath(path))
            return os.path.commonpath([root, target]) == root
        except (OSError, ValueError):
            return False

    def _history_sidecars_for_file(self, file_path: str) -> List[str]:
        base, _ = os.path.splitext(file_path)
        sidecars = []
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = base + ext
            if candidate != file_path and os.path.exists(candidate):
                sidecars.append(candidate)
        return sidecars

    def _refresh_history_after_delete(self):
        while self.history_context_stack:
            current_dir = str(self.history_context_stack[-1].get("dir") or "")
            if os.path.isdir(current_dir):
                break
            self.history_context_stack.pop()

        if self.history_context_stack:
            self._load_history_context(self.history_context_stack[-1])
        else:
            self._show_history()

    def _show_history_empty_state(self, allow_folders: bool):
        empty_text = (
            "No saved songs yet. Start a download to build your history."
            if allow_folders
            else "No audio files detected in this folder."
        )
        empty_label = ctk.CTkLabel(
            self.history_list_frame,
            text=empty_text,
            text_color=MUTED_DARK,
            font=("Segoe UI", 13),
            wraplength=420,
            justify="left",
        )
        empty_label.pack(padx=12, pady=12, anchor="w")

    def _history_widget_exists(self, widget) -> bool:
        try:
            return bool(widget and widget.winfo_exists())
        except Exception:
            return False

    def _history_entry_widgets(self, entry: Dict) -> Dict:
        widgets = entry.get("_history_row_widgets")
        return widgets if isinstance(widgets, dict) else {}

    def _set_history_entry_delete_state(self, entry: Dict, state: str, message: str, message_color: str):
        widgets = self._history_entry_widgets(entry)
        row = widgets.get("row")
        if not self._history_widget_exists(row):
            return

        row._history_delete_state = state
        row.configure(fg_color=SURFACE_ALT)

        title_label = widgets.get("title_label")
        if self._history_widget_exists(title_label):
            title_label.configure(text_color=MUTED_DARK)

        meta_label = widgets.get("meta_label")
        if self._history_widget_exists(meta_label):
            meta_label.configure(text=message, text_color=message_color)

        thumb_container = widgets.get("thumb_container")
        if self._history_widget_exists(thumb_container):
            thumb_container.configure(fg_color=SURFACE)

        thumb_label = widgets.get("thumb_label")
        if self._history_widget_exists(thumb_label):
            try:
                thumb_label.configure(text_color=MUTED_DARK)
            except Exception:
                pass

        if "_button_styles" not in widgets:
            widgets["_button_styles"] = []
            for button in widgets.get("buttons", []):
                if not self._history_widget_exists(button):
                    continue
                try:
                    widgets["_button_styles"].append(
                        (
                            button,
                            button.cget("fg_color"),
                            button.cget("hover_color"),
                            button.cget("text_color"),
                        )
                    )
                except Exception:
                    widgets["_button_styles"].append((button, None, None, None))

        for button in widgets.get("buttons", []):
            if not self._history_widget_exists(button):
                continue
            try:
                disabled_image = getattr(button, "_disabled_image", None)
                if disabled_image is not None:
                    button.configure(image=disabled_image)
                button.configure(
                    state="disabled",
                    fg_color="transparent",
                    hover_color=SURFACE_HOVER,
                    text_color_disabled=MUTED_DARK,
                )
            except Exception:
                button.configure(state="disabled")

    def _restore_history_entry_after_delete_error(self, entry: Dict):
        widgets = self._history_entry_widgets(entry)
        row = widgets.get("row")
        if not self._history_widget_exists(row):
            return

        row._history_delete_state = ""
        row.configure(fg_color=ROW_BG)

        title_label = widgets.get("title_label")
        if self._history_widget_exists(title_label):
            title_label.configure(text_color=TEXT)

        meta_label = widgets.get("meta_label")
        if self._history_widget_exists(meta_label):
            meta_label.configure(text=widgets.get("meta_text") or "", text_color=MUTED_DARK)

        thumb_container = widgets.get("thumb_container")
        if self._history_widget_exists(thumb_container):
            thumb_container.configure(fg_color=THUMB_BG)

        restored = set()
        for button, fg_color, hover_color, text_color in widgets.get("_button_styles", []):
            if self._history_widget_exists(button):
                restored.add(button)
                config = {"state": "normal"}
                if fg_color is not None:
                    config["fg_color"] = fg_color
                if hover_color is not None:
                    config["hover_color"] = hover_color
                if text_color is not None:
                    config["text_color"] = text_color
                normal_image = getattr(button, "_normal_image", None)
                if normal_image is not None:
                    config["image"] = normal_image
                button.configure(**config)

        for button in widgets.get("buttons", []):
            if self._history_widget_exists(button) and button not in restored:
                normal_image = getattr(button, "_normal_image", None)
                if normal_image is not None:
                    button.configure(state="normal", image=normal_image)
                else:
                    button.configure(state="normal")

    def _remove_history_entry_row(self, entry: Dict):
        widgets = self._history_entry_widgets(entry)
        row = widgets.get("row")
        allow_folders = bool(widgets.get("allow_folders"))
        if self._history_widget_exists(row):
            row.destroy()

        remaining_rows = [
            child for child in self.history_list_frame.winfo_children()
            if self._history_widget_exists(child)
        ]
        if not remaining_rows:
            self._show_history_empty_state(allow_folders)

    def _delete_history_entry(self, entry: Dict):
        path = str(entry.get("path") or "").strip()
        entry_type = entry.get("type")
        name = entry.get("name") or os.path.basename(path) or "selected item"
        if not path:
            return

        target = os.path.abspath(path)
        root = self._history_root_dir()
        if os.path.normcase(target) == os.path.normcase(root) or not self._is_within_history_root(target):
            messagebox.showerror("Delete item", "This item is outside the current download folder, so it was not deleted.")
            return

        if entry_type == "folder":
            prompt = f"Delete this playlist folder and everything inside it?\n\n{name}\n\nThis cannot be undone."
        else:
            prompt = f"Delete this song from disk?\n\n{name}\n\nThis also removes its thumbnail if one exists."

        if not messagebox.askyesno("Delete item", prompt):
            return

        self._set_history_entry_delete_state(entry, "deleting", "Deleting...", WARNING)

        root_norm = os.path.normcase(os.path.abspath(root))

        def is_within_root(candidate: str) -> bool:
            try:
                target_norm = os.path.normcase(os.path.abspath(candidate))
                return os.path.commonpath([root_norm, target_norm]) == root_norm
            except (OSError, ValueError):
                return False

        def worker():
            try:
                if entry_type == "folder":
                    if os.path.islink(target):
                        os.unlink(target)
                    elif os.path.isdir(target):
                        shutil.rmtree(target)
                    elif os.path.exists(target):
                        os.remove(target)
                else:
                    targets = [target] + self._history_sidecars_for_file(target)
                    for candidate in targets:
                        if not is_within_root(candidate):
                            continue
                        if os.path.isfile(candidate) or os.path.islink(candidate):
                            os.remove(candidate)
            except Exception as exc:  # pylint: disable=broad-except
                error_text = str(exc)

                def apply_error():
                    self._restore_history_entry_after_delete_error(entry)
                    messagebox.showerror("Delete item", f"Could not delete this item.\n\n{error_text}")

                self.after(0, apply_error)
                return

            def apply_success():
                self._set_history_entry_delete_state(entry, "deleted", "Deleted", ACCENT)
                self.after(DELETED_HOLD_MS, lambda: self._remove_history_entry_row(entry))

            self.after(0, apply_success)

        threading.Thread(target=worker, daemon=True).start()

    def _ensure_history_panel(self):
        if self.history_panel is not None:
            return

        panel = ctk.CTkFrame(self, fg_color=BG, corner_radius=18)
        panel.place_forget()
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        header_row = ctk.CTkFrame(panel, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 8))
        header_row.grid_columnconfigure(1, weight=1)

        self.history_back_btn = ctk.CTkButton(
            header_row,
            text="",
            image=self.icons["back_muted"],
            width=38,
            height=38,
            corner_radius=0,
            command=self._on_history_back,
            state="disabled",
            fg_color="transparent",
            hover_color=BG,
            text_color=TEXT,
        )
        self.history_back_btn.grid(row=0, column=0, padx=(0, 12))
        InAppTooltip(self, self.history_back_btn, "Back")

        self.history_title_var = ctk.StringVar(value="Download history")
        title_label = ctk.CTkLabel(
            header_row,
            textvariable=self.history_title_var,
            font=("Segoe UI", 20, "bold"),
            text_color=TEXT,
        )
        title_label.grid(row=0, column=1, sticky="w")

        close_btn = ctk.CTkButton(
            header_row,
            text="",
            image=self.icons["close"],
            width=38,
            height=38,
            corner_radius=0,
            command=self._hide_history_panel,
            fg_color="transparent",
            hover_color=BG,
        )
        close_btn.grid(row=0, column=2)

        self.history_dir_var = ctk.StringVar(value="")
        dir_label = ctk.CTkLabel(
            panel,
            textvariable=self.history_dir_var,
            font=("Segoe UI", 12),
            text_color=MUTED,
            justify="left",
            anchor="w",
            wraplength=760,
        )
        dir_label.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))

        self.history_list_frame = SmoothScrollableFrame(
            panel,
            fg_color=SURFACE,
        )
        self.history_list_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))

        self.history_player = HistoryMediaPlayer(panel, on_close=self._close_history_player)
        self.history_player.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.history_player.grid_remove()

        self.history_panel = panel

    def _cancel_history_render(self):
        if self._history_render_after_id is None:
            return
        try:
            self.after_cancel(self._history_render_after_id)
        except Exception:
            pass
        self._history_render_after_id = None

    def _clear_history_entries(self):
        self._cancel_history_render()
        for child in self.history_list_frame.winfo_children():
            child.destroy()
        self.history_images.clear()

    def _show_history_loading(self, title: str = "Loading history...", detail: str = "Scanning the selected folder."):
        self._clear_history_entries()
        loading = LoadingPlaceholderFrame(
            self.history_list_frame,
            message=title,
            detail=detail,
            row_count=4,
            compact=True,
        )
        loading.pack(fill="x", padx=4, pady=4)

    def _populate_history_panel(self, entries: List[Dict], allow_folders: bool):
        self._clear_history_entries()

        if not entries:
            self._show_history_empty_state(allow_folders)
            return

        render_token = self._history_load_token

        def render_batch(start_index: int = 0):
            if render_token != self._history_load_token or self.history_panel is None:
                self._history_render_after_id = None
                return

            end_index = min(start_index + HISTORY_RENDER_BATCH_SIZE, len(entries))
            self._render_history_entries(
                self.history_list_frame,
                entries[start_index:end_index],
                allow_folders=allow_folders,
                image_store=self.history_images,
            )

            if end_index < len(entries):
                self._history_render_after_id = self.after(1, lambda: render_batch(end_index))
            else:
                self._history_render_after_id = None

        render_batch()

    def _show_history_panel(self):
        if not self.history_panel:
            return
        self.history_panel.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self.history_panel.lift()

    def _hide_history_panel(self):
        if not self.history_panel:
            return
        self._history_load_token += 1
        if self.history_player:
            self.history_player.close_media()
            self.history_player.grid_remove()
        if self.history_list_frame:
            self.history_list_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
            self._clear_history_entries()
        self.history_panel.place_forget()
        self.history_context_stack = []

    def _show_history_player(self, file_path: str):
        self._ensure_history_panel()
        if not self.history_player:
            return

        artwork_path = self._find_thumbnail(file_path)
        self._history_load_token += 1
        player_token = self._history_load_token
        self._cancel_history_render()
        self.history_list_frame.grid_remove()
        self.history_player.grid()
        self.history_title_var.set("Now playing")
        self.history_dir_var.set(file_path)
        self.history_player.player_title_var.set(os.path.basename(file_path) or "Now playing")
        self.history_player.player_meta_var.set("Opening media...")
        self.history_player.player_status_var.set("Loading playback...")
        self.history_player.elapsed_var.set("0:00")
        self.history_player.total_var.set("0:00")
        self.history_player.progress_slider.set(0)
        self.history_player.playing = False
        self.history_player._update_play_button_icon()
        self.history_player.play_btn.configure(state="disabled")
        self.history_player._draw_message("Loading media...")
        self.history_back_btn.configure(
            text="",
            image=self.icons["back"],
            state="normal",
            command=self._close_history_player,
            fg_color="transparent",
            hover_color=BG,
            text_color=TEXT,
        )

        def open_when_visible():
            if player_token != self._history_load_token or not self.history_player:
                return
            if not self.history_player.winfo_manager():
                return
            self.history_player.open_media(file_path, artwork_path=artwork_path)

        self.after(40, open_when_visible)

    def _close_history_player(self):
        self._history_load_token += 1
        if self.history_player:
            self.history_player.close_media()
            self.history_player.grid_remove()
        if self.history_list_frame:
            self.history_list_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        if self.history_context_stack:
            self._load_history_context(self.history_context_stack[-1])

    def _load_history_context(self, context: Dict[str, object]):
        self._ensure_history_panel()
        if self.history_player and self.history_player.winfo_manager():
            self.history_player.close_media()
            self.history_player.grid_remove()
            self.history_list_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        directory = (context.get("dir") or "").strip()
        allow_folders = bool(context.get("allow_folders"))
        title = context.get("title") or "History"

        self.history_title_var.set(title)
        if directory:
            self.history_dir_var.set(f"Folder: {directory}")
        else:
            self.history_dir_var.set("")

        if len(self.history_context_stack) > 1:
            self.history_back_btn.configure(
                text="",
                image=self.icons["back"],
                command=self._on_history_back,
                state="normal",
                fg_color="transparent",
                hover_color=BG,
                text_color=TEXT,
            )
        else:
            self.history_back_btn.configure(
                text="",
                image=self.icons["back_muted"],
                command=self._on_history_back,
                state="disabled",
                fg_color="transparent",
                hover_color=BG,
                text_color=TEXT,
            )

        self._history_load_token += 1
        load_token = self._history_load_token
        self._show_history_loading()

        def start_worker():
            if load_token != self._history_load_token or self.history_panel is None:
                return

            def worker():
                entries = self._gather_history(directory, include_folders=allow_folders)

                def apply_entries():
                    if load_token != self._history_load_token or self.history_panel is None:
                        return
                    self._populate_history_panel(entries, allow_folders)

                self.after(0, apply_entries)

            threading.Thread(target=worker, daemon=True).start()

        self.after(60, start_worker)

    def _on_history_back(self):
        if len(self.history_context_stack) <= 1:
            return
        self.history_context_stack.pop()
        self._load_history_context(self.history_context_stack[-1])

    def _open_history_folder(self, folder_path: str, folder_name: str):
        self._ensure_history_panel()
        context = {
            "title": f"📁 {folder_name}",
            "dir": folder_path,
            "allow_folders": False,
        }
        self.history_context_stack.append(context)
        self._load_history_context(context)

    def _show_history(self):
        self._hide_format_dropdown()
        directory = (self.out_dir_var.get() or "").strip() or default_download_dir()

        if not os.path.isdir(directory):
            messagebox.showinfo("History", "The selected folder does not exist yet.")
            return

        self._ensure_history_panel()

        root_context = {
            "title": "Download history",
            "dir": directory,
            "allow_folders": True,
        }
        self.history_context_stack = [root_context]
        self._show_history_panel()
        self.after(1, lambda: self._load_history_context(root_context))

    def _start_audio_queue(self, url: str, out_dir: str, bitrate: str):
        def runner():
            try:
                self.manager.start_audio(url, out_dir, bitrate)
            except Exception as exc:  # pylint: disable=broad-except
                self._enqueue_log(f"[{human_time()}] 💥 Failed to start audio download: {exc}")
                self._enqueue_progress(DownloadProgress(status="error", message=str(exc)))

        threading.Thread(target=runner, daemon=True).start()

    def _start_video_queue(self, url: str, out_dir: str, quality: str):
        def runner():
            try:
                self.manager.start_video(url, out_dir, quality)
            except Exception as exc:  # pylint: disable=broad-except
                self._enqueue_log(f"[{human_time()}] 💥 Failed to start video download: {exc}")
                self._enqueue_progress(DownloadProgress(status="error", message=str(exc)))

        threading.Thread(target=runner, daemon=True).start()



    def _on_start(self):
        format_choice = self.format_var.get()
        self._update_window_title()

        url = (self.url_entry.get() or "").strip()
        out_dir = (self.out_dir_var.get() or "").strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please paste a YouTube URL.")
            return False
        if not out_dir:
            messagebox.showwarning("Missing Folder", "Please choose a destination folder.")
            return False
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Download folder", f"Could not create the download folder.\n\n{exc}")
            return False

        if self.manager.has_active_jobs():
            messagebox.showinfo("Busy", "Downloads are already running. Please wait.")
            return False

        self._set_downloads_clear_visible(False)
        self._reveal_downloads_area()
        self._cancel_requested = False
        self._set_ui_running(True)
        self._clear_activity()
        self._log(f"[{human_time()}] 🚀 Starting download…")
        self.download_list.reset()
        self.download_list.show_loading(
            "Fetching data...",
            "Reading the link, resolving playlist items, and preparing the queue.",
        )
        self._current_total_items = None
        self.jobs_title_var.set("Fetching data...")
        self._download_start_token += 1
        start_token = self._download_start_token

        if format_choice == "Audio":
            quality_choice = self.audio_quality_var.get()
            bitrate = AUDIO_QUALITIES.get(quality_choice, DEFAULT_BITRATE)
            self.status_var.set(f"Preparing audio: {quality_choice}")

            def begin_download():
                self._download_start_after_id = None
                if start_token != self._download_start_token or self._cancel_requested:
                    return
                self._start_audio_queue(url, out_dir, bitrate)
        else:
            quality_choice = self.video_quality_var.get()
            self.status_var.set(f"Preparing video: {quality_choice}")

            def begin_download():
                self._download_start_after_id = None
                if start_token != self._download_start_token or self._cancel_requested:
                    return
                self._start_video_queue(url, out_dir, quality_choice)

        self._download_start_after_id = self.after(60, begin_download)

        return True



    def _on_format_change(self, choice: str):
        if hasattr(self, "format_menu"):
            self.format_menu.configure(text=self._format_dropdown_label())
        self._update_input_hint()
        self._update_window_title()


    def _on_stop(self):
        if self._download_start_after_id is not None:
            try:
                self.after_cancel(self._download_start_after_id)
            except Exception:
                pass
            self._download_start_after_id = None
            self._download_start_token += 1
            self._cancel_requested = True
            self.download_list.set_placeholder_text("Download stopped before it started.", show=True)
            self.jobs_title_var.set("Stopped")
            self.status_var.set("Stopped.")
            self._set_ui_running(False)
            return True

        if self.manager.has_active_jobs():
            self._cancel_requested = True
            self.manager.stop_all()
            threading.Thread(target=self._wait_for_manager, daemon=True).start()
            self._log(f"[{human_time()}] ⏳ Stopping… please wait.")
            self.status_var.set("Stopping…")
            self.start_btn.configure(
                text="Stopping…",
                state="disabled",
                fg_color=WARNING,
                hover_color=WARNING_HOVER,
                text_color=ACCENT_TEXT,
            )
            return True

        self._set_ui_running(False)
        return False

    def _toggle_download(self):
        # toggles between start and stop modes
        if self.start_btn.cget("text") == "Start":
            if self._on_start():
                self.start_btn.configure(
                    text="Stop",
                    state="normal",
                    fg_color=DANGER,
                    hover_color=DANGER_HOVER,
                    text_color=TEXT,
                )
        else:
            self._on_stop()


    # ------------------------------------------------------------
    # Thread target
    # ------------------------------------------------------------
    def _wait_for_manager(self):
        self.manager.wait_for_current_jobs()

    # ------------------------------------------------------------
    # Logging & progress
    # ------------------------------------------------------------
    def _enqueue_log(self, text):
        self.log_queue.put(text)

    def _log(self, text):
        self._add_activity_line(text)

    def _format_log_line(self, text: str) -> str:
        cleaned = text.strip()
        if "] " in cleaned:
            cleaned = cleaned.split("] ", 1)[1]
        if cleaned.lower().startswith("[download] "):
            cleaned = cleaned.split(" ", 1)[1]
        return cleaned

    def _clear_activity(self, message: str = "Ready"):
        self.activity_history = []

    def _add_activity_line(self, text: str):
        clean = self._format_log_line(text)
        if not clean:
            return
        self.activity_history.append(clean)
        if len(self.activity_history) > 4:
            self.activity_history = self.activity_history[-4:]

    def _drain_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self._add_activity_line(line)
        except queue.Empty:
            pass
        finally:
            self.after(80, self._drain_log_queue)

    def _enqueue_progress(self, prog: DownloadProgress):
        self.progress_queue.put(prog)

    def _drain_progress_queue(self):
        try:
            while True:
                prog: DownloadProgress = self.progress_queue.get_nowait()
                self.download_list.update_from_progress(prog)

                if prog.item_count and prog.job_id:
                    if self._current_total_items != prog.item_count:
                        self._current_total_items = prog.item_count
                        if prog.item_count > 1:
                            self.jobs_title_var.set(f"Playlist • {prog.item_count} tracks")
                        else:
                            self.jobs_title_var.set("Single track")
                elif self._current_total_items is None and prog.title:
                    if prog.job_id and prog.item_index is not None and prog.item_count is None:
                        self.jobs_title_var.set("Playlist • discovering tracks")
                    else:
                        self.jobs_title_var.set("Single track")

                if prog.job_id is None:
                    if prog.item_count and prog.item_count > 1:
                        self.jobs_title_var.set(f"Playlist • {prog.item_count} tracks")

                    if prog.status == "finished" and prog.message == "all_done":
                        self.download_list.mark_all_inactive()
                        self.jobs_title_var.set("Download Completed")
                        if not self.download_list.has_rows():
                            self.download_list.set_placeholder_text("Download completed.", show=True)
                        self._on_downloads_complete("finished")
                    elif prog.status == "stopped":
                        self.jobs_title_var.set("Stopped")
                        self.download_list.mark_all_inactive()
                        self._on_downloads_complete("cancelled")
                    elif prog.status == "error":
                        self.jobs_title_var.set("Error during download")
                        self._on_downloads_complete("error")
                else:
                    if prog.status == "error":
                        self.jobs_title_var.set("Error during download")
                    elif prog.status == "stopped" and not self._cancel_requested:
                        self.jobs_title_var.set("Stopped item")
        except queue.Empty:
            pass
        finally:
            self.after(120, self._drain_progress_queue)

    # ------------------------------------------------------------
    # UI state
    # ------------------------------------------------------------
    def _set_ui_running(self, running: bool):
        """Disable inputs while downloading; handled by single toggle button."""
        if running:
            self._hide_format_dropdown()
            self.url_entry.configure(state="disabled")
            self._set_format_menu_state("disabled")
            self.start_btn.configure(state="normal")
            if self.settings_btn:
                self.settings_btn.configure(state="disabled")
        else:
            self.url_entry.configure(state="normal")
            self._set_format_menu_state("normal")
            self.start_btn.configure(state="normal")
            if self.settings_btn:
                self.settings_btn.configure(state="normal")
            # Reset Start button appearance if the job finished naturally
            if self.start_btn.cget("text") in ("Stop", "Stopping…"):
                self.start_btn.configure(
                    text="Start",
                    fg_color=ACCENT,
                    hover_color=ACCENT_HOVER,
                    text_color=ACCENT_TEXT,
                )


    def _on_downloads_complete(self, result: str):
        """Restore UI after the queue finishes."""
        self._cancel_requested = result == "cancelled"
        self._set_ui_running(False)
        if result == "finished":
            self.status_var.set("Done.")
        elif result == "error":
            self.status_var.set("Error.")
        else:
            self.status_var.set("Stopped.")
        self.start_btn.configure(
            text="Start",
            state="normal",
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT,
        )
        self._set_downloads_clear_visible(not self.manager.has_active_jobs())
