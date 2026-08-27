from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional


APPDATA = Path(os.environ.get("APPDATA", Path.home()))
ROOT_CONFIG_FILE = APPDATA / "ru_helper_root.json"
USER_CONFIG_FILE = Path.home() / ".ru_helper_root.json"


def get_base_dir() -> Path:
    for cfg in (ROOT_CONFIG_FILE, USER_CONFIG_FILE):
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                p = data.get("base_dir")
                if p:
                    return Path(p)
            except Exception:
                pass
    return APPDATA / "ru_helper"


def set_base_dir(new_base: Path) -> Path:
    global HELPER_DIR, ZAPRET_DIR, TGPROXY_DIR
    p = Path(new_base).resolve()
    HELPER_DIR = p
    ZAPRET_DIR = HELPER_DIR / "zapret"
    TGPROXY_DIR = HELPER_DIR / "tg-ws-proxy"
    payload = json.dumps({"base_dir": str(p)}, ensure_ascii=False, indent=2)
    for cfg in (ROOT_CONFIG_FILE, USER_CONFIG_FILE):
        try:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(payload, encoding="utf-8")
        except Exception:
            pass
    return p


HELPER_DIR = get_base_dir()
ZAPRET_DIR = HELPER_DIR / "zapret"
TGPROXY_DIR = HELPER_DIR / "tg-ws-proxy"
TG_APP_DIR = APPDATA / "TgWsProxy"
TG_CONFIG_FILE = TG_APP_DIR / "config.json"
TG_LOG_FILE = TG_APP_DIR / "proxy.log"
COMSPEC = os.environ.get("ComSpec", "cmd.exe")
SC_EXE = "sc.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TG_AUTOSTART_NAME = "TgWsProxy"

DEFAULT_TG_CONFIG: Dict[str, Any] = {
    "port": 1443,
    "host": "127.0.0.1",
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
    "check_updates": True,
    "log_max_mb": 5,
    "pool_size": 4,
    "buf_kb": 256,
    "cfproxy": True,
    "cfproxy_user_domain_enabled": False,
    "cfproxy_user_domain": [],
    "cfproxy_worker_enabled": False,
    "cfproxy_worker_domain": [],
    "force_test_dc": False,
    "language": "ru",
}


def _atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _valid_secret(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{32}", value.strip()))


def load_tg_config() -> Dict[str, Any]:
    config = dict(DEFAULT_TG_CONFIG)
    if TG_CONFIG_FILE.exists():
        try:
            data = json.loads(TG_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update(data)
        except (OSError, ValueError):
            pass
    if not _valid_secret(config.get("secret")):
        config["secret"] = os.urandom(16).hex()
    else:
        config["secret"] = config["secret"].strip().lower()
    try:
        config["port"] = min(65535, max(1, int(config.get("port", 1443))))
    except (TypeError, ValueError):
        config["port"] = 1443
    try:
        config["pool_size"] = max(0, int(config.get("pool_size", 4)))
    except (TypeError, ValueError):
        config["pool_size"] = 4
    try:
        config["buf_kb"] = max(4, int(config.get("buf_kb", 256)))
    except (TypeError, ValueError):
        config["buf_kb"] = 256
    return config


def save_tg_config(config: Dict[str, Any]) -> Dict[str, Any]:
    config = dict(config)
    if not _valid_secret(config.get("secret")):
        raise ValueError("Secret должен содержать ровно 32 hex-символа")
    config["secret"] = config["secret"].strip().lower()
    try:
        config["port"] = min(65535, max(1, int(config["port"])))
    except (TypeError, ValueError) as exc:
        raise ValueError("Порт должен быть числом от 1 до 65535") from exc
    try:
        config["pool_size"] = max(0, int(config.get("pool_size", 4)))
        config["buf_kb"] = max(4, int(config.get("buf_kb", 256)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Размер буфера и пул должны быть целыми числами") from exc
    _atomic_json_write(TG_CONFIG_FILE, config)
    return config


def tg_proxy_url(config: Optional[Dict[str, Any]] = None) -> str:
    config = config or load_tg_config()
    host = str(config.get("host", "127.0.0.1"))
    if host == "0.0.0.0":
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("8.8.8.8", 80))
                host = probe.getsockname()[0]
        except OSError:
            host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"tg://proxy?server={host}&port={config.get('port', 1443)}&secret=dd{config['secret']}"


def _arch_suffixes() -> List[str]:
    machine = platform.machine().lower()
    if "arm64" in machine or "aarch64" in machine:
        return ["_windows_arm64.exe", "_windows.exe"]
    if machine in {"x86", "i386", "i686"}:
        return ["_windows_7_32bit.exe"]
    return ["_windows.exe", "_windows_7_64bit.exe"]


def select_tgproxy_asset(assets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    windows = [
        asset for asset in assets
        if str(asset.get("name", "")).lower().endswith(".exe")
        and "windows" in str(asset.get("name", "")).lower()
    ]
    for suffix in _arch_suffixes():
        for asset in windows:
            if str(asset.get("name", "")).lower().endswith(suffix.lower()):
                return asset
    return windows[0] if windows else None


def find_tg_executable(root: Path = TGPROXY_DIR) -> Optional[Path]:
    candidates = [p for p in root.glob("TgWsProxy*.exe") if p.is_file()] if root.exists() else []
    for suffix in _arch_suffixes():
        for candidate in candidates:
            if candidate.name.lower().endswith(suffix.lower()):
                return candidate
    return sorted(candidates, key=lambda p: p.name.lower())[0] if candidates else None


def _tasklist_contains(image_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            line = line.strip().lower()
            if line.startswith(f'"{image_name.lower()}"') or line.startswith(f"{image_name.lower()},"):
                return True
        return False
    except OSError:
        return False


def _is_process_running(image_name: str) -> bool:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)
                ]
            kernel32 = ctypes.windll.kernel32
            h_snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if h_snapshot == -1:
                return _tasklist_contains(image_name)
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            target = image_name.strip().lower()
            try:
                success = kernel32.Process32FirstW(h_snapshot, ctypes.byref(entry))
                while success:
                    if entry.szExeFile.lower() == target:
                        return True
                    success = kernel32.Process32NextW(h_snapshot, ctypes.byref(entry))
            finally:
                kernel32.CloseHandle(h_snapshot)
            return False
        except Exception:
            pass
    return _tasklist_contains(image_name)


def parse_strategy_args(bat_path: Path, zapret_root: Path) -> str:
    text = bat_path.read_text(encoding="utf-8", errors="replace")
    bin_dir = str(zapret_root / "bin") + "\\"
    lists_dir = str(zapret_root / "lists") + "\\"

    game_flag = zapret_root / "utils" / "game_filter.enabled"
    game_mode = "disabled"
    if game_flag.exists():
        try:
            game_mode = game_flag.read_text().strip().lower()
        except OSError:
            pass
    if game_mode == "all":
        gf_tcp = "1024-65535"
        gf_udp = "1024-65535"
    elif game_mode == "tcp":
        gf_tcp = "1024-65535"
        gf_udp = "12"
    elif game_mode == "udp":
        gf_tcp = "12"
        gf_udp = "1024-65535"
    else:
        gf_tcp = "12"
        gf_udp = "12"

    lines = []
    capture = False
    for line in text.splitlines():
        line = line.strip()
        if "winws.exe" in line.lower():
            capture = True
            line = line.split("winws.exe", 1)[1].lstrip(' "')
        if capture:
            if line.endswith("^"):
                line = line[:-1].rstrip()
                lines.append(line)
            else:
                lines.append(line)
                break

    full_args = " ".join(lines)
    full_args = full_args.replace("%BIN%", bin_dir).replace("%LISTS%", lists_dir)
    full_args = full_args.replace("%GameFilterTCP%", gf_tcp).replace("%GameFilterUDP%", gf_udp)
    full_args = full_args.replace("%GameFilter%", gf_tcp)
    full_args = full_args.replace("%~dp0bin\\", bin_dir).replace("%~dp0lists\\", lists_dir)
    return full_args.strip()


def is_port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_tg_autostart_enabled(executable: Optional[Path] = None) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, TG_AUTOSTART_NAME)
        expected = str(executable or find_tg_executable() or "").strip()
        return expected and str(value).strip().strip('"') == expected
    except (OSError, ImportError):
        return False


def set_tg_autostart(enabled: bool, executable: Optional[Path] = None) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Автозапуск TG поддерживается только в Windows")
    executable = executable or find_tg_executable()
    if enabled and not executable:
        raise FileNotFoundError("TgWsProxy_windows.exe не найден")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, TG_AUTOSTART_NAME, 0, winreg.REG_SZ, f'"{executable}"')
        else:
            try:
                winreg.DeleteValue(key, TG_AUTOSTART_NAME)
            except FileNotFoundError:
                pass


class TgWsProxyAdapter:
    name = "tg-ws-proxy"

    def __init__(self, root: Path = TGPROXY_DIR):
        self.root = root

    @property
    def executable(self) -> Optional[Path]:
        return find_tg_executable(self.root)

    def installed(self) -> bool:
        return self.executable is not None and self.executable.exists()

    @property
    def is_installed(self) -> bool:
        return self.installed()

    @property
    def config_file(self) -> Path:
        return TG_CONFIG_FILE

    @property
    def log_file(self) -> Path:
        return TG_LOG_FILE

    def is_running(self) -> bool:
        exe = self.executable
        if not exe:
            return False
        names = ["TgWsProxy.exe", "TgWsProxy_windows.exe", "TgWsProxy_windows_arm64.exe", "TgWsProxy_windows_7_64bit.exe", "TgWsProxy_windows_7_32bit.exe"]
        if exe.name not in names:
            names.insert(0, exe.name)
        return any(_is_process_running(n) for n in names)

    def status(self) -> Dict[str, Any]:
        config = load_tg_config()
        return {
            "installed": self.executable is not None,
            "running": self.is_running(),
            "port_open": is_port_open(str(config.get("host", "127.0.0.1")), int(config["port"])),
            "port": config["port"],
            "autostart": is_tg_autostart_enabled(self.executable),
            "config": self.config_file,
            "log": self.log_file,
        }

    def start(self) -> subprocess.Popen:
        executable = self.executable
        if not executable:
            raise FileNotFoundError("Сначала установите tg-ws-proxy")
        if self.is_running():
            raise RuntimeError("tg-ws-proxy уже запущен")
        return subprocess.Popen([str(executable)], cwd=str(self.root), close_fds=True, creationflags=NO_WINDOW)

    def stop(self) -> None:
        names = ["TgWsProxy.exe", "TgWsProxy_windows.exe", "TgWsProxy_windows_arm64.exe", "TgWsProxy_windows_7_64bit.exe", "TgWsProxy_windows_7_32bit.exe"]
        exe = self.executable
        if exe and exe.name not in names:
            names.insert(0, exe.name)
        for name in names:
            try:
                subprocess.run(
                    ["taskkill.exe", "/F", "/IM", name],
                    capture_output=True, text=True, check=False,
                    creationflags=NO_WINDOW,
                )
            except OSError:
                pass
        t_end = time.monotonic() + 1.5
        while time.monotonic() < t_end:
            if not self.is_running():
                return
            time.sleep(0.1)

    def open_in_telegram(self) -> None:
        url = tg_proxy_url(load_tg_config())
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill.exe", "/F", "/IM", "Telegram.exe"],
                    capture_output=True, check=False, creationflags=NO_WINDOW,
                )
                time.sleep(0.5)
            except OSError:
                pass
            try:
                os.startfile(url)
                return
            except OSError:
                pass
        if not webbrowser.open(url):
            raise RuntimeError("Не удалось открыть ссылку tg://proxy")


class ZapretAdapter:
    name = "zapret-discord-youtube"

    def __init__(self, root: Path = ZAPRET_DIR):
        self.root = root

    @property
    def service_file(self) -> Path:
        return self.root / "service.bat"

    def installed(self) -> bool:
        return self.root.exists() and (self.root / "bin" / "winws.exe").exists()

    @property
    def is_installed(self) -> bool:
        return self.installed()

    def strategies(self) -> List[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("general*.bat"), key=lambda p: p.name.lower())

    def normalize_strategy(self, value: str) -> Optional[Path]:
        if not value:
            return None
        raw_val = Path(str(value)).stem.strip().lower()
        clean_val = re.sub(r"\s+", "", raw_val)
        for path in self.strategies():
            if path.stem.lower() == raw_val:
                return path
        for path in self.strategies():
            if re.sub(r"\s+", "", path.stem.lower()) == clean_val:
                return path
        for path in self.strategies():
            if clean_val in re.sub(r"\s+", "", path.stem.lower()):
                return path
        return None

    def status(self) -> Dict[str, Any]:
        winws_running = _is_process_running("winws.exe")
        svc = self._service_status()
        is_running = winws_running or svc == "running"
        return {
            "installed": self.root.exists() and (self.root / "bin" / "winws.exe").exists(),
            "service_file": self.service_file.exists(),
            "strategy_count": len(self.strategies()),
            "service": svc,
            "winws": winws_running,
            "running": is_running,
        }

    def _service_status(self) -> str:
        try:
            result = subprocess.run([SC_EXE, "query", "zapret"], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            output = f"{result.stdout}\n{result.stderr}".lower()
            if "1060" in output or "does not exist" in output or "не установлена" in output or "не существует" in output:
                return "missing"
            if "state" in output or "состояние" in output or "running" in output:
                if "running" in output or "работает" in output or ": 4" in output or " 4 " in output:
                    return "running"
                if "stopped" in output or "остановлена" in output or ": 1" in output or " 1 " in output:
                    return "stopped"
                if "stop_pending" in output or "start_pending" in output:
                    return "pending"
            if result.returncode != 0:
                return "missing"
            return "stopped"
        except OSError:
            return "unknown"

    def patch_service(self) -> List[Path]:
        if not self.root.exists():
            return []
        from ru_helper import patch_service_bat
        return patch_service_bat(self.root)

    def run_service_manager(self) -> subprocess.CompletedProcess:
        if not self.service_file.exists():
            raise FileNotFoundError("service.bat не найден")
        self.patch_service()
        command = f'call "{self.service_file.name}"'
        return subprocess.run([COMSPEC, "/d", "/c", command], cwd=str(self.root))

    def start(self, strategy: Optional[str] = None) -> subprocess.Popen:
        path = self.normalize_strategy(strategy) if strategy else self.strategies()[0] if self.strategies() else None
        if not path:
            raise FileNotFoundError(f"Не найдена конфигурация {strategy or ''}")

        if self._service_status() == "running":
            try:
                subprocess.run(["net", "stop", "zapret"], capture_output=True, check=False, creationflags=NO_WINDOW)
            except OSError:
                pass
        if _is_process_running("winws.exe"):
            try:
                subprocess.run(["taskkill.exe", "/F", "/IM", "winws.exe"], capture_output=True, check=False, creationflags=NO_WINDOW)
                time.sleep(0.3)
            except OSError:
                pass

        bin_dir = self.root / "bin"
        winws_exe = bin_dir / "winws.exe"
        process = None

        if winws_exe.exists():
            try:
                raw_args = parse_strategy_args(path, self.root)
                if raw_args:
                    cmd_line = f'"{winws_exe}" {raw_args}'
                    process = subprocess.Popen(
                        cmd_line,
                        cwd=str(bin_dir),
                        creationflags=NO_WINDOW,
                    )
                    t_end = time.monotonic() + 2.5
                    while time.monotonic() < t_end:
                        if _is_process_running("winws.exe"):
                            return process
                        if process.poll() is not None and process.returncode != 0:
                            break
                        time.sleep(0.15)
            except Exception:
                process = None

        if not _is_process_running("winws.exe"):
            environment = os.environ.copy()
            environment["NO_UPDATE_CHECK"] = "1"
            process = subprocess.Popen(
                [COMSPEC, "/d", "/c", f'call "{path.name}"'],
                cwd=str(self.root),
                env=environment,
                creationflags=NO_WINDOW,
            )

            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if _is_process_running("winws.exe"):
                    return process
                if process.poll() is not None and process.returncode != 0:
                    break
                time.sleep(0.2)

        if _is_process_running("winws.exe"):
            return process

        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        code = process.returncode if process else None
        raise RuntimeError(
            f"Альт «{path.stem}» не запустил winws.exe "
            f"(код cmd: {code if code is not None else 'неизвестен'})"
        )

    def install_service(self, strategy: Optional[str] = None) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Установка службы поддерживается только в Windows")
        path = self.normalize_strategy(strategy) if strategy else self.strategies()[0] if self.strategies() else None
        if not path:
            raise FileNotFoundError(f"Не найдена конфигурация {strategy or ''}")

        bin_dir = self.root / "bin"
        winws_exe = bin_dir / "winws.exe"
        if not winws_exe.exists():
            raise FileNotFoundError("winws.exe не найден")

        self.stop()
        time.sleep(0.3)

        try:
            subprocess.run(["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"],
                           capture_output=True, check=False, creationflags=NO_WINDOW)
        except OSError:
            pass

        raw_args = parse_strategy_args(path, self.root)
        if not raw_args:
            raise ValueError(f"Не удалось получить аргументы из {path.name}")

        bin_path_val = f'"{winws_exe}" {raw_args}'

        try:
            subprocess.run([SC_EXE, "stop", "zapret"], capture_output=True, check=False, creationflags=NO_WINDOW)
            subprocess.run([SC_EXE, "delete", "zapret"], capture_output=True, check=False, creationflags=NO_WINDOW)
            time.sleep(0.3)
        except OSError:
            pass

        sc_cmd = f'"{SC_EXE}" create zapret binPath= "{winws_exe}" DisplayName= zapret start= auto'
        res = subprocess.run(
            sc_cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
        if res.returncode != 0 and "exists" not in (res.stdout + res.stderr).lower():
            # Fallback to PowerShell New-Service if sc.exe fails
            ps_create = (
                f'$bin = [System.IO.Path]::GetFullPath("{winws_exe}"); '
                f'New-Service -Name "zapret" -BinaryPathName $bin -DisplayName "zapret" -StartupType Automatic -ErrorAction Stop'
            )
            res_ps = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_create],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=NO_WINDOW,
            )
            if res_ps.returncode != 0 and "exists" not in (res_ps.stdout + res_ps.stderr).lower():
                err_msg = res.stderr.strip() or res.stdout.strip() or res_ps.stderr.strip() or "Не удалось создать службу zapret"
                raise RuntimeError(err_msg)

        subprocess.run(
            [SC_EXE, "description", "zapret", "Zapret DPI bypass software"],
            capture_output=True, check=False, creationflags=NO_WINDOW,
        )

        try:
            import winreg
            with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"System\CurrentControlSet\Services\zapret") as key:
                winreg.SetValueEx(key, "ImagePath", 0, winreg.REG_EXPAND_SZ, bin_path_val)
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "zapret")
                winreg.SetValueEx(key, "Description", 0, winreg.REG_SZ, "Zapret DPI bypass software")
                winreg.SetValueEx(key, "zapret-discord-youtube", 0, winreg.REG_SZ, path.stem)
        except Exception as reg_err:
            raise RuntimeError(f"Не удалось записать параметры службы в реестр: {reg_err}")

        res_start = subprocess.run(
            [SC_EXE, "start", "zapret"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )

        time.sleep(0.5)
        if self._service_status() != "running" and not _is_process_running("winws.exe"):
            raise RuntimeError(f"Служба установлена, но не запустилась: {res_start.stdout.strip() or res_start.stderr.strip()}")

    def stop(self) -> None:
        try:
            subprocess.run(["taskkill.exe", "/F", "/IM", "winws.exe"],
                           capture_output=True, check=False, creationflags=NO_WINDOW)
        except OSError:
            pass
        try:
            if self._service_status() == "running":
                subprocess.run(["net", "stop", "zapret"], capture_output=True, check=False, creationflags=NO_WINDOW)
        except OSError:
            pass
        t_end = time.monotonic() + 1.5
        while time.monotonic() < t_end:
            if not _is_process_running("winws.exe") and self._service_status() != "running":
                return
            time.sleep(0.1)


class UnifiedAdapter:
    def __init__(self, base_dir: Optional[Path] = None):
        root = Path(base_dir) if base_dir else get_base_dir()
        self.zapret = ZapretAdapter(root / "zapret")
        self.tgproxy = TgWsProxyAdapter(root / "tg-ws-proxy")

    def status(self) -> Dict[str, Dict[str, Any]]:
        return {"zapret": self.zapret.status(), "tgproxy": self.tgproxy.status()}

    def start_all(self, strategy: Optional[str] = None) -> None:
        self.zapret.start(strategy)
        try:
            self.tgproxy.start()
        except RuntimeError as exc:
            if "уже запущен" not in str(exc):
                raise

    def stop_all(self) -> None:
        self.zapret.stop()
        self.tgproxy.stop()
