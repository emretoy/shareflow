#!/usr/bin/env python3
"""ShareFlow Client - Windows tarafı.

Server'dan gelen fare/klavye eventlerini Windows'ta enjekte eder.
Sol kenar algılama ile Mac'e geri dönüş sağlar.
"""

import ctypes
import socket
import ssl
import sys
import threading
import time

from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key

from clipboard import ClipboardMonitor
from config import load_config
from discovery import discover_server
from file_transfer import FileReceiver
from protocol import send_message, read_message, switch_back, clipboard_msg


# --- Windows API ---

def get_screen_size():
    """Windows ekran çözünürlüğünü al."""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


# Scancode tabanlı tuş enjeksiyonu - layout'a bağlı değil
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION),
    ]


def send_scancode(scan, key_up=False, extended=False):
    """Windows'a scancode tabanlı tuş event'i gönder."""
    flags = KEYEVENTF_SCANCODE
    if key_up:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wScan = scan
    inp.union.ki.dwFlags = flags
    try:
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    except Exception:
        pass


# Mac keycode -> Windows Scancode (Set 1)
# Fiziksel tuş pozisyonu - her iki OS'te de aynı tuşa karşılık gelir
MAC_TO_SCANCODE = {
    # Harfler
    0: 0x1E,   # A
    1: 0x1F,   # S
    2: 0x20,   # D
    3: 0x21,   # F
    4: 0x23,   # H
    5: 0x22,   # G
    6: 0x2C,   # Z
    7: 0x2D,   # X
    8: 0x2E,   # C
    9: 0x2F,   # V
    11: 0x30,  # B
    12: 0x10,  # Q
    13: 0x11,  # W
    14: 0x12,  # E
    15: 0x13,  # R
    16: 0x15,  # Y
    17: 0x14,  # T
    31: 0x18,  # O
    32: 0x16,  # U
    34: 0x17,  # I
    35: 0x19,  # P
    37: 0x26,  # L
    38: 0x24,  # J
    40: 0x25,  # K
    45: 0x31,  # N
    46: 0x32,  # M
    # Rakamlar
    18: 0x02,  # 1
    19: 0x03,  # 2
    20: 0x04,  # 3
    21: 0x05,  # 4
    22: 0x07,  # 6
    23: 0x06,  # 5
    25: 0x0A,  # 9
    26: 0x08,  # 7
    28: 0x09,  # 8
    29: 0x0B,  # 0
    # Semboller
    24: 0x0D,  # =/+
    27: 0x0C,  # -/_
    30: 0x1B,  # ]/}
    33: 0x1A,  # [/{
    39: 0x28,  # '/"
    41: 0x27,  # ;/:  (Türkçe: ş)
    42: 0x2B,  # backslash
    43: 0x33,  # ,/<  (Türkçe: ö)
    44: 0x35,  # //?
    47: 0x34,  # ./>  (Türkçe: ç)
    50: 0x29,  # `/~
    10: 0x56,  # § / < (ISO key)
}

# Extended scancode gerektiren tuşlar
MAC_EXTENDED_SCANCODE = {
    123: 0x4B,  # Left arrow
    124: 0x4D,  # Right arrow
    125: 0x50,  # Down arrow
    126: 0x48,  # Up arrow
    116: 0x49,  # Page Up
    121: 0x51,  # Page Down
    115: 0x47,  # Home
    119: 0x4F,  # End
    117: 0x53,  # Delete (forward)
}

# Normal (non-extended) özel tuşlar
MAC_SPECIAL_SCANCODE = {
    36: 0x1C,  # Enter
    48: 0x0F,  # Tab
    49: 0x39,  # Space
    51: 0x0E,  # Backspace
    53: 0x01,  # Escape
    76: 0x1C,  # Numpad Enter (extended)
    # F tuşları
    122: 0x3B,  # F1
    120: 0x3C,  # F2
    99: 0x3D,   # F3
    118: 0x3E,  # F4
    96: 0x3F,   # F5
    97: 0x40,   # F6
    98: 0x41,   # F7
    100: 0x42,  # F8
    101: 0x43,  # F9
    109: 0x44,  # F10
    103: 0x57,  # F11
    111: 0x58,  # F12
}

# Modifier tuşları scancode
MAC_MODIFIER_SCANCODE = {
    56: 0x2A,   # Left Shift
    60: 0x36,   # Right Shift
    59: 0x1D,   # Left Ctrl
    62: 0x1D,   # Right Ctrl (extended)
    58: 0x38,   # Left Alt/Option
    61: 0x38,   # Right Alt (extended)
    55: 0x1D,   # Left Cmd -> Ctrl
    54: 0x1D,   # Right Cmd -> Ctrl (extended)
}

BUTTON_MAP = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
}


class ShareFlowClient:
    def __init__(self, server_host: str):
        self.config = load_config()
        self.server_host = server_host
        self.sock = None
        self.running = True

        self.mouse = MouseController()
        self.keyboard = KeyboardController()

        # Ekran boyutu
        self.screen_w, self.screen_h = get_screen_size()
        print(f"[Client] Ekran: {self.screen_w}x{self.screen_h}")

        # Modifier durumu
        self._modifiers = {"shift": False, "ctrl": False, "alt": False, "cmd": False}

        # Kenar algılama (sol kenar -> Mac'e dön)
        self._edge_threshold = self.config["edge_threshold"]
        self._edge_enter_time = None
        self._edge_dwell = self.config["edge_dwell_time"]

        # Geçiş cooldown
        self._last_switch_time = 0
        self._switch_cooldown = 0.5


        # Clipboard
        self.clipboard = ClipboardMonitor(self._on_clipboard_change)

        # Dosya alıcı
        self.file_receiver = FileReceiver()

    def connect(self):
        """Server'a bağlan."""
        port = self.config["port"]
        print(f"[Client] Bağlanılıyor: {self.server_host}:{port}")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        if self.config["ssl_enabled"]:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self.sock = ctx.wrap_socket(self.sock)

        self.sock.connect((self.server_host, port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(0.5)
        print(f"[Client] Bağlandı!")

        # Handshake - ekran boyutunu gönder
        send_message(self.sock, {
            "type": "hello",
            "screen_width": self.screen_w,
            "screen_height": self.screen_h,
        })

        # Clipboard başlat
        if self.config["clipboard_sync"]:
            self.clipboard.start()

        # Mesajları dinle
        self._receive_loop()

    def _on_clipboard_change(self, content: str):
        """Clipboard değişince server'a gönder."""
        if self.sock:
            try:
                send_message(self.sock, clipboard_msg(content))
                print(f"[Clipboard] Gönderildi ({len(content)} karakter)")
            except Exception as e:
                print(f"[Clipboard] Gönderme hatası: {e}")

    def _receive_loop(self):
        """Server'dan gelen mesajları işle."""
        print("[Client] Mesaj bekleniyor...")
        while self.running:
            try:
                msg = read_message(self.sock)
                if msg is None:
                    print("[Client] Bağlantı kapandı")
                    break
                self._handle_message(msg)
            except socket.timeout:
                continue
            except KeyboardInterrupt:
                print("\n[Client] Kapatılıyor...")
                break
            except Exception as e:
                print(f"[Client] Hata: {e}")
                break

        self.running = False
        self.clipboard.stop()
        if self.sock:
            self.sock.close()

    def _handle_message(self, msg: dict):
        """Gelen mesajı türüne göre işle."""
        msg_type = msg.get("type")

        if msg_type == "mouse_delta":
            self._handle_mouse_delta(msg)
        elif msg_type == "mouse_move":
            self._handle_mouse_move(msg)
        elif msg_type == "mouse_click":
            self._handle_mouse_click(msg)
        elif msg_type == "mouse_scroll":
            self._handle_mouse_scroll(msg)
        elif msg_type == "key":
            self._handle_key(msg)
        elif msg_type == "modifiers":
            self._handle_modifiers(msg)
        elif msg_type == "clipboard":
            self._handle_clipboard(msg)
        elif msg_type in ("file_start", "file_chunk", "file_end"):
            self.file_receiver.handle_message(msg)

    def _handle_mouse_delta(self, msg: dict):
        """Delta tabanlı fare hareketi."""
        dx, dy = msg["dx"], msg["dy"]
        cx, cy = self.mouse.position
        new_x = max(0, min(self.screen_w - 1, cx + dx))
        new_y = max(0, min(self.screen_h - 1, cy + dy))
        self.mouse.position = (new_x, new_y)

        # Sol kenar algılama
        now = time.time()
        if new_x <= self._edge_threshold:
            # Cooldown kontrolü
            if now - self._last_switch_time < self._switch_cooldown:
                return
            if self._edge_enter_time is None:
                self._edge_enter_time = now
            elif now - self._edge_enter_time >= self._edge_dwell:
                self._last_switch_time = now
                self._switch_back_to_server(new_y)
                self._edge_enter_time = None
        else:
            self._edge_enter_time = None

    def _handle_mouse_move(self, msg: dict):
        """Mutlak fare hareketi."""
        self.mouse.position = (msg["x"], msg["y"])

    def _handle_mouse_click(self, msg: dict):
        """Fare tıklama."""
        button = BUTTON_MAP.get(msg["button"], Button.left)
        if msg["action"] == "press":
            self.mouse.press(button)
        else:
            self.mouse.release(button)

    def _handle_mouse_scroll(self, msg: dict):
        """Fare scroll."""
        self.mouse.scroll(msg.get("dx", 0), msg.get("dy", 0))

    def _handle_key(self, msg: dict):
        """Tuş basma/bırakma - scancode tabanlı."""
        action = msg["action"]
        keycode = msg.get("keycode")
        key_up = action == "release"

        if keycode is None:
            return

        # Normal tuşlar (harfler, rakamlar, semboller)
        if keycode in MAC_TO_SCANCODE:
            send_scancode(MAC_TO_SCANCODE[keycode], key_up=key_up)
        # Özel tuşlar (enter, tab, space, F tuşları vb.)
        elif keycode in MAC_SPECIAL_SCANCODE:
            send_scancode(MAC_SPECIAL_SCANCODE[keycode], key_up=key_up)
        # Extended tuşlar (ok tuşları, home, end vb.)
        elif keycode in MAC_EXTENDED_SCANCODE:
            send_scancode(MAC_EXTENDED_SCANCODE[keycode], key_up=key_up, extended=True)
        # Modifier tuşları
        elif keycode in MAC_MODIFIER_SCANCODE:
            extended = keycode in (62, 61, 54)  # Sağ ctrl, sağ alt, sağ cmd
            send_scancode(MAC_MODIFIER_SCANCODE[keycode], key_up=key_up, extended=extended)
        else:
            print(f"[Client] Bilinmeyen keycode: {keycode}")

    def _handle_modifiers(self, msg: dict):
        """Modifier durum güncelleme - scancode tabanlı."""
        new_state = msg.get("state", {})

        # Modifier -> scancode + extended flag
        mod_mapping = {
            "shift": (0x2A, False),   # Left Shift
            "ctrl":  (0x1D, False),   # Left Ctrl
            "alt":   (0x38, False),   # Left Alt
            "cmd":   (0x1D, False),   # Cmd -> Ctrl
        }

        for mod, pressed in new_state.items():
            was_pressed = self._modifiers.get(mod, False)
            mapping = mod_mapping.get(mod)
            if mapping is None:
                continue
            scan, extended = mapping
            if pressed and not was_pressed:
                send_scancode(scan, key_up=False, extended=extended)
            elif not pressed and was_pressed:
                send_scancode(scan, key_up=True, extended=extended)

        self._modifiers = new_state

    def _handle_clipboard(self, msg: dict):
        """Clipboard güncelleme."""
        content = msg.get("content", "")
        self.clipboard.set_clipboard(content)
        print(f"[Clipboard] Alındı ({len(content)} karakter)")

    def _switch_back_to_server(self, y: int):
        """Kontrolü Mac'e geri ver."""
        print("[Client] -> Mac'e dönüş")
        y_ratio = y / self.screen_h if self.screen_h > 0 else 0.5
        try:
            send_message(self.sock, {**switch_back(), "y_ratio": y_ratio})
        except Exception as e:
            print(f"[Client] Geri dönüş hatası: {e}")

        # Tüm modifier'ları bırak
        send_scancode(0x2A, key_up=True)   # Shift
        send_scancode(0x1D, key_up=True)   # Ctrl
        send_scancode(0x38, key_up=True)   # Alt
        self._modifiers = {"shift": False, "ctrl": False, "alt": False, "cmd": False}


def main():
    print("=" * 50)
    print("  ShareFlow Client (Windows)")
    print("=" * 50)

    if len(sys.argv) >= 2:
        server_host = sys.argv[1]
    else:
        # Otomatik keşif
        print("[Client] Server IP verilmedi, otomatik aranıyor...")
        result = discover_server(timeout=30)
        if result is None:
            print("[Client] Server bulunamadı. IP ile deneyin:")
            print("  python client.py <server_ip>")
            sys.exit(1)
        server_host = result["host"]

    while True:
        client = ShareFlowClient(server_host)
        try:
            client.connect()
        except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
            print(f"[Client] Bağlantı hatası: {e}")
        print("[Client] 5 saniye sonra tekrar bağlanılacak...")
        time.sleep(5)


if __name__ == "__main__":
    main()
