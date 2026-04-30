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
from pynput.keyboard import Controller as KeyboardController, Key, KeyCode

from clipboard import ClipboardMonitor
from config import load_config
from discovery import discover_server
from file_transfer import FileReceiver
from protocol import send_message, read_message, switch_back, clipboard_msg


# Windows API - ekran boyutu
def get_screen_size():
    """Windows ekran çözünürlüğünü al."""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


# Mac keycode -> Windows Virtual Key code eşlemesi
# Fiziksel tuş pozisyonuna göre - klavye diline bağlı değil
MAC_TO_WIN_VK = {
    # Harfler
    0: 0x41,   # A
    1: 0x53,   # S
    2: 0x44,   # D
    3: 0x46,   # F
    4: 0x48,   # H
    5: 0x47,   # G
    6: 0x5A,   # Z
    7: 0x58,   # X
    8: 0x43,   # C
    9: 0x56,   # V
    11: 0x42,  # B
    12: 0x51,  # Q
    13: 0x57,  # W
    14: 0x45,  # E
    15: 0x52,  # R
    16: 0x59,  # Y
    17: 0x54,  # T
    31: 0x4F,  # O
    32: 0x55,  # U
    34: 0x49,  # I
    35: 0x50,  # P
    37: 0x4C,  # L
    38: 0x4A,  # J
    40: 0x4B,  # K
    45: 0x4E,  # N
    46: 0x4D,  # M
    # Rakamlar
    18: 0x31,  # 1
    19: 0x32,  # 2
    20: 0x33,  # 3
    21: 0x34,  # 4
    22: 0x36,  # 6
    23: 0x35,  # 5
    25: 0x39,  # 9
    26: 0x37,  # 7
    28: 0x38,  # 8
    29: 0x30,  # 0
    # Semboller
    24: 0xBB,  # =
    27: 0xBD,  # -
    30: 0xDD,  # ]
    33: 0xDB,  # [
    39: 0xDE,  # '
    41: 0xBA,  # ;
    42: 0xDC,  # backslash
    43: 0xBC,  # ,
    44: 0xBF,  # /
    47: 0xBE,  # .
    50: 0xC0,  # `
}

# Mac keycode -> pynput özel tuş eşlemesi
MAC_SPECIAL_KEYS = {
    36: Key.enter,
    48: Key.tab,
    49: Key.space,
    51: Key.backspace,
    53: Key.esc,
    76: Key.enter,
    117: Key.delete,
    123: Key.left,
    124: Key.right,
    125: Key.down,
    126: Key.up,
    116: Key.page_up,
    121: Key.page_down,
    115: Key.home,
    119: Key.end,
    122: Key.f1, 120: Key.f2, 99: Key.f3, 118: Key.f4,
    96: Key.f5, 97: Key.f6, 98: Key.f7, 100: Key.f8,
    101: Key.f9, 109: Key.f10, 103: Key.f11, 111: Key.f12,
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
        """Tuş basma/bırakma - keycode tabanlı."""
        action = msg["action"]
        keycode = msg.get("keycode")

        if keycode is None:
            return

        # Özel tuşlar (enter, tab, ok tuşları vb.)
        if keycode in MAC_SPECIAL_KEYS:
            key_obj = MAC_SPECIAL_KEYS[keycode]
        # Normal tuşlar - VK code ile
        elif keycode in MAC_TO_WIN_VK:
            vk = MAC_TO_WIN_VK[keycode]
            key_obj = KeyCode.from_vk(vk)
        else:
            print(f"[Client] Bilinmeyen keycode: {keycode}")
            return

        try:
            if action == "press":
                self.keyboard.press(key_obj)
            else:
                self.keyboard.release(key_obj)
        except Exception as e:
            print(f"[Client] Tuş hatası: keycode {keycode} -> {e}")

    def _handle_modifiers(self, msg: dict):
        """Modifier durum güncelleme."""
        new_state = msg.get("state", {})

        # cmd -> ctrl mapping
        key_mapping = {
            "shift": Key.shift,
            "ctrl": Key.ctrl,
            "alt": Key.alt,
            "cmd": Key.ctrl,  # Mac cmd = Win ctrl
        }

        for mod, pressed in new_state.items():
            was_pressed = self._modifiers.get(mod, False)
            if pressed and not was_pressed:
                win_key = key_mapping.get(mod)
                if win_key:
                    self.keyboard.press(win_key)
            elif not pressed and was_pressed:
                win_key = key_mapping.get(mod)
                if win_key:
                    self.keyboard.release(win_key)

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
        for key in (Key.shift, Key.ctrl, Key.alt):
            try:
                self.keyboard.release(key)
            except Exception:
                pass
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
