"""ShareFlow - Dosya transfer modülü."""

import base64
import os

from protocol import send_message, file_start, file_chunk, file_end


def send_file(sock, filepath: str, chunk_size: int = 65536):
    """Dosyayı parçalı olarak karşı tarafa gönder."""
    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)

    print(f"[Dosya] Gönderiliyor: {filename} ({filesize} bytes)")
    send_message(sock, file_start(filename, filesize))

    sent = 0
    with open(filepath, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            encoded = base64.b64encode(data).decode("ascii")
            send_message(sock, file_chunk(encoded))
            sent += len(data)
            pct = int(sent / filesize * 100) if filesize > 0 else 100
            print(f"\r[Dosya] {pct}%", end="", flush=True)

    send_message(sock, file_end())
    print(f"\n[Dosya] Gönderildi: {filename}")


class FileReceiver:
    """Gelen dosya parçalarını birleştirip kaydeder."""

    def __init__(self, save_dir: str = None):
        self.save_dir = save_dir or os.path.expanduser("~/Downloads")
        self._current_file = None
        self._current_handle = None
        self._received = 0
        self._total = 0

    def handle_message(self, msg: dict) -> str | None:
        """Dosya mesajını işle. Tamamlanınca dosya yolunu döner."""
        msg_type = msg["type"]

        if msg_type == "file_start":
            name = msg["name"]
            self._total = msg["size"]
            self._received = 0
            # Aynı isimde dosya varsa numara ekle
            path = os.path.join(self.save_dir, name)
            path = self._unique_path(path)
            self._current_file = path
            self._current_handle = open(path, "wb")
            print(f"[Dosya] Alınıyor: {name} ({self._total} bytes)")
            return None

        elif msg_type == "file_chunk":
            if self._current_handle:
                data = base64.b64decode(msg["data"])
                self._current_handle.write(data)
                self._received += len(data)
                pct = int(self._received / self._total * 100) if self._total > 0 else 100
                print(f"\r[Dosya] {pct}%", end="", flush=True)
            return None

        elif msg_type == "file_end":
            if self._current_handle:
                self._current_handle.close()
                self._current_handle = None
                path = self._current_file
                self._current_file = None
                print(f"\n[Dosya] Kaydedildi: {path}")
                return path
            return None

    def _unique_path(self, path: str) -> str:
        """Dosya zaten varsa benzersiz isim üret."""
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 1
        while os.path.exists(f"{base}_{i}{ext}"):
            i += 1
        return f"{base}_{i}{ext}"
