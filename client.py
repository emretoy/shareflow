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
from pynput.keyboard import Controller as KeyboardController, Key, Listener as KeyboardListener

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


# Tuş eşleme: Mac key string -> pynput Key
SPECIAL_KEYS = {
    "enter": Key.enter,
    "tab": Key.tab,
    "space": Key.space,
    "backspace": Key.backspace,
    "escape": Key.esc,
    "delete": Key.delete,
    "left": Key.left,
    "right": Key.right,
    "up": Key.up,
    "down": Key.down,
    "page_up": Key.page_up,
    "page_down": Key.page_down,
    "home": Key.home,
    "end": Key.end,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
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

        # Ctrl+Ctrl çift basma ile geçiş (Windows -> Mac)
        self._ctrl_tap_time = 0
        self._ctrl_tap_window = 0.4
        self._listening_hotkey = False

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

        # Ctrl+Ctrl hotkey dinleyici başlat
        self._start_hotkey_listener()

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
        """Tuş basma/bırakma."""
        key_str = msg["key"]
        action = msg["action"]

        # Modifier tuşları cmd -> ctrl'ye çevir (Mac -> Win)
        if key_str in ("cmd", "command"):
            key_obj = Key.ctrl
        elif key_str == "alt":
            key_obj = Key.alt
        elif key_str == "ctrl":
            key_obj = Key.ctrl
        elif key_str == "shift":
            key_obj = Key.shift
        elif key_str in SPECIAL_KEYS:
            key_obj = SPECIAL_KEYS[key_str]
        else:
            # Normal karakter
            if len(key_str) == 1:
                key_obj = key_str
            else:
                print(f"[Client] Bilinmeyen tuş: {key_str}")
                return

        try:
            if action == "press":
                self.keyboard.press(key_obj)
            else:
                self.keyboard.release(key_obj)
        except Exception as e:
            print(f"[Client] Tuş hatası: {key_str} -> {e}")

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

    def _start_hotkey_listener(self):
        """Ctrl+Ctrl çift basma dinleyicisi başlat."""
        def on_release(key):
            if key == Key.ctrl_l or key == Key.ctrl_r:
                now = time.time()
                if now - self._ctrl_tap_time <= self._ctrl_tap_window:
                    # Çift tap! Mac'e dön
                    self._ctrl_tap_time = 0
                    if now - self._last_switch_time >= self._switch_cooldown:
                        self._last_switch_time = now
                        cx, cy = self.mouse.position
                        self._switch_back_to_server(cy)
                else:
                    self._ctrl_tap_time = now

        listener = KeyboardListener(on_release=on_release)
        listener.daemon = True
        listener.start()

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
