import time
import driver
import afterburner
import overlay_layout
import pystray
from PIL import Image, ImageFont, ImageDraw, ImageChops
from io import BytesIO
import queue
from threading import Thread, Lock
from utils import debug, timing
import json
import psutil
import sys
import os
import webbrowser
from urllib.parse import urlparse, unquote
from workers import FrameWriter
from http.server import BaseHTTPRequestHandler, HTTPServer
import base64
from socketserver import ThreadingMixIn
import shutil
import ctypes.wintypes
import mimetypes

PORT = 30003
BASE_PATH = "."
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BASE_PATH = sys._MEIPASS

FONT_FILE = os.path.join(BASE_PATH, "fonts/Rubik-Bold.ttf")
APP_ICON = os.path.join(BASE_PATH, "images/plugin.png")
OVERLAY_EDITOR_DIR = os.path.join(BASE_PATH, "overlay_editor")

MIN_SPEED = 2
BASE_SPEED = 18
# Disabled: blanking when frames stall made the Kraken stay black during
# fullscreen games (SignalRGB stops Render), and /frame was dropped while
# paused so the panel never recovered without an explicit /resume.
FRAME_WATCHDOG_S = 0

# Spinner compatibility (cpu/pump 0-100, liquid °C)
stats = {
    "cpu": 0,
    "pump": 0,
    "liquid": 0,
}

metrics_lock = Lock()
metrics = {
    "liquid_c": None,
    "fps": None,
    "gpu_power_w": None,
    "gpu_power_max_w": None,
    "cpu_power_w": None,
    "cpu_power_max_w": None,
    "gpu_temp_c": None,
    "cpu_temp_c": None,
    "gpu_usage_pct": None,
    "cpu_usage_pct": None,
    "vram_used": None,
    "vram_total": None,
    "ram_used": None,
    "ram_total": None,
    "pump": 0,
    "afterburner": False,
}

layout_lock = Lock()
_layout_cache = {"mtime": None, "data": None}
_widget_fonts = {}

MIN_COLORS = 64
colors = MIN_COLORS * 2

stream_lock = Lock()
stream_state = {
    "paused": False,
    "saved_brightness": 100,
    "lcd_orientation_degrees": 90,
    "last_frame_at": 0.0,
    "ever_framed": False,
}


lcd = driver.KrakenLCD()
lcd.setupStream()

pluginInstalled = False
try:
    CSIDL_PERSONAL = 5  # My Documents
    SHGFP_TYPE_CURRENT = 0  # Get current, not default value
    buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(
        None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf
    )
    shutil.copytree(
        os.path.join(BASE_PATH, "SignalRGBPlugin"),
        os.path.join(buf.value, "WhirlwindFX/Plugins/KrakenLCDBridge/"),
        dirs_exist_ok=True,
    )
    print("Successfully installed SignalRGB plugin")
    pluginInstalled = True

except Exception:
    print("Could not automatically install SignalRGB plugin")


ThreadingMixIn.daemon_threads = True


def black_frame_bytes() -> bytes:
    img = Image.new("RGBA", lcd.resolution, (0, 0, 0, 255))
    return lcd.imageToFrame(img, adaptive=False)


def do_pause(reason: str = "pause"):
    with stream_lock:
        if stream_state["paused"]:
            return
        stream_state["paused"] = True
        orient = stream_state["lcd_orientation_degrees"] // 90
    try:
        lcd.blankPanel(orientation=orient)
        print("LCD paused ({})".format(reason), flush=True)
    except Exception as e:
        print("pause failed: {}".format(e), flush=True)


def do_resume(brightness=None):
    with stream_lock:
        stream_state["paused"] = False
        if brightness is not None:
            stream_state["saved_brightness"] = max(0, min(100, int(brightness)))
        restore = stream_state["saved_brightness"]
        if restore <= 0:
            restore = 100
            stream_state["saved_brightness"] = restore
        orient = stream_state["lcd_orientation_degrees"] // 90
        stream_state["last_frame_at"] = time.time()
    try:
        lcd.setLcdMode(driver.DISPLAY_MODE.BUCKET)
        # Re-enable display (3rd byte 0x01) then restore brightness.
        lcd.setBrightnessImmediate(restore, orientation=orient)
        print("LCD resumed (brightness={})".format(restore), flush=True)
    except Exception as e:
        print("resume failed: {}".format(e), flush=True)


def set_orientation_degrees(degrees: int):
    degrees = int(degrees) % 360
    with stream_lock:
        stream_state["lcd_orientation_degrees"] = degrees
    print("LCD orientation set to {}°".format(degrees), flush=True)


def get_metrics_snapshot():
    with metrics_lock:
        return dict(metrics)


def get_overlay_layout(force=False):
    path = overlay_layout.layout_path()
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else None
    except Exception:
        mtime = None
    with layout_lock:
        if (
            not force
            and _layout_cache["data"] is not None
            and _layout_cache["mtime"] == mtime
        ):
            return _layout_cache["data"]
        data = overlay_layout.load_layout()
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            mtime = None
        _layout_cache["data"] = data
        _layout_cache["mtime"] = mtime
        return data


def set_overlay_layout(layout):
    saved = overlay_layout.save_layout(layout)
    with layout_lock:
        try:
            mtime = os.path.getmtime(overlay_layout.layout_path())
        except Exception:
            mtime = None
        _layout_cache["data"] = saved
        _layout_cache["mtime"] = mtime
    return saved


def _widget_font(size: int):
    size = max(8, min(200, int(size)))
    font = _widget_fonts.get(size)
    if font is None:
        font = ImageFont.truetype(FONT_FILE, size)
        _widget_fonts[size] = font
    return font


def open_overlay_editor(icon=None, item=None):
    webbrowser.open("http://127.0.0.1:{}/overlay".format(PORT))


class RawProducer(Thread):
    def __init__(self, rawBuffer: queue.Queue):
        Thread.__init__(self, name="RawProducer")
        self.daemon = True
        self.rawBuffer = rawBuffer

    def run(self):
        debug("Server worker started")
        rawBuffer = self.rawBuffer
        lastFrame = time.time()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _set_headers(self, contentType="application/json"):
                self.send_response(200)
                self.send_header("Content-type", contentType)
                self.end_headers()

            def do_HEAD(self):
                self._set_headers()

            def do_GET(self):
                parsed = urlparse(self.path)
                path = unquote(parsed.path)

                if path in (
                    "/images/2023elite.png",
                    "/images/2023.png",
                    "/images/z3.png",
                    "/images/plugin.png",
                ):
                    file = open(BASE_PATH + path, "rb")
                    data = file.read()
                    file.close()
                    self._set_headers("image/png")
                    self.wfile.write(data)
                    return

                if path == "/fonts/Rubik-Bold.ttf":
                    font_path = os.path.join(BASE_PATH, "fonts", "Rubik-Bold.ttf")
                    if not os.path.isfile(font_path):
                        self.send_error(404)
                        return
                    with open(font_path, "rb") as f:
                        data = f.read()
                    self._set_headers("font/ttf")
                    self.wfile.write(data)
                    return

                if path == "/metrics":
                    snap = overlay_layout.snapshot_for_api(get_metrics_snapshot())
                    self._set_headers()
                    self.wfile.write(bytes(json.dumps(snap), "utf-8"))
                    return

                if path == "/overlay/layout":
                    self._set_headers()
                    self.wfile.write(
                        bytes(json.dumps(get_overlay_layout()), "utf-8")
                    )
                    return

                if path == "/overlay" or path == "/overlay/":
                    self._serve_overlay_file("index.html")
                    return

                if path.startswith("/overlay/"):
                    rel = path[len("/overlay/") :]
                    if ".." in rel or rel.startswith("/"):
                        self.send_error(404)
                        return
                    self._serve_overlay_file(rel)
                    return

                info = lcd.getInfo()
                with stream_lock:
                    info["paused"] = stream_state["paused"]
                    info["lcdOrientation"] = stream_state[
                        "lcd_orientation_degrees"
                    ]
                    info["brightness"] = stream_state["saved_brightness"]
                self._set_headers()
                self.wfile.write(bytes(json.dumps(info), "utf-8"))

            def _serve_overlay_file(self, rel):
                full = os.path.normpath(os.path.join(OVERLAY_EDITOR_DIR, rel))
                if not full.startswith(os.path.normpath(OVERLAY_EDITOR_DIR)):
                    self.send_error(404)
                    return
                if not os.path.isfile(full):
                    self.send_error(404)
                    return
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                with open(full, "rb") as f:
                    data = f.read()
                self._set_headers(ctype)
                self.wfile.write(data)

            def do_PUT(self):
                parsed = urlparse(self.path)
                path = unquote(parsed.path)
                length = int(self.headers.get("Content-Length") or "0")
                postData = self.rfile.read(length) if length else b"{}"
                if path == "/overlay/layout":
                    try:
                        data = json.loads(postData.decode("utf-8"))
                    except Exception:
                        self.send_error(400)
                        return
                    saved = set_overlay_layout(data)
                    self._set_headers()
                    self.wfile.write(bytes(json.dumps(saved), "utf-8"))
                    return
                self.send_error(404)

            def do_POST(self):
                nonlocal lastFrame
                length = int(self.headers.get("Content-Length") or "0")
                postData = self.rfile.read(length) if length else b"{}"
                data = {}
                if postData:
                    try:
                        data = json.loads(postData.decode("utf-8"))
                    except Exception:
                        data = {}

                parsed = urlparse(self.path)
                path = unquote(parsed.path)

                if path == "/brightness":
                    brightness = int(data.get("brightness", 100))
                    with stream_lock:
                        stream_state["saved_brightness"] = max(
                            0, min(100, brightness)
                        )
                        paused = stream_state["paused"]
                    if not paused:
                        lcd.setBrightness(brightness)

                elif path == "/pause":
                    do_pause("http")

                elif path == "/resume":
                    do_resume(data.get("brightness"))

                elif path == "/orientation":
                    if "degrees" in data:
                        set_orientation_degrees(data["degrees"])

                elif path == "/overlay/layout/reset":
                    saved = set_overlay_layout(overlay_layout.default_layout())
                    self._set_headers()
                    self.wfile.write(bytes(json.dumps(saved), "utf-8"))
                    return

                elif path == "/frame":
                    with stream_lock:
                        paused = stream_state["paused"]
                    if paused:
                        # Drop frames while blanked — do not auto-resume here.
                        # SignalRGB pause still streams getImageBuffer; waking on
                        # every /frame undoes /pause and leaves the LCD active.
                        self._set_headers()
                        return
                    rawTime = time.time() - lastFrame
                    try:
                        rawBuffer.put_nowait((postData, rawTime))
                    except queue.Full:
                        try:
                            rawBuffer.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            rawBuffer.put_nowait((postData, rawTime))
                        except queue.Full:
                            pass
                    lastFrame = time.time()
                    with stream_lock:
                        stream_state["last_frame_at"] = lastFrame
                        stream_state["ever_framed"] = True

                self._set_headers()

        class ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
            pass

        server_address = ("", PORT)
        server = ThreadingSimpleServer(server_address, Handler)
        server.serve_forever()


class OverlayProducer(Thread):
    def __init__(self, rawBuffer: queue.Queue, frameBuffer: queue.Queue):
        Thread.__init__(self, name="OverlayProducer")
        self.daemon = True
        self.rawBuffer = rawBuffer
        self.frameBuffer = frameBuffer
        self.lastAngle = 0
        self.circleImg = Image.new("RGBA", lcd.resolution, (0, 0, 0, 0))
        self.fonts = {
            "titleFontSize": 10,
            "sensorFontSize": 100,
            "sensorLabelFontSize": 10,
            "fontTitle": ImageFont.truetype(FONT_FILE, 10),
            "fontSensor": ImageFont.truetype(FONT_FILE, 100),
            "fontSensorLabel": ImageFont.truetype(FONT_FILE, 10),
            "fontDegree": ImageFont.truetype(FONT_FILE, 10 // 3),
        }

    def updateFonts(self, data):
        if data["titleFontSize"] != self.fonts["titleFontSize"]:
            data["titleFontSize"] = data["titleFontSize"]
            self.fonts["fontTitle"] = ImageFont.truetype(
                FONT_FILE, data["titleFontSize"]
            )
        if data["sensorFontSize"] != self.fonts["sensorFontSize"]:
            data["sensorFontSize"] = data["sensorFontSize"]
            self.fonts["fontSensor"] = ImageFont.truetype(
                FONT_FILE, data["sensorFontSize"]
            )
            self.fonts["fontDegree"] = ImageFont.truetype(
                FONT_FILE, data["sensorFontSize"] // 3
            )
        if data["sensorLabelFontSize"] != self.fonts["sensorLabelFontSize"]:
            data["sensorLabelFontSize"] = data["sensorLabelFontSize"]
            self.fonts["fontSensorLabel"] = ImageFont.truetype(
                FONT_FILE, data["sensorLabelFontSize"]
            )

    def run(self):
        debug("Overlay converter worker started")
        while True:
            if self.frameBuffer.full():
                time.sleep(0.001)
                continue

            self.addOverlay(*self.rawBuffer.get())

    @timing
    def parseImage(self, data):
        raw = base64.b64decode(data["raw"])

        return (
            Image.open(BytesIO(raw))
            .convert("RGBA")
            .resize(
                lcd.resolution,
                Image.Resampling.LANCZOS,
            )
        )

    @timing
    def renderOverlay(self, data):
        alpha = 255
        overlay = Image.new("RGBA", data["size"], (0, 0, 0, 0))
        overlayCanvas = ImageDraw.Draw(overlay)

        use_layout = data.get("textOverlay") or data.get("customOverlay")
        layout = get_overlay_layout()
        widgets = layout.get("widgets") or []
        bg_raw = layout.get("backgroundColor")
        text_raw = layout.get("textColor")
        bg_transparent = overlay_layout.is_transparent(bg_raw)
        text_transparent = overlay_layout.is_transparent(text_raw)

        if text_transparent and not bg_transparent:
            # Solid dial + transparent text → punch only numbers; labels/units stay solid
            text_color = (255, 255, 255, 255)
            label_color = overlay_layout.parse_color(
                layout.get("labelColor") or "#9a9a9a", alpha
            )
            punch_text = True
        else:
            punch_text = False
            if text_transparent:
                text_color = (255, 255, 255, alpha)
            else:
                text_color = overlay_layout.parse_color(text_raw or "#ffffff", alpha)
            label_color = overlay_layout.parse_color(
                layout.get("labelColor") or "#9a9a9a", alpha
            )

        if use_layout:
            if not bg_transparent:
                overlayCanvas.rectangle(
                    [(0, 0), lcd.resolution],
                    fill=overlay_layout.parse_color(bg_raw, alpha),
                )
            if layout.get("arcs", {}).get("enabled", layout.get("tempArcs", True)):
                self._render_temp_arcs(
                    overlayCanvas,
                    alpha,
                    None if bg_transparent else bg_raw,
                    text_color,
                    label_color,
                    layout,
                    draw_side_text=True,
                    punch_numbers=punch_text,
                )

            if punch_text:
                # Draw labels / units / totals in label color, then punch number glyphs
                if widgets:
                    self._render_layout_widgets(
                        overlayCanvas,
                        widgets,
                        text_color,
                        label_color,
                        layout,
                        punch_numbers=True,
                    )
                mask = Image.new("L", data["size"], 0)
                mask_draw = ImageDraw.Draw(mask)
                if layout.get("arcs", {}).get("enabled", layout.get("tempArcs", True)):
                    self._render_arc_side_labels(
                        mask_draw, 255, 255, layout, numbers_only=True
                    )
                if widgets:
                    self._render_layout_widgets(
                        mask_draw,
                        widgets,
                        255,
                        255,
                        layout,
                        numbers_only=True,
                    )
                r, g, b, a = overlay.split()
                a = ImageChops.subtract(a, mask)
                overlay = Image.merge("RGBA", (r, g, b, a))
            else:
                if widgets:
                    self._render_layout_widgets(
                        overlayCanvas, widgets, text_color, label_color, layout
                    )
                elif data.get("textOverlay"):
                    self.updateFonts(data)
                    overlayCanvas.text(
                        (lcd.resolution.width // 2, lcd.resolution.height // 2),
                        text="{:.0f}".format(stats["liquid"]),
                        anchor="mm",
                        font=self.fonts["fontSensor"],
                        fill=text_color,
                    )

        return overlay.rotate(data["rotation"])

    def _render_temp_arcs(
        self,
        canvas,
        alpha,
        bg=None,
        text_color=None,
        label_color=None,
        layout=None,
        draw_side_text=True,
        punch_numbers=False,
    ):
        """Configurable left/right rim arcs."""
        snap = get_metrics_snapshot()
        arcs = (layout or {}).get("arcs") or overlay_layout.default_arcs()
        colors = arcs.get("colors") or overlay_layout.DEFAULT_ARC_COLORS
        width = 28
        pad = width // 2 + 1
        bbox = [
            (pad, pad),
            (lcd.resolution.width - 1 - pad, lcd.resolution.height - 1 - pad),
        ]
        if bg and not overlay_layout.is_transparent(bg):
            track = overlay_layout.parse_color(bg, alpha)
            canvas.arc(bbox, start=90, end=270, fill=track, width=width)
            canvas.arc(bbox, start=270, end=450, fill=track, width=width)

        left = arcs.get("left") or {}
        right = arcs.get("right") or {}
        self._fill_metric_arc(
            canvas, bbox, 90, 270, left, snap, colors, width, alpha, from_end=False
        )
        self._fill_metric_arc(
            canvas, bbox, 270, 450, right, snap, colors, width, alpha, from_end=True
        )

        if draw_side_text:
            self._render_arc_side_labels(
                canvas,
                text_color,
                label_color,
                layout,
                punch_numbers=punch_numbers,
            )

    def _render_arc_side_labels(
        self,
        canvas,
        text_color,
        label_color,
        layout,
        numbers_only=False,
        punch_numbers=False,
    ):
        snap = get_metrics_snapshot()
        arcs = (layout or {}).get("arcs") or overlay_layout.default_arcs()
        for key in ("left", "right"):
            side = arcs.get(key) or {}
            metric = side.get("metric") or ""
            if not metric:
                continue
            x = int(side.get("x", 70 if key == "left" else 570))
            y = int(side.get("y", 320))
            font_size = int(side.get("fontSize", 30))
            label_size = int(side.get("labelFontSize", 16))
            title = overlay_layout.arc_side_title(side)
            label_cy, value_cy = overlay_layout.widget_stack_ys(
                y, font_size, label_size, bool(title)
            )
            if title and not numbers_only and label_cy is not None:
                canvas.text(
                    (x, label_cy),
                    text=title,
                    anchor="mm",
                    font=_widget_font(label_size),
                    fill=label_color,
                )
            if numbers_only or not punch_numbers:
                parts = overlay_layout.format_metric_parts(metric, snap)
                text = "".join(t for t, _ in parts)
                canvas.text(
                    (x, value_cy),
                    text=text,
                    anchor="mm",
                    font=_widget_font(font_size),
                    fill=text_color,
                )

    def _fill_metric_arc(
        self, canvas, bbox, start, end, side, snap, colors, width, alpha, from_end
    ):
        metric = side.get("metric") or ""
        u = overlay_layout.arc_fill_unit(metric, side, snap)
        if u is None or u <= 0:
            return
        span = end - start
        steps = max(8, int(56 * u))
        for i in range(steps):
            if from_end:
                a0 = end - span * u * (i + 1) / steps
                a1 = end - span * u * i / steps
            else:
                a0 = start + span * u * i / steps
                a1 = start + span * u * (i + 1) / steps
            tip_u = ((i + 1) / steps) * u
            r, g, b = overlay_layout.gradient_rgb(tip_u, colors)
            canvas.arc(bbox, start=a0, end=a1, fill=(r, g, b, alpha), width=width)

    def _draw_metric_parts(
        self,
        canvas,
        cx,
        cy,
        parts,
        font_size,
        fill,
        label_fill=None,
        numbers_only=False,
        skip_numbers=False,
    ):
        unit_size = max(10, int(round(font_size * 0.55)))
        total_size = max(9, int(round(font_size * 0.42)))
        tiny_size = max(9, int(round(font_size * 0.36)))
        fonts = {
            "num": _widget_font(font_size),
            "unit": _widget_font(unit_size),
            "total": _widget_font(total_size),
            "tiny": _widget_font(tiny_size),
        }
        label_fill = label_fill if label_fill is not None else fill
        widths = []
        for text, role in parts:
            font = fonts.get(role, fonts["num"])
            bbox = canvas.textbbox((0, 0), text, font=font)
            widths.append(bbox[2] - bbox[0])
        total_w = sum(widths)
        x = cx - total_w / 2
        for (text, role), w in zip(parts, widths):
            is_num = role == "num"
            if numbers_only and not is_num:
                x += w
                continue
            if skip_numbers and is_num:
                x += w
                continue
            font = fonts.get(role, fonts["num"])
            color = fill if is_num else label_fill
            canvas.text((x, cy), text=text, anchor="lm", font=font, fill=color)
            x += w

    def _render_layout_widgets(
        self,
        canvas,
        widgets,
        text_color,
        label_color,
        layout,
        numbers_only=False,
        punch_numbers=False,
    ):
        snap = get_metrics_snapshot()
        for w in widgets:
            parts = overlay_layout.format_metric_parts(
                w.get("metric", ""), snap, w.get("format") or "auto"
            )
            label = w.get("label") or ""
            x = int(w.get("x", 320))
            y = int(w.get("y", 320))
            font_size = int(w.get("fontSize", 28))
            label_size = int(w.get("labelFontSize", 12))
            label_font = _widget_font(label_size)
            label_cy, value_cy = overlay_layout.widget_stack_ys(
                y, font_size, label_size, bool(label)
            )
            if label and not numbers_only and label_cy is not None:
                canvas.text(
                    (x, label_cy),
                    text=label,
                    anchor="mm",
                    font=label_font,
                    fill=label_color,
                )
            self._draw_metric_parts(
                canvas,
                x,
                value_cy,
                parts,
                font_size,
                text_color,
                label_fill=label_color,
                numbers_only=numbers_only,
                skip_numbers=punch_numbers and not numbers_only,
            )

    @timing
    def compose(self, data, img, overlay):
        if data["composition"] == "MIX":
            return Image.composite(
                img, Image.new("RGBA", img.size, (0, 0, 0, 0)), overlay
            )

        if data["composition"] == "OVERLAY":
            return Image.alpha_composite(img, overlay)

    @timing
    def addOverlay(self, postData, rawTime):
        startTime = time.time()

        data = json.loads(postData.decode("utf-8"))
        data["size"] = lcd.resolution
        img = self.parseImage(data)

        if data["composition"] != "OFF":
            overlay = self.renderOverlay(data)
            img = self.compose(data, img, overlay)

        # Soft-rotate for mount orientation (do not spam HID orientation mid-stream)
        if data.get("lcdOrientation") is not None:
            degrees = int(data["lcdOrientation"]) % 360
            with stream_lock:
                stream_state["lcd_orientation_degrees"] = degrees
        else:
            with stream_lock:
                degrees = stream_state["lcd_orientation_degrees"]
        if degrees % 360:
            img = img.rotate(-(degrees % 360), expand=False, fillcolor=(0, 0, 0, 255))

        overlayTime = time.time() - startTime

        frame = (
            lcd.imageToFrame(img, adaptive=False),
            rawTime,
            overlayTime,
        )
        try:
            self.frameBuffer.put_nowait(frame)
        except queue.Full:
            try:
                self.frameBuffer.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frameBuffer.put_nowait(frame)
            except queue.Full:
                pass


class MetricsProducer(Thread):
    """Merge Afterburner sensors + Kraken liquid into canonical metrics (~2 Hz)."""

    def __init__(self):
        Thread.__init__(self, name="MetricsProducer")
        self.daemon = True

    def run(self):
        debug("Metrics producer started")
        while True:
            raw = {}
            ab = {}
            try:
                raw = afterburner.read_raw_entries()
                ab = afterburner.to_canonical(raw)
            except Exception as e:
                debug("afterburner read failed: {}".format(e))

            # Prefer Afterburner CPU %; fall back to psutil
            cpu_pct = ab.get("cpu_usage_pct")
            if cpu_pct is None:
                try:
                    cpu_pct = psutil.cpu_percent(0)
                except Exception:
                    cpu_pct = stats["cpu"]

            ram_used = ab.get("ram_used")
            ram_total = ab.get("ram_total")
            if ram_used is None or ram_total is None:
                try:
                    vm = psutil.virtual_memory()
                    if ram_used is None:
                        ram_used = vm.used / (1024.0 * 1024.0)
                    if ram_total is None:
                        ram_total = vm.total / (1024.0 * 1024.0)
                except Exception:
                    pass

            # VRAM: Afterburner "Memory usage" is often disabled in MAHM —
            # fall back to nvidia-smi (matches what the OSD can show).
            vram_used = ab.get("vram_used")
            vram_total = ab.get("vram_total")
            if vram_used is None or vram_total is None:
                nv_used, nv_total = afterburner.read_nvidia_vram_mb()
                if vram_used is None:
                    vram_used = nv_used
                if vram_total is None:
                    vram_total = nv_total

            fps = ab.get("fps")
            if fps is None:
                fps = afterburner.read_rtss_fps()

            with metrics_lock:
                metrics["afterburner"] = bool(raw)
                for key in (
                    "gpu_power_w",
                    "gpu_power_max_w",
                    "cpu_power_w",
                    "cpu_power_max_w",
                    "gpu_temp_c",
                    "cpu_temp_c",
                    "gpu_usage_pct",
                ):
                    metrics[key] = ab.get(key)
                metrics["fps"] = fps
                metrics["cpu_usage_pct"] = cpu_pct
                metrics["vram_used"] = vram_used
                metrics["vram_total"] = vram_total
                metrics["ram_used"] = ram_used
                metrics["ram_total"] = ram_total
                stats["cpu"] = float(cpu_pct or 0)

            time.sleep(0.5)


class PauseWatchdog(Thread):
    def __init__(self):
        Thread.__init__(self, name="PauseWatchdog")
        self.daemon = True

    def run(self):
        if FRAME_WATCHDOG_S <= 0:
            debug("Pause watchdog disabled")
            while True:
                time.sleep(3600)
            return
        debug("Pause watchdog started ({:.1f}s)".format(FRAME_WATCHDOG_S))
        while True:
            time.sleep(0.1)
            with stream_lock:
                paused = stream_state["paused"]
                ever = stream_state["ever_framed"]
                last = stream_state["last_frame_at"]
            if paused or not ever:
                continue
            if time.time() - last >= FRAME_WATCHDOG_S:
                do_pause("watchdog")


class Systray(Thread):
    def __init__(self):
        Thread.__init__(self)
        from pystray._util import win32

        win32.WM_LBUTTONUP = 0x0205
        win32.WM_RBUTTONUP = 0x0202

        self.menu = pystray.Menu(
            pystray.MenuItem("Device: " + lcd.name, self.noop, enabled=False),
            pystray.MenuItem(
                "Bridge: http://127.0.0.1:{}".format(PORT), self.noop, enabled=False
            ),
            pystray.MenuItem(
                "SignalRGBPlugin: "
                + ("installed" if pluginInstalled else "not installed"),
                self.noop,
                enabled=False,
            ),
            pystray.MenuItem(
                self.getFPS,
                self.noop,
                enabled=False,
            ),
            pystray.MenuItem("Overlay editor", open_overlay_editor),
            pystray.MenuItem("Exit", self.stop),
        )
        self.icon = pystray.Icon(
            name="KrakenLCDBridge",
            title="KrakenLCDBridge",
            icon=Image.open(APP_ICON).resize((64, 64)),
            menu=self.menu,
        )

    def run(self):
        debug("Systray icon started")
        self.icon.run()

    def getFPS(self, _):
        return "FPS: {:.2f}".format(frameWriterWithStats.fps.value)

    def noop(self):
        pass

    def stop(self):
        self.icon.stop()


class FrameWriterWithStats(FrameWriter):
    def __init__(self, frameBuffer: queue.Queue, lcd: driver.KrakenLCD):
        super().__init__(frameBuffer, lcd)
        self.updateAIOStats()

    def updateAIOStats(self):
        if time.time() - self.lastDataTime > 1:
            self.lastDataTime = time.time()
            with stream_lock:
                paused = stream_state["paused"]
            if not paused:
                try:
                    aio = self.lcd.getStats()
                except Exception:
                    return
                stats.update(aio)
                with metrics_lock:
                    if "liquid" in aio:
                        metrics["liquid_c"] = aio["liquid"]
                    if "pump" in aio:
                        metrics["pump"] = aio["pump"]
                        stats["pump"] = aio["pump"]

    def onFrame(self):
        with stream_lock:
            paused = stream_state["paused"]
        if paused:
            # Drain without writing
            try:
                self.frameBuffer.get_nowait()
            except queue.Empty:
                pass
            return
        super().onFrame()
        self.updateAIOStats()


dataBuffer = queue.Queue(maxsize=2)
frameBuffer = queue.Queue(maxsize=2)

rawProducer = RawProducer(dataBuffer)
overlayProducer = OverlayProducer(dataBuffer, frameBuffer)
frameWriterWithStats = FrameWriterWithStats(frameBuffer, lcd)
metricsProducer = MetricsProducer()
pauseWatchdog = PauseWatchdog()
systray = Systray()

# Ensure default overlay layout exists on disk
get_overlay_layout(force=True)

rawProducer.start()
overlayProducer.start()
frameWriterWithStats.start()
metricsProducer.start()
pauseWatchdog.start()
systray.start()

print("SignalRGB Kraken bridge started")


try:
    while True:
        time.sleep(1)
        systray.icon.update_menu()
        if not (
            metricsProducer.is_alive()
            and rawProducer.is_alive()
            and overlayProducer.is_alive()
            and frameWriterWithStats.is_alive()
            and pauseWatchdog.is_alive()
            and systray.is_alive()
        ):
            raise KeyboardInterrupt("Some thread is dead")
except KeyboardInterrupt:
    frameWriterWithStats.shouldStop = True
    frameWriterWithStats.join()
    systray.stop()
