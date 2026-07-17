import time
import driver
import afterburner
import overlay_layout
import pystray
from PIL import Image, ImageFont, ImageDraw, ImageChops, ImageSequence
from io import BytesIO
import queue
from threading import Thread, Lock
from utils import debug, timing
import json
import psutil
import sys
import os
import webbrowser
from urllib.parse import urlparse, unquote, parse_qs, urlencode
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
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
DEFAULT_GIF_PATH = os.path.join(BASE_PATH, "images", "404.gif")
LAST_GIF_ENCODED = os.path.join(os.getcwd(), "last_gif.encoded.gif")
LAST_GIF_SOURCE = os.path.join(os.getcwd(), "last_gif.source.gif")
LAST_GIF_META = os.path.join(os.getcwd(), "last_gif.json")

gif_progress_lock = Lock()
gif_progress = {
    "active": False,
    "percent": 0,
    "phase": "",
    "frames": 0,
    "frame": 0,
}

_GIF_PROXY_HOSTS = (
    "web.archive.org",
    "gifcities.archive.org",
    "i.giphy.com",
    "media.giphy.com",
    "media0.giphy.com",
    "media1.giphy.com",
    "media2.giphy.com",
    "media3.giphy.com",
    "media4.giphy.com",
    "tenor.com",
    "media.tenor.com",
    "c.tenor.com",
)

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


def _metrics_fingerprint(snap: dict) -> tuple:
    """Coarse value tuple so overlay cache invalidates when sensors move."""

    def r(v):
        if v is None:
            return None
        try:
            return round(float(v), 1)
        except Exception:
            return v

    keys = (
        "liquid_c",
        "fps",
        "cpu_temp_c",
        "gpu_temp_c",
        "cpu_usage_pct",
        "gpu_usage_pct",
        "cpu_power_w",
        "gpu_power_w",
        "cpu_power_max_w",
        "gpu_power_max_w",
        "vram_used",
        "vram_total",
        "ram_used",
        "ram_total",
        "pump",
        "afterburner",
    )
    return tuple(r(snap.get(k)) for k in keys)


def _classic_overlay_fingerprint(data: dict) -> tuple:
    return (
        str(data.get("spinner") or "OFF").upper(),
        str(data.get("overlayMetric") or "Liquid"),
        str(data.get("overlayBgMode") or "Transparent"),
        str(data.get("overlayBgColor") or "#000000"),
        str(data.get("titleText") or ""),
        int(data.get("titleFontSize") or 40),
        int(data.get("sensorFontSize") or 160),
        int(data.get("sensorLabelFontSize") or 40),
    )


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
    webbrowser.open("http://127.0.0.1:{}/monitor".format(PORT))


def open_gif_editor(icon=None, item=None):
    webbrowser.open("http://127.0.0.1:{}/gif".format(PORT))


def _gif_proxy_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in _GIF_PROXY_HOSTS:
        return True
    return any(host.endswith("." + h) for h in _GIF_PROXY_HOSTS)


def fetch_gif_url(url: str, timeout: float = 20.0) -> bytes:
    if not _gif_proxy_allowed(url):
        raise ValueError("URL host not allowed")
    req = Request(
        url,
        headers={
            "User-Agent": "KrakenLCDBridge/1.0",
            "Accept": "image/gif,image/*,*/*",
        },
    )

    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not _gif_proxy_allowed(newurl):
                raise ValueError("redirect host not allowed")
            return HTTPRedirectHandler.redirect_request(
                self, req, fp, code, msg, headers, newurl
            )

    opener = build_opener(_NoRedirect())
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read()
    if not data:
        raise ValueError("empty response")
    return data


def _giphy_key_path() -> str:
    # Prefer live project dir (not PyInstaller extract) so the key persists.
    return os.path.join(os.getcwd(), "giphy.key")


def get_giphy_api_key(override: str = None) -> str:
    if override and override.strip():
        return override.strip()
    env = (os.environ.get("GIPHY_API_KEY") or "").strip()
    if env:
        return env
    try:
        with open(_giphy_key_path(), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def set_giphy_api_key(key: str) -> str:
    key = (key or "").strip()
    path = _giphy_key_path()
    if not key:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return ""
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def search_giphy(query: str, limit: int = 24, api_key: str = None) -> list:
    key = get_giphy_api_key(api_key)
    if not key:
        raise ValueError(
            "Missing Giphy API key. Paste one in the GIF editor "
            "(free at https://developers.giphy.com/dashboard/)."
        )
    limit = max(1, min(48, int(limit or 24)))
    q = (query or "").strip()
    if q:
        api = "https://api.giphy.com/v1/gifs/search?" + urlencode(
            {"api_key": key, "q": q, "limit": str(limit), "rating": "pg-13", "lang": "en"}
        )
    else:
        api = "https://api.giphy.com/v1/gifs/trending?" + urlencode(
            {"api_key": key, "limit": str(limit), "rating": "pg-13"}
        )
    req = Request(
        api,
        headers={"User-Agent": "KrakenLCDBridge/1.0", "Accept": "application/json"},
    )
    with urlopen(req, timeout=12.0) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    meta = (payload or {}).get("meta") or {}
    if int(meta.get("status") or 200) >= 400:
        raise ValueError(meta.get("msg") or "Giphy error {}".format(meta.get("status")))
    results = []
    for it in payload.get("data") or []:
        images = it.get("images") or {}
        original = (images.get("original") or {}).get("url") or ""
        downsized = (images.get("downsized_medium") or images.get("downsized") or {}).get(
            "url"
        ) or original
        preview = (
            (images.get("fixed_width") or {}).get("url")
            or (images.get("preview_gif") or {}).get("url")
            or downsized
        )
        if not (original or downsized):
            continue
        results.append(
            {
                "url": downsized or original,
                "preview": preview,
                "width": int((images.get("original") or {}).get("width") or 0),
                "height": int((images.get("original") or {}).get("height") or 0),
                "label": (it.get("title") or it.get("slug") or "giphy")[:48],
            }
        )
    return results


def set_gif_progress(percent: float, phase: str = "", frame: int = 0, frames: int = 0):
    with gif_progress_lock:
        gif_progress["active"] = True
        gif_progress["percent"] = int(max(0, min(100, round(percent))))
        gif_progress["phase"] = phase or gif_progress["phase"]
        if frames:
            gif_progress["frames"] = frames
        if frame:
            gif_progress["frame"] = frame


def clear_gif_progress():
    with gif_progress_lock:
        gif_progress["active"] = False
        gif_progress["percent"] = 0
        gif_progress["phase"] = ""
        gif_progress["frames"] = 0
        gif_progress["frame"] = 0


def get_gif_progress() -> dict:
    with gif_progress_lock:
        return dict(gif_progress)


def build_gif_bytes(
    path: str = None,
    degrees: int = 0,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    raw: bytes = None,
    bg_color: str = "#000000",
) -> bytes:
    """Quantize/resize a GIF to fit the Kraken bucket (same approach as writeGif.py)."""
    if raw is not None:
        img = Image.open(BytesIO(raw))
    else:
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(path or "(no path)")
        img = Image.open(path)
    degrees = int(degrees) % 360
    zoom = max(0.25, min(4.0, float(zoom) or 1.0))
    pan_x = max(-1.0, min(1.0, float(pan_x) or 0.0))
    pan_y = max(-1.0, min(1.0, float(pan_y) or 0.0))
    tw, th = lcd.resolution
    try:
        bg_rgb = overlay_layout.parse_color(bg_color or "#000000", 255)[:3]
    except Exception:
        bg_rgb = (0, 0, 0)

    set_gif_progress(1, "Reading frames")
    raw_frames = []
    raw_durs = []
    for frame in ImageSequence.Iterator(img):
        raw_frames.append(frame.copy())
        raw_durs.append(
            max(
                20,
                int(frame.info.get("duration") or img.info.get("duration") or 100),
            )
        )
    if not raw_frames:
        raise ValueError("GIF has no frames")
    nframes = len(raw_frames)
    set_gif_progress(5, "Fitting {} frames".format(nframes), frames=nframes)

    def fit_frame(frame: Image.Image) -> Image.Image:
        """Frame in editor space (pan/zoom), then rotate for LCD orientation.

        Pan is applied before rotation so left/right in the web preview match
        the image on the Kraken (was rotate-then-pan, which swapped axes at 90°).
        """
        frame = frame.convert("RGB")

        side = max(1, int(round(max(tw, th) * zoom)))
        fw, fh = frame.size
        scale = max(side / float(fw), side / float(fh))
        nw = max(1, int(round(fw * scale)))
        nh = max(1, int(round(fh * scale)))
        fitted = frame.resize((nw, nh), Image.Resampling.BILINEAR)
        cx0 = (nw - side) // 2
        cy0 = (nh - side) // 2
        square = fitted.crop((cx0, cy0, cx0 + side, cy0 + side))

        canvas = Image.new("RGB", (tw, th), bg_rgb)
        if side <= tw:
            # zoomed out: +panX moves content right (matches CSS preview)
            max_pan = (tw - side) / 2.0
            ox = int(round(max_pan * pan_x))
            oy = int(round(max_pan * pan_y))
            px = (tw - side) // 2 + ox
            py = (th - side) // 2 + oy
            canvas.paste(square, (px, py))
        else:
            # zoomed in: crop window — negate pan so +panX still moves content right
            max_pan = (side - tw) / 2.0
            ox = int(round(max_pan * pan_x))
            oy = int(round(max_pan * pan_y))
            left = (side - tw) // 2 - ox
            top = (side - th) // 2 - oy
            left = max(0, min(side - tw, left))
            top = max(0, min(side - th, top))
            canvas = square.crop((left, top, left + tw, top + th))

        if degrees:
            canvas = canvas.rotate(-degrees, expand=False, fillcolor=bg_rgb)
        return canvas

    fitted = []
    for i, frame in enumerate(raw_frames):
        fitted.append(fit_frame(frame))
        set_gif_progress(
            5 + (65.0 * (i + 1) / float(nframes)),
            "Fitting frames",
            frame=i + 1,
            frames=nframes,
        )

    def encode_colors(colors: int) -> bytes:
        set_gif_progress(
            max(70, get_gif_progress().get("percent") or 70),
            "Quantizing ({} colors)".format(colors),
            frames=nframes,
        )
        pal = fitted[0].quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        qframes = []
        for i, f in enumerate(fitted):
            qframes.append(
                f.quantize(colors=colors, palette=pal, dither=Image.Dither.NONE)
            )
            # Quantize pass sits in 70–88%
            set_gif_progress(
                70 + (18.0 * (i + 1) / float(nframes)),
                "Quantizing ({} colors)".format(colors),
                frame=i + 1,
                frames=nframes,
            )
        set_gif_progress(90, "Packing GIF", frames=nframes)
        byteio = BytesIO()
        qframes[0].save(
            byteio,
            "GIF",
            interlace=False,
            optimize=False,
            save_all=True,
            append_images=qframes[1:],
            duration=raw_durs,
            loop=0,
        )
        return byteio.getvalue()

    gif_data = b""
    color_steps = (96, 64, 48, 32, 24, 16)
    for colors in color_steps:
        gif_data = encode_colors(colors)
        print(
            "GIF encode: {} frames, {} colors -> {} bytes".format(
                nframes, colors, len(gif_data)
            ),
            flush=True,
        )
        if len(gif_data) <= lcd.maxBucketSize:
            break
    if len(gif_data) > lcd.maxBucketSize:
        raise ValueError(
            "GIF still too large after quantize ({} > {})".format(
                len(gif_data), lcd.maxBucketSize
            )
        )
    set_gif_progress(92, "Encode done", frames=nframes)
    return gif_data


def save_last_gif(
    encoded: bytes,
    source: bytes = None,
    degrees: int = 0,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    bg_color: str = "#000000",
):
    try:
        with open(LAST_GIF_ENCODED, "wb") as f:
            f.write(encoded)
        if source:
            with open(LAST_GIF_SOURCE, "wb") as f:
                f.write(source)
        with open(LAST_GIF_META, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "degrees": int(degrees) % 360,
                    "zoom": float(zoom),
                    "panX": float(pan_x),
                    "panY": float(pan_y),
                    "bgColor": bg_color or "#000000",
                    "hasSource": bool(source),
                    "bytes": len(encoded),
                },
                f,
            )
    except Exception as e:
        print("save last gif failed: {}".format(e), flush=True)


def load_last_gif_meta() -> dict:
    try:
        with open(LAST_GIF_META, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def has_last_gif() -> bool:
    return os.path.isfile(LAST_GIF_ENCODED) and os.path.getsize(LAST_GIF_ENCODED) > 0


def _push_gif_bucket(gif_data: bytes) -> None:
    """USB write of an already-encoded GIF bucket."""
    with lcd.usb_lock:
        lcd._setLcdModeUnlocked(driver.DISPLAY_MODE.LIQUID, 0)
        time.sleep(0.12)
        for bucket in range(lcd.totalBuckets):
            for i in range(10):
                if lcd.deleteBucket(bucket, i):
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("Could not delete bucket {}".format(bucket))
        if not lcd.createBucket(0, size=len(gif_data)):
            raise RuntimeError("createBucket failed")
        if not lcd.writeGIF(gif_data, 0):
            raise RuntimeError("writeGIF failed (USB)")
        if not lcd._setLcdModeUnlocked(driver.DISPLAY_MODE.BUCKET, 0):
            raise RuntimeError("set BUCKET mode failed")


def write_gif_to_device(
    path: str = None,
    degrees: int = 0,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    raw: bytes = None,
    bg_color: str = "#000000",
    encoded: bytes = None,
    persist: bool = True,
) -> dict:
    set_gif_progress(0, "Starting")
    source_for_save = raw
    try:
        with stream_lock:
            stream_state["paused"] = True
            stream_state["last_frame_at"] = time.time()
        time.sleep(0.2)

        if encoded is not None:
            gif_data = encoded
            set_gif_progress(90, "Restoring saved GIF")
        else:
            if source_for_save is None and path and os.path.isfile(path):
                try:
                    with open(path, "rb") as f:
                        source_for_save = f.read()
                except Exception:
                    source_for_save = None
            gif_data = build_gif_bytes(
                path=path,
                degrees=degrees,
                zoom=zoom,
                pan_x=pan_x,
                pan_y=pan_y,
                raw=raw,
                bg_color=bg_color,
            )

        last_err = None
        for attempt in range(1, 4):
            try:
                set_gif_progress(
                    93 + attempt, "Writing to LCD (try {})".format(attempt)
                )
                set_gif_progress(97, "USB transfer")
                _push_gif_bucket(gif_data)
                set_gif_progress(100, "Done")
                print(
                    "GIF loaded ({} bytes, attempt {})".format(len(gif_data), attempt),
                    flush=True,
                )
                if persist:
                    save_last_gif(
                        encoded=gif_data,
                        source=source_for_save,
                        degrees=degrees,
                        zoom=zoom,
                        pan_x=pan_x,
                        pan_y=pan_y,
                        bg_color=bg_color,
                    )
                return {
                    "ok": True,
                    "bytes": len(gif_data),
                    "path": path or "(upload)",
                }
            except Exception as e:
                last_err = e
                print(
                    "GIF write attempt {} failed: {}".format(attempt, e),
                    flush=True,
                )
                time.sleep(0.35 * attempt)

        try:
            with lcd.usb_lock:
                lcd._setLcdModeUnlocked(driver.DISPLAY_MODE.BUCKET, 0)
        except Exception:
            pass
        raise RuntimeError(str(last_err) if last_err else "GIF write failed")
    finally:
        if get_gif_progress().get("percent") != 100:
            clear_gif_progress()


def restore_last_or_default_gif(degrees: int = None) -> dict:
    """Re-apply last user GIF, or the default 404 if none saved yet."""
    degrees = int(
        degrees
        if degrees is not None
        else stream_state.get("lcd_orientation_degrees", 90)
    ) % 360
    meta = load_last_gif_meta()
    if has_last_gif():
        if int(meta.get("degrees", -1)) % 360 == degrees:
            with open(LAST_GIF_ENCODED, "rb") as f:
                encoded = f.read()
            result = write_gif_to_device(
                encoded=encoded,
                degrees=degrees,
                zoom=float(meta.get("zoom") or 1),
                pan_x=float(meta.get("panX") or 0),
                pan_y=float(meta.get("panY") or 0),
                bg_color=str(meta.get("bgColor") or "#000000"),
                persist=True,
            )
            result["restored"] = True
            return result
        if os.path.isfile(LAST_GIF_SOURCE):
            with open(LAST_GIF_SOURCE, "rb") as f:
                raw = f.read()
            result = write_gif_to_device(
                raw=raw,
                degrees=degrees,
                zoom=float(meta.get("zoom") or 1),
                pan_x=float(meta.get("panX") or 0),
                pan_y=float(meta.get("panY") or 0),
                bg_color=str(meta.get("bgColor") or "#000000"),
                persist=True,
            )
            result["restored"] = True
            return result
        with open(LAST_GIF_ENCODED, "rb") as f:
            encoded = f.read()
        result = write_gif_to_device(
            encoded=encoded, degrees=degrees, persist=True
        )
        result["restored"] = True
        return result

    result = write_gif_to_device(
        path=DEFAULT_GIF_PATH,
        degrees=degrees,
        zoom=1.0,
        pan_x=0.0,
        pan_y=0.0,
        bg_color="#000000",
        persist=False,
    )
    result["default"] = True
    result["path"] = "images/404.gif"
    return result


def do_shutdown_color(color: str = "#000000"):
    """Paint shutdown color, or true-off blank when #000000."""
    c = (color or "#000000").strip().lower()
    if c in ("#000000", "#000", "000000", "black", "0"):
        do_pause("shutdown")
        return
    try:
        rgb = overlay_layout.parse_color(c, 255)[:3]
    except Exception:
        do_pause("shutdown")
        return
    with stream_lock:
        stream_state["paused"] = True
        orient = stream_state["lcd_orientation_degrees"] // 90
        stream_state["last_frame_at"] = time.time()
        brightness = stream_state["saved_brightness"] or 100
    try:
        img = Image.new("RGBA", lcd.resolution, (*rgb, 255))
        frame = lcd.imageToFrame(img, adaptive=False)
        lcd.writeFrame(frame)
        lcd.setBrightnessImmediate(brightness, orientation=orient)
        print("LCD shutdown color {}".format(c), flush=True)
    except Exception as e:
        print("shutdown color failed: {}".format(e), flush=True)
        do_pause("shutdown")


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

                if path == "/images/404.gif":
                    if not os.path.isfile(DEFAULT_GIF_PATH):
                        self.send_error(404)
                        return
                    with open(DEFAULT_GIF_PATH, "rb") as f:
                        data = f.read()
                    self._set_headers("image/gif")
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

                if path in ("/monitor/layout", "/overlay/layout"):
                    self._set_headers()
                    self.wfile.write(
                        bytes(json.dumps(get_overlay_layout()), "utf-8")
                    )
                    return

                if path == "/gif/progress":
                    self._set_headers()
                    self.wfile.write(bytes(json.dumps(get_gif_progress()), "utf-8"))
                    return

                if path == "/gif/search":
                    qs = parse_qs(parsed.query)
                    q = (qs.get("q") or [""])[0]
                    try:
                        limit = int((qs.get("limit") or ["24"])[0])
                    except Exception:
                        limit = 24
                    header_key = self.headers.get("X-Giphy-Key") or ""
                    try:
                        results = search_giphy(q, limit=limit, api_key=header_key or None)
                        self._set_headers()
                        self.wfile.write(
                            bytes(
                                json.dumps(
                                    {
                                        "ok": True,
                                        "results": results,
                                        "hasKey": bool(get_giphy_api_key(header_key)),
                                    }
                                ),
                                "utf-8",
                            )
                        )
                    except Exception as e:
                        self.send_response(400)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            bytes(
                                json.dumps(
                                    {
                                        "ok": False,
                                        "error": str(e),
                                        "hasKey": bool(get_giphy_api_key(header_key)),
                                    }
                                ),
                                "utf-8",
                            )
                        )
                    return

                if path == "/gif/key":
                    self._set_headers()
                    has = bool(get_giphy_api_key())
                    self.wfile.write(
                        bytes(json.dumps({"ok": True, "hasKey": has}), "utf-8")
                    )
                    return

                if path == "/gif/proxy":
                    qs = parse_qs(parsed.query)
                    url = unquote((qs.get("url") or [""])[0])
                    try:
                        data = fetch_gif_url(url)
                        self._set_headers("image/gif")
                        self.wfile.write(data)
                    except Exception as e:
                        self.send_response(400)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            bytes(json.dumps({"ok": False, "error": str(e)}), "utf-8")
                        )
                    return

                if path in ("/monitor", "/monitor/", "/overlay", "/overlay/"):
                    self._serve_overlay_file("index.html")
                    return

                if path == "/gif" or path == "/gif/":
                    self._serve_overlay_file("gif.html")
                    return

                for prefix in ("/monitor/", "/overlay/"):
                    if path.startswith(prefix):
                        rel = path[len(prefix) :]
                        if ".." in rel or rel.startswith("/"):
                            self.send_error(404)
                            return
                        self._serve_overlay_file(rel)
                        return

                if path.startswith("/gif/"):
                    rel = path[len("/gif/") :]
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
                if path in ("/monitor/layout", "/overlay/layout"):
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

                elif path in ("/monitor/layout/reset", "/overlay/layout/reset"):
                    saved = set_overlay_layout(overlay_layout.default_layout())
                    self._set_headers()
                    self.wfile.write(bytes(json.dumps(saved), "utf-8"))
                    return

                elif path == "/gif/key":
                    key = str(data.get("key") or "").strip()
                    set_giphy_api_key(key)
                    self._set_headers()
                    self.wfile.write(
                        bytes(
                            json.dumps({"ok": True, "hasKey": bool(key)}),
                            "utf-8",
                        )
                    )
                    return

                elif path == "/gif":
                    gif_path = str(data.get("path") or "").strip()
                    degrees = int(
                        data.get(
                            "degrees",
                            stream_state["lcd_orientation_degrees"],
                        )
                    )
                    zoom = float(data.get("zoom") or 1.0)
                    pan_x = float(data.get("panX") or 0.0)
                    pan_y = float(data.get("panY") or 0.0)
                    bg_color = str(data.get("bgColor") or "#000000")
                    raw = None
                    b64 = data.get("raw")
                    if b64:
                        raw = base64.b64decode(b64)
                    try:
                        if bool(data.get("restore")) or bool(data.get("default")):
                            # default/restore: last applied GIF, else 404
                            result = restore_last_or_default_gif(degrees=degrees)
                        else:
                            result = write_gif_to_device(
                                path=gif_path or None,
                                degrees=degrees,
                                zoom=zoom,
                                pan_x=pan_x,
                                pan_y=pan_y,
                                raw=raw,
                                bg_color=bg_color,
                            )
                        self._set_headers()
                        self.wfile.write(bytes(json.dumps(result), "utf-8"))
                    except Exception as e:
                        print("GIF load failed: {}".format(e), flush=True)
                        self.send_response(400)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            bytes(json.dumps({"ok": False, "error": str(e)}), "utf-8")
                        )
                    return

                elif path == "/shutdown":
                    do_shutdown_color(str(data.get("color") or "#000000"))
                    self._set_headers()
                    self.wfile.write(b'{"ok":true}')
                    return

                elif path == "/open-ui":
                    page = str(data.get("page") or "monitor").strip().lower()
                    if page == "gif":
                        webbrowser.open("http://127.0.0.1:{}/gif".format(PORT))
                    else:
                        webbrowser.open("http://127.0.0.1:{}/monitor".format(PORT))
                    self._set_headers()
                    self.wfile.write(b'{"ok":true}')
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

        server_address = ("127.0.0.1", PORT)
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
        # Cached final RGBA overlay (MONITOR / static OVERLAY)
        self._overlay_cache_img = None
        self._overlay_cache_key = None

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
            try:
                if self.frameBuffer.full():
                    time.sleep(0.001)
                    continue
                self.addOverlay(*self.rawBuffer.get())
            except Exception as e:
                # Never kill the worker on a bad frame — LCD would freeze.
                print("addOverlay error: {}".format(e), flush=True)
                time.sleep(0.01)

    @timing
    def parseImage(self, data):
        b64 = data.get("raw")
        if not b64:
            raise ValueError("missing frame raw")
        raw = base64.b64decode(b64)
        img = Image.open(BytesIO(raw)).convert("RGBA")
        target = lcd.resolution
        if img.size == (target.width, target.height):
            return img
        return img.resize(target, Image.Resampling.BILINEAR)

    def _overlay_cacheable(self, mode: str, data: dict) -> bool:
        if mode in ("MONITOR", "EDITOR", "CUSTOM"):
            return True
        if mode != "OVERLAY":
            return False
        # Animated spinner must redraw every frame
        spinner = str(data.get("spinner") or "OFF").upper()
        return spinner not in ("CPU", "PUMP")

    def _make_overlay_cache_key(self, mode: str, data: dict) -> tuple:
        snap = get_metrics_snapshot()
        layout_mtime = None
        with layout_lock:
            layout_mtime = _layout_cache.get("mtime")
        key = (mode, layout_mtime, _metrics_fingerprint(snap))
        if mode == "OVERLAY":
            key = key + _classic_overlay_fingerprint(data)
        return key

    def _get_cached_overlay(self, data, mode: str):
        if not self._overlay_cacheable(mode, data):
            return self.renderOverlay(data)
        key = self._make_overlay_cache_key(mode, data)
        if self._overlay_cache_img is not None and self._overlay_cache_key == key:
            return self._overlay_cache_img
        overlay = self.renderOverlay(data)
        self._overlay_cache_img = overlay
        self._overlay_cache_key = key
        return overlay

    @timing
    def renderOverlay(self, data):
        mode = str(data.get("composition") or "OFF").upper()
        if mode in ("MONITOR", "EDITOR", "CUSTOM"):
            return self._render_custom_layout(data)
        if mode == "OVERLAY":
            return self._render_classic_overlay(data)
        return Image.new("RGBA", data["size"], (0, 0, 0, 0))

    def _pil_fallback_frame(self, data, img, overlay, degrees: int):
        mode = str(data.get("composition") or "OFF").upper()
        if mode in ("OVERLAY", "MONITOR", "EDITOR", "CUSTOM") and overlay is not None:
            img = Image.alpha_composite(img, overlay)
        if degrees % 360:
            img = img.rotate(-(degrees % 360), expand=False, fillcolor=(0, 0, 0, 255))
        return lcd.imageToFrame(img, adaptive=False)

    def _rust_compose_frame(self, img, overlay, degrees: int):
        """Fast path: blend + ortho rotate + mask + Q565 in one Rust call."""
        rotate_ccw = (-(degrees % 360)) % 360
        if rotate_ccw % 90 != 0:
            return None
        try:
            import q565_rust
        except Exception as e:
            if not getattr(OverlayProducer, "_rust_import_warned", False):
                OverlayProducer._rust_import_warned = True
                print("q565_rust unavailable, using PIL: {}".format(e), flush=True)
            return None
        if not hasattr(q565_rust, "py_compose_encode"):
            if not getattr(OverlayProducer, "_rust_api_warned", False):
                OverlayProducer._rust_api_warned = True
                print("q565_rust missing py_compose_encode, using PIL", flush=True)
            return None
        w, h = img.size
        canvas = img.tobytes("raw", "RGBA")
        if overlay is not None:
            if overlay.size != img.size:
                overlay = overlay.resize(img.size, Image.Resampling.BILINEAR)
            if overlay.mode != "RGBA":
                overlay = overlay.convert("RGBA")
            ov = overlay.tobytes("raw", "RGBA")
        else:
            ov = b""
        try:
            return q565_rust.py_compose_encode(w, h, canvas, ov, rotate_ccw)
        except Exception as e:
            if not getattr(OverlayProducer, "_rust_encode_warned", False):
                OverlayProducer._rust_encode_warned = True
                print("py_compose_encode failed, using PIL: {}".format(e), flush=True)
            return None

    @timing
    def compose(self, data, img, overlay):
        mode = str(data.get("composition") or "OFF").upper()
        if mode in ("OVERLAY", "MONITOR", "EDITOR", "CUSTOM"):
            return Image.alpha_composite(img, overlay)
        return img

    @timing
    def addOverlay(self, postData, rawTime):
        startTime = time.time()

        data = json.loads(postData.decode("utf-8"))
        data["size"] = lcd.resolution
        img = self.parseImage(data)
        mode = str(data.get("composition") or "OFF").upper()

        overlay = None
        if mode in ("OVERLAY", "MONITOR", "EDITOR", "CUSTOM"):
            overlay = self._get_cached_overlay(data, mode)

        if data.get("lcdOrientation") is not None:
            degrees = int(data["lcdOrientation"]) % 360
            with stream_lock:
                stream_state["lcd_orientation_degrees"] = degrees
        else:
            with stream_lock:
                degrees = stream_state["lcd_orientation_degrees"]

        encoded = self._rust_compose_frame(img, overlay, degrees)
        if encoded is None:
            encoded = self._pil_fallback_frame(data, img, overlay, degrees)

        overlayTime = time.time() - startTime

        frame = (encoded, rawTime, overlayTime)
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
    def _overlay_metric_value(self, metric_name: str):
        """Sensors available without Afterburner (Kraken + psutil)."""
        name = (metric_name or "Liquid").strip().lower()
        snap = get_metrics_snapshot()
        if name == "pump":
            v = snap.get("pump")
            if v is None:
                v = stats.get("pump")
            return None if v is None else float(v), "Pump", "%"
        if name in ("cpu", "cpu %", "cpu%"):
            v = snap.get("cpu_usage_pct")
            if v is None:
                v = stats.get("cpu")
            return None if v is None else float(v), "CPU", "%"
        if name in ("cpu °", "cpu temp", "cpu°", "cpu_temp"):
            v = snap.get("cpu_temp_c")
            if v is None:
                try:
                    import native_sensors

                    v = native_sensors.read_wmi_thermal_c()
                except Exception:
                    v = None
            return None if v is None else float(v), "CPU", "\u00b0"
        v = snap.get("liquid_c")
        if v is None:
            v = stats.get("liquid")
        return None if v is None else float(v), "Liquid", "\u00b0"

    def _render_classic_overlay(self, data):
        """Upstream-style spinner + single metric on the SignalRGB canvas."""
        alpha = 255
        overlay = Image.new("RGBA", data["size"], (0, 0, 0, 0))
        overlayCanvas = ImageDraw.Draw(overlay)

        bg_mode = str(data.get("overlayBgMode") or "Transparent").strip().lower()
        if bg_mode.startswith("fix") or bg_mode == "solid":
            bg = overlay_layout.parse_color(
                data.get("overlayBgColor") or "#000000", alpha
            )
            overlayCanvas.rectangle([(0, 0), lcd.resolution], fill=bg)

        spinner = str(data.get("spinner") or "OFF").upper()

        if spinner in ("CPU", "PUMP"):
            bands = list(self.circleImg.split())
            bands[3] = bands[3].point(lambda x: round(x / 1.1) if x > 10 else 0)
            self.circleImg = Image.merge(self.circleImg.mode, bands)
            circleCanvas = ImageDraw.Draw(self.circleImg)
            key = "cpu" if spinner == "CPU" else "pump"
            snap = get_metrics_snapshot()
            speed_src = snap.get("cpu_usage_pct" if key == "cpu" else "pump")
            if speed_src is None:
                speed_src = stats.get(key, 0)
            angle = MIN_SPEED + BASE_SPEED * float(speed_src or 0) / 100.0
            new_angle = self.lastAngle + angle
            circleCanvas.arc(
                [(0, 0), lcd.resolution],
                fill=(255, 255, 255, round(alpha / 1.05)),
                width=lcd.resolution.width // 20,
                start=self.lastAngle,
                end=self.lastAngle + angle / 2,
            )
            circleCanvas.arc(
                [(0, 0), lcd.resolution],
                fill=(255, 255, 255, alpha),
                width=lcd.resolution.width // 20,
                start=self.lastAngle + angle / 2,
                end=new_angle,
            )
            self.lastAngle = new_angle
            overlay.paste(self.circleImg)

        if spinner == "STATIC":
            overlayCanvas.ellipse(
                [(0, 0), lcd.resolution],
                outline=(255, 255, 255, alpha),
                width=lcd.resolution.width // 20,
            )

        value, label, unit = self._overlay_metric_value(data.get("overlayMetric"))
        self.updateFonts(
            {
                "titleFontSize": int(data.get("titleFontSize") or 40),
                "sensorFontSize": int(data.get("sensorFontSize") or 160),
                "sensorLabelFontSize": int(data.get("sensorLabelFontSize") or 40),
            }
        )
        fill = (255, 255, 255, alpha)
        overlayCanvas.text(
            (lcd.resolution.width // 2, lcd.resolution.height // 5),
            text=str(data.get("titleText") or "SignalRGB"),
            anchor="mm",
            align="center",
            font=self.fonts["fontTitle"],
            fill=fill,
        )
        value_text = "--" if value is None else "{:.0f}".format(value)
        overlayCanvas.text(
            (lcd.resolution.width // 2, lcd.resolution.height // 2),
            text=value_text,
            anchor="mm",
            align="center",
            font=self.fonts["fontSensor"],
            fill=fill,
        )
        if unit and value is not None:
            text_bbox = overlayCanvas.textbbox(
                (lcd.resolution.width // 2, lcd.resolution.height // 2),
                text=value_text,
                anchor="mm",
                align="center",
                font=self.fonts["fontSensor"],
            )
            if unit == "\u00b0":
                overlayCanvas.text(
                    (text_bbox[2], text_bbox[1]),
                    text=unit,
                    anchor="lt",
                    align="center",
                    font=self.fonts["fontDegree"],
                    fill=fill,
                )
            else:
                overlayCanvas.text(
                    (text_bbox[2] + 4, (text_bbox[1] + text_bbox[3]) / 2),
                    text=unit,
                    anchor="lm",
                    align="center",
                    font=self.fonts["fontDegree"],
                    fill=fill,
                )
        overlayCanvas.text(
            (lcd.resolution.width // 2, 4 * lcd.resolution.height // 5),
            text=label,
            anchor="mm",
            align="center",
            font=self.fonts["fontSensorLabel"],
            fill=fill,
        )
        return overlay.rotate(data.get("rotation") or 0)

    def _render_custom_layout(self, data):
        alpha = 255
        overlay = Image.new("RGBA", data["size"], (0, 0, 0, 0))
        overlayCanvas = ImageDraw.Draw(overlay)

        layout = get_overlay_layout()
        widgets = layout.get("widgets") or []
        bg_raw = layout.get("backgroundColor")
        text_raw = layout.get("textColor")
        bg_transparent = overlay_layout.is_transparent(bg_raw)
        text_transparent = overlay_layout.is_transparent(text_raw)

        if text_transparent and not bg_transparent:
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
        elif widgets:
            self._render_layout_widgets(
                overlayCanvas, widgets, text_color, label_color, layout
            )

        return overlay.rotate(data.get("rotation") or 0)

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
        unit_font_size=None,
        total_font_size=None,
    ):
        unit_size = (
            max(8, int(unit_font_size))
            if unit_font_size is not None
            else overlay_layout.default_unit_font(font_size)
        )
        total_size = (
            max(8, int(total_font_size))
            if total_font_size is not None
            else overlay_layout.default_total_font(font_size)
        )
        fonts = {
            "num": _widget_font(font_size),
            "unit": _widget_font(unit_size),
            "total": _widget_font(total_size),
            "tiny": _widget_font(unit_size),
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
            show_total = w.get("showTotal", True)
            parts = overlay_layout.format_metric_parts(
                w.get("metric", ""),
                snap,
                w.get("format") or "auto",
                show_total=bool(show_total),
            )
            label = w.get("label") or ""
            x = int(w.get("x", 320))
            y = int(w.get("y", 320))
            font_size = int(w.get("fontSize", 28))
            label_size = int(w.get("labelFontSize", 12))
            unit_size = w.get("unitFontSize")
            total_size = w.get("totalFontSize")
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
                unit_font_size=unit_size,
                total_font_size=total_size,
            )


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
                print("afterburner read failed: {}".format(e), flush=True)
                raw = {}
                ab = {}

            if not raw and getattr(MetricsProducer, "_had_afterburner", False):
                print("Afterburner MAHM went offline", flush=True)
            MetricsProducer._had_afterburner = bool(raw)

            cpu_pct = ab.get("cpu_usage_pct")
            if cpu_pct is None:
                try:
                    cpu_pct = psutil.cpu_percent(0)
                except Exception:
                    cpu_pct = stats["cpu"]

            cpu_temp = ab.get("cpu_temp_c")
            if cpu_temp is None:
                try:
                    import native_sensors

                    cpu_temp = native_sensors.read_wmi_thermal_c()
                except Exception:
                    cpu_temp = None

            ram_used = ab.get("ram_used")
            ram_total = ab.get("ram_total")
            # psutil only if Afterburner has no "RAM usage" (commit charge ignored).
            if ram_used is None or ram_total is None:
                try:
                    phys_used, phys_total = afterburner.read_physical_ram_mb()
                    if ram_used is None and phys_used is not None:
                        ram_used = phys_used
                    if ram_total is None and phys_total is not None:
                        ram_total = phys_total
                except Exception:
                    pass
            # VRAM: Afterburner "Memory usage" when present; else nvidia-smi.
            vram_used = ab.get("vram_used")
            vram_total = ab.get("vram_total")
            if vram_used is None or vram_total is None:
                nv_used, nv_total = afterburner.read_nvidia_vram_mb()
                if vram_used is None:
                    vram_used = nv_used
                if vram_total is None:
                    vram_total = nv_total

            # Live RTSS wins. If RTSS is present but idle, clear sticky MAHM Framerate.
            rtss_fps, rtss_present = afterburner.read_rtss_fps()
            mahm_fps = afterburner.sanitize_fps(ab.get("fps"))
            fps = afterburner.pick_display_fps(mahm_fps, rtss_fps, rtss_present)

            with metrics_lock:
                metrics["afterburner"] = bool(raw)
                for key in (
                    "gpu_power_w",
                    "gpu_power_max_w",
                    "cpu_power_w",
                    "cpu_power_max_w",
                    "gpu_temp_c",
                    "gpu_usage_pct",
                ):
                    metrics[key] = ab.get(key)
                metrics["cpu_temp_c"] = cpu_temp
                metrics["fps"] = fps
                metrics["cpu_usage_pct"] = cpu_pct
                metrics["vram_used"] = vram_used
                metrics["vram_total"] = vram_total
                metrics["ram_used"] = ram_used
                metrics["ram_total"] = ram_total
                stats["cpu"] = float(cpu_pct or 0)

            # Align with Afterburner hardware polling (often 200–1000 ms).
            time.sleep(0.3)


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
            pystray.MenuItem("Monitor editor", open_overlay_editor),
            pystray.MenuItem("GIF editor", open_gif_editor),
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
