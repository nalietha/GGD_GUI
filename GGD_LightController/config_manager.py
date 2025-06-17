from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import json
import threading
import time

class AppSettingsManager(FileSystemEventHandler):
    def __init__(self, path="appsettings.json"):
        self.path = path
        self.data = {}
        self.load_settings()
        self.lock = threading.Lock()
        self._watch()

    def load_settings(self):
        try:
            with open(self.path, "r") as f:
                self.data = json.load(f)
        except Exception as e:
            print(f"[Config] Failed to load: {e}")

    def get(self, key_path, default=None):
        with self.lock:
            data = self.data
            for k in key_path.split("."):
                data = data.get(k, {})
            return data if data != {} else default

    def on_modified(self, event):
        if event.src_path.endswith(self.path):
            print("[Config] appsettings.json updated, reloading...")
            with self.lock:
                self.load_settings()

    def _watch(self):
        observer = Observer()
        observer.schedule(self, path='.', recursive=False)
        observer.daemon = True
        observer.start()


def read_brightness(filepath="appsettings.json", default=64):
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
            return int(data.get("GGDDisplay", {}).get("Brightness", default))
    except Exception as e:
        print(f"Error reading appsettings.json: {e}")
        return default
