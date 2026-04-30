#!/usr/bin/env python3
"""ShareFlow Server - Mac tarafı.

Fare/klavye eventlerini yakalar ve Windows client'a gönderir.
Ekran kenarı algılama ile otomatik geçiş yapar.
"""

import socket
import ssl
import sys
import threading
import time

import Quartz
from pynput import keyboard, mouse

from clipboard import ClipboardMonitor
from config import load_config
from discovery import DiscoveryBroadcaster
from file_transfer import send_file, FileReceiver
from protocol import (
    send_message, read_message,
    mouse_move, mouse_click, mouse_scroll,
    key_event, clipboard_msg,
)


class ShareFlowServer:
    def __init__(self):
        self.config = load_config()
        self.client_sock = None
        self.active = False  # True = eventler Windows'a gidiyor
        self.running = True
        self.lock = threading.Lock()

        # Ekran boyutu
        main_display = Quartz.CGMainDisplayID()
        self.screen_w = Quartz.CGDisplayPixelsWide(main_display)
        self.screen_h = Quartz.CGDisplayPixelsHigh(main_display)
        print(f"[Server] Ekran: {self.screen_w}x{self.screen_h}")

        # Client ekran boyutu (bağlandığında güncellenir)
        self.client_screen_w = self.config["screen_width"]
        self.client_screen_h = self.config["screen_height"]

        # Clipboard
        self.clipboard = ClipboardMonitor(self._on_clipboard_change)

        # Dosya alıcı
        self.file_receiver = FileReceiver()

        # Son fare pozisyonu (geçiş hesabı için)
        self._last_y_ratio = 0.5

        # Kenar algılama
        self._edge_enter_time = None
        self._edge_threshold = self.config["edge_threshold"]
        self._edge_dwell = self.config["edge_dwell_time"]

        # Geçiş cooldown - bouncing önleme
        self._last_switch_time = 0
        self._switch_cooldown = 0.5  # Geçişler arası minimum süre

        # Mouse throttle - event yığılmasını önle
        self._last_mouse_send = 0
        self._mouse_interval = 0.005  # ~200Hz max
        self._pending_dx = 0
        self._pending_dy = 0

        # Ctrl+Ctrl çift basma ile geçiş
        self._ctrl_tap_time = 0       # Son ctrl release zamanı
        self._ctrl_tap_window = 0.4   # İki ctrl arası max süre (saniye)
        self._ctrl_was_pressed = False

    def start(self):
        """Sunucuyu başlat."""
        port = self.config["port"]
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((self.config["host"], port))
        self.srv.listen(1)
        print(f"[Server] Port {port} üzerinde bekleniyor...")

        # Auto-discovery broadcast başlat
        self.discovery = DiscoveryBroadcaster(port)
        self.discovery.start()

        # İlk bağlantıyı kabul et
        self._accept_client()

        # CGEvent tap ile input yakalama (ana thread'de çalışır)
        self._start_event_tap()

    def _accept_client(self):
        """Client bağlantısını kabul et."""
        conn, addr = self.srv.accept()
        print(f"[Server] Bağlantı: {addr}")

        if self.config["ssl_enabled"]:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.config["ssl_certfile"], self.config["ssl_keyfile"])
            conn = ctx.wrap_socket(conn, server_side=True)

        self.client_sock = conn
        self.active = False
        self.running = True
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(0.5)

        # Handshake - client ekran boyutunu al
        try:
            hello = read_message(conn)
            if hello and hello.get("type") == "hello":
                self.client_screen_w = hello.get("screen_width", self.client_screen_w)
                self.client_screen_h = hello.get("screen_height", self.client_screen_h)
                print(f"[Server] Client ekranı: {self.client_screen_w}x{self.client_screen_h}")
        except Exception:
            pass

        # Clipboard başlat
        if self.config["clipboard_sync"]:
            self.clipboard.start()

        # Client mesajlarını dinle
        recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        recv_thread.start()

    def _on_clipboard_change(self, content: str):
        """Clipboard değişince client'a gönder."""
        if self.client_sock:
            try:
                send_message(self.client_sock, clipboard_msg(content))
                print(f"[Clipboard] Gönderildi ({len(content)} karakter)")
            except Exception as e:
                print(f"[Clipboard] Gönderme hatası: {e}")

    def _receive_loop(self):
        """Client'tan gelen mesajları dinle."""
        while self.running:
            try:
                msg = read_message(self.client_sock)
                if msg is None:
                    print("[Server] Bağlantı kapandı, yeniden bekleniyor...")
                    self._reconnect()
                    break
                self._handle_client_msg(msg)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[Server] Alma hatası: {e}")
                self._reconnect()
                break

    def _reconnect(self):
        """Bağlantı kopunca yeniden kabul et."""
        self.active = False
        Quartz.CGAssociateMouseAndMouseCursorPosition(True)
        self.clipboard.stop()
        try:
            self.client_sock.close()
        except Exception:
            pass
        self.client_sock = None
        # Yeni bağlantı bekle (ayrı thread)
        threading.Thread(target=self._accept_client, daemon=True).start()

    def _handle_client_msg(self, msg: dict):
        """Client'tan gelen mesajı işle."""
        msg_type = msg.get("type")

        if msg_type == "switch_back":
            print("[Server] Kontrol Mac'e döndü")
            self.active = False
            self._last_switch_time = time.time()
            # Fareyi sol kenardan döndür
            y = int(self.screen_h * msg.get("y_ratio", 0.5))
            Quartz.CGWarpMouseCursorPosition((self.screen_w - 20, y))
            Quartz.CGAssociateMouseAndMouseCursorPosition(True)

        elif msg_type == "clipboard":
            content = msg.get("content", "")
            self.clipboard.set_clipboard(content)
            print(f"[Clipboard] Alındı ({len(content)} karakter)")

        elif msg_type in ("file_start", "file_chunk", "file_end"):
            self.file_receiver.handle_message(msg)

    def _start_event_tap(self):
        """CGEvent tap ile tüm input olaylarını yakala."""
        event_mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDragged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        )

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,  # Eventleri yutabilir
            event_mask,
            self._cg_event_callback,
            None,
        )

        if tap is None:
            print("[Server] HATA: Event tap oluşturulamadı!")
            print("[Server] Sistem Ayarları > Gizlilik > Erişilebilirlik izni gerekli")
            sys.exit(1)

        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)

        print("[Server] Event tap aktif.")
        print("[Server] Geçiş: Fareyi sağ kenara götür veya Ctrl+Ctrl çift bas")
        print("[Server] Çıkmak için Ctrl+C")

        try:
            Quartz.CFRunLoopRun()
        except KeyboardInterrupt:
            print("\n[Server] Kapatılıyor...")
            self.running = False
            self.discovery.stop()
            if self.client_sock:
                self.client_sock.close()
            self.clipboard.stop()

    def _cg_event_callback(self, proxy, event_type, event, refcon):
        """CGEvent callback - her input olayı için çağrılır."""
        if not self.running or not self.client_sock:
            return event

        try:
            return self._process_event(proxy, event_type, event)
        except Exception as e:
            print(f"[Server] Event hatası: {e}")
            return event

    def _process_event(self, proxy, event_type, event):
        """Event'i işle ve gerekirse client'a gönder."""
        # Ctrl+Ctrl çift basma algılama (her iki modda da çalışır)
        if event_type == Quartz.kCGEventFlagsChanged:
            flags = Quartz.CGEventGetFlags(event)
            ctrl_pressed = bool(flags & Quartz.kCGEventFlagMaskControl)
            if self._ctrl_was_pressed and not ctrl_pressed:
                # Ctrl bırakıldı
                now = time.time()
                if now - self._ctrl_tap_time <= self._ctrl_tap_window:
                    # Çift tap! Geçiş yap
                    self._ctrl_tap_time = 0
                    if now - self._last_switch_time >= self._switch_cooldown:
                        self._last_switch_time = now
                        if self.active:
                            # Windows -> Mac
                            print("[Server] Ctrl+Ctrl -> Mac'e dönüş")
                            self.active = False
                            Quartz.CGAssociateMouseAndMouseCursorPosition(True)
                            return event
                        else:
                            # Mac -> Windows
                            point = Quartz.CGEventGetLocation(event)
                            self._switch_to_client(int(point.y))
                            return None
                else:
                    self._ctrl_tap_time = now
            self._ctrl_was_pressed = ctrl_pressed

        point = Quartz.CGEventGetLocation(event)
        x, y = int(point.x), int(point.y)

        # Aktif değilken kenar algılama
        if not self.active:
            return self._check_edge_and_switch(event_type, event, x, y)

        # Aktifken eventleri client'a gönder
        return self._forward_event(event_type, event, x, y)

    def _check_edge_and_switch(self, event_type, event, x, y):
        """Kenar algılama - sağ kenara gelince Windows'a geç."""
        now = time.time()
        if event_type in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDragged):
            if x >= self.screen_w - self._edge_threshold:
                # Cooldown kontrolü
                if now - self._last_switch_time < self._switch_cooldown:
                    return event
                if self._edge_enter_time is None:
                    self._edge_enter_time = now
                elif now - self._edge_enter_time >= self._edge_dwell:
                    # Windows'a geçiş!
                    self._edge_enter_time = None
                    self._last_y_ratio = y / self.screen_h
                    self._last_switch_time = now
                    self._switch_to_client(y)
            else:
                self._edge_enter_time = None
        return event  # Event'i yutma

    def _switch_to_client(self, y: int):
        """Windows client'a geçiş yap."""
        print("[Server] -> Windows'a geçiş")
        self.active = True
        # Fareyi Mac'te gizle/kilitle
        Quartz.CGAssociateMouseAndMouseCursorPosition(False)
        # Fareyi ekranın ortasına taşı ki kenarda takılmasın
        Quartz.CGWarpMouseCursorPosition((self.screen_w // 2, self.screen_h // 2))

    def _forward_event(self, event_type, event, x, y):
        """Aktif moddayken event'i client'a ilet."""
        try:
            if event_type in (Quartz.kCGEventMouseMoved,
                              Quartz.kCGEventLeftMouseDragged,
                              Quartz.kCGEventRightMouseDragged):
                # Delta biriktir ve throttle ile gönder
                dx = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaX)
                dy = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaY)
                self._pending_dx += dx
                self._pending_dy += dy
                now = time.time()
                if now - self._last_mouse_send >= self._mouse_interval:
                    send_message(self.client_sock, {
                        "type": "mouse_delta",
                        "dx": self._pending_dx,
                        "dy": self._pending_dy,
                    })
                    self._pending_dx = 0
                    self._pending_dy = 0
                    self._last_mouse_send = now

            elif event_type == Quartz.kCGEventLeftMouseDown:
                send_message(self.client_sock, mouse_click("left", "press"))

            elif event_type == Quartz.kCGEventLeftMouseUp:
                send_message(self.client_sock, mouse_click("left", "release"))

            elif event_type == Quartz.kCGEventRightMouseDown:
                send_message(self.client_sock, mouse_click("right", "press"))

            elif event_type == Quartz.kCGEventRightMouseUp:
                send_message(self.client_sock, mouse_click("right", "release"))

            elif event_type == Quartz.kCGEventScrollWheel:
                dy = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGScrollWheelEventDeltaAxis1)
                dx = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGScrollWheelEventDeltaAxis2)
                send_message(self.client_sock, mouse_scroll(dx, dy))

            elif event_type == Quartz.kCGEventKeyDown:
                keycode = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                chars = self._get_key_string(event, keycode)
                send_message(self.client_sock, key_event(chars, "press"))

            elif event_type == Quartz.kCGEventKeyUp:
                keycode = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                chars = self._get_key_string(event, keycode)
                send_message(self.client_sock, key_event(chars, "release"))

            elif event_type == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event)
                self._handle_modifier_change(flags)

        except (BrokenPipeError, OSError):
            print("[Server] Bağlantı koptu")
            self.active = False
            self.running = False
            return event

        # Aktifken event'i yut (Mac'te işlenmesin)
        return None

    def _get_key_string(self, event, keycode: int) -> str:
        """CGEvent'ten tuş adını çıkar. Keycode tabanlı - klavye diline bağlı değil."""
        # Keycode -> fiziksel tuş eşlemesi (Mac keycode -> karakter)
        KEYCODE_MAP = {
            # Özel tuşlar
            36: "enter", 48: "tab", 49: "space", 51: "backspace",
            53: "escape", 76: "enter",
            123: "left", 124: "right", 125: "down", 126: "up",
            116: "page_up", 121: "page_down", 115: "home", 119: "end",
            117: "delete",
            122: "f1", 120: "f2", 99: "f3", 118: "f4",
            96: "f5", 97: "f6", 98: "f7", 100: "f8",
            101: "f9", 109: "f10", 103: "f11", 111: "f12",
            # Harfler (fiziksel US layout pozisyonları)
            0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g",
            6: "z", 7: "x", 8: "c", 9: "v", 11: "b",
            12: "q", 13: "w", 14: "e", 15: "r", 16: "y", 17: "t",
            31: "o", 32: "u", 34: "i", 35: "p",
            37: "l", 38: "j", 40: "k",
            45: "n", 46: "m",
            # Rakamlar
            18: "1", 19: "2", 20: "3", 21: "4", 22: "6",
            23: "5", 24: "=", 25: "9", 26: "7", 27: "-",
            28: "8", 29: "0",
            # Semboller
            30: "]", 33: "[", 39: "'", 41: ";", 42: "\\",
            43: ",", 44: "/", 47: ".",
            50: "`",
        }

        if keycode in KEYCODE_MAP:
            return KEYCODE_MAP[keycode]

        # Bilinmeyen keycode - NSEvent'ten almayı dene
        try:
            ns_event = Quartz.NSEvent.eventWithCGEvent_(event)
            if ns_event:
                chars = ns_event.charactersIgnoringModifiers()
                if chars:
                    return chars
        except Exception:
            pass
        return f"keycode_{keycode}"

    def _handle_modifier_change(self, flags: int):
        """Modifier tuş değişikliklerini gönder."""
        modifiers = {
            "shift": bool(flags & Quartz.kCGEventFlagMaskShift),
            "ctrl": bool(flags & Quartz.kCGEventFlagMaskControl),
            "alt": bool(flags & Quartz.kCGEventFlagMaskAlternate),
            "cmd": bool(flags & Quartz.kCGEventFlagMaskCommand),
        }
        send_message(self.client_sock, {"type": "modifiers", "state": modifiers})


def main():
    print("=" * 50)
    print("  ShareFlow Server (Mac)")
    print("=" * 50)

    server = ShareFlowServer()
    server.start()


if __name__ == "__main__":
    main()
