from pathlib import Path

from fastapi.testclient import TestClient

from fisoku import studio_setup
from fisoku.server import Worker, create_app
from fisoku.store import Store


def test_find_studio_default_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(studio_setup, "_registry_dirs", lambda: [])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert studio_setup.find_studio() is None
    exe = tmp_path / "Programs" / "AugeLab Studio" / studio_setup.EXE_NAME
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert studio_setup.find_studio() == exe


def test_find_studio_registry_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    other = tmp_path / "D" / "Studio"
    other.mkdir(parents=True)
    (other / studio_setup.EXE_NAME).write_bytes(b"MZ")
    monkeypatch.setattr(studio_setup, "_registry_dirs", lambda: [other])
    assert studio_setup.find_studio() == other / studio_setup.EXE_NAME


def test_existing_installation_never_starts_an_installer(tmp_path, monkeypatch):
    setup = studio_setup.StudioSetup()
    setup.installer = tmp_path / "cached-installer.exe"
    setup.installer.touch()
    monkeypatch.setattr(studio_setup, "find_studio", lambda: tmp_path / studio_setup.EXE_NAME)
    monkeypatch.setattr(studio_setup, "studio_running", lambda: False)

    assert setup.state()["installed"] is True
    assert setup.start_install() is False
    assert setup.open_installer() is False
    assert setup.phase == "idle"


class FakeSetup(studio_setup.StudioSetup):
    def __init__(self):
        super().__init__()
        self.started = 0

    def state(self):
        return {"supported": True, "installed": False, "running": False, "phase": self.phase,
                "downloaded": 0, "total": 0, "error": ""}

    def start_install(self):
        self.started += 1
        return True


def test_setup_endpoints_local_only(tmp_path):
    store = Store(tmp_path)
    worker = Worker(store, "http://127.0.0.1:9/mcp")  # never started
    setup = FakeSetup()
    app = create_app(store, worker, 8787, setup)
    local = TestClient(app, client=("127.0.0.1", 5000))
    s = local.get("/api/setup").json()
    assert s["installed"] is False and s["connected"] is False
    assert local.post("/api/setup/install").json() == {"started": True}
    assert setup.started == 1
    phone = TestClient(app, client=("192.168.1.20", 5000))
    assert phone.get("/api/setup").status_code == 403
    assert phone.post("/api/setup/install").status_code == 403
    assert setup.started == 1
