"""Find, download, install and start AugeLab Studio when this PC does not have it (Windows).

The desk page turns this into a step-by-step wizard:
1. download AugeLab's official installer,
2. install the Microsoft Visual C++ runtime if it is missing (downloaded from Microsoft,
   signature checked),
3. run AugeLab's installer in its silent mode into the standard per-user folder,
4. start Studio.
If the silent install fails, the person can open the installer's own window instead.
Fisoku never touches the licence: the person enters it in Studio.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

INSTALLER_URL = "https://studio-desktop-application.s3.eu-central-1.amazonaws.com/AugeLab%20Ai%20Studio.exe"
VC_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
EXE_NAME = "AugeLab AI Studio.exe"
WINDOWS = sys.platform == "win32"


def default_install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "AugeLab Studio"


def _registry_dirs() -> list[Path]:
    """Install folders from the Windows "Apps & features" entries whose name mentions AugeLab."""
    if not WINDOWS:
        return []
    import winreg

    found: list[Path] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for root in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                key = winreg.OpenKey(hive, root)
            except OSError:
                continue
            with key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            if "augelab" not in str(winreg.QueryValueEx(sub, "DisplayName")[0]).lower():
                                continue
                            for name in ("InstallLocation", "DisplayIcon"):
                                try:
                                    val = str(winreg.QueryValueEx(sub, name)[0]).split(",")[0].strip('"')
                                except OSError:
                                    continue
                                if val:
                                    p = Path(val)
                                    found.append(p if not p.suffix else p.parent)
                    except OSError:
                        continue
    return found


def find_studio() -> Path | None:
    """Path of the installed Studio executable, or None."""
    for folder in [*_registry_dirs(), default_install_dir()]:
        exe = folder / EXE_NAME
        if exe.is_file():
            return exe
    return None


def registered_studio() -> Path | None:
    """Studio as listed in "Apps & features"; the installer writes that entry when it is done."""
    for folder in _registry_dirs():
        exe = folder / EXE_NAME
        if exe.is_file():
            return exe
    return None


def studio_running() -> bool:
    if not WINDOWS:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return EXE_NAME.lower() in out.lower()


def vc_runtime_installed() -> bool:
    """Microsoft Visual C++ 2015-2022 runtime (x64), which Studio needs."""
    if not WINDOWS:
        return True
    import winreg

    for path in (r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
                 r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                if winreg.QueryValueEx(key, "Installed")[0] == 1:
                    return True
        except OSError:
            continue
    return False


def signed_by_microsoft(path: Path) -> bool:
    """Windows' own signature check (Authenticode), via PowerShell."""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:FISOKU_FILE; "
              "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'O=Microsoft Corporation') { 'ok' }")
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ, "FISOKU_FILE": str(path)},
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return out.strip() == "ok"


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


def run_program(exe: Path, params: str = "", show: bool = True, wait: bool = True) -> int:
    """Start a program the way Explorer would (a UAC prompt can appear) and optionally wait.

    Returns the exit code (0 when not waiting). Raises OSError when it could not start,
    e.g. the permission prompt was refused (winerror 1223).
    """
    if not WINDOWS:
        raise OSError("Yalnızca Windows'ta desteklenir.")
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 if wait else 0  # SEE_MASK_NOCLOSEPROCESS: keep a handle to wait on
    info.lpVerb = "open"
    info.lpFile = str(exe)
    info.lpParameters = params or None
    info.lpDirectory = str(exe.parent)
    info.nShow = 1 if show else 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError()
    if not wait or not info.hProcess:
        return 0
    k32 = ctypes.windll.kernel32
    k32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    k32.CloseHandle(info.hProcess)
    return code.value


class StudioSetup:
    """Background download + install, polled by the desk page."""

    def __init__(self, installer_url: str = INSTALLER_URL) -> None:
        self.url = installer_url
        self.lock = threading.Lock()
        self.phase = "idle"  # idle | downloading | vcredist | installing | manual | error
        self.done_bytes = 0
        self.total_bytes = 0
        self.error = ""
        self.what = ""
        self.started = 0.0
        self.folder = Path(tempfile.gettempdir()) / "Fisoku"
        self.installer = self.folder / "AugeLab-Studio-Setup.exe"

    def state(self) -> dict:
        exe = find_studio()
        return {
            "supported": WINDOWS,
            "installed": bool(exe),
            "running": studio_running() if exe else False,
            "phase": self.phase,
            "downloaded": self.done_bytes,
            "total": self.total_bytes,
            "what": self.what,
            "elapsed": round(time.monotonic() - self.started) if self.started else 0,
            "error": self.error,
            "can_open_installer": self.installer.is_file(),
        }

    def _busy(self) -> bool:
        return self.phase in ("downloading", "vcredist", "installing", "manual")

    def start_install(self) -> bool:
        with self.lock:
            if self._busy() or find_studio():
                return False
            self.phase, self.error, self.done_bytes, self.total_bytes = "downloading", "", 0, 0
        threading.Thread(target=self._install, daemon=True, name="fisoku-setup").start()
        return True

    def open_installer(self) -> bool:
        """Fallback: AugeLab's installer with its own window; Fisoku waits for the result."""
        with self.lock:
            if self._busy() or find_studio() or not self.installer.is_file():
                return False
            self.phase, self.error = "manual", ""
        threading.Thread(target=self._manual, daemon=True, name="fisoku-setup").start()
        return True

    def _fail(self, exc: Exception) -> None:
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 1223:
            self.error = "Windows izni verilmedi. Tekrar deneyip izin isteğine Evet deyin."
        else:
            self.error = str(exc)
        self.phase = "error"

    def _install(self) -> None:
        try:
            if not (self.installer.is_file() and self.installer.stat().st_size > 5_000_000):
                self._download(self.url, self.installer, "AugeLab Studio")
            if not vc_runtime_installed():
                self.phase = "vcredist"
                vc = self.folder / "vc_redist.x64.exe"
                self._download(VC_URL, vc, "Visual C++ bileşeni")
                if not signed_by_microsoft(vc):
                    vc.unlink(missing_ok=True)
                    raise RuntimeError("Visual C++ bileşeninin Microsoft imzası doğrulanamadı; kurulmadı.")
                code = run_program(vc, "/install /passive /norestart")
                if code not in (0, 1638, 3010) or not vc_runtime_installed():
                    raise RuntimeError(f"Visual C++ bileşeni kurulamadı (kod {code}).")
            self.phase, self.started = "installing", time.monotonic()
            target = default_install_dir()
            code = run_program(self.installer, f'--silent --install-dir "{target}" --start-menu-shortcut', show=False)
            if code != 0 and not find_studio():
                raise RuntimeError(
                    f"AugeLab Studio kurulamadı (kod {code}). Kurulum penceresini açıp elle kurabilirsiniz."
                )
            self._wait_installed(minutes=20)
            self._finish()
        except Exception as exc:
            self._fail(exc)

    def _manual(self) -> None:
        try:
            self.started = time.monotonic()
            run_program(self.installer, wait=False)
            self._wait_installed(minutes=60)
            self._finish()
        except Exception as exc:
            self._fail(exc)

    def _wait_installed(self, minutes: int) -> None:
        """Until Studio is listed in "Apps & features" or its program file stopped changing."""
        deadline = time.monotonic() + minutes * 60
        last, stable_since = None, time.monotonic()
        while not registered_studio():
            exe = default_install_dir() / EXE_NAME
            mark = (exe.stat().st_size, exe.stat().st_mtime) if exe.is_file() else None
            if mark != last:
                last, stable_since = mark, time.monotonic()
            elif mark and time.monotonic() - stable_since > 60:
                return
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "AugeLab Studio kurulumu tamamlanmadı. Kurulum penceresini açıp elle kurabilirsiniz."
                )
            time.sleep(2)

    def _finish(self) -> None:
        time.sleep(5)  # let the installer close its files
        self.phase = "idle"
        self.launch()

    def _download(self, url: str, dest: Path, what: str) -> None:
        # Standard library only: Fisoku.exe bundles nothing else for this.
        import urllib.error
        import urllib.request

        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        self.done_bytes = self.total_bytes = 0
        self.what = what
        req = urllib.request.Request(url, headers={"User-Agent": "Fisoku"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                self.total_bytes = int(r.headers.get("Content-Length") or 0)
                while chunk := r.read(1 << 16):
                    f.write(chunk)
                    self.done_bytes += len(chunk)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{what} indirilemedi (HTTP {exc.code}).") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"{what} indirilemedi: internet bağlantısını kontrol edin ({exc}).") from exc
        with open(part, "rb") as f:
            head = f.read(2)
        if head != b"MZ" or part.stat().st_size < 1_000_000 or (
            self.total_bytes and part.stat().st_size != self.total_bytes
        ):
            part.unlink(missing_ok=True)
            raise RuntimeError(f"İndirilen {what} dosyası eksik ya da bozuk. Tekrar deneyin.")
        part.replace(dest)

    def launch(self) -> bool:
        exe = find_studio()
        if not exe or not WINDOWS:
            return False
        if not studio_running():
            os.startfile(str(exe))  # noqa: S606 - the installed Studio, found above
        return True
