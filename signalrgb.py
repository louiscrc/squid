import time
import driver
import pystray
from PIL import Image, ImageFont, ImageDraw
from io import BytesIO
import queue
from threading import Thread, Lock
from utils import debug, timing
import json
import psutil
import sys
import os
from workers import FrameWriter
from http.server import BaseHTTPRequestHandler, HTTPServer
import base64
from socketserver import ThreadingMixIn
import shutil
import ctypes.wintypes

PORT = 30003
BASE_PATH = "."
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BASE_PATH = sys._MEIPASS

FONT_FILE = os.path.join(BASE_PATH, "fonts/Rubik-Bold.ttf")
APP_ICON = os.path.join(BASE_PATH, "images/plugin.png")

MIN_SPEED = 2
BASE_SPEED = 18
# Auto-blank if frames stop arriving (covers pause without Shutdown).
# 1.5s stays safe for ~1 FPS SIGNALRGB LIMITED.
FRAME_WATCHDOG_S = 1.5

stats = {
    "cpu": 0,
    "pump": 0,
    "liquid": 0,
}

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
                if (
                    self.path == "/images/2023elite.png"
                    or self.path == "/images/2023.png"
                    or self.path == "/images/z3.png"
                    or self.path == "/images/plugin.png"
                ):
                    file = open(BASE_PATH + self.path, "rb")
                    data = file.read()
                    file.close()
                    self._set_headers("image/png")
                    self.wfile.write(data)
                else:
                    info = lcd.getInfo()
                    with stream_lock:
                        info["paused"] = stream_state["paused"]
                        info["lcdOrientation"] = stream_state[
                            "lcd_orientation_degrees"
                        ]
                        info["brightness"] = stream_state["saved_brightness"]
                    self._set_headers()
                    self.wfile.write(bytes(json.dumps(info), "utf-8"))

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

                if self.path == "/brightness":
                    brightness = int(data.get("brightness", 100))
                    with stream_lock:
                        stream_state["saved_brightness"] = max(
                            0, min(100, brightness)
                        )
                        paused = stream_state["paused"]
                    if not paused:
                        lcd.setBrightness(brightness)

                elif self.path == "/pause":
                    do_pause("http")

                elif self.path == "/resume":
                    do_resume(data.get("brightness"))

                elif self.path == "/orientation":
                    if "degrees" in data:
                        set_orientation_degrees(data["degrees"])

                elif self.path == "/frame":
                    with stream_lock:
                        paused = stream_state["paused"]
                    if paused:
                        # Drop frames while blanked
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
        if data["composition"] == "OVERLAY":
            alpha = round((100 - data["overlayTransparency"]) * 255 / 100)
        overlay = Image.new("RGBA", data["size"], (0, 0, 0, 0))
        overlayCanvas = ImageDraw.Draw(overlay)

        if data["spinner"] == "CPU" or data["spinner"] == "PUMP":
            bands = list(self.circleImg.split())
            bands[3] = bands[3].point(lambda x: round(x / 1.1) if x > 10 else 0)
            self.circleImg = Image.merge(self.circleImg.mode, bands)
            circleCanvas = ImageDraw.Draw(self.circleImg)

            angle = MIN_SPEED + BASE_SPEED * stats[data["spinner"].lower()] / 100

            newAngle = self.lastAngle + angle
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
                end=newAngle,
            )
            self.lastAngle = newAngle
            overlay.paste(self.circleImg)

        if data["spinner"] == "STATIC":
            overlayCanvas.ellipse(
                [(0, 0), lcd.resolution],
                outline=(255, 255, 255, alpha),
                width=lcd.resolution.width // 20,
            )
        if data["textOverlay"]:
            self.updateFonts(data)
            overlayCanvas.text(
                (lcd.resolution.width // 2, lcd.resolution.height // 5),
                text=data["titleText"],
                anchor="mm",
                align="center",
                font=self.fonts["fontTitle"],
                fill=(255, 255, 255, alpha),
            )
            overlayCanvas.text(
                (lcd.resolution.width // 2, lcd.resolution.height // 2),
                text="{:.0f}".format(stats["liquid"]),
                anchor="mm",
                align="center",
                font=self.fonts["fontSensor"],
                fill=(255, 255, 255, alpha),
            )
            textBbox = overlayCanvas.textbbox(
                (lcd.resolution.width // 2, lcd.resolution.height // 2),
                text="{:.0f}".format(stats["liquid"]),
                anchor="mm",
                align="center",
                font=self.fonts["fontSensor"],
            )
            overlayCanvas.text(
                ((textBbox[2], textBbox[1])),
                text="°",
                anchor="lt",
                align="center",
                font=self.fonts["fontDegree"],
                fill=(255, 255, 255, alpha),
            )
            overlayCanvas.text(
                (lcd.resolution.width // 2, 4 * lcd.resolution.height // 5),
                text="Liquid",
                anchor="mm",
                align="center",
                font=self.fonts["fontSensorLabel"],
                fill=(255, 255, 255, alpha),
            )

        return overlay.rotate(data["rotation"])

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
            lcd.imageToFrame(img, adaptive=data["colorPalette"] == "ADAPTIVE"),
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


class StatsProducer(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.daemon = True

    def run(self):
        debug("CPU stats producer started")
        while True:
            stats["cpu"] = psutil.cpu_percent(1)


class PauseWatchdog(Thread):
    def __init__(self):
        Thread.__init__(self, name="PauseWatchdog")
        self.daemon = True

    def run(self):
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
                stats.update(self.lcd.getStats())

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
statsProducer = StatsProducer()
pauseWatchdog = PauseWatchdog()
systray = Systray()


rawProducer.start()
overlayProducer.start()
frameWriterWithStats.start()
statsProducer.start()
pauseWatchdog.start()
systray.start()

print("SignalRGB Kraken bridge started")


try:
    while True:
        time.sleep(1)
        systray.icon.update_menu()
        if not (
            statsProducer.is_alive()
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
