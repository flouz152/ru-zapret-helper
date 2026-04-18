#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ru Helper — управление zapret-discord-youtube и tg-ws-proxy
"""

import os
import sys
import time
import json
import shutil
import zipfile
import threading
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

# ─── Windows ANSI + отключение QuickEdit ────────────────────────────────────
if sys.platform == "win32":
    import ctypes
    kernel32 = ctypes.windll.kernel32
    # Включаем ANSI
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    # Отключаем QuickEdit и Insert mode чтобы клик по окну не прерывал программу
    handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
    mode = ctypes.c_ulong()
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    # Убираем ENABLE_QUICK_EDIT_MODE (0x0040) и ENABLE_INSERT_MODE (0x0020)
    mode.value &= ~0x0040
    mode.value &= ~0x0020
    kernel32.SetConsoleMode(handle, mode)

# ─── Цвета ───────────────────────────────────────────────────────────────────
R   = "\033[91m"
Y   = "\033[93m"
DIM = "\033[2m"
RST = "\033[0m"
TXT = "\033[97m"  # белый

def _rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"

# Градиент: лайм #00ff41 → тёмно-зелёный #003d10
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

# ─── Константы ───────────────────────────────────────────────────────────────
ZAPRET_API  = "https://api.github.com/repos/Flowseal/zapret-discord-youtube/releases/latest"
TGPROXY_API = "https://api.github.com/repos/Flowseal/tg-ws-proxy/releases/latest"

BASE_DIR    = Path(os.environ.get("APPDATA", ".")) / "ru_helper"
ZAPRET_DIR  = BASE_DIR / "zapret"
TGPROXY_DIR = BASE_DIR / "tg-ws-proxy"
STATE_FILE  = BASE_DIR / "state.json"
LOG_FILE    = BASE_DIR / "ru_helper.log"

ZAPRET_SVC_NAME  = "zapret"
TGPROXY_SVC_NAME = "TgWsProxy"

DIAG_TARGETS = [
    ("Discord",   "https://discord.com"),
    ("YouTube",   "https://www.youtube.com"),
    ("Telegram",  "https://web.telegram.org"),
    ("GitHub",    "https://github.com"),
]

# ─── Утилиты ─────────────────────────────────────────────────────────────────

def cls():
    os.system("cls" if sys.platform == "win32" else "clear")

def pause(msg="Нажмите Enter для продолжения..."):
    input(f"\n{DIM}{msg}{RST}")

def is_admin():
    if sys.platform == "win32":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    return os.geteuid() == 0

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {"zapret_version": None, "tgproxy_version": None,
            "zapret_strategy": None}

def save_state(state):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ru-helper/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())

def http_check(url, timeout=5):
    """Возвращает (ok: bool, ms: int, status: str)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ms = int((time.time() - t0) * 1000)
            return True, ms, str(r.status)
    except urllib.error.HTTPError as e:
        ms = int((time.time() - t0) * 1000) if 't0' in dir() else 0
        return e.code < 500, 0, str(e.code)
    except Exception as e:
        return False, 0, str(e)[:30]

def log(msg):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

def download_file(url, dest: Path, label=""):
    req = urllib.request.Request(url, headers={"User-Agent": "ru-helper/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            total = int(r.headers.get("Content-Length", 0))
            done  = 0
            chunk = 65536
            with open(dest, "wb") as f:
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
    except KeyboardInterrupt:
        print(f"\n  {Y}Скачивание прервано.{RST}")
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise
    print()

# ─── ASCII логотип ────────────────────────────────────────────────────────────
# "ru Helper" — ru маленькими, Helper с большой
# Каждая строка логотипа окрашивается в свой оттенок градиента

_LOGO_LINES = [
    r"                 _   _      _                  ",
    r"   _ __ _   _   | | | | ___| |_ __   ___ _ __ ",
    r"  | '__| | | |  | |_| |/ _ \ | '_ \ / _ \ '__|",
    r"  | |  | |_| |  |  _  |  __/ | |_) |  __/ |   ",
    r"  |_|   \__,_|  |_| |_|\___|_| .__/ \___|_|   ",
    r"                              |_|               ",
]

def _grad_logo():
    """Возвращает строки логотипа с градиентом по строкам."""
    result = []
    n = len(_LOGO_LINES)
    for i, line in enumerate(_LOGO_LINES):
        gi = int(i * 7 / max(n - 1, 1))
        result.append(f"{GRAD[gi]}{line}{RST}")
    result.append(f"{TXT}{DIM}         управление zapret-discord-youtube & tg-ws-proxy{RST}")
    return result

def _grad_line(text):
    """Красит каждый символ строки с градиентом слева направо."""
    out = ""
    n = max(len(text), 1)
    for i, ch in enumerate(text):
        gi = int(i * 7 / n)
        out += f"{GRAD[gi]}{ch}"
    return out + RST

def spinner_dots(stop_event, prefix):
    """Анимация точек пока stop_event не установлен."""
    frames = ["   ", ".  ", ".. ", "..."]
    i = 0
    while not stop_event.is_set():
        dots = f"{GRAD[i % 4]}{frames[i % 4]}{RST}"
        print(f"\r  {TXT}{prefix}{RST}{dots}", end="", flush=True)
        i += 1
        time.sleep(0.3)

def boot_animation():
    """Анимация запуска: логотип появляется построчно, затем полоска typewriter."""
    cls()
    logo_lines = _grad_logo()
    for line in logo_lines:
        print(line)
        time.sleep(0.07)
    time.sleep(0.15)
    # Полоска появляется символ за символом
    sep_char = "─"
    sep_len  = 50
    print("  ", end="", flush=True)
    for i in range(sep_len):
        gi = int(i * 7 / sep_len)
        print(f"{GRAD[gi]}{sep_char}{RST}", end="", flush=True)
        time.sleep(0.018)
    print()
    time.sleep(0.1)

def version_line(label, latest, installed):
    """Возвращает строку с цветной версией."""
    lbl = f"{TXT}{label}{RST}"
    if installed and installed != latest:
        ver_str = f"{R}{installed} → {latest}{RST}"
    elif installed == latest:
        ver_str = f"{GRAD[0]}{installed}{RST}"
    else:
        ver_str = f"{Y}{latest} {DIM}(не установлен){RST}"
    return f"  {lbl} {ver_str}"

# ─── GitHub API ───────────────────────────────────────────────────────────────

def get_latest_versions(state):
    """Получает последние версии в фоне."""
    results = {}

    def fetch_zapret():
        try:
            data = fetch_json(ZAPRET_API)
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
    spin = threading.Thread(
        target=spinner_dots, args=(stop, "Проверка версий"), daemon=True)
    spin.start()

    t1.join(); t2.join()
    stop.set()
    spin.join()
    print("\r" + " " * 50 + "\r", end="")

    return results.get("zapret", "?"), results.get("tgproxy", "?")

# ─── Главное меню ─────────────────────────────────────────────────────────────

def draw_menu(zapret_latest, tgproxy_latest, state):
    cls()
    for line in _grad_logo():
        print(line)
    print()

    sep = _grad_line("  " + "─" * 50)
    print(sep)

    # Версии
    print(version_line("ds-yt zapret  :", zapret_latest, state.get("zapret_version")))
    print(version_line("tg-ws-proxy   :", tgproxy_latest, state.get("tgproxy_version")))

    # Активный альт
    alt = state.get("zapret_strategy")
    alt_str = f"{GRAD[1]}{alt}{RST}" if alt else f"{DIM}не выбран{RST}"
    print(f"  {TXT}активный альт :{RST} {alt_str}")

    # Статус сервисов
    zap_st = _svc_status(ZAPRET_SVC_NAME)
    tgp_st = _svc_status(TGPROXY_SVC_NAME)
    print(f"  {TXT}zapret svc    :{RST} {_svc_color(zap_st)}  {TXT}tg-ws-proxy svc:{RST} {_svc_color(tgp_st)}")

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
    print(mi("0", "Выход"))
    print()

# ─── Установка zapret ─────────────────────────────────────────────────────────

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
    try:
        data = fetch_json(ZAPRET_API)
    except Exception as e:
        stop.set(); spin.join()
        print(f"\n  {R}Ошибка: {e}{RST}")
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

    if ZAPRET_DIR.exists():
        print(f"  {TXT}Удаляем старую установку...{RST}")
        # Сначала останавливаем всё что может держать файлы
        _kill_winws()
        subprocess.run(["sc", "stop",   "zapret"],    capture_output=True)
        subprocess.run(["sc", "stop",   "WinDivert"], capture_output=True)
        subprocess.run(["sc", "stop",   "WinDivert14"], capture_output=True)
        time.sleep(1)
        shutil.rmtree(ZAPRET_DIR, ignore_errors=True)

    try:
        download_file(url, tmp, "Скачивание")
    except KeyboardInterrupt:
        pause(); return

    print(f"  {TXT}Распаковка...{RST}")
    try:
        with zipfile.ZipFile(tmp, "r") as z:
            z.extractall(ZAPRET_DIR)
    except PermissionError as e:
        tmp.unlink(missing_ok=True)
        print(f"\n  {R}Ошибка доступа при распаковке: {e}{RST}")
        print(f"  {Y}Возможные причины:{RST}")
        print(f"  {DIM}• Антивирус блокирует WinDivert64.sys — добавьте папку в исключения{RST}")
        print(f"  {DIM}• Файл занят другим процессом — перезагрузите ПК и попробуйте снова{RST}")
        pause(); return
    tmp.unlink(missing_ok=True)

    subdirs = [d for d in ZAPRET_DIR.iterdir() if d.is_dir()]
    if len(subdirs) == 1 and not any(ZAPRET_DIR.glob("*.bat")):
        inner = subdirs[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(ZAPRET_DIR / item.name))
        inner.rmdir()

    state["zapret_version"] = tag
    save_state(state)
    print(f"\n  {GRAD[0]}Готово! {TXT}zapret установлен в:{RST}")
    print(f"  {DIM}{ZAPRET_DIR}{RST}")
    pause()

# ─── Установка tg-ws-proxy ────────────────────────────────────────────────────

def install_tgproxy(state):
    cls()
    for line in _grad_logo(): print(line)
    print(f"\n  {GRAD[0]}=== Установка tg-ws-proxy ==={RST}\n")

    if not is_admin():
        print(f"  {R}Требуются права администратора!{RST}")
        pause(); return

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
    asset = next((a for a in data["assets"] if "windows" in a["name"].lower() and a["name"].endswith(".exe")), None)
    if not asset:
        asset = next((a for a in data["assets"] if a["name"].endswith(".exe")), None)
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

    state["tgproxy_version"] = tag
    save_state(state)
    print(f"\n  {GRAD[0]}Готово! {TXT}tg-ws-proxy установлен в:{RST}")
    print(f"  {DIM}{dest}{RST}")
    pause()

# ─── Поиск рабочей стратегии ─────────────────────────────────────────────────

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

    # Проверяем наличие встроенного тест-скрипта zapret
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

        # Запускаем ps1 и ждём завершения
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(ps1)],
            cwd=str(ZAPRET_DIR),
        )

        # Ищем последний файл результатов
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
        # Fallback: собственный тест — быстрый вариант (5 сек + curl)
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

            # Параллельная проверка discord + youtube через curl
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
    """Параллельная проверка discord.com и youtube.com через curl (5 сек таймаут)."""
    targets = ["https://discord.com", "https://www.youtube.com"]
    results = {}

    def _check(url):
        try:
            t0 = time.time()
            r = subprocess.run(
                ["curl", "-s", "-o", "NUL", "-w", "%{http_code}",
                 "-m", "5", "--http1.1", "-I", url],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            ms = int((time.time() - t0) * 1000)
            code = r.stdout.strip()
            results[url] = (code not in ("", "000") and int(code or 0) < 500, ms)
        except Exception:
            results[url] = (False, 0)

    threads = [threading.Thread(target=_check, args=(u,), daemon=True) for u in targets]
    for t in threads: t.start()
    for t in threads: t.join(timeout=7)

    ok_count = sum(1 for ok, _ in results.values() if ok)
    avg_ms   = int(sum(ms for _, ms in results.values() if ms) / max(ok_count, 1))
    return ok_count >= 1, avg_ms


def _parse_ps1_results(results_dir: Path):
    """Парсит последний файл результатов test zapret.ps1 и возвращает лучший альт."""
    if not results_dir.exists():
        return None
    files = sorted(results_dir.glob("test_results_*.txt"), reverse=True)
    if not files:
        return None
    content = files[0].read_text(encoding="utf-8", errors="ignore")
    # Ищем строку "Best strategy: ..." или "Best config: ..."
    for line in content.splitlines():
        if line.lower().startswith("best strategy:") or line.lower().startswith("best config:"):
            val = line.split(":", 1)[-1].strip()
            # Убираем расширение .bat если есть
            val = val.replace(".bat", "").strip()
            if val:
                return val
    return None


def _manual_alt_pick(bats, state):
    """Предлагает выбрать альт вручную из списка."""
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
    """Позволяет вручную выбрать альт из списка."""
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
    """Проверяет доступность ключевых сервисов."""
    cls()
    for line in _grad_logo(): print(line)
    print(f"\n  {GRAD[0]}=== Диагностика соединения ==={RST}\n")
    log("=== Диагностика ===")

    col_w = 12
    print(f"  {TXT}{'Сервис':<{col_w}} {'Статус':<12} {'Пинг':<10} {'HTTP'}{RST}")
    print(f"  {_grad_line('─' * 46)}")

    for name, url in DIAG_TARGETS:
        # Спиннер пока идёт запрос
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

def _svc_color(s):
    if s == "running":  return f"{GRAD[0]}● запущен{RST}"
    if s == "stopped":  return f"{Y}○ остановлен{RST}"
    if s == "missing":  return f"{R}✗ не установлен{RST}"
    return f"{DIM}{s}{RST}"

# ─── Управление сервисами ─────────────────────────────────────────────────────

def services_menu(state):
    while True:
        cls()
        for line in _grad_logo(): print(line)
        print(f"\n  {GRAD[0]}=== Управление сервисами ==={RST}\n")

        zap_status = _svc_status(ZAPRET_SVC_NAME)
        tgp_status = _svc_status(TGPROXY_SVC_NAME)

        alt = state.get("zapret_strategy", "не выбран")
        print(f"  {TXT}zapret      :{RST} {_svc_color(zap_status)}  {DIM}(альт: {alt}){RST}")
        print(f"  {TXT}tg-ws-proxy :{RST} {_svc_color(tgp_status)}")
        print()

        def mi(num, text):
            return f"  {GRAD[7]}[{GRAD[0]}{num}{GRAD[7]}]{RST} {TXT}{text}{RST}"

        print(mi("1", "Установить zapret в автозапуск (активный альт)"))
        print(mi("2", "Установить tg-ws-proxy в автозапуск"))
        print(mi("3", "Запустить zapret сейчас"))
        print(mi("4", "Запустить tg-ws-proxy сейчас"))
        print(mi("5", "Остановить zapret"))
        print(mi("6", "Остановить tg-ws-proxy"))
        print(mi("7", "Удалить zapret из автозапуска"))
        print(mi("8", "Удалить tg-ws-proxy из автозапуска"))
        print(mi("9", "Удалить оба сервиса"))
        print(mi("A", "Удалить файлы zapret"))
        print(mi("B", "Удалить файлы tg-ws-proxy"))
        print(mi("C", "Удалить файлы обеих программ"))
        print(mi("0", "Назад"))
        print()

        ch = input(f"  {GRAD[0]}>{RST} ").strip().upper()

        if ch == "1":   _install_zapret_service(state)
        elif ch == "2": _install_tgproxy_service(state)
        elif ch == "3": _start_zapret_now(state)
        elif ch == "4": _start_tgproxy_now()
        elif ch == "5": _stop_svc_cmd(ZAPRET_SVC_NAME)
        elif ch == "6": _stop_svc_cmd(TGPROXY_SVC_NAME)
        elif ch == "7": _remove_one_service(ZAPRET_SVC_NAME)
        elif ch == "8": _remove_one_service(TGPROXY_SVC_NAME)
        elif ch == "9": _remove_services()
        elif ch == "A": _delete_files(ZAPRET_DIR, "zapret", state, clear_key="zapret_version")
        elif ch == "B": _delete_files(TGPROXY_DIR, "tg-ws-proxy", state, clear_key="tgproxy_version")
        elif ch == "C": _delete_files(ZAPRET_DIR, "zapret", state, clear_key="zapret_version"); _delete_files(TGPROXY_DIR, "tg-ws-proxy", state, clear_key="tgproxy_version")
        elif ch == "0": break

def _svc_status(name):
    try:
        r = subprocess.run(
            ["sc", "query", name],
            capture_output=True, text=True
        )
        if "does not exist" in r.stdout or r.returncode == 1060:
            return "missing"
        if "RUNNING" in r.stdout:
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

    bat = ZAPRET_DIR / "service.bat"
    if not bat.exists():
        print(f"\n  {R}service.bat не найден в {ZAPRET_DIR}{RST}"); pause(); return

    print(f"\n  {TXT}Устанавливаем сервис через service.bat...{RST}")
    print(f"  {DIM}Альт: {alt}{RST}\n")
    subprocess.run(["cmd", "/c", str(bat)], cwd=str(ZAPRET_DIR))
    pause()

def _install_tgproxy_service(state):
    if not is_admin():
        print(f"\n  {R}Нужны права администратора!{RST}"); pause(); return

    exe = next(TGPROXY_DIR.glob("TgWsProxy*.exe"), None) if TGPROXY_DIR.exists() else None
    if not exe:
        print(f"\n  {R}tg-ws-proxy не установлен. Сначала установите (пункт 2).{RST}")
        pause(); return

    svc = TGPROXY_SVC_NAME
    _stop_service(svc)
    subprocess.run(["sc", "delete", svc], capture_output=True)
    time.sleep(1)

    r = subprocess.run([
        "sc", "create", svc,
        "binPath=", f'"{exe}"',
        "start=", "auto",
        "DisplayName=", "TG WS Proxy"
    ], capture_output=True, text=True)

    if r.returncode == 0:
        subprocess.run(["sc", "start", svc], capture_output=True)
        print(f"\n  {GRAD[0]}Сервис {TXT}{svc} установлен и запущен.{RST}")
    else:
        print(f"\n  {R}Ошибка: {r.stdout}{r.stderr}{RST}")
    pause()

def _start_zapret_now(state):
    alt = state.get("zapret_strategy")
    if not alt:
        print(f"\n  {R}Альт не выбран. Запустите поиск (пункт 3).{RST}"); pause(); return
    bat = ZAPRET_DIR / f"{alt}.bat"
    if not bat.exists():
        bat = ZAPRET_DIR / "general.bat"
    if not bat.exists():
        print(f"\n  {R}Файл альта не найден.{RST}"); pause(); return
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        cwd=str(ZAPRET_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    )
    print(f"\n  {GRAD[0]}✓ {TXT}zapret запущен в фоне.{RST}")
    pause()

def _start_tgproxy_now():
    exe = next(TGPROXY_DIR.glob("TgWsProxy*.exe"), None) if TGPROXY_DIR.exists() else None
    if not exe:
        print(f"\n  {R}tg-ws-proxy не установлен.{RST}"); pause(); return
    subprocess.Popen(
        [str(exe)],
        cwd=str(TGPROXY_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    )
    print(f"\n  {GRAD[0]}✓ {TXT}tg-ws-proxy запущен в фоне.{RST}")
    pause()

def _stop_svc_cmd(name):
    r = subprocess.run(["sc", "stop", name], capture_output=True, text=True)
    if "SUCCESS" in r.stdout or r.returncode == 0:
        print(f"\n  {GRAD[0]}✓ {TXT}{name} остановлен.{RST}")
    else:
        print(f"\n  {Y}{name}: {r.stdout.strip() or 'нет ответа'}{RST}")
    pause()

def _remove_one_service(name):
    if not is_admin():
        print(f"\n  {R}Нужны права администратора!{RST}"); pause(); return
    subprocess.run(["sc", "stop",   name], capture_output=True)
    r = subprocess.run(["sc", "delete", name], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"\n  {GRAD[0]}✓ {TXT}Сервис {name} удалён.{RST}")
        log(f"Сервис удалён: {name}")
    else:
        print(f"\n  {Y}Не удалось удалить {name}: {r.stdout.strip()}{RST}")
    pause()

def _remove_services():
    if not is_admin():
        print(f"\n  {R}Нужны права администратора!{RST}"); pause(); return
    for svc in [ZAPRET_SVC_NAME, TGPROXY_SVC_NAME]:
        subprocess.run(["sc", "stop",   svc], capture_output=True)
        subprocess.run(["sc", "delete", svc], capture_output=True)
    print(f"\n  {GRAD[0]}✓ {TXT}Оба сервиса удалены из автозапуска.{RST}")
    log("Оба сервиса удалены")
    pause()

def _delete_files(directory: Path, label: str, state: dict, clear_key: str):
    """Останавливает сервис, удаляет папку с файлами программы."""
    if not directory.exists():
        print(f"\n  {Y}{label}: файлы не найдены ({directory}){RST}")
        pause(); return

    svc = ZAPRET_SVC_NAME if "zapret" in label else TGPROXY_SVC_NAME
    print(f"\n  {TXT}Останавливаем сервис {label}...{RST}")
    subprocess.run(["sc", "stop",   svc], capture_output=True)
    subprocess.run(["sc", "delete", svc], capture_output=True)
    if "zapret" in label:
        _kill_winws()

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

# ─── Точка входа ──────────────────────────────────────────────────────────────

def main():
    boot_animation()
    state = load_state()

    # Первичная загрузка версий
    zapret_latest, tgproxy_latest = get_latest_versions(state)

    while True:
        draw_menu(zapret_latest, tgproxy_latest, state)
        ch = input(f"  {GRAD[0]}>{RST} ").strip()

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
    except Exception as e:
        print(f"\n  {R}Критическая ошибка: {e}{RST}")
        import traceback
        traceback.print_exc()
        input("\n  Нажмите Enter для выхода...")
