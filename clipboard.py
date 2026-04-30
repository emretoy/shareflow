"""ShareFlow - Clipboard senkronizasyon modülü."""

import threading
import time
import pyperclip


class ClipboardMonitor:
    """Clipboard değişikliklerini izler ve callback çağırır."""

    def __init__(self, on_change, poll_interval=0.5):
        self._on_change = on_change
        self._poll_interval = poll_interval
        self._last_content = ""
        self._running = False
        self._thread = None
        self._ignore_next = False  # Kendi yazdığımız içeriği yoksay

    def start(self):
        """İzlemeyi başlat."""
        self._running = True
        try:
            self._last_content = pyperclip.paste()
        except Exception:
            self._last_content = ""
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """İzlemeyi durdur."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def set_clipboard(self, content: str):
        """Clipboard'a yaz (uzak taraftan gelen içerik)."""
        self._ignore_next = True
        self._last_content = content
        try:
            pyperclip.copy(content)
        except Exception as e:
            print(f"[Clipboard] Yazma hatası: {e}")

    def _poll_loop(self):
        while self._running:
            try:
                current = pyperclip.paste()
                if current != self._last_content:
                    if self._ignore_next:
                        self._ignore_next = False
                    else:
                        self._last_content = current
                        self._on_change(current)
            except Exception:
                pass
            time.sleep(self._poll_interval)
