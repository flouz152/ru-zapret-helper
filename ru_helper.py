import os
import sys
import time
import json
import re
import shutil
import zipfile
import threading
import subprocess
import urllib.request
import urllib.error
import webbrowser
from pathlib import Path

from project_adapters import UnifiedAdapter, get_base_dir, select_tgproxy_asset, set_base_dir, set_tg_autostart

if sys.platform == "win32":
    import ctypes
    try:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        kernel32 = ctypes.windll.kernel32
        out_handle = kernel32.GetStdHandle(-11)
        out_mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(out_handle, ctypes.byref(out_mode)):
            kernel32.SetConsoleMode(out_handle, out_mode.value | 0x0004)

        handle = kernel32.GetStdHandle(-10)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            mode.value &= ~0x0040
            mode.value &= ~0x0020
            kernel32.SetConsoleMode(handle, mode)
    except (AttributeError, OSError):
        pass

R   = "\033[91m"
Y   = "\033[93m"
DIM = "\033[2m"
RST = "\033[0m"
TXT = "\033[97m"

def _rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"

GRAD = [
    _rgb(0,   255, 65),
    _rgb(0,   220, 55),
    _rgb(0,   185, 46),
    _rgb(0,   150, 38),
    _rgb(0,   120, 30),
    _rgb(0,    90, 22),
    _rgb(0,    65, 16),
    _rgb(0,    40,  8),
]

ZAPRET_API          = "https://api.github.com/repos/Flowseal/zapret-discord-youtube/releases/latest"
ZAPRET_FALLBACK_API = "https://api.github.com/repos/bol-van/zapret-win-bundle/releases/latest"
TGPROXY_API         = "https://api.github.com/repos/Flowseal/tg-ws-proxy/releases/latest"

BASE_DIR    = Path(os.environ.get("APPDATA", ".")) / "ru_helper"
ZAPRET_DIR  = BASE_DIR / "zapret"
TGPROXY_DIR = BASE_DIR / "tg-ws-proxy"
STATE_FILE  = BASE_DIR / "state.json"
LOG_FILE    = BASE_DIR / "ru_helper.log"

ZAPRET_SVC_NAME  = "zapret"
TGPROXY_SVC_NAME = "TgWsProxy"
COMSPEC          = os.environ.get("ComSpec", "cmd.exe")
SC_EXE           = "sc.exe"
SERVICE_PATCH_MARKER = "rem ru_helper: inline config tests; Esc returns to menu"
DISCUSSIONS_URL = "https://github.com/Flowseal/zapret-discord-youtube/discussions"
PROJECTS = UnifiedAdapter()

DIAG_TARGETS = [
    ("Discord",   "https://discord.com"),
    ("YouTube",   "https://www.youtube.com"),
    ("Telegram",  "https://web.telegram.org"),
    ("GitHub",    "https://github.com"),
]


def hide_console():
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except (AttributeError, OSError):
            pass


def show_console():
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            hwnd = kernel32.GetConsoleWindow()
            if not hwnd:
                kernel32.AllocConsole()
                hwnd = kernel32.GetConsoleWindow()
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
                user32.SetForegroundWindow(hwnd)

            try:
                sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
            except OSError:
                pass
            try:
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            except OSError:
                pass
            try:
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            except OSError:
                pass

            out_handle = kernel32.GetStdHandle(-11)
            out_mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(out_handle, ctypes.byref(out_mode)):
                kernel32.SetConsoleMode(out_handle, out_mode.value | 0x0004)
        except (AttributeError, OSError):
            pass


def cls():
    os.system("cls" if sys.platform == "win32" else "clear")

def pause(msg="Нажмите Enter для продолжения..."):
    try:
        input(f"\n{DIM}{msg}{RST}")
    except EOFError:
        pass

def is_admin():
    if sys.platform == "win32":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    return os.geteuid() == 0


def ensure_admin():
    if sys.platform == "win32" and not is_admin():
        try:
            import ctypes
            if getattr(sys, "frozen", False):
                executable = sys.executable
                params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
            else:
                executable = sys.executable
                script = str(Path(__file__).resolve())
                args = [f'"{script}"'] + [f'"{arg}"' for arg in sys.argv[1:]]
                params = " ".join(args)

            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", executable, params, None, 1
            )
            if ret > 32:
                sys.exit(0)
            else:
                print(f"\n  {R}Для работы ru Helper требуются права администратора.{RST}")
                pause("Нажмите Enter для выхода...")
                sys.exit(1)
        except Exception as e:
            print(f"\n  {R}Ошибка запроса прав администратора: {e}{RST}")
            pause()
            sys.exit(1)

def load_state():
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                return state
        except Exception:
            pass
    return {"zapret_version": None, "tgproxy_version": None,
            "zapret_strategy": None, "auto_update_tg": True}

def save_state(state):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ru-helper/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())

def http_check(url, timeout=5):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ms = int((time.time() - t0) * 1000)
            return 200 <= r.status < 500, ms, str(r.status)
    except urllib.error.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        return e.code < 500, ms, str(e.code)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return False, ms, str(e)[:60]

def log(msg):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def show_recent_logs(limit=240):
    log_paths = [LOG_FILE, BASE_DIR / "ru_helper_gui.log"]
    entries = []
    full_pattern = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]\s?(.*)$")
    short_pattern = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s?(.*)$")

    for path in log_paths:
        if not path.exists():
            continue
        try:
            fallback_day = time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                match = full_pattern.match(raw.strip())
                if match:
                    day, clock, message = match.groups()
                else:
                    match = short_pattern.match(raw.strip())
                    if not match:
                        continue
                    clock, message = match.groups()
                    day = fallback_day
                entries.append((f"{day} {clock}", day, clock, message))
        except OSError:
            continue

    cls()
    for line in _grad_logo():
        print(line)
    print(f"\n  {GRAD[0]}=== Последние логи ==={RST}\n")

    if not entries:
        print(f"  {DIM}Логи пока не записаны.{RST}")
        pause()
        return

    entries.sort(key=lambda item: item[0])
    last_day = None
    for _, day, clock, message in entries[-limit:]:
        if last_day != day:
            print(f"\n  {GRAD[0]}--- {day} ---{RST}")
            last_day = day
        print(f"  {DIM}{clock}{RST} {message}")

    print(f"\n  {DIM}Источники: {LOG_FILE} и {BASE_DIR / 'ru_helper_gui.log'}{RST}")
    pause()

def download_file(url, dest: Path, label=""):
    req = urllib.request.Request(url, headers={"User-Agent": "ru-helper/1.0"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            try:
                total = int(r.headers.get("Content-Length", 0) or 0)
            except (TypeError, ValueError):
                total = 0
            done  = 0
            chunk = 65536
            with open(partial, "wb") as f:
                while True:
                    buf = r.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if total:
                        pct = done * 100 // total
                        filled = pct // 5
                        bar = ""
                        for i in range(20):
                            gi = min(7, int(i * 7 / 20))
                            bar += f"{GRAD[gi]}{'█' if i < filled else '░'}{RST}"
                        print(f"\r  {TXT}{label}{RST} [{bar}{RST}] {GRAD[0]}{pct}%{RST}", end="", flush=True)
                    elif done % (chunk * 8) == 0:
                        print(f"\r  {TXT}{label}{RST}: {done // 1024} KB", end="", flush=True)
        partial.replace(dest)
    except KeyboardInterrupt:
        print(f"\n  {Y}Скачивание прервано.{RST}")
        partial.unlink(missing_ok=True)
        raise
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    print()


def safe_extract_zip(archive: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive, "r") as z:
        for member in z.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Небезопасный путь в архиве: {member.filename}") from exc
        z.extractall(destination)


def _read_text_with_fallback(path: Path):
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "cp437", "utf-16"):
        try:
            return raw, raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw, raw.decode("utf-8", errors="replace"), "utf-8"


def patch_service_bat(root: Path):
    patched = []
    for path in root.rglob("service.bat"):
        try:
            raw, text, encoding = _read_text_with_fallback(path)
        except OSError:
            continue
        if SERVICE_PATCH_MARKER in text:
            continue

        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        in_tests = False
        changed = False
        output = []
        for line in lines:
            stripped = line.strip().lower()
            if stripped.startswith(":run_tests"):
                in_tests = True
            elif in_tests and stripped.startswith(":") and not stripped.startswith("::"):
                in_tests = False

            if in_tests and stripped.startswith("echo starting configuration tests in powershell window"):
                line = re.sub(r"in powershell window", "in this window", line, count=1, flags=re.IGNORECASE)
                changed = True
            if in_tests and stripped.lower().startswith('start "" powershell '):
                line = re.sub(r'(?i)(^\s*)start (""\s+)?powershell\s+', r'\1powershell ', line, count=1)
                changed = True
            if in_tests and stripped.lower() == "pause":
                line = f"{line[:len(line) - len(line.lstrip())]}rem Esc возвращает в меню service.bat"
                changed = True
            output.append(line)

        if not changed:
            continue
        output.insert(0, SERVICE_PATCH_MARKER)
        patched_text = newline.join(output) + (newline if text.endswith(("\r", "\n")) else "")
        bom = raw.startswith(b"\xef\xbb\xbf")
        output_encoding = "utf-8" if encoding == "utf-8-sig" else encoding
        data = patched_text.encode(output_encoding)
        if bom and output_encoding == "utf-8":
            data = b"\xef\xbb\xbf" + data
        path.write_bytes(data)
        patched.append(path)
    return patched


_LOGO_LINES = [
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⠏⡸⠈⢢⡀⠀⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡸⠀⡇⠀⠀⠱⡄⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⠃⢰⣀⠀⠀⠀⢃⠀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡰⠃⠀⠘⠙⠦⣀⠀⢸⡀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⠖⠉⠀⠀⠀⠀⠀⠀⠀⠁⠀⢇⠀⠀⠀⠀⠀",
    "⠀⠀⢀⣀⣀⣤⠤⠤⠤⠒⠒⠋⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠀⠀⠘⣦⡴⠋⠀⠀",
    "⢸⡉⠁⢀⣀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⡇⠀⠀⠋⣣⡤⠔⠂",
    "⠈⠳⣄⠀⠀⠈⠉⣶⠂⠀⠀⣀⣀⡀⠀⠀⠀⠀⠀⠀⠉⠛⠁⠀⠀⠉⠀⠆⠀⠀",
    "⠀⠀⠈⢦⡀⠀⠀⠸⠄⠀⠀⣿⣿⣿⠀⠀⠐⠒⠀⠀⠀⠀⠀⠀⠀⠀⣸⠀⠀⠀",
    "⠀⠀⠀⠀⠈⠓⠢⠤⡄⠀⠀⠈⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣰⠃⠀⠀⠀",
    "⠀⠀⠀⠀⠀⢠⠤⠖⣻⠍⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⠜⠁⠀⠀⠀⠀",
    "⢀⡤⠤⠤⣀⠀⠠⠎⠉⢧⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠹⡀⠀⠀⠀⠀⠀",
    "⢎⠀⠀⠀⠀⠱⡄⠀⠀⠀⠈⠉⠉⠏⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⡇⠀⠀⠀⠀⠀",
    "⠈⠳⡄⠀⠀⠀⢸⡀⠀⠀⠀⠀⡼⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⠀⠀⠀⠀⠀",
    "⠀⠀⢹⠀⠀⠀⠈⡇⠀⠀⠀⡼⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⡀⠈⡇⠀⠀⠀⠀",
    "⠀⠀⠀⡇⠀⠀⢰⡇⠀⠀⡼⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⠧⠀⢸⡀⠀⠀⠀",
    "⠀⠀⠀⢷⠀⠀⢸⡇⠀⣰⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠃⠀⠀⠇⠀⠀⠀",
    "⠀⠀⠀⠘⡄⠀⠘⡇⢰⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠀⠀⠀⢸⠀⠀⠀",
    "⠀⠀⠀⠀⠳⡄⠀⠘⢺⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠈⠒⠦⠌⣇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⡄⠀⠀⢀⠀⠀⠀⢀⠀⠀⠀⠀⠀⠀⠀⠀⢸⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⣄⠀⠘⣄⠀⠀⢸⠀⠀⠀⢸⠀⠀⠀⠀⣸⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠛⠦⣜⣆⠀⢸⡀⠀⠀⡼⠀⠀⣀⡴⠃⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠁⠉⠉⠉⠉⠉⠉⠁⠀⠀⠀⠀⠀",
]

def _grad_logo():
    result = []
    n = len(_LOGO_LINES)
    for i, line in enumerate(_LOGO_LINES):
        gi = int(i * 7 / max(n - 1, 1))
        result.append(f"{GRAD[gi]}{line}{RST}")
    result.append(f"{DIM}       by EleanorMay | github - eleanor-ln | tg @notslep{RST}")
    return result

def choose_folder_dialog(initial_dir=""):
    if sys.platform != "win32":
        return None
    try:
        ps_script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.ShowNewFolderButton = $true
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}
"""
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
        if lines and os.path.isdir(lines[-1]):
            return lines[-1]
    except Exception:
        pass
    return None

def ensure_base_directory() -> Path:
    global BASE_DIR, ZAPRET_DIR, TGPROXY_DIR, STATE_FILE, LOG_FILE, PROJECTS
    ROOT_CONFIG_FILE = Path(os.environ.get("APPDATA", Path.home())) / "ru_helper_root.json"
    USER_CONFIG_FILE = Path.home() / ".ru_helper_root.json"

    for cfg in (ROOT_CONFIG_FILE, USER_CONFIG_FILE):
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                p = data.get("base_dir")
                if p and Path(p).exists():
                    BASE_DIR = set_base_dir(Path(p))
                    ZAPRET_DIR = BASE_DIR / "zapret"
                    TGPROXY_DIR = BASE_DIR / "tg-ws-proxy"
                    STATE_FILE = BASE_DIR / "state.json"
                    LOG_FILE = BASE_DIR / "ru_helper.log"
                    PROJECTS = UnifiedAdapter(BASE_DIR)
                    return BASE_DIR
            except Exception:
                pass

    cls()
    for line in _grad_logo():
        print(line)
    print()
    default_parent = Path(os.environ.get("APPDATA", Path.home()))
    default_dir = default_parent / "ru_helper"

    print(f"  {TXT}Укажите путь к корневой папке. В ней будет создана папка {GRAD[0]}ru_helper{RST}{TXT}.{RST}")
    print(f"  {DIM}[Enter] — путь по умолчанию ({default_dir}){RST}")
    print(f"  {DIM}[B] или [O] — открыть окно выбора папки в проводнике{RST}")
    print()

    chosen_base = None
    while not chosen_base:
        try:
            val = input(f"  {GRAD[0]}>{RST} ").strip().strip('"').strip("'")
        except (KeyboardInterrupt, EOFError):
            val = ""

        if not val:
            chosen_base = default_dir
            break
        elif val.lower() in ("b", "o", "browse", "open"):
            chosen_folder = choose_folder_dialog(str(default_parent))
            if chosen_folder:
                p = Path(chosen_folder)
                chosen_base = p if p.name.lower() == "ru_helper" else p / "ru_helper"
                break
            else:
                print(f"  {Y}Выбор отменён. Введите путь вручную или нажмите Enter:{RST}")
        else:
            p = Path(val)
            chosen_base = p if p.name.lower() == "ru_helper" else p / "ru_helper"
            break

    chosen_base.mkdir(parents=True, exist_ok=True)
    BASE_DIR = set_base_dir(chosen_base)
    ZAPRET_DIR = BASE_DIR / "zapret"
    TGPROXY_DIR = BASE_DIR / "tg-ws-proxy"
    STATE_FILE = BASE_DIR / "state.json"
    LOG_FILE = BASE_DIR / "ru_helper.log"
    PROJECTS = UnifiedAdapter(BASE_DIR)
    return BASE_DIR

def _grad_line(text):
    out = ""
    n = max(len(text), 1)
    for i, ch in enumerate(text):
        gi = int(i * 7 / n)
        out += f"{GRAD[gi]}{ch}"
    return out + RST

def spinner_dots(stop_event, prefix):
    frames = ["   ", ".  ", ".. ", "..."]
    i = 0
    while not stop_event.is_set():
        dots = f"{GRAD[i % 4]}{frames[i % 4]}{RST}"
        try:
            print(f"\r  {TXT}{prefix}{RST}{dots}", end="", flush=True)
        except (AttributeError, UnicodeEncodeError, OSError):
            try:
                print(f"\r  {prefix}{frames[i % 4]}", end="", flush=True)
            except (AttributeError, OSError):
                pass
        i += 1
        time.sleep(0.06)

def boot_animation():
    cls()
    logo_lines = _grad_logo()
    for line in logo_lines:
        print(line)
        time.sleep(0.005)
    time.sleep(0.02)
    sep_char = "─"
    sep_len  = 50
    print("  ", end="", flush=True)
    for i in range(sep_len):
        gi = int(i * 7 / sep_len)
        print(f"{GRAD[gi]}{sep_char}{RST}", end="", flush=True)
        time.sleep(0.002)
    print()
    time.sleep(0.02)

def version_line(label, latest, installed, present=None):
    lbl = f"{TXT}{label}{RST}"
    if present is False:
        ver_str = f"{Y}{latest} {DIM}(не установлен){RST}"
    elif present is True and not installed:
        ver_str = f"{GRAD[0]}установлен{RST}"
    elif installed and installed != latest:
        ver_str = f"{R}{installed} → {latest}{RST}"
    elif installed == latest:
        ver_str = f"{GRAD[0]}{installed}{RST}"
    elif present is True:
        ver_str = f"{GRAD[0]}установлен{RST}"
    else:
        ver_str = f"{Y}{latest} {DIM}(не установлен){RST}"
    return f"  {lbl} {ver_str}"


def get_latest_versions(state, show_progress=True):
    results = {}

    def fetch_zapret():
        try:
            data = fetch_json(ZAPRET_API)
            results["zapret"] = data["tag_name"]
        except Exception:
            try:
                data = fetch_json(ZAPRET_FALLBACK_API)
                results["zapret"] = data["tag_name"]
            except Exception:
                results["zapret"] = "?"

    def fetch_tgproxy():
        try:
            data = fetch_json(TGPROXY_API)
            results["tgproxy"] = data["tag_name"]
        except Exception:
            results["tgproxy"] = "?"

    t1 = threading.Thread(target=fetch_zapret, daemon=True)
    t2 = threading.Thread(target=fetch_tgproxy, daemon=True)
    t1.start(); t2.start()

    stop = threading.Event()
    spin = None
    if show_progress:
        spin = threading.Thread(
            target=spinner_dots, args=(stop, "Проверка версий"), daemon=True)
        spin.start()

    t1.join(); t2.join()
    stop.set()
    if spin is not None:
        spin.join()
        try:
            print("\r" + " " * 50 + "\r", end="")
        except (AttributeError, OSError, UnicodeEncodeError):
            pass

    return results.get("zapret", "?"), results.get("tgproxy", "?")


def draw_menu(zapret_latest, tgproxy_latest, state):
    cls()
    for line in _grad_logo():
        print(line)
    print()

    sep = _grad_line("  " + "─" * 50)
    print(sep)

    zap_st = PROJECTS.zapret.status()
    tg_st = PROJECTS.tgproxy.status()
    print(version_line("ds-yt zapret  :", zapret_latest, state.get("zapret_version"), zap_st["installed"]))
    print(version_line("tg-ws-proxy   :", tgproxy_latest, state.get("tgproxy_version"), tg_st["installed"]))

    alt = state.get("zapret_strategy")
    alt_str = f"{GRAD[1]}{alt}{RST}" if alt else f"{DIM}не выбран{RST}"
    print(f"  {TXT}активный альт :{RST} {alt_str}")

    zap_label = "запущен" if zap_st["running"] else "остановлен"
    if zap_st["winws"] and zap_st["service"] == "running":
        zap_mode = "служба + winws"
    elif zap_st["service"] == "running":
        zap_mode = "служба"
    elif zap_st["winws"]:
        zap_mode = "winws"
    else:
        zap_mode = ""
    if zap_mode:
        zap_label += f" ({zap_mode})"
    zap_color_str = f"{GRAD[0]}● {zap_label}{RST}" if zap_st["running"] else f"{Y}○ {zap_label}{RST}"
    if not zap_st["installed"]:
        zap_color_str = f"{R}✗ не установлен{RST}"
    print(f"  {TXT}zapret         :{RST} {zap_color_str}  {DIM}альтов: {zap_st['strategy_count']}{RST}")
    tg_label = "запущен" if tg_st["running"] else "остановлен"
    if tg_st["port_open"]:
        tg_label += f", порт {tg_st['port']}"
    print(f"  {TXT}tg-ws-proxy    :{RST} "
          f"{GRAD[0] + '● ' + tg_label + RST if tg_st['installed'] else R + '✗ не установлен' + RST}")

    print(sep)
    print()

    def mi(num, text):
        return f"  {GRAD[7]}[{GRAD[0]}{num}{GRAD[7]}]{RST} {TXT}{text}{RST}"

    print(mi("1", "Установить / обновить  zapret-discord-youtube"))
    print(mi("2", "Установить / обновить  tg-ws-proxy"))
    print(mi("3", "Найти рабочий альт zapret"))
    print(mi("4", "Сменить активный альт"))
    print(mi("5", "Управление сервисами"))
    print(mi("6", "Диагностика соединения"))
    print(mi("7", "Обновить информацию о версиях"))
    print(mi("8", "Перейти в оконную версию"))
    print(mi("9", "Запустить оба адаптера (или Ctrl + R)"))
    print(mi("10", "Выключить оба адаптера (или Ctrl + E)"))
    print(mi("11", "Открыть корневую папку"))
    print(mi("12", "README проекта"))
    print(mi("L", "Последние логи"))
    print(mi("D", "Открыть обсуждения zapret на GitHub (можно найти решение большинства проблем)"))
    print(mi("0", "Выход"))
    print()


def install_zapret(state):
    cls()
    for line in _grad_logo(): print(line)
    print(f"\n  {GRAD[0]}=== Установка zapret-discord-youtube ==={RST}\n")

    if not is_admin():
        print(f"  {R}Требуются права администратора! Перезапустите от имени администратора.{RST}")
        pause(); return

    stop = threading.Event()
    spin = threading.Thread(
        target=spinner_dots, args=(stop, "Получение информации о релизе"), daemon=True)
    spin.start()
    data = None
    try:
        data = fetch_json(ZAPRET_API)
    except Exception as e:
        stop.set(); spin.join()
        print(f"\n  {Y}Основной репозиторий недоступен. Пробуем резервный источник bol-van/zapret-win-bundle...{RST}")
        log(f"Основной репозиторий zapret недоступен: {e}. Переключение на {ZAPRET_FALLBACK_API}")
        stop = threading.Event()
        spin = threading.Thread(
            target=spinner_dots, args=(stop, "Получение информации о релизе bol-van"), daemon=True)
        spin.start()
        try:
            data = fetch_json(ZAPRET_FALLBACK_API)
        except Exception as e2:
            stop.set(); spin.join()
            print(f"\n  {R}Ошибка получения релиза: {e2}{RST}")
            pause(); return
    stop.set(); spin.join()
    print("\r" + " " * 50 + "\r", end="")

    tag  = data["tag_name"]
    asset = next((a for a in data["assets"] if a["name"].endswith(".zip")), None)
    if not asset:
        print(f"  {R}Не найден .zip в релизе{RST}")
        pause(); return

    url  = asset["browser_download_url"]
    name = asset["name"]
    tmp  = BASE_DIR / name

    BASE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"  {TXT}Версия:{RST} {GRAD[0]}{tag}{RST}")
    print(f"  {TXT}Файл:  {RST} {DIM}{name}{RST}\n")

    try:
        download_file(url, tmp, "Скачивание")
    except KeyboardInterrupt:
        pause(); return
    except Exception as e:
        print(f"\n  {R}Ошибка скачивания: {e}{RST}")
        pause(); return

    print(f"  {TXT}Распаковка...{RST}")
    staging = BASE_DIR / ".zapret-staging"
    try:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        safe_extract_zip(tmp, staging)

        subdirs = [d for d in staging.iterdir() if d.is_dir()]
        if len(subdirs) == 1 and not any(staging.glob("*.bat")):
            inner = subdirs[0]
            for item in inner.iterdir():
                shutil.move(str(item), str(staging / item.name))
            inner.rmdir()

        changed = bool(patch_service_bat(staging))

        if ZAPRET_DIR.exists():
            print(f"  {TXT}Останавливаем старую установку...{RST}")
            _kill_winws()
            for service in (ZAPRET_SVC_NAME, "WinDivert", "WinDivert14"):
                _stop_service(service)
            time.sleep(1)
            shutil.rmtree(ZAPRET_DIR, ignore_errors=True)
            if ZAPRET_DIR.exists():
                raise PermissionError(f"Не удалось удалить старую папку: {ZAPRET_DIR}")
        staging.replace(ZAPRET_DIR)
    except (PermissionError, zipfile.BadZipFile, ValueError, OSError) as e:
        tmp.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        print(f"\n  {R}Ошибка доступа или распаковки: {e}{RST}")
        print(f"  {Y}Возможные причины:{RST}")
        print(f"  {DIM}• Антивирус блокирует WinDivert64.sys — добавьте папку в исключения{RST}")
        print(f"  {DIM}• Файл занят другим процессом — перезагрузите ПК и попробуйте снова{RST}")
        pause(); return
    except Exception as e:
        tmp.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        print(f"\n  {R}Ошибка установки: {e}{RST}")
        pause(); return
    tmp.unlink(missing_ok=True)

    state["zapret_version"] = tag
    save_state(state)
    if changed:
        print(f"  {DIM}service.bat исправлен: тесты запускаются в этом окне, Esc возвращает в меню.{RST}")
    print(f"\n  {GRAD[0]}Готово! {TXT}zapret установлен в:{RST}")
    print(f"  {DIM}{ZAPRET_DIR}{RST}")
    pause()


def install_tgproxy(state):
    cls()
    for line in _grad_logo(): print(line)
    print(f"\n  {GRAD[0]}=== Установка tg-ws-proxy ==={RST}\n")

    stop = threading.Event()
    spin = threading.Thread(
        target=spinner_dots, args=(stop, "Получение информации о релизе"), daemon=True)
    spin.start()
    try:
        data = fetch_json(TGPROXY_API)
    except Exception as e:
        stop.set(); spin.join()
        print(f"\n  {R}Ошибка: {e}{RST}")
        pause(); return
    stop.set(); spin.join()
    print("\r" + " " * 50 + "\r", end="")

    tag   = data["tag_name"]
    asset = select_tgproxy_asset(data.get("assets", []))
    if not asset:
        print(f"  {R}Не найден Windows .exe в релизе{RST}")
        for a in data["assets"]:
            print(f"    {DIM}{a['name']}{RST}")
        pause(); return

    url  = asset["browser_download_url"]
    name = asset["name"]

    TGPROXY_DIR.mkdir(parents=True, exist_ok=True)
    dest = TGPROXY_DIR / name

    print(f"  {TXT}Версия:{RST} {GRAD[0]}{tag}{RST}")
    print(f"  {TXT}Файл:  {RST} {DIM}{name}{RST}\n")

    try:
        download_file(url, dest, "Скачивание")
    except KeyboardInterrupt:
        pause(); return
    except Exception as e:
        print(f"\n  {R}Ошибка скачивания: {e}{RST}")
        pause(); return

    state["tgproxy_version"] = tag
    save_state(state)
    print(f"\n  {GRAD[0]}Готово! {TXT}tg-ws-proxy установлен в:{RST}")
    print(f"  {DIM}{dest}{RST}")
    pause()


def find_strategy(state):
    cls()
    for line in _grad_logo(): print(line)
    print(f"\n  {GRAD[0]}=== Поиск рабочего альта zapret ==={RST}\n")

    if not is_admin():
        print(f"  {R}Требуются права администратора!{RST}")
        pause(); return

    if not ZAPRET_DIR.exists():
        print(f"  {R}zapret не установлен. Сначала установите его (пункт 1).{RST}")
        pause(); return

    ps1 = ZAPRET_DIR / "utils" / "test zapret.ps1"
    use_builtin = ps1.exists()

    bats = sorted(ZAPRET_DIR.glob("general*.bat"))
    if not bats:
        print(f"  {R}Не найдены general*.bat в {ZAPRET_DIR}{RST}")
        pause(); return

    print(f"  {TXT}Найдено альтов: {GRAD[0]}{len(bats)}{RST}")

    if use_builtin:
        print(f"  {DIM}Нажмите Enter для запуска или Ctrl+C для отмены{RST}")
        try:
            input()
        except KeyboardInterrupt:
            return

        log("=== Запуск встроенного test zapret.ps1 ===")

        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(ps1)],
            cwd=str(ZAPRET_DIR),
        )

        results_dir = ZAPRET_DIR / "utils" / "test results"
        best = _parse_ps1_results(results_dir)

        if best:
            print(f"\n  {GRAD[0]}✓ {TXT}Лучший альт по результатам теста:{RST} {GRAD[1]}{best}{RST}")
            state["zapret_strategy"] = best
            save_state(state)
            log(f"Выбран альт (ps1): {best}")
            print(f"  {DIM}Сохранено.{RST}")
        else:
            print(f"\n  {Y}Не удалось автоматически определить лучший альт из результатов.{RST}")
            print(f"  {DIM}Смотрите файлы в: {results_dir}{RST}")
            _manual_alt_pick(bats, state)

    else:
        print(f"  {Y}test zapret.ps1 не найден — используем встроенный тест{RST}\n")
        print(f"  {TXT}Каждый альт: 5 сек запуск → curl discord.com/youtube.com → результат{RST}")
        print(f"  {DIM}Нажмите Enter для начала или Ctrl+C для отмены{RST}")
        try:
            input()
        except KeyboardInterrupt:
            return

        working = []
        log("=== Начало поиска альта (fallback) ===")

        for idx, bat in enumerate(bats, 1):
            name = bat.stem
            print(f"\n  {GRAD[7]}[{GRAD[0]}{idx}/{len(bats)}{GRAD[7]}]{RST} {TXT}{name}{RST}")
            log(f"Тест: {name}")

            proc = subprocess.Popen(
                ["cmd", "/c", str(bat)],
                cwd=str(ZAPRET_DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )

            output_lines = []
            def _read(p=proc, ol=output_lines, n=name):
                for line in p.stdout:
                    line = line.rstrip()
                    if line:
                        ol.append(line)
                        print(f"\r    {DIM}> {line[:72]:<72}{RST}", end="", flush=True)
                        log(f"  [{n}] {line}")

            reader = threading.Thread(target=_read, daemon=True)
            reader.start()

            for sec in range(5, 0, -1):
                time.sleep(1)
                print(f"\r    {DIM}ожидание {GRAD[0]}{sec}{DIM}с...{' '*60}{RST}", end="", flush=True)

            print(f"\r    {DIM}проверка через curl...{' '*55}{RST}", end="", flush=True)
            ok, ms = _curl_check_multi()

            try:
                proc.terminate(); proc.wait(timeout=3)
            except Exception:
                pass
            reader.join(timeout=2)
            _kill_winws()
            time.sleep(0.5)

            if ok:
                print(f"\r  {GRAD[0]}✓ РАБОТАЕТ{RST} {TXT}{name}{RST} {DIM}({ms}ms){RST}          ")
                log(f"  РАБОТАЕТ: {name} ({ms}ms)")
                working.append(name)
            else:
                print(f"\r  {R}✗ не работает{RST} {DIM}{name}{RST}          ")
                log(f"  не работает: {name}")

        print(f"\n  {_grad_line('─' * 50)}")
        if working:
            print(f"\n  {GRAD[0]}Рабочие альты:{RST}")
            for w in working:
                print(f"    {GRAD[0]}•{RST} {TXT}{w}{RST}")
            best = working[0]
            print(f"\n  {TXT}Рекомендуется:{RST} {GRAD[0]}{best}{RST}")
            state["zapret_strategy"] = best
            save_state(state)
            log(f"Выбран альт: {best}")
            print(f"  {DIM}Сохранено.{RST}")
        else:
            print(f"\n  {R}Ни один альт не прошёл тест.{RST}")
            print(f"  {DIM}Проверьте Secure DNS или обновите zapret.{RST}")
            log("Ни один альт не прошёл тест")

    pause()


def _curl_check_multi():
    targets = ["https://discord.com", "https://www.youtube.com"]
    results = {}
    curl_exe = shutil.which("curl.exe") or shutil.which("curl")

    def _check(url):
        if not curl_exe:
            ok, ms, _ = http_check(url, timeout=5)
            results[url] = (ok, ms)
            return
        try:
            t0 = time.time()
            r = subprocess.run(
                [curl_exe, "-s", "-o", "NUL", "-w", "%{http_code}",
                 "-m", "5", "--http1.1", "-I", url],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            ms = int((time.time() - t0) * 1000)
            code = r.stdout.strip()
            try:
                status_code = int(code or 0)
            except ValueError:
                status_code = 0
            results[url] = (status_code != 0 and status_code < 500, ms)
        except Exception:
            ok, ms, _ = http_check(url, timeout=5)
            results[url] = (ok, ms)

    threads = [threading.Thread(target=_check, args=(u,), daemon=True) for u in targets]
    for t in threads: t.start()
    for t in threads: t.join(timeout=8)

    ok_count = sum(1 for ok, _ in results.values() if ok)
    avg_ms   = int(sum(ms for _, ms in results.values() if ms) / max(len(results), 1))
    return ok_count == len(targets), avg_ms


def _parse_ps1_results(results_dir: Path):
    if not results_dir.exists():
        return None
    files = sorted(results_dir.glob("test_results_*.txt"), reverse=True)
    if not files:
        return None
    try:
        content = files[0].read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return None
    for line in content.splitlines():
        if line.lower().strip().startswith("best strategy:") or line.lower().strip().startswith("best config:"):
            val = line.split(":", 1)[-1].strip()
            if val.lower().endswith(".bat"):
                val = val[:-4].strip()
            if val:
                return val
    return None


def _manual_alt_pick(bats, state):
    print(f"\n  {TXT}Выберите альт вручную:{RST}")
    for i, b in enumerate(bats, 1):
        print(f"  {GRAD[7]}[{GRAD[0]}{i}{GRAD[7]}]{RST} {TXT}{b.stem}{RST}")
    print()
    try:
        ch = input(f"  {TXT}Номер (0 — пропустить):{RST} {GRAD[0]}>{RST} ").strip()
    except KeyboardInterrupt:
        return
    if ch.isdigit() and 1 <= int(ch) <= len(bats):
        chosen = bats[int(ch) - 1].stem
        state["zapret_strategy"] = chosen
        save_state(state)
        print(f"  {GRAD[0]}✓ {TXT}Выбран: {GRAD[1]}{chosen}{RST}")

def change_alt(state):
    cls()
    for line in _grad_logo(): print(line)
    print(f"\n  {GRAD[0]}=== Смена активного альта ==={RST}\n")

    if not ZAPRET_DIR.exists():
        print(f"  {R}zapret не установлен.{RST}"); pause(); return

    bats = sorted(ZAPRET_DIR.glob("general*.bat"))
    if not bats:
        print(f"  {R}Не найдены general*.bat{RST}"); pause(); return

    current = state.get("zapret_strategy", "")
    for i, b in enumerate(bats, 1):
        marker = f" {GRAD[0]}← текущий{RST}" if b.stem == current else ""
        gi = min(7, int(i * 7 / max(len(bats), 1)))
        print(f"  {GRAD[7]}[{GRAD[0]}{i}{GRAD[7]}]{RST} {TXT}{b.stem}{RST}{marker}")

    print()
    try:
        ch = input(f"  {TXT}Введите номер альта (0 — отмена):{RST} {GRAD[0]}>{RST} ").strip()
    except KeyboardInterrupt:
        return

    if ch == "0" or not ch.isdigit():
        return
    idx = int(ch) - 1
    if 0 <= idx < len(bats):
        chosen = bats[idx].stem
        state["zapret_strategy"] = chosen
        save_state(state)
        log(f"Альт сменён вручную: {chosen}")
        print(f"\n  {GRAD[0]}✓ {TXT}Активный альт: {GRAD[1]}{chosen}{RST}")
    else:
        print(f"  {R}Неверный номер.{RST}")
    pause()


def diagnostics():
    cls()
    for line in _grad_logo(): print(line)
    print(f"\n  {GRAD[0]}=== Диагностика соединения ==={RST}\n")
    log("=== Диагностика ===")

    col_w = 12
    print(f"  {TXT}{'Сервис':<{col_w}} {'Статус':<12} {'Пинг':<10} {'HTTP'}{RST}")
    print(f"  {_grad_line('─' * 46)}")

    for name, url in DIAG_TARGETS:
        result = {}
        done_ev = threading.Event()

        def _do_check(u=url, r=result, e=done_ev):
            ok, ms, status = http_check(u, timeout=6)
            r["ok"] = ok; r["ms"] = ms; r["status"] = status
            e.set()

        t = threading.Thread(target=_do_check, daemon=True)
        t.start()

        frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
        fi = 0
        print(f"  {TXT}{name:<{col_w}}{RST}", end="", flush=True)
        while not done_ev.wait(0.1):
            print(f"\r  {TXT}{name:<{col_w}}{RST} {GRAD[2]}{frames[fi % len(frames)]}{RST}", end="", flush=True)
            fi += 1

        ok  = result["ok"]
        ms  = result["ms"]
        st  = result["status"]
        ms_str  = f"{ms}ms" if ms else "—"
        if ok:
            status_str = f"{GRAD[0]}● доступен{RST}"
            log(f"  {name}: OK ({ms}ms, {st})")
        else:
            status_str = f"{R}✗ недоступен{RST}"
            log(f"  {name}: FAIL ({st})")

        print(f"\r  {TXT}{name:<{col_w}}{RST} {status_str:<20} {DIM}{ms_str:<10}{RST} {DIM}{st}{RST}")

    print(f"\n  {DIM}Лог сохранён: {LOG_FILE}{RST}")
    pause()

def _kill_winws():
    try:
        subprocess.run(["taskkill", "/F", "/IM", "winws.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _svc_color(s, installed=True):
    if s == "missing" and installed:
        s = "stopped"
    if s == "running":  return f"{GRAD[0]}● запущен{RST}"
    if s == "stopped":  return f"{Y}○ остановлен{RST}"
    if s == "missing":  return f"{R}✗ не установлен{RST}"
    return f"{DIM}{s}{RST}"


def _stop_service(name):
    try:
        return subprocess.run(
            [SC_EXE, "stop", name], capture_output=True, text=True,
            encoding="utf-8", errors="replace"
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _run_batch_same_console(batch: Path, *args):
    command = f'call "{batch.name}"'
    if args:
        command += " " + " ".join(f'"{arg}"' for arg in args)
    return subprocess.run([COMSPEC, "/d", "/c", command], cwd=str(batch.parent))

def services_menu(state):
    while True:
        cls()
        for line in _grad_logo(): print(line)
        print(f"\n  {GRAD[0]}=== Управление сервисами ==={RST}\n")

        zap_status = PROJECTS.zapret.status()
        tgp_status = PROJECTS.tgproxy.status()

        alt = state.get("zapret_strategy", "не выбран")
        zap_label = "запущен" if zap_status["running"] else "остановлен"
        if zap_status["winws"] and zap_status["service"] == "running":
            zap_mode = "служба + winws"
        elif zap_status["service"] == "running":
            zap_mode = "служба"
        elif zap_status["winws"]:
            zap_mode = "winws"
        else:
            zap_mode = ""
        if zap_mode:
            zap_label += f" ({zap_mode})"
        zap_color_str = f"{GRAD[0]}● {zap_label}{RST}" if zap_status["running"] else f"{Y}○ {zap_label}{RST}"
        if not zap_status["installed"]:
            zap_color_str = f"{R}✗ не установлен{RST}"
        print(f"  {TXT}zapret      :{RST} {zap_color_str}  {DIM}(альт: {alt}){RST}")
        tg_text = "запущен" if tgp_status["running"] else "остановлен"
        print(f"  {TXT}tg-ws-proxy :{RST} "
              f"{GRAD[0] + '● ' + tg_text + RST if tgp_status['installed'] else R + '✗ не установлен' + RST}")
        
        auto_zap = state.get("auto_update_zapret", False)
        auto_tg = state.get("auto_update_tg", True)
        zap_auto_str = f"{GRAD[0]}вкл{RST}" if auto_zap else f"{DIM}выкл{RST}"
        tg_auto_str = f"{GRAD[0]}вкл{RST}" if auto_tg else f"{DIM}выкл{RST}"
        print(f"  {TXT}Автообновление:{RST} zapret [{zap_auto_str}] | tg-ws-proxy [{tg_auto_str}]")
        print()

        def mi(num, text):
            return f"  {GRAD[7]}[{GRAD[0]}{num}{GRAD[7]}]{RST} {TXT}{text}{RST}"

        print(mi("1", "Установить zapret в автозапуск (активный альт)"))
        print(mi("2", "Включить автозапуск tg-ws-proxy"))
        print(mi("3", "Запустить zapret сейчас"))
        print(mi("4", "Запустить tg-ws-proxy сейчас"))
        print(mi("5", "Остановить zapret"))
        print(mi("6", "Остановить tg-ws-proxy"))
        print(mi("T", "Открыть в Telegram (tg-ws-proxy)"))
        print(mi("U", "Автообновление обоих сервисов (настройка / оба сервиса)"))
        print(mi("7", "Удалить zapret из автозапуска"))
        print(mi("8", "Выключить автозапуск tg-ws-proxy"))
        print(mi("9", "Удалить zapret-сервис и автозапуск TG"))
        print(mi("A", "Удалить файлы zapret"))
        print(mi("B", "Удалить файлы tg-ws-proxy"))
        print(mi("C", "Удалить файлы обеих программ"))
        print(mi("0", "Назад"))
        print()

        ch = input(f"  {GRAD[0]}>{RST} ").strip().upper()

        if ch == "1":   _install_zapret_service(state)
        elif ch == "2": _set_tgproxy_autostart(True)
        elif ch == "3": _start_zapret_now(state)
        elif ch == "4": _start_tgproxy_now()
        elif ch == "5": _stop_zapret_now()
        elif ch == "6": _stop_tgproxy_now()
        elif ch == "T": _open_tg_now()
        elif ch == "U": _manage_auto_updates(state)
        elif ch == "7": _remove_one_service(ZAPRET_SVC_NAME)
        elif ch == "8": _set_tgproxy_autostart(False)
        elif ch == "9": _remove_one_service(ZAPRET_SVC_NAME); _set_tgproxy_autostart(False)
        elif ch == "A": _delete_files(ZAPRET_DIR, "zapret", state, clear_key="zapret_version")
        elif ch == "B": _delete_files(TGPROXY_DIR, "tg-ws-proxy", state, clear_key="tgproxy_version")
        elif ch == "C": _delete_files(ZAPRET_DIR, "zapret", state, clear_key="zapret_version"); _delete_files(TGPROXY_DIR, "tg-ws-proxy", state, clear_key="tgproxy_version")
        elif ch == "0": break


def _manage_auto_updates(state):
    while True:
        cls()
        for line in _grad_logo(): print(line)
        print(f"\n  {GRAD[0]}=== Настройка автообновления сервисов ==={RST}\n")

        auto_zap = state.get("auto_update_zapret", False)
        auto_tg = state.get("auto_update_tg", True)

        zap_status_str = f"{GRAD[0]}ВКЛЮЧЕНО{RST}" if auto_zap else f"{R}ВЫКЛЮЧЕНО{RST}"
        tg_status_str = f"{GRAD[0]}ВКЛЮЧЕНО{RST}" if auto_tg else f"{R}ВЫКЛЮЧЕНО{RST}"

        print(f"  {TXT}Автообновление zapret      :{RST} {zap_status_str}")
        print(f"  {TXT}Автообновление tg-ws-proxy :{RST} {tg_status_str}")
        print()

        def mi(num, text):
            return f"  {GRAD[7]}[{GRAD[0]}{num}{GRAD[7]}]{RST} {TXT}{text}{RST}"

        print(mi("1", f"Автообновление zapret: {'выключить' if auto_zap else 'включить'}"))
        print(mi("2", f"Автообновление tg-ws-proxy: {'выключить' if auto_tg else 'включить'}"))
        print(mi("3", "Включить автообновление для обоих сервисов"))
        print(mi("4", "Выключить автообновление для обоих сервисов"))
        print(mi("5", "Проверить и обновить оба сервиса сейчас"))
        print(mi("0", "Назад"))
        print()

        ch = input(f"  {GRAD[0]}>{RST} ").strip().upper()
        if ch == "1":
            state["auto_update_zapret"] = not auto_zap
            save_state(state)
            log(f"Автообновление zapret: {state['auto_update_zapret']}")
            print(f"\n  {GRAD[0]}✓ {TXT}Автообновление zapret {'включено' if state['auto_update_zapret'] else 'выключено'}.{RST}")
            time.sleep(0.7)
        elif ch == "2":
            state["auto_update_tg"] = not auto_tg
            save_state(state)
            log(f"Автообновление tg-ws-proxy: {state['auto_update_tg']}")
            print(f"\n  {GRAD[0]}✓ {TXT}Автообновление tg-ws-proxy {'включено' if state['auto_update_tg'] else 'выключено'}.{RST}")
            time.sleep(0.7)
        elif ch == "3":
            state["auto_update_zapret"] = True
            state["auto_update_tg"] = True
            save_state(state)
            log("Автообновление обоих сервисов включено")
            print(f"\n  {GRAD[0]}✓ {TXT}Автообновление обоих сервисов включено.{RST}")
            time.sleep(0.7)
        elif ch == "4":
            state["auto_update_zapret"] = False
            state["auto_update_tg"] = False
            save_state(state)
            log("Автообновление обоих сервисов выключено")
            print(f"\n  {GRAD[0]}✓ {TXT}Автообновление обоих сервисов выключено.{RST}")
            time.sleep(0.7)
        elif ch == "5":
            _check_and_apply_updates(state, force=True)
            pause()
        elif ch == "0":
            break


def _check_and_apply_updates(state, force=False):
    print(f"\n  {TXT}Проверка обновлений сервисов...{RST}")
    zap_latest, tg_latest = get_latest_versions(state, show_progress=True)

    updated_any = False
    zap_inst = state.get("zapret_version")
    auto_zap = state.get("auto_update_zapret", False)
    if (force or auto_zap) and zap_inst and zap_latest not in ("?", None, "") and zap_inst != zap_latest and PROJECTS.zapret.installed():
        print(f"\n  {GRAD[0]}● {TXT}Доступно обновление zapret: {DIM}{zap_inst} → {zap_latest}{RST}")
        log(f"Обновление zapret: {zap_inst} → {zap_latest}")
        try:
            install_zapret(state)
            updated_any = True
        except Exception as e:
            print(f"  {R}Ошибка обновления zapret: {e}{RST}")

    tg_inst = state.get("tgproxy_version")
    auto_tg = state.get("auto_update_tg", True)
    if (force or auto_tg) and tg_inst and tg_latest not in ("?", None, "") and tg_inst != tg_latest and PROJECTS.tgproxy.installed():
        print(f"\n  {GRAD[0]}● {TXT}Доступно обновление tg-ws-proxy: {DIM}{tg_inst} → {tg_latest}{RST}")
        log(f"Обновление tg-ws-proxy: {tg_inst} → {tg_latest}")
        try:
            install_tgproxy(state)
            updated_any = True
        except Exception as e:
            print(f"  {R}Ошибка обновления tg-ws-proxy: {e}{RST}")

    if not updated_any:
        if force:
            print(f"\n  {GRAD[0]}✓ {TXT}Все установленные сервисы уже имеют актуальные версии.{RST}")
        else:
            log("Проверка автообновлений: актуальные версии установлены")

def _svc_status(name):
    try:
        r = subprocess.run(
            [SC_EXE, "query", name],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        output = f"{r.stdout}\n{r.stderr}"
        if "does not exist" in output.lower() or r.returncode == 1060:
            return "missing"
        if "RUNNING" in output:
            return "running"
        return "stopped"
    except Exception:
        return "unknown"

def _install_zapret_service(state):
    if not is_admin():
        print(f"\n  {R}Нужны права администратора!{RST}"); pause(); return

    alt = state.get("zapret_strategy")
    if not alt:
        print(f"\n  {R}Сначала найдите рабочий альт (пункт 3).{RST}"); pause(); return

    print(f"\n  {TXT}Установка службы zapret в автозапуск...{RST}")
    print(f"  {DIM}Альт: {alt}{RST}\n")
    log(f"Установка службы zapret (альт: {alt})")
    try:
        PROJECTS.zapret.install_service(alt)
        log(f"Служба zapret успешно установлена в автозапуск: {alt}")
        print(f"  {GRAD[0]}✓ {TXT}Служба zapret установлена в автозапуск и запущена ({alt}).{RST}")
    except Exception as e:
        log(f"Ошибка установки службы zapret: {e}")
        print(f"  {R}Ошибка установки службы zapret: {e}{RST}")
    pause()

def _set_tgproxy_autostart(enabled):
    exe = PROJECTS.tgproxy.executable
    if not exe:
        print(f"\n  {R}tg-ws-proxy не установлен. Сначала установите его (пункт 2).{RST}")
        pause(); return
    try:
        set_tg_autostart(enabled, exe)
        state = "включён" if enabled else "выключен"
        print(f"\n  {GRAD[0]}✓ {TXT}Автозапуск tg-ws-proxy {state}.{RST}")
        log(f"Автозапуск tg-ws-proxy: {enabled}")
    except Exception as e:
        print(f"\n  {R}Не удалось изменить автозапуск: {e}{RST}")
    pause()

def _start_zapret_now(state):
    alt = state.get("zapret_strategy")
    if not alt:
        print(f"\n  {R}Альт не выбран. Запустите поиск (пункт 3).{RST}"); pause(); return
    log(f"Запуск zapret: альт {alt}")
    try:
        PROJECTS.zapret.start(alt)
        log(f"zapret запущен и работает: {alt}")
        print(f"\n  {GRAD[0]}✓ {TXT}zapret запущен и работает: {alt}.{RST}")
    except Exception as e:
        log(f"Ошибка запуска zapret ({alt}): {e}")
        print(f"\n  {R}Не удалось запустить zapret: {e}{RST}")
    pause()

def _stop_zapret_now():
    log("Остановка zapret")
    try:
        PROJECTS.zapret.stop()
        if PROJECTS.zapret.status()["running"]:
            raise RuntimeError("процесс всё ещё работает после остановки")
        log("zapret остановлен")
        print(f"\n  {GRAD[0]}✓ {TXT}zapret остановлен.{RST}")
    except Exception as e:
        log(f"Ошибка остановки zapret: {e}")
        print(f"\n  {R}Не удалось остановить zapret: {e}{RST}")
    pause()

def _start_tgproxy_now():
    log("Запуск tg-ws-proxy")
    try:
        PROJECTS.tgproxy.start()
        if not PROJECTS.tgproxy.is_running():
            raise RuntimeError("процесс не найден после запуска")
        log("tg-ws-proxy запущен и работает")
        print(f"\n  {GRAD[0]}✓ {TXT}tg-ws-proxy запущен и работает в трее.{RST}")
    except Exception as e:
        log(f"Ошибка запуска tg-ws-proxy: {e}")
        print(f"\n  {R}Не удалось запустить tg-ws-proxy: {e}{RST}")
    pause()


def _stop_tgproxy_now():
    log("Остановка tg-ws-proxy")
    try:
        PROJECTS.tgproxy.stop()
        if PROJECTS.tgproxy.is_running():
            raise RuntimeError("процесс всё ещё работает после остановки")
        log("tg-ws-proxy остановлен")
        print(f"\n  {GRAD[0]}✓ {TXT}tg-ws-proxy остановлен.{RST}")
    except Exception as e:
        log(f"Ошибка остановки tg-ws-proxy: {e}")
        print(f"\n  {R}Не удалось остановить tg-ws-proxy: {e}{RST}")
    pause()

def _open_tg_now():
    if not PROJECTS.tgproxy.executable:
        print(f"\n  {R}tg-ws-proxy не установлен. Сначала установите его (пункт 2).{RST}")
        pause(); return
    print(f"\n  {TXT}Перезапуск Telegram и подключение прокси...{RST}")
    log("Перезапуск Telegram с подключением tg-ws-proxy")
    try:
        PROJECTS.tgproxy.open_in_telegram()
        print(f"  {GRAD[0]}✓ {TXT}Telegram перезапущен, подключение к прокси отправлено.{RST}")
    except Exception as e:
        print(f"  {R}Ошибка: {e}{RST}")
    pause()

def _open_base_dir():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(BASE_DIR))
        else:
            subprocess.Popen(["xdg-open", str(BASE_DIR)])
        print(f"\n  {GRAD[0]}✓ {TXT}Корневая папка открыта: {BASE_DIR}{RST}")
    except Exception as e:
        print(f"\n  {R}Не удалось открыть папку: {e}{RST}")
    pause()

def _stop_svc_cmd(name):
    r = _stop_service(name)
    if r is not None and ("SUCCESS" in r.stdout.upper() or r.returncode == 0):
        print(f"\n  {GRAD[0]}✓ {TXT}{name} остановлен.{RST}")
    else:
        details = (r.stdout.strip() if r is not None else "нет ответа")
        print(f"\n  {Y}{name}: {details}{RST}")
    pause()

def _remove_one_service(name):
    if not is_admin():
        print(f"\n  {R}Нужны права администратора!{RST}"); pause(); return
    _stop_service(name)
    r = subprocess.run([SC_EXE, "delete", name], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"\n  {GRAD[0]}✓ {TXT}Сервис {name} удалён.{RST}")
        log(f"Сервис удалён: {name}")
    else:
        print(f"\n  {Y}Не удалось удалить {name}: {r.stdout.strip()}{RST}")
    pause()

def _remove_services():
    if not is_admin():
        print(f"\n  {R}Нужны права администратора!{RST}"); pause(); return
    _stop_service(ZAPRET_SVC_NAME)
    subprocess.run([SC_EXE, "delete", ZAPRET_SVC_NAME], capture_output=True)
    try:
        set_tg_autostart(False, PROJECTS.tgproxy.executable)
    except Exception:
        pass
    print(f"\n  {GRAD[0]}✓ {TXT}zapret-сервис и автозапуск TG удалены.{RST}")
    log("Удалён zapret-сервис и автозапуск TG")
    pause()

def _delete_files(directory: Path, label: str, state: dict, clear_key: str):
    if not directory.exists():
        print(f"\n  {Y}{label}: файлы не найдены ({directory}){RST}")
        pause(); return

    print(f"\n  {TXT}Останавливаем сервис {label}...{RST}")
    if "zapret" in label:
        _stop_service(ZAPRET_SVC_NAME)
        subprocess.run([SC_EXE, "delete", ZAPRET_SVC_NAME], capture_output=True)
        _kill_winws()
    else:
        PROJECTS.tgproxy.stop()
        _set_tgproxy_autostart(False)

    print(f"  {TXT}Удаляем файлы: {DIM}{directory}{RST}")
    try:
        shutil.rmtree(directory)
        state[clear_key] = None
        if "zapret" in label:
            state["zapret_strategy"] = None
        save_state(state)
        print(f"  {GRAD[0]}✓ {TXT}Файлы {label} удалены.{RST}")
        log(f"Файлы удалены: {label}")
    except Exception as e:
        print(f"  {R}Ошибка при удалении: {e}{RST}")
    pause()


def _switch_to_gui():
    try:
        hide_console()
        from ru_helper_gui import main as gui_main
        switched_to_console = gui_main()
        if switched_to_console:
            show_console()
            cls()
            print(f"\n  {GRAD[0]}✓ {TXT}Возврат в консольный режим.{RST}\n")
            return False
        else:
            sys.exit(0)
    except SystemExit:
        sys.exit(0)
    except Exception as e:
        show_console()
        print(f"\n  {R}Не удалось открыть HTML-окно: {e}{RST}")
        pause()
        return False


def _start_both_adapters(state):
    alt = state.get("zapret_strategy") or "по умолчанию"
    log(f"Запуск обоих адаптеров: zapret (альт: {alt}) и tg-ws-proxy")
    try:
        PROJECTS.zapret.start(state.get("zapret_strategy"))
        if not PROJECTS.tgproxy.is_running():
            PROJECTS.tgproxy.start()
        status = PROJECTS.status()
        if not status["zapret"]["winws"] or not status["tgproxy"]["running"]:
            raise RuntimeError("не оба процесса работают после запуска")
        log("Оба адаптера запущены и работают")
        print(f"\n  {GRAD[0]}✓ {TXT}Оба адаптера запущены и работают.{RST}")
    except Exception as e:
        log(f"Ошибка запуска обоих адаптеров: {e}")
        print(f"\n  {R}Не удалось запустить оба адаптера: {e}{RST}")
    pause()


def _stop_both_adapters():
    log("Остановка обоих адаптеров")
    try:
        PROJECTS.stop_all()
        status = PROJECTS.status()
        if status["zapret"]["winws"] or status["tgproxy"]["running"]:
            raise RuntimeError("не оба процесса остановились")
        log("Оба адаптера остановлены")
        print(f"\n  {GRAD[0]}✓ {TXT}Оба адаптера остановлены.{RST}")
    except Exception as e:
        log(f"Ошибка остановки обоих адаптеров: {e}")
        print(f"\n  {R}Не удалось остановить оба адаптера: {e}{RST}")
    pause()


def _read_main_command(prompt):
    if sys.platform != "win32":
        return input(prompt).strip().upper()

    import msvcrt

    print(prompt, end="", flush=True)
    chars = []
    while True:
        char = msvcrt.getwch()
        if char in ("\r", "\n"):
            print()
            return "".join(chars).strip().upper()
        if char == "\x03":
            raise KeyboardInterrupt
        if char == "\x12":  # Ctrl+R
            print("Ctrl+R")
            return "CTRL+R"
        if char == "\x05":  # Ctrl+E
            print("Ctrl+E")
            return "CTRL+E"
        if char in ("\x00", "\xe0"):
            msvcrt.getwch()
            continue
        if char == "\b":
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
            continue
        chars.append(char)
        print(char, end="", flush=True)


ZAPRET_README_TEXT = """
Flowseal/zapret-discord-youtube

NEW: Ускорение Telegram Desktop - https://github.com/Flowseal/tg-ws-proxy
Альтернатива https://github.com/bol-van/zapret-win-bundle
Также вы можете материально поддержать оригинального разработчика zapret тут:
https://github.com/bol-van/zapret?tab=readme-ov-file#%D0%BF%D0%BE%D0%B4%D0%B4%D0%B5%D1%80%D0%B6%D0%B0%D1%82%D1%8C-%D1%80%D0%B0%D0%B7%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D1%87%D0%B8%D0%BA%D0%B0

[!] ФЕЙКИ:
Я не веду никакие другие страницы/группы в телеграм/ютуб каналы.
Если вы наткнулись на что-то вне этой страницы гитхаба, что распространяется от моего лица - ФЕЙК.

[!] АНТИВИРУСЫ:
WinDivert может вызвать реакцию антивируса.
WinDivert - это инструмент для перехвата и фильтрации трафика, необходимый для работы zapret.
Он может использоваться как хорошими, так и плохими программами, но сам по себе не является вирусом.

Выдержка из readme.md репозитория bol-van/zapret-win-bundle:
Некоторые антивирусы склонны относить файлы WinDivert к классам повышенного риска или хакерским инструментам.
Происходит удаление файла и помещение его в карантин. При этом детект обязательно имеет название WinDivert или Not-a-virus:RiskTool.Multi.WinDivert.
Добавьте папку с запретом в исключения антивируса, либо отключите детектирование PUA.

[!] ВАЖНО:
Все бинарные файлы в папке bin взяты из zapret-win-bundle/zapret-winws и zapret/releases.
Вы можете это проверить с помощью хэшей/контрольных сумм. Проверяйте, что запускаете!

--------------------------------------------------------------------------------
Использование:
1. Включите Безопасный DNS (Secure DNS):
   * В Chrome - "Использовать безопасный DNS", выбрать поставщика услуг DNS.
   * В Firefox - "DNS через HTTPS", выбрать режим "Персональный", вписать https://dns.google/dns-query.
   * В Windows 11 поддерживается включение Secure DNS прямо в настройках ОС.
   * На роутере Keenetic включите опцию "Транзит запросов".
2. Скачайте архив со страницы последнего релиза.
3. Разблокируйте архив в свойствах файла при необходимости.
4. Распакуйте содержимое архива по пути без кириллицы и спец. символов.
5. Запустите нужный файл.

--------------------------------------------------------------------------------
Краткие описания файлов:
- general.bat ... - запуск стратегии вручную (ALT, FAKE и др.).
- service.bat - функции управления:
  * Install Service - установка любой стратегии в автозапуск (services.msc)
  * Remove Services - удаление стратегии и WinDivert из служб
  * Check Status - проверка статуса обхода и служб
  * Game Filter - переключение режима обхода для игр (UDP/TCP > 1023)
  * IPSet Filter - переключение режима обхода сервисов из ipset-all.txt
  * Auto-Update Check - вкл/выкл автоматическую проверку обновлений
  * Replace active fakes - замена фейка на другой из папки bin
  * Update IPSet List - обновление списка ipset-all.txt актуальным из репозитория
  * Update Hosts File - обновление hosts для починки веб-версии Telegram и Discord
  * Check for Updates - проверка на обновления
  * Run Diagnostics - диагностика частых проблем и очистка кэша Discord
  * Run Tests - запуск утилиты проверки стратегий

--------------------------------------------------------------------------------
Распространенные вопросы:
* Ни одна стратегия не подходит:
  В командной строке от имени администратора:
    netsh winsock reset
    netsh int ip reset all
    netsh winhttp reset proxy
    ipconfig /flushdns
  и перезагрузите компьютер.

* Не работает веб-версия Telegram или голосовой чат Discord:
  Запустите service.bat -> Update hosts file или используйте tg-ws-proxy.

* YouTube / Discord:
  Убедитесь в настройке Secure DNS, пробуйте разные general (ALT, ALT2, ALT9 и др.).
"""

TGPROXY_README_TEXT = """
TG WS Proxy (tg-ws-proxy)

Локальный MTProto-прокси для Telegram Desktop, который ускоряет работу Telegram,
перенаправляя трафик через WebSocket-соединения. Данные передаются в том же
зашифрованном виде, а для работы не нужны сторонние серверы.

[!] Реакция антивирусов:
Антивирусы часто ошибочно помечают приложение как вирус из-за упаковщика.
Если вы не можете скачать из-за блокировки антивирусом:
1) Попробуйте версию для Windows 7 (по функциональности не отличается)
2) Добавьте файл в исключения антивируса.

--------------------------------------------------------------------------------
Windows: быстрый вход
- Скачайте TgWsProxy_windows.exe (или запустите через ru Helper)
- При первом запуске откроется окно с инструкцией. Приложение сворачивается в трей.

Меню трея:
- Открыть в Telegram - настроить прокси через tg://proxy
- Скопировать ссылку - скопировать ссылку для подключения
- Перезапустить прокси - перезапуск без выхода
- Настройки... - GUI-редактор конфигурации
- Открыть логи - открыть файл логов
- Выход - остановить прокси и закрыть

Ручная настройка Telegram Desktop:
1. Telegram -> Настройки -> Продвинутые настройки -> Тип подключения -> Прокси
2. Добавьте MTProto прокси: Сервер 127.0.0.1, Порт 1443, Secret из настроек/логов.

--------------------------------------------------------------------------------
Как это работает:
Telegram Desktop -> MTProto Proxy (127.0.0.1:1443) -> WebSocket -> Telegram DC

1. Приложение поднимает MTProto прокси на 127.0.0.1:1443
2. Перехватывает подключения к IP-адресам Telegram
3. Извлекает DC ID из MTProto obfuscation init-пакета
4. Устанавливает WebSocket-соединение (TLS) к DC через домены Telegram
5. Если WS недоступен - автоматически переключается на CfProxy / прямое TCP-соединение

[!] Не грузит фото/видео?
Удалите в настройках прокси в DC -> IP всё, кроме 4:149.154.167.220
Если это не помогло, полностью очистите это поле.
"""

def show_readme_menu():
    while True:
        cls()
        for line in _grad_logo(): print(line)
        print(f"\n  {GRAD[0]}=== README проекта ==={RST}\n")
        def mi(num, text):
            return f"  {GRAD[7]}[{GRAD[0]}{num}{GRAD[7]}]{RST} {TXT}{text}{RST}"
        print(mi("1", "zapret (zapret-discord-youtube)"))
        print(mi("2", "tg-ws-proxy"))
        print(mi("0", "Назад"))
        print()
        ch = input(f"  {GRAD[0]}>{RST} ").strip()
        if ch == "1":
            cls()
            print(f"\n  {GRAD[0]}=== README: zapret-discord-youtube ==={RST}\n")
            print(ZAPRET_README_TEXT.strip())
            print()
            pause()
        elif ch == "2":
            cls()
            print(f"\n  {GRAD[0]}=== README: tg-ws-proxy ==={RST}\n")
            print(TGPROXY_README_TEXT.strip())
            print()
            pause()
        elif ch in ("0", ""):
            break

def _open_discussions_console():
    try:
        if webbrowser.open(DISCUSSIONS_URL):
            print(f"\n  {GRAD[0]}✓ {TXT}Открыты обсуждения zapret на GitHub.{RST}")
        else:
            print(f"\n  {R}Не удалось открыть браузер. Ссылка: {DISCUSSIONS_URL}{RST}")
    except Exception as exc:
        print(f"\n  {R}Не удалось открыть Discussions: {exc}{RST}")
    pause()


def main():
    ensure_admin()
    ensure_base_directory()
    if "--gui" in sys.argv[1:]:
        hide_console()
        from ru_helper_gui import main as gui_main
        switched_to_console = gui_main()
        if not switched_to_console:
            sys.exit(0)
        show_console()
    boot_animation()
    state = load_state()

    if ZAPRET_DIR.exists():
        try:
            patched = patch_service_bat(ZAPRET_DIR)
            if patched:
                log(f"Исправлен service.bat: {', '.join(str(p) for p in patched)}")
        except OSError as e:
            log(f"Не удалось исправить service.bat: {e}")

    try:
        zapret_latest, tgproxy_latest = get_latest_versions(state)
        if state.get("auto_update_tg", True) or state.get("auto_update_zapret", False):
            _check_and_apply_updates(state, force=False)
    except (KeyboardInterrupt, EOFError):
        return

    while True:
        draw_menu(zapret_latest, tgproxy_latest, state)
        try:
            ch = _read_main_command(f"  {GRAD[0]}>{RST} ")
        except EOFError:
            break

        if ch == "1":
            install_zapret(state)
        elif ch == "2":
            install_tgproxy(state)
        elif ch == "3":
            find_strategy(state)
        elif ch == "4":
            change_alt(state)
        elif ch == "5":
            services_menu(state)
        elif ch == "6":
            diagnostics()
        elif ch == "7":
            zapret_latest, tgproxy_latest = get_latest_versions(state)
        elif ch == "8":
            if _switch_to_gui():
                break
        elif ch in ("9", "CTRL+R"):
            _start_both_adapters(state)
        elif ch in ("10", "CTRL+E"):
            _stop_both_adapters()
        elif ch == "11":
            _open_base_dir()
        elif ch == "12":
            show_readme_menu()
        elif ch == "L":
            show_recent_logs()
        elif ch == "D":
            _open_discussions_console()
        elif ch == "0":
            cls()
            print(f"\n  {TXT}пока!{RST}\n")
            break

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        cls()
        print(f"\n  {TXT}Выход.{RST}\n")
    except EOFError:
        cls()
        print(f"\n  {TXT}Выход.{RST}\n")
    except Exception as e:
        print(f"\n  {R}Критическая ошибка: {e}{RST}")
        import traceback
        traceback.print_exc()
        input("\n  Нажмите Enter для выхода...")