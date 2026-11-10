# -*- coding: utf-8 -*-
"""
KeySim · 键盘指令模拟器（API + 可视化窗口）

功能：
1. 启动一个 HTTP API 服务，接收 "1" "2" "3" "4" "5" "6" 等按键指令；
2. 模拟真实按键（发给当前获得焦点的窗口）：Windows 用 SendInput，macOS 用 CGEvent；
3. 窗口内有 1~6 可点击按钮，并实时显示收到的指令。

macOS 说明：需要在「系统设置 → 隐私与安全性 → 辅助功能」中授权运行本程序的
终端 / Python / exe，否则系统会静默丢弃模拟的按键。

接口示例：
    GET  http://127.0.0.1:8765/press?key=1
    GET  http://127.0.0.1:8765/press?keys=1,2,3
    POST http://127.0.0.1:8765/press         {"key": "1"}
    POST http://127.0.0.1:8765/press         {"keys": ["1", "2", "enter"]}
    GET  http://127.0.0.1:8765/status
"""

import ctypes
import json
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
SUPPORTS_SIMULATION = IS_WINDOWS or IS_MAC
DEFAULT_PORT = 8765

# ---------------------------------------------------------------- 按键映射
# Windows 虚拟键码（VK）
WIN_KEY_MAP = {
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "enter": 0x0D, "space": 0x20, "tab": 0x09, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    WIN_KEY_MAP[_c] = ord(_c.upper())
for _i in range(1, 13):
    WIN_KEY_MAP["f%d" % _i] = 0x6F + _i  # F1=0x70

# macOS 虚拟键码（CGKeyCode）
MAC_KEY_MAP = {
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21,
    "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
    "left": 123, "up": 126, "right": 124, "down": 125,
    "enter": 36, "space": 49, "tab": 48, "esc": 53,
    "backspace": 51, "delete": 117, "home": 115, "end": 119,
    "pageup": 116, "pagedown": 121,
    "ctrl": 59, "alt": 58, "shift": 56, "cmd": 55,
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37,
    "j": 38, "k": 40, "n": 45, "m": 46,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


# ---------------------------------------------------------------- 键盘模拟
if IS_WINDOWS:
    from ctypes import wintypes

    KEYEVENTF_KEYUP = 0x0002
    INPUT_KEYBOARD = 1

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT
    _user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    _user32.MapVirtualKeyW.restype = wintypes.UINT

elif IS_MAC:
    _cg = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    _cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    _as = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")

    _cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    _cg.CGEventCreateKeyboardEvent.argtypes = (ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool)
    _cg.CGEventPost.restype = None
    _cg.CGEventPost.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
    _cf.CFRelease.restype = None
    _cf.CFRelease.argtypes = (ctypes.c_void_p,)
    _as.AXIsProcessTrusted.restype = ctypes.c_bool
    _as.AXIsProcessTrusted.argtypes = ()

    K_CG_HID_EVENT_TAP = 0


def mac_accessibility_trusted():
    """macOS 是否已授予「辅助功能」权限（未授权时系统会丢弃模拟按键）。"""
    if not IS_MAC:
        return True
    try:
        return bool(_as.AXIsProcessTrusted())
    except Exception:
        return False


def open_accessibility_settings():
    if not IS_MAC:
        return
    subprocess.run([
        "open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    ], check=False)


def _win_error_text(code):
    if code == 5:
        return "错误码 5：权限不足，目标窗口可能是管理员权限（请右键以管理员身份运行本程序）"
    if code:
        return "错误码 %d" % code
    return "SendInput 返回 0"


def _resolve_key(name):
    """把按键名解析成当前系统的键码，不支持时返回 None。"""
    key = name.strip().lower()
    if IS_WINDOWS:
        return WIN_KEY_MAP.get(key)
    if IS_MAC:
        return MAC_KEY_MAP.get(key)
    return None


def _mac_press(keycode):
    down = _cg.CGEventCreateKeyboardEvent(None, keycode, True)
    if not down:
        return False, "创建键盘事件失败"
    _cg.CGEventPost(K_CG_HID_EVENT_TAP, down)
    _cf.CFRelease(down)
    time.sleep(0.02)
    up = _cg.CGEventCreateKeyboardEvent(None, keycode, False)
    if not up:
        return False, "创建键盘事件失败"
    _cg.CGEventPost(K_CG_HID_EVENT_TAP, up)
    _cf.CFRelease(up)
    return True, ""


def simulate_key(name):
    """按名称发送一次“按下 + 抬起”，返回 (是否成功, 失败原因)。"""
    keycode = _resolve_key(name)
    if keycode is None:
        return False, "不支持的按键名"
    if IS_WINDOWS:
        down = _INPUT(type=INPUT_KEYBOARD)
        down.ki.wVk = keycode
        down.ki.wScan = _user32.MapVirtualKeyW(keycode, 0)
        down.ki.dwFlags = 0
        up = _INPUT(type=INPUT_KEYBOARD)
        up.ki.wVk = keycode
        up.ki.wScan = down.ki.wScan
        up.ki.dwFlags = KEYEVENTF_KEYUP
        if _user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT)) != 1:
            return False, _win_error_text(ctypes.get_last_error())
        time.sleep(0.02)
        if _user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT)) != 1:
            return False, _win_error_text(ctypes.get_last_error())
        return True, ""
    if IS_MAC:
        if not mac_accessibility_trusted():
            return False, ("未获得“辅助功能”权限：请在系统设置 → 隐私与安全性 → 辅助功能中"
                           "勾选运行本程序的终端 / Python")
        return _mac_press(keycode)
    return False, "当前系统 %s 暂不支持模拟按键" % sys.platform


def press_keys(keys):
    """按顺序模拟一组按键，返回 (成功的键, 模拟失败的键, 无效的键, 失败原因字典)。"""
    pressed, failed, invalid, errors = [], [], [], {}
    for key in keys:
        name = str(key).strip()
        if not name:
            continue
        if _resolve_key(name) is None:
            invalid.append(name)
            continue
        ok, reason = simulate_key(name)
        if ok:
            pressed.append(name)
        else:
            failed.append(name)
            errors[name] = reason
    return pressed, failed, invalid, errors


# ---------------------------------------------------------------- API 服务
class ApiServer:
    def __init__(self, host, port, on_event):
        self.host = host
        self.port = port
        self.on_event = on_event  # 回调：把收到的指令交给界面显示
        self._httpd = None
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KeyboardApi/1.0"

        def log_message(self, fmt, *args):  # 关闭默认控制台日志
            pass

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _handle_press(self, keys, source):
            pressed, failed, invalid, errors = press_keys(keys)
            if pressed or failed or invalid:
                app.on_event({
                    "source": source,
                    "pressed": pressed,
                    "failed": failed,
                    "invalid": invalid,
                    "errors": errors,
                    "client": "%s:%s" % self.client_address[:2],
                    "time": datetime.now().strftime("%H:%M:%S"),
                })
            ok = bool(pressed) and not failed and not invalid
            self._send_json({
                "ok": ok,
                "pressed": pressed,
                "failed": failed,
                "invalid": invalid,
                "errors": errors,
                "simulated": SUPPORTS_SIMULATION,
            }, 200 if ok else 400)

        def _keys_from_params(self, data):
            raw = data.get("keys")
            if raw is None:
                raw = data.get("key", [])
            if not isinstance(raw, list):
                raw = [raw]
            keys = []
            for item in raw:
                for part in str(item).replace("，", ",").split(","):
                    if part.strip():
                        keys.append(part.strip())
            return keys

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/status"):
                self._send_json({
                    "ok": True,
                    "service": "keyboard-api",
                    "platform": sys.platform,
                    "simulated": SUPPORTS_SIMULATION,
                    "accessibility_trusted": mac_accessibility_trusted(),
                })
                return
            if url.path == "/press":
                params = parse_qs(url.query)
                keys = self._keys_from_params(params)
                if not keys:
                    self._send_json({"ok": False, "error": "缺少 key 参数"}, 400)
                    return
                self._handle_press(keys, "接口")
                return
            self._send_json({"ok": False, "error": "未知接口"}, 404)

        def do_POST(self):
            url = urlparse(self.path)
            if url.path != "/press":
                self._send_json({"ok": False, "error": "未知接口"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send_json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
                return
            keys = self._keys_from_params(data)
            if not keys:
                self._send_json({"ok": False, "error": "缺少 key/keys 字段"}, 400)
                return
            self._handle_press(keys, "接口")

    return Handler


# ---------------------------------------------------------------- 界面
class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.server = None

        root.title("KeySim · 键盘指令模拟器")
        root.geometry("640x560")
        root.minsize(560, 480)
        self._build_ui()
        self.toggle_topmost()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.root.after(100, self.poll_queue)
        self.start_server()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        top = ttk.LabelFrame(self.root, text="API 服务")
        top.pack(fill="x", **pad)

        row = ttk.Frame(top)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="监听端口").pack(side="left")
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.port_var, width=8).pack(side="left", padx=6)
        self.btn_start = ttk.Button(row, text="启动", command=self.start_server)
        self.btn_start.pack(side="left", padx=2)
        self.btn_stop = ttk.Button(row, text="停止", command=self.stop_server, state="disabled")
        self.btn_stop.pack(side="left", padx=2)
        self.topmost_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="窗口置顶", variable=self.topmost_var,
                        command=self.toggle_topmost).pack(side="right")

        self.status_var = tk.StringVar(value="未启动")
        ttk.Label(top, textvariable=self.status_var, foreground="#0a7d32").pack(
            anchor="w", padx=10, pady=(0, 6))

        keys = ttk.LabelFrame(self.root, text="按键（点击可直接测试）")
        keys.pack(fill="x", **pad)
        grid = ttk.Frame(keys)
        grid.pack(fill="x", padx=8, pady=8)
        for i in range(1, 7):
            grid.columnconfigure(i - 1, weight=1, uniform="key")
            ttk.Button(grid, text=str(i),
                       command=lambda k=str(i): self.local_press(k)).grid(
                row=0, column=i - 1, sticky="ew", padx=4, pady=2)
        extra = ttk.Frame(keys)
        extra.pack(fill="x", padx=8, pady=(0, 8))
        for name in ("enter", "space", "esc", "tab"):
            ttk.Button(extra, text=name, width=8,
                       command=lambda k=name: self.local_press(k)).pack(side="left", padx=4)

        test_row = ttk.Frame(keys)
        test_row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(test_row, text="本地测试输入框").pack(side="left")
        self.test_entry = ttk.Entry(test_row)
        self.test_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(keys, text="提示：点击上面按钮时，按键会输入到本地测试输入框，便于本机验证；"
                             "通过接口发送的按键则进入当前获得焦点的窗口。",
                  foreground="#666", wraplength=600, justify="left").pack(
            anchor="w", padx=10, pady=(0, 8))

        log_frame = ttk.LabelFrame(self.root, text="接收到的指令")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, state="disabled",
                                                  font=("Consolas", 11))
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        bar = ttk.Frame(log_frame)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="清空记录", command=self.clear_log).pack(side="right")
        ttk.Label(bar,
                  text='接口：GET /press?key=1    POST /press {"key":"1"}',
                  foreground="#666").pack(side="left")

        if IS_MAC:
            perm = ttk.Frame(self.root)
            perm.pack(fill="x", padx=12, pady=(0, 6))
            self.perm_var = tk.StringVar()
            self.perm_label = ttk.Label(perm, textvariable=self.perm_var, foreground="#b00")
            self.perm_label.pack(side="left")
            ttk.Button(perm, text="打开辅助功能设置",
                       command=open_accessibility_settings).pack(side="right")
            self.refresh_permission()
        elif not IS_WINDOWS:
            ttk.Label(self.root, text="当前系统不支持模拟按键：仅记录指令。",
                      foreground="#b00").pack(anchor="w", padx=12, pady=(0, 6))

    def refresh_permission(self):
        if not IS_MAC:
            return
        trusted = mac_accessibility_trusted()
        self.perm_var.set("已获得「辅助功能」权限，可模拟真实按键"
                          if trusted else
                          "未授权「辅助功能」：系统会丢弃按键，请点右侧按钮去勾选运行本程序的终端 / Python")
        self.perm_label.config(foreground="#0a7d32" if trusted else "#b00")
        self.root.after(3000, self.refresh_permission)

    # ------------------------------------------------------------ 服务控制
    def start_server(self):
        if self.server and self.server.running:
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("端口错误", "端口必须是数字")
            return
        server = ApiServer("0.0.0.0", port, self.queue.put)
        try:
            server.start()
        except OSError as exc:
            self.status_var.set("启动失败：%s（端口可能被占用）" % exc)
            messagebox.showerror("启动失败", "端口 %d 无法监听：\n%s" % (port, exc))
            return
        self.server = server
        self.status_var.set("运行中  http://127.0.0.1:%d   局域网可直接用本机 IP 访问" % port)
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

    def stop_server(self):
        if self.server:
            self.server.stop()
            self.server = None
        self.status_var.set("已停止")
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def toggle_topmost(self):
        self.root.attributes("-topmost", self.topmost_var.get())

    def local_press(self, key):
        # 让本地测试输入框先拿到焦点，这样按键效果能直接看到
        self.test_entry.focus_force()
        self.root.update_idletasks()
        pressed, failed, invalid, errors = press_keys([key])
        self.append_log("本地", pressed, failed, invalid, errors, "窗口按钮")

    # ------------------------------------------------------------ 日志
    def poll_queue(self):
        while True:
            try:
                event = self.queue.get_nowait()
            except queue.Empty:
                break
            self.append_log(event["source"], event["pressed"], event["failed"],
                            event["invalid"], event.get("errors", {}), event["client"])
        self.root.after(100, self.poll_queue)

    def append_log(self, source, pressed, failed, invalid, errors, client):
        stamp = datetime.now().strftime("%H:%M:%S")
        parts = []
        if pressed:
            parts.append("按键 %s" % " ".join(pressed))
        if failed:
            reason = "；".join(sorted(set(errors.values()))) if errors else ""
            parts.append("模拟失败 %s%s" % (" ".join(failed), "（%s）" % reason if reason else ""))
        if invalid:
            parts.append("无效按键 %s" % " ".join(invalid))
        line = "[%s] %s ← %s   %s\n" % (stamp, source, client, "；".join(parts) or "空指令")
        self.log_text.config(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def on_close(self):
        self.stop_server()
        self.root.destroy()


def main():
    if IS_WINDOWS:
        try:  # 高分屏下界面不模糊
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
