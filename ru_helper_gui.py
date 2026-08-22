import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tempfile
import urllib.request
import webbrowser
import zipfile
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview
import ru_helper as core
from project_adapters import (
    UnifiedAdapter,
    load_tg_config,
    save_tg_config,
    select_tgproxy_asset,
    set_tg_autostart,
    tg_proxy_url,
)

try:
    from winotify import Notification
except ImportError:
    Notification = None

GUI_LOG_FILE = core.BASE_DIR / "ru_helper_gui.log"


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
                user32.ShowWindow(hwnd, 9)
                user32.ShowWindow(hwnd, 5)
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


def _clean_markup_text(value):
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


class _DiscussionListParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.active = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        href = attributes.get("href", "")
        if tag == "a" and "discussion" in href and re.search(r"/discussions/\d+", href):
            number = re.search(r"/discussions/(\d+)", href).group(1)
            self.active = {"number": number, "url": "https://github.com" + href}
            self.parts = []

    def handle_data(self, data):
        if self.active is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.active is not None:
            self.active["title"] = _clean_markup_text("".join(self.parts))
            if self.active["title"]:
                self.items.append(self.active)
            self.active = None
            self.parts = []


class _DiscussionDetailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.number = ""
        self.bodies = []
        self.title_active = False
        self.title_parts = []
        self.body_parts = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "")
        if tag == "span" and "js-issue-title" in classes:
            self.title_active = True
            self.title_parts = []
        if tag == "td" and "comment-body" in classes:
            self.body_parts = []
        if self.body_parts is not None and tag in {"br", "p", "div", "li", "pre", "h1", "h2", "h3"}:
            self.body_parts.append("\n")

    def handle_data(self, data):
        if self.title_active:
            self.title_parts.append(data)
        if self.body_parts is not None:
            self.body_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "span" and self.title_active:
            self.title = _clean_markup_text("".join(self.title_parts))
            self.title_active = False
        if tag == "td" and self.body_parts is not None:
            value = re.sub(r"\n{3,}", "\n\n", "".join(self.body_parts)).strip()
            if value:
                self.bodies.append(value)
            self.body_parts = None


class DiscussionsClient:
    base_url = "https://github.com/Flowseal/zapret-discord-youtube/discussions"

    @staticmethod
    def _get(url):
        request = urllib.request.Request(url, headers={"User-Agent": "ru-helper/1.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", "replace")

    @classmethod
    def list_page(cls, page):
        parser = _DiscussionListParser()
        parser.feed(cls._get(f"{cls.base_url}?page={page}"))
        return parser.items

    @classmethod
    def detail(cls, number):
        parser = _DiscussionDetailParser()
        parser.feed(cls._get(f"{cls.base_url}/{number}"))
        parser.number = str(number)
        return {"title": parser.title or f"Обсуждение #{number}", "number": str(number), "bodies": parser.bodies}


def _download(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "ru-helper/1.0"})
    part = destination.with_name(destination.name + ".part")
    part.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(part, "wb") as output:
            while chunk := response.read(65536):
                output.write(chunk)
        part.replace(destination)
    except Exception:
        part.unlink(missing_ok=True)
        raise


def _parse_alt_results(results_dir):
    if not results_dir.exists():
        return [], None
    files = sorted(results_dir.glob("test_results_*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return [], None
    try:
        text = files[0].read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return [], None
    results = []
    analytics = {}
    current = None
    in_analytics = False
    best = None
    for raw in text.splitlines():
        line = raw.strip()
        match = re.match(r"Config:\s*(.*?)\s+\(Type:\s*(.*?)\)$", line)
        if match:
            current = {"name": Path(match.group(1)).stem, "type": match.group(2), "summary": "", "ok": False, "best": False}
            results.append(current)
            continue
        if line == "=== ANALYTICS ===":
            in_analytics = True
            continue
        if line.lower().startswith("best strategy:"):
            best = line.split(":", 1)[1].strip()
            if best.lower().endswith(".bat"):
                best = best[:-4].strip()
            continue
        if in_analytics and line:
            name, separator, summary = line.partition(" :")
            if separator:
                analytics[Path(name.strip()).stem] = summary.strip()
        elif current and line:
            current.setdefault("details", []).append(line)
    for item in results:
        summary = analytics.get(item["name"], "")
        if not summary:
            summary = " · ".join(item.get("details", [])[-2:]) or "нет данных"
        item["summary"] = summary
        numbers = [int(value) for value in re.findall(r"(?:HTTP OK|OK):\s*(\d+)", summary)]
        failures = [int(value) for value in re.findall(r"(?:ERR|FAIL):\s*(\d+)", summary)]
        item["ok"] = bool(numbers and max(numbers) > 0 and (not failures or max(failures) == 0))
        if best and item["name"].lower() == Path(best).stem.lower():
            item["best"] = True
    return results, best


def _automated_test_script(source, zapret_root):
    root_literal = str(zapret_root).replace("'", "''")
    source = source.replace(
        "$rootDir = Split-Path $PSScriptRoot",
        "$rootDir = '" + root_literal + "'",
        1,
    )
    start = source.find("function Read-TestType {")
    config_start = source.find("function Read-ConfigSelection", start)
    if start < 0 or config_start < 0:
        raise RuntimeError("В test zapret.ps1 не найдены функции выбора теста")
    replacement = "function Read-TestType { return 'standard' }\nfunction Read-ModeSelection { return 'all' }\n"
    source = source[:start] + replacement + source[config_start:]
    source = source.replace("while ($true) {\n    $globalResults = @()", "if ($true) {\n    $globalResults = @()", 1)
    source = source.replace("[void][System.Console]::ReadKey($true)", "$null")
    source = source.replace("-WindowStyle Minimized", "-WindowStyle Hidden")
    return source


def _notify(title, message):
    if Notification is None or sys.platform != "win32":
        return
    try:
        Notification(app_id="ru Helper", title=title, msg=message).show()
    except Exception:
        pass


class Backend:
    def __init__(self):
        self.state = core.load_state()
        state_changed = "auto_update_tg" not in self.state or "auto_update_zapret" not in self.state
        self.state.setdefault("auto_update_tg", True)
        self.state.setdefault("auto_update_zapret", False)
        if state_changed:
            core.save_state(self.state)
        self.projects = UnifiedAdapter()
        self.latest = {"zapret": "…", "tgproxy": "…"}
        self.diagnostics = [{"name": name, "status": "—", "ms": "—", "http": "—", "ok": False} for name, _ in core.DIAG_TARGETS]
        self.alt_results, best = _parse_alt_results(core.ZAPRET_DIR / "utils" / "test results")
        if best and not self.state.get("zapret_strategy"):
            self.state["zapret_strategy"] = best
            core.save_state(self.state)
        self.active_subprocesses = []
        self.busy = False
        self.status = "Готово"
        self.message = ""
        self.logs = []
        self.switch_to_console = False
        self.window = None
        self.server = None
        self.lock = threading.RLock()
        try:
            GUI_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            GUI_LOG_FILE.touch(exist_ok=True)
        except OSError:
            pass

    def _log(self, message):
        with self.lock:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            entry = f"[{stamp}] {message}"
            self.logs.append(entry)
            del self.logs[:-150]
            try:
                with GUI_LOG_FILE.open("a", encoding="utf-8") as log_file:
                    log_file.write(entry + "\n")
            except OSError:
                pass

    def snapshot(self):
        with self.lock:
            state = dict(self.state)
            busy = self.busy
            status = self.status
            message = self.message
            latest = dict(self.latest)
            diagnostics = list(self.diagnostics)
            logs = list(self.logs)
            alt_results = [dict(item) for item in self.alt_results]
            strategies = [p.stem for p in self.projects.zapret.strategies()]
        return {
            "busy": busy,
            "status": status,
            "message": message,
            "state": state,
            "latest": latest,
            "projects": self.projects.status(),
            "strategies": strategies,
            "diagnostics": diagnostics,
            "logs": logs,
            "alt_results": alt_results,
        }

    def submit(self, action, data):
        with self.lock:
            if self.busy:
                return {"message": "Предыдущая операция ещё выполняется", "busy": True}
            self.busy = True
            self.status = "Выполняется: " + action
            self.message = ""
            self._log("Запущена операция: " + action)
        threading.Thread(target=self._worker, args=(action, data), daemon=True).start()
        return {"message": "Операция запущена", "busy": True}

    def _worker(self, action, data):
        try:
            func = self._actions.get(action)
            if not func:
                raise ValueError(f"Неизвестная операция: {action}")
            result = func(self, data)
            with self.lock:
                self.status = result or "Готово"
                self.message = result or "Готово"
                self._log("Завершено: " + (result or "Готово"))
        except Exception as exc:
            with self.lock:
                self.status = "Ошибка"
                self.message = str(exc)
                self._log("Ошибка: " + str(exc))
        finally:
            with self.lock:
                self.busy = False

    def install_zapret(self, _data):
        if not core.is_admin():
            raise PermissionError("Для установки zapret требуются права администратора")
        data = None
        try:
            data = core.fetch_json(core.ZAPRET_API)
        except Exception as exc:
            self._log(f"Основной репозиторий недоступен: {exc}. Пробуем bol-van/zapret-win-bundle...")
            try:
                data = core.fetch_json(core.ZAPRET_FALLBACK_API)
            except Exception as fallback_exc:
                raise RuntimeError(f"Не удалось получить релиз zapret: {fallback_exc}")
        asset = next((item for item in data.get("assets", []) if item.get("name", "").lower().endswith(".zip")), None)
        if not asset:
            raise RuntimeError("В последнем релизе не найден ZIP-архив")
        core.BASE_DIR.mkdir(parents=True, exist_ok=True)
        archive = core.BASE_DIR / Path(asset["name"]).name
        staging = core.BASE_DIR / ".zapret-staging"
        try:
            _download(asset["browser_download_url"], archive)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            core.safe_extract_zip(archive, staging)
            subdirs = [item for item in staging.iterdir() if item.is_dir()]
            if len(subdirs) == 1 and not any(staging.glob("*.bat")):
                for item in subdirs[0].iterdir():
                    shutil.move(str(item), str(staging / item.name))
                subdirs[0].rmdir()
            core.patch_service_bat(staging)
            if core.ZAPRET_DIR.exists():
                core._kill_winws()
                for service in (core.ZAPRET_SVC_NAME, "WinDivert", "WinDivert14"):
                    core._stop_service(service)
                shutil.rmtree(core.ZAPRET_DIR, ignore_errors=True)
            staging.replace(core.ZAPRET_DIR)
            self.state["zapret_version"] = data.get("tag_name")
            core.save_state(self.state)
            return "zapret успешно установлен"
        finally:
            archive.unlink(missing_ok=True)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def install_tg(self, _data):
        data = core.fetch_json(core.TGPROXY_API)
        asset = select_tgproxy_asset(data.get("assets", []))
        if not asset:
            raise RuntimeError("В релизе не найден подходящий EXE-файл")
        target = core.TGPROXY_DIR / Path(asset["name"]).name
        was_running = self.projects.tgproxy.is_running()
        if was_running:
            self.projects.tgproxy.stop()
        try:
            for old in core.TGPROXY_DIR.glob("TgWsProxy*.exe"):
                if old.name.lower() != target.name.lower():
                    old.unlink(missing_ok=True)
            _download(asset["browser_download_url"], target)
        finally:
            if was_running:
                self.projects.tgproxy.start()
        self.state["tgproxy_version"] = data.get("tag_name")
        core.save_state(self.state)
        return "tg-ws-proxy успешно установлен / обновлён"

    def refresh(self, _data):
        versions = core.get_latest_versions(self.state, show_progress=False)
        with self.lock:
            self.latest = {"zapret": versions[0], "tgproxy": versions[1]}
        return "Информация о версиях обновлена"

    def set_auto_update(self, data):
        self.state["auto_update_tg"] = bool(data.get("enabled"))
        core.save_state(self.state)
        return "Автообновление TG включено" if self.state["auto_update_tg"] else "Автообновление TG выключено"

    def set_auto_update_zapret(self, data):
        self.state["auto_update_zapret"] = bool(data.get("enabled"))
        core.save_state(self.state)
        return "Автообновление zapret включено" if self.state["auto_update_zapret"] else "Автообновление zapret выключено"

    def stop_zapret_tasks(self, _data=None):
        self._log("Остановка процессов поиска и фоновых скриптов zapret...")
        with self.lock:
            for p in list(self.active_subprocesses):
                try:
                    p.terminate()
                    p.kill()
                except Exception:
                    pass
            self.active_subprocesses.clear()
            self.busy = False
            self.status = "Процессы zapret остановлены"
            self.message = "Все фоновые поиски и скрипты zapret остановлены"
        try:
            subprocess.run(["taskkill.exe", "/F", "/FI", "WINDOWTITLE eq *test zapret*"],
                           capture_output=True, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            subprocess.run(["taskkill.exe", "/F", "/FI", "WINDOWTITLE eq *service.bat*"],
                           capture_output=True, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        return "Все фоновые процессы zapret остановлены"

    def test_alts(self, _data):
        if not core.is_admin():
            raise PermissionError("Для проверки конфигураций нужны права администратора")
        root = core.ZAPRET_DIR
        service = root / "service.bat"
        source = root / "utils" / "test zapret.ps1"
        if not service.exists() or not source.exists():
            raise FileNotFoundError("В zapret не найден service.bat или test zapret.ps1")
        core.patch_service_bat(root)
        fd, temp_name = tempfile.mkstemp(prefix="ru-helper-test-", suffix=".ps1")
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            script = _automated_test_script(source.read_text(encoding="utf-8-sig", errors="replace"), root)
            temp_path.write_text(script, encoding="utf-8")
            self._log("Запущена автоматическая проверка конфигураций...")
            environment = os.environ.copy()
            environment["NO_UPDATE_CHECK"] = "1"
            process = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(temp_path)],
                cwd=str(root), env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self.lock:
                self.active_subprocesses.append(process)
            try:
                for line in process.stdout:
                    line = line.strip()
                    if line:
                        self._log(line[:280])
                process.wait()
            finally:
                with self.lock:
                    if process in self.active_subprocesses:
                        self.active_subprocesses.remove(process)
            results, best = _parse_alt_results(root / "utils" / "test results")
            with self.lock:
                self.alt_results = results
            if best:
                self.state["zapret_strategy"] = best
                core.save_state(self.state)
            if not results:
                raise RuntimeError("Результаты проверки не получены")
            return f"Проверка завершена! Найдено результатов: {len(results)}"
        finally:
            temp_path.unlink(missing_ok=True)

    def save_alt(self, data):
        value = str(data.get("strategy", "")).strip()
        if not self.projects.zapret.normalize_strategy(value):
            raise ValueError("Выберите существующую конфигурацию")
        self.state["zapret_strategy"] = value
        core.save_state(self.state)
        if self.projects.zapret.status()["winws"]:
            self._log("Перезапуск zapret с новой стратегией: " + value)
            self.projects.zapret.stop()
            time.sleep(0.5)
            self.projects.zapret.start(value)
            return f"Активная конфигурация «{value}» сохранена и применена"
        return f"Активная конфигурация: {value}"

    def start_zapret(self, _data):
        strategy = self.state.get("zapret_strategy")
        self._log("Запуск zapret: альт " + (strategy or "по умолчанию"))
        self.projects.zapret.start(strategy)
        st = self.projects.zapret.status()
        if not (st.get("winws") or st.get("running")):
            raise RuntimeError("zapret не запустился: процесс winws.exe не найден")
        return "zapret запущен и работает"

    def stop_zapret(self, _data):
        self._log("Остановка zapret")
        self.projects.zapret.stop()
        st = self.projects.zapret.status()
        if st.get("winws") or st.get("running"):
            raise RuntimeError("Не удалось остановить процесс winws.exe")
        return "zapret остановлен"

    def toggle_zapret(self, _data):
        st = self.projects.zapret.status()
        if st.get("winws") or st.get("running"):
            return self.stop_zapret(_data)
        return self.start_zapret(_data)

    def restart_zapret(self, _data):
        self.stop_zapret(_data)
        time.sleep(0.5)
        return self.start_zapret(_data)

    def start_tg(self, _data):
        self._log("Запуск tg-ws-proxy")
        self.projects.tgproxy.start()
        if not self.projects.tgproxy.is_running():
            raise RuntimeError("tg-ws-proxy не запустился")
        return "tg-ws-proxy запущен и работает"

    def stop_tg(self, _data):
        self._log("Остановка tg-ws-proxy")
        self.projects.tgproxy.stop()
        if self.projects.tgproxy.is_running():
            raise RuntimeError("Не удалось остановить процесс tg-ws-proxy")
        return "tg-ws-proxy остановлен"

    def toggle_tg(self, _data):
        if self.projects.tgproxy.is_running():
            return self.stop_tg(_data)
        return self.start_tg(_data)

    def restart_tg(self, _data):
        self.stop_tg(_data)
        time.sleep(0.5)
        return self.start_tg(_data)

    def start_all(self, _data):
        self._log("Запуск обоих адаптеров")
        self.projects.start_all(self.state.get("zapret_strategy"))
        status = self.projects.status()
        zap_ok = status["zapret"]["winws"] or status["zapret"]["running"]
        tg_ok = status["tgproxy"]["running"]
        if not zap_ok or not tg_ok:
            raise RuntimeError("Не все сервисы запустились")
        return "Оба адаптера запущены и работают"

    def stop_all(self, _data):
        self._log("Остановка обоих адаптеров")
        self.projects.stop_all()
        status = self.projects.status()
        zap_act = status["zapret"]["winws"] or status["zapret"]["running"]
        tg_act = status["tgproxy"]["running"]
        if zap_act or tg_act:
            raise RuntimeError("Не все сервисы остановились")
        return "Оба адаптера остановлены"

    def diagnostics_action(self, _data):
        for index, (name, url) in enumerate(core.DIAG_TARGETS):
            ok, ms, http_status = core.http_check(url, timeout=6)
            row = {"name": name, "status": "доступен" if ok else "ошибка", "ms": f"{ms} ms" if ms else "—", "http": http_status, "ok": ok}
            with self.lock:
                self.diagnostics[index] = row
        return "Диагностика успешно выполнена"

    def load_alts(self, _data):
        return f"Найдено конфигураций: {len(self.projects.zapret.strategies())}"

    def autostart(self, _data):
        current = self.projects.tgproxy.status()["autostart"]
        set_tg_autostart(not current, self.projects.tgproxy.executable)
        return "Автозапуск TG включён" if not current else "Автозапуск TG выключен"

    def telegram(self, _data):
        self.projects.tgproxy.open_in_telegram()
        return "Ссылка подключения отправлена в Telegram"

    def service(self, _data):
        bat = core.ZAPRET_DIR / "service.bat"
        if not bat.exists():
            raise FileNotFoundError("Сначала установите zapret")
        core.patch_service_bat(core.ZAPRET_DIR)
        proc = subprocess.Popen(
            [core.COMSPEC, "/d", "/c", f'call "{bat.name}"'],
            cwd=str(bat.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self.lock:
            self.active_subprocesses.append(proc)
        return "service.bat запущен"

    def base(self, _data):
        core.BASE_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(core.BASE_DIR))
        else:
            subprocess.Popen(["xdg-open", str(core.BASE_DIR)])
        return "Папка ru_helper открыта"

    def console(self, _data):
        self.switch_to_console = True
        if self.window:
            threading.Thread(target=lambda: (time.sleep(0.05), self.window.destroy()), daemon=True).start()
        return "Переключение в консольный режим..."

    def clear_logs(self, _data):
        with self.lock:
            self.logs = []
        return "Логи очищены"

    def discussions(self, _data):
        webbrowser.open(core.DISCUSSIONS_URL)
        return "Обсуждения на GitHub открыты в браузере"

    def startup_followup(self):
        updates = []
        for key, label in (("zapret_version", "zapret"), ("tgproxy_version", "tg-ws-proxy")):
            installed = self.state.get(key)
            latest = self.latest.get("zapret" if key == "zapret_version" else "tgproxy")
            if installed and latest not in (None, "", "?", "…") and installed != latest:
                updates.append((label, installed, latest))
                self._log(f"Доступно обновление {label}: {installed} → {latest}")
        for label, installed, latest in updates:
            _notify("Доступно обновление", f"{label}: {installed} → {latest}")
        tg_update = next((item for item in updates if item[0] == "tg-ws-proxy"), None)
        if tg_update and self.state.get("auto_update_tg", True) and self.projects.tgproxy.executable:
            self._log("Автообновление tg-ws-proxy запущено")
            self.submit("install_tg", {})
        zap_update = next((item for item in updates if item[0] == "zapret"), None)
        if zap_update and self.state.get("auto_update_zapret", False) and self.projects.zapret.installed():
            self._log("Автообновление zapret запущено")
            self.submit("install_zapret", {})

    def install_zapret_service(self, _data):
        strategy = self.state.get("zapret_strategy")
        if not strategy:
            raise ValueError("Сначала выберите активный альт")
        self._log("Установка службы zapret в автозапуск: " + strategy)
        self.projects.zapret.install_service(strategy)
        return f"Служба zapret установлена в автозапуск: {strategy}"

    _actions = {
        "install_zapret": install_zapret,
        "install_zapret_service": install_zapret_service,
        "install_tg": install_tg,
        "refresh": refresh,
        "set_auto_update": set_auto_update,
        "set_auto_update_zapret": set_auto_update_zapret,
        "stop_zapret_tasks": stop_zapret_tasks,
        "test_alts": test_alts,
        "save_alt": save_alt,
        "start_zapret": start_zapret,
        "stop_zapret": stop_zapret,
        "toggle_zapret": toggle_zapret,
        "restart_zapret": restart_zapret,
        "start_tg": start_tg,
        "stop_tg": stop_tg,
        "toggle_tg": toggle_tg,
        "restart_tg": restart_tg,
        "start_all": start_all,
        "stop_all": stop_all,
        "diagnostics": diagnostics_action,
        "load_alts": load_alts,
        "autostart": autostart,
        "telegram": telegram,
        "service": service,
        "base": base,
        "console": console,
        "clear_logs": clear_logs,
        "discussions": discussions,
    }


HTML_PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ru Helper</title>
<style>
:root {
  --bg: #050608;
  --surface: #0a0c10;
  --surface-hover: #10141b;
  --surface-card: #080a0e;
  --border: #151922;
  --border-focus: #28303f;
  --text: #edf2f7;
  --text-muted: #748094;
  --text-dim: #444e5d;
  --green: #25bb64;
  --green-glow: rgba(37, 187, 100, 0.15);
  --green-bg: #0a1810;
  --green-border: #133220;
  --red: #df4738;
  --red-glow: rgba(223, 71, 56, 0.15);
  --red-bg: #1e0b0d;
  --red-border: #3b1418;
  --yellow: #e6b800;
  --yellow-bg: #1a1708;
  --yellow-border: #332b0d;
  --blue: #2980b9;
  --blue-bg: #091724;
  --blue-border: #112d45;
  --radius-sm: 4px;
  --radius: 8px;
  --radius-lg: 10px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.45;
  user-select: none;
  min-height: 100vh;
  padding: 16px 20px 24px;
}
input, select, button, textarea {
  font: inherit;
  color: inherit;
}
button {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 14px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  font-size: 13px;
  transition: all 0.08s ease;
}
button:hover:not(:disabled) {
  background: var(--surface-hover);
  border-color: var(--border-focus);
  color: #fff;
}
button:active:not(:disabled) {
  transform: translateY(1px);
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
button.btn-primary {
  background: var(--green-bg);
  border-color: var(--green-border);
  color: var(--green);
}
button.btn-primary:hover:not(:disabled) {
  background: #153823;
  border-color: var(--green);
  box-shadow: 0 0 8px var(--green-glow);
}
button.btn-danger {
  background: var(--red-bg);
  border-color: var(--red-border);
  color: var(--red);
}
button.btn-danger:hover:not(:disabled) {
  background: #3e1a1d;
  border-color: var(--red);
  box-shadow: 0 0 8px var(--red-glow);
}
button.btn-blue {
  background: var(--blue-bg);
  border-color: var(--blue-border);
  color: var(--blue);
}
button.btn-blue:hover:not(:disabled) {
  background: #143552;
  border-color: var(--blue);
}
.app-container {
  max-width: 1340px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* Header */
.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 18px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 14px;
}
.ascii-logo {
  font-family: monospace, "Courier New", Courier;
  font-size: 3px;
  line-height: 3px;
  letter-spacing: 0;
  color: var(--green);
  text-shadow: 0 0 6px var(--green-glow);
  white-space: pre;
  user-select: none;
  background: transparent;
  border: none;
  padding: 0;
  margin: 0;
  display: inline-block;
}
.logo-icon {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  background: linear-gradient(135deg, #2ecc71, #27ae60);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 17px;
  color: #0b0e14;
  box-shadow: 0 0 12px var(--green-glow);
}
.app-title {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.app-subtitle {
  font-size: 12px;
  color: var(--text-muted);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 5px 10px;
  border-radius: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
}
.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
  animation: pulse 0.8s infinite;
}
.pulse-dot.busy {
  background: var(--yellow);
  box-shadow: 0 0 6px var(--yellow);
}
.pulse-dot.error {
  background: var(--red);
  box-shadow: 0 0 6px var(--red);
}
@keyframes pulse {
  0% { transform: scale(0.95); opacity: 0.8; }
  50% { transform: scale(1.15); opacity: 1; }
  100% { transform: scale(0.95); opacity: 0.8; }
}
/* Main Power Grid */
.power-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.service-card {
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.service-card.running {
  border-color: var(--green-border);
}
.service-card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
.service-title-wrap {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.service-name {
  font-size: 17px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
}
.badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-muted);
}
.badge.badge-green { background: var(--green-bg); color: var(--green); border-color: var(--green-border); }
.badge.badge-red { background: var(--red-bg); color: var(--red); border-color: var(--red-border); }
.badge.badge-yellow { background: var(--yellow-bg); color: var(--yellow); border-color: var(--yellow-border); }
.badge.badge-blue { background: var(--blue-bg); color: var(--blue); border-color: var(--blue-border); }

.power-widget {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 16px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.power-widget:hover {
  background: var(--surface-hover);
  border-color: var(--border-focus);
}
.service-card.running .power-widget {
  background: linear-gradient(90deg, rgba(46, 204, 113, 0.08) 0%, transparent 100%);
  border-color: var(--green-border);
}
.power-info {
  display: flex;
  flex-direction: column;
}
.power-state-label {
  font-size: 15px;
  font-weight: 700;
  color: var(--red);
}
.service-card.running .power-state-label {
  color: var(--green);
}
.power-hint {
  font-size: 12px;
  color: var(--text-muted);
}
.power-btn-circle {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: 2px solid var(--red);
  background: var(--red-bg);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--red);
  font-size: 20px;
  box-shadow: 0 0 10px var(--red-glow);
  transition: all 0.2s ease;
}
.service-card.running .power-btn-circle {
  border-color: var(--green);
  background: var(--green-bg);
  color: var(--green);
  box-shadow: 0 0 12px var(--green-glow);
}
.details-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  background: var(--surface);
  padding: 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  font-size: 12px;
}
.detail-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.detail-label {
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.detail-val {
  color: var(--text);
  font-weight: 500;
}
.service-actions-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* Secondary Panels Grid */
.main-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.panel {
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.panel-title {
  font-size: 14px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Alt Selector */
.alt-control-row {
  display: flex;
  gap: 8px;
}
.custom-select {
  flex: 1;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  color: var(--text);
  outline: none;
}
.custom-select:focus {
  border-color: var(--green);
}
.alt-results-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 180px;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.alt-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  border-radius: 4px;
  background: var(--surface-card);
  font-size: 12px;
}
.alt-item.best {
  border-left: 3px solid var(--green);
  font-weight: 600;
}
.alt-item.ok { color: var(--green); }
.alt-item.bad { color: var(--text-dim); }

/* Diagnostics Table */
.diag-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.diag-table th {
  text-align: left;
  color: var(--text-dim);
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
}
.diag-table td {
  padding: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
}
.diag-table tr:last-child td { border-bottom: none; }

/* TG Settings Form */
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.form-group.full-width {
  grid-column: 1 / -1;
}
.form-group label {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.02em;
}
.form-input {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 7px 10px;
  color: var(--text);
  font-size: 13px;
  outline: none;
}
.form-input:focus {
  border-color: var(--green);
}
.secret-group {
  display: flex;
  gap: 6px;
}
.secret-group .form-input {
  flex: 1;
  font-family: monospace;
}
.checkbox-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}
.checkbox-line input {
  accent-color: var(--green);
  width: 16px;
  height: 16px;
}

/* Terminal Log */
.terminal-box {
  background: #07090c;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px;
  font-family: "Cascadia Code", "Consolas", monospace;
  font-size: 11.5px;
  line-height: 1.5;
  color: #a0aec0;
  max-height: 190px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.terminal-box::-webkit-scrollbar, .alt-results-box::-webkit-scrollbar {
  width: 6px;
}
.terminal-box::-webkit-scrollbar-thumb, .alt-results-box::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  backdrop-filter: blur(3px);
}
.modal-box {
  background: var(--surface-card);
  border: 1px solid var(--border-focus);
  border-radius: var(--radius-lg);
  padding: 22px;
  max-width: 440px;
  width: 90%;
  display: flex;
  flex-direction: column;
  gap: 14px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
}
.modal-title {
  font-size: 16px;
  font-weight: 700;
}
.modal-desc {
  font-size: 13px;
  color: var(--text-muted);
  line-height: 1.5;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 6px;
}

/* Toast */
.toast-msg {
  position: fixed;
  bottom: 20px;
  right: 20px;
  background: var(--surface-card);
  border: 1px solid var(--border-focus);
  border-radius: var(--radius);
  padding: 12px 18px;
  font-size: 13px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
  z-index: 200;
  display: none;
  max-width: 400px;
}
.toast-msg.error {
  border-color: var(--red-border);
  background: var(--red-bg);
  color: var(--red);
}
.toast-msg.success {
  border-color: var(--green-border);
  background: var(--green-bg);
  color: var(--green);
}
@media (max-width: 900px) {
  .power-grid, .main-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="app-container">

  <!-- Header -->
  <header class="app-header">
    <div class="header-left">
      <pre class="ascii-logo">⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⠏⡸⠈⢢⡀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡸⠀⡇⠀⠀⠱⡄⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⠃⢰⣀⠀⠀⠀⢃⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡰⠃⠀⠘⠙⠦⣀⠀⢸⡀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⠖⠉⠀⠀⠀⠀⠀⠀⠀⠁⠀⢇⠀⠀⠀⠀⠀
⠀⠀⢀⣀⣀⣤⠤⠤⠤⠒⠒⠋⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⠀⠀⠘⣦⡴⠋⠀⠀
⢸⡉⠁⢀⣀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⡇⠀⠀⠋⣣⡤⠔⠂
⠈⠳⣄⠀⠀⠈⠉⣶⠂⠀⠀⣀⣀⡀⠀⠀⠀⠀⠀⠀⠉⠛⠁⠀⠀⠉⠀⠆⠀⠀
⠀⠀⠈⢦⡀⠀⠀⠸⠄⠀⠀⣿⣿⣿⠀⠀⠐⠒⠀⠀⠀⠀⠀⠀⠀⠀⣸⠀⠀⠀
⠀⠀⠀⠀⠈⠓⠢⠤⡄⠀⠀⠈⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣰⠃⠀⠀⠀
⠀⠀⠀⠀⠀⢠⠤⠖⣻⠍⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⠜⠁⠀⠀⠀⠀
⢀⡤⠤⠤⣀⠀⠠⠎⠉⢧⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠹⡀⠀⠀⠀⠀⠀
⢎⠀⠀⠀⠀⠱⡄⠀⠀⠀⠈⠉⠉⠏⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⡇⠀⠀⠀⠀⠀
⠈⠳⡄⠀⠀⠀⢸⡀⠀⠀⠀⠀⡼⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⠀⠀⠀⠀⠀
⠀⠀⢹⠀⠀⠀⠈⡇⠀⠀⠀⡼⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⡀⠈⡇⠀⠀⠀⠀
⠀⠀⠀⡇⠀⠀⢰⡇⠀⠀⡼⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⠧⠀⢸⡀⠀⠀⠀
⠀⠀⠀⢷⠀⠀⢸⡇⠀⣰⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠃⠀⠀⠇⠀⠀⠀
⠀⠀⠀⠘⡄⠀⠘⡇⢰⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠁⠀⠀⠀⢸⠀⠀⠀
⠀⠀⠀⠀⠳⡄⠀⠘⢺⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⠀⠀⠀
⠀⠀⠀⠀⠀⠈⠒⠦⠌⣇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⡄⠀⠀⢀⠀⠀⠀⢀⠀⠀⠀⠀⠀⠀⠀⠀⢸⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⣄⠀⠘⣄⠀⠀⢸⠀⠀⠀⢸⠀⠀⠀⠀⣸⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠛⠦⣜⣆⠀⢸⡀⠀⠀⡼⠀⠀⣀⡴⠃⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠁⠉⠉⠉⠉⠉⠉⠁⠀⠀⠀⠀⠀</pre>
      <div>
        <div class="app-title">ru Helper</div>
        <div class="app-subtitle">by EleanorMay | github - eleanor-ln | tg @notslep</div>
      </div>
    </div>
    <div class="header-right">
      <div class="status-pill">
        <div class="pulse-dot" id="live-dot"></div>
        <span id="live-text">Подключение…</span>
        <span class="badge" id="live-count-badge" style="margin-left:4px; font-size:11px;">0/2</span>
      </div>
      <button class="btn-primary" onclick="run('start_all')">Запустить всё</button>
      <button class="btn-danger" onclick="run('stop_all')">Остановить всё</button>
      <button class="btn-blue" onclick="run('console')">Консоль</button>
    </div>
  </header>

  <!-- Power Controls Grid -->
  <section class="power-grid">
    <!-- zapret Card -->
    <div class="service-card" id="zapret-card">
      <div class="service-card-top">
        <div class="service-title-wrap">
          <div class="service-name">
            zapret
            <span class="badge" id="zap-ver-badge">v...</span>
            <span class="badge badge-yellow" id="zap-update-badge" style="display:none">Обновление</span>
          </div>
          <span class="app-subtitle">Обход замедления Discord / YouTube</span>
        </div>
        <button onclick="run('refresh')" title="Проверить обновления">Обновить</button>
      </div>

      <div class="power-widget" onclick="run('toggle_zapret')">
        <div class="power-info">
          <span class="power-state-label" id="zap-state-text">ОСТАНОВЛЕН</span>
          <span class="power-hint" id="zap-hint">Нажмите для переключения</span>
        </div>
        <div class="power-btn-circle">
          <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path>
            <line x1="12" y1="2" x2="12" y2="12"></line>
          </svg>
        </div>
      </div>

      <div class="details-grid">
        <div class="detail-item">
          <span class="detail-label">winws.exe</span>
          <span class="detail-val" id="zap-winws-val">—</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Служба</span>
          <span class="detail-val" id="zap-service-val">—</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Конфигураций</span>
          <span class="detail-val" id="zap-count-val">0</span>
        </div>
      </div>

      <div class="service-actions-bar">
        <button onclick="run('install_zapret_service')" title="Установить активный альт в автозапуск как службу">В автозапуск</button>
        <button onclick="run('restart_zapret')">Перезапустить</button>
        <button onclick="run('stop_zapret_tasks')" class="btn-danger" title="Остановить поиски, service.bat и фоновые процессы zapret">Остановить процессы</button>
        <button onclick="run('install_zapret')">Установить / Обновить</button>
        <button onclick="run('service')">service.bat</button>
      </div>
    </div>

    <!-- tg-ws-proxy Card -->
    <div class="service-card" id="tg-card">
      <div class="service-card-top">
        <div class="service-title-wrap">
          <div class="service-name">
            tg-ws-proxy
            <span class="badge" id="tg-ver-badge">v...</span>
            <span class="badge badge-yellow" id="tg-update-badge" style="display:none">Обновление</span>
          </div>
          <span class="app-subtitle">MTProto WebSocket прокси для Telegram</span>
        </div>
        <button onclick="run('telegram')" class="btn-primary" title="Подключить в Telegram">В Telegram</button>
      </div>

      <div class="power-widget" onclick="run('toggle_tg')">
        <div class="power-info">
          <span class="power-state-label" id="tg-state-text">ОСТАНОВЛЕН</span>
          <span class="power-hint" id="tg-hint">Нажмите для переключения</span>
        </div>
        <div class="power-btn-circle">
          <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path>
            <line x1="12" y1="2" x2="12" y2="12"></line>
          </svg>
        </div>
      </div>

      <div class="details-grid">
        <div class="detail-item">
          <span class="detail-label">Процесс</span>
          <span class="detail-val" id="tg-proc-val">—</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Порт</span>
          <span class="detail-val" id="tg-port-val">1443</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Автозапуск</span>
          <span class="detail-val" id="tg-auto-val">—</span>
        </div>
      </div>

      <div class="service-actions-bar">
        <button onclick="run('restart_tg')">Перезапустить</button>
        <button onclick="run('install_tg')">Установить / Обновить</button>
        <button onclick="run('autostart')">Автозапуск</button>
      </div>
    </div>
  </section>

  <!-- Secondary Panels Grid -->
  <section class="main-grid">

    <!-- Left Column: Strategy & Diagnostics -->
    <div style="display:flex; flex-direction:column; gap:16px;">
      <!-- Working Alt -->
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">Рабочий альт (стратегия zapret)</div>
          <span class="badge badge-green" id="active-alt-badge">не выбран</span>
        </div>
        <div class="alt-control-row">
          <select id="alt-select" class="custom-select" onchange="saveAlt(this.value)">
            <option value="">Выберите стратегию...</option>
          </select>
          <button class="btn-primary" onclick="showModal('alt-modal')">Найти лучший альт</button>
          <button class="btn-danger" onclick="run('stop_zapret_tasks')" title="Остановить поиск лучшего альта">Остановить поиск</button>
        </div>
        <div class="alt-results-box" id="alt-results-list">
          <div style="color:var(--text-dim); text-align:center; padding:10px;">Результаты тестирования пока отсутствуют</div>
        </div>
      </div>

      <!-- Diagnostics -->
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">Диагностика доступности</div>
          <button class="btn-blue" onclick="run('diagnostics')">Проверить сейчас</button>
        </div>
        <table class="diag-table">
          <thead>
            <tr><th>Сервис</th><th>Статус</th><th>Задержка</th><th>HTTP код</th></tr>
          </thead>
          <tbody id="diag-rows">
            <tr><td colspan="4" style="color:var(--text-dim); text-align:center;">Загрузка данных…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Right Column: Tools & Updates, TG Config, Logs -->
    <div style="display:flex; flex-direction:column; gap:16px;">

      <!-- Tools & Updates Panel -->
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">Инструменты и обновления</div>
          <button onclick="run('refresh')" title="Проверить обновления">Проверить обновления</button>
        </div>
        <div style="display:flex; flex-direction:column; gap:12px;">
          <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:8px;">
            <button onclick="showModal('readme-modal')" class="btn-blue" title="README проектов и решение проблем">README</button>
            <button onclick="run('base')" title="Открыть папку данных ru_helper">Папка</button>
            <button onclick="run('discussions')" title="Обсуждения zapret на GitHub (решения проблем)">GitHub</button>
          </div>
          <div style="display:flex; flex-direction:column; gap:8px; padding-top:8px; border-top:1px solid var(--border);">
            <label class="checkbox-line">
              <input type="checkbox" id="auto-update-tg-toggle" onchange="run('set_auto_update', {enabled: this.checked})">
              Автообновление tg-ws-proxy при запуске
            </label>
            <label class="checkbox-line">
              <input type="checkbox" id="auto-update-zapret-toggle" onchange="run('set_auto_update_zapret', {enabled: this.checked})">
              Автообновление zapret при запуске
            </label>
          </div>
        </div>
      </div>

      <!-- TG Config Form -->
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">Настройки tg-ws-proxy</div>
          <button class="btn-primary" onclick="submitConfig()">Сохранить</button>
        </div>
        <form id="tg-form" onsubmit="submitConfig(); return false;" class="form-grid">
          <div class="form-group">
            <label>Хост / Адрес</label>
            <input class="form-input" id="cfg-host" name="host" placeholder="127.0.0.1">
          </div>
          <div class="form-group">
            <label>Порт</label>
            <input class="form-input" id="cfg-port" name="port" type="number" placeholder="1443">
          </div>
          <div class="form-group full-width">
            <label>Secret ключ (32 hex)</label>
            <div class="secret-group">
              <input class="form-input" id="cfg-secret" name="secret" placeholder="32 hex символа">
              <button type="button" onclick="generateSecret()" title="Сгенерировать">Сгенерировать</button>
              <button type="button" onclick="copySecret()" title="Скопировать">Скопировать</button>
            </div>
          </div>
          <div class="form-group">
            <label>Пул соединений</label>
            <input class="form-input" id="cfg-pool" name="pool_size" type="number" placeholder="4">
          </div>
          <div class="form-group">
            <label>Буфер (KB)</label>
            <input class="form-input" id="cfg-buf" name="buf_kb" type="number" placeholder="256">
          </div>
          <div class="form-group full-width" style="margin-top:4px;">
            <label class="checkbox-line">
              <input type="checkbox" id="cfg-cfproxy" name="cfproxy">
              Включить Cloudflare fallback
            </label>
          </div>
        </form>
      </div>

      <!-- Logs Terminal -->
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">Журнал событий (Логи)</div>
          <div style="display:flex; gap:6px;">
            <button onclick="copyLogs()" title="Скопировать логи">Скопировать</button>
            <button onclick="run('clear_logs')" title="Очистить">Очистить</button>
          </div>
        </div>
        <div class="terminal-box" id="terminal-log">Ожидание записей журнала…</div>
      </div>
    </div>

  </section>

</div>

<!-- Modal Dialog -->
<div class="modal-overlay" id="alt-modal" style="display:none;">
  <div class="modal-box">
    <div class="modal-title">Тестирование конфигураций zapret</div>
    <div class="modal-desc">
      Будут протестированы все доступные стратегии general*.bat по целевым сайтам.
      Это займет 2-4 минуты и временно перезапустит службу winws.<br><br>
      Запустить поиск лучшего альта прямо сейчас?
    </div>
    <div class="modal-actions">
      <button onclick="hideModal('alt-modal')">Отмена</button>
      <button class="btn-primary" onclick="hideModal('alt-modal'); run('test_alts')">Начать тестирование</button>
    </div>
  </div>
</div>

<!-- README Modal Dialog -->
<div class="modal-overlay" id="readme-modal" style="display:none;">
  <div class="modal-box" style="max-width: 720px; width: 92%; max-height: 85vh; display: flex; flex-direction: column;">
    <div class="modal-title" style="display: flex; justify-content: space-between; align-items: center; gap: 10px;">
      <span>README проектов</span>
      <div style="display: flex; gap: 6px;">
        <button id="readme-tab-zap" class="btn-primary" style="padding: 4px 10px; font-size: 12px;" onclick="switchReadme('zap')">zapret</button>
        <button id="readme-tab-tg" style="padding: 4px 10px; font-size: 12px;" onclick="switchReadme('tg')">tg-ws-proxy</button>
      </div>
    </div>
    <div id="readme-content-zap" class="modal-desc" style="overflow-y: auto; font-family: monospace; font-size: 11px; line-height: 1.45; white-space: pre-wrap; margin: 12px 0; background: var(--surface); padding: 12px; border-radius: 6px; border: 1px solid var(--border); flex: 1; text-align: left; user-select: text;">Flowseal/zapret-discord-youtube

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
  Убедитесь в настройке Secure DNS, пробуйте разные general (ALT, ALT2, ALT9 и др.).</div>
    <div id="readme-content-tg" class="modal-desc" style="display: none; overflow-y: auto; font-family: monospace; font-size: 11px; line-height: 1.45; white-space: pre-wrap; margin: 12px 0; background: var(--surface); padding: 12px; border-radius: 6px; border: 1px solid var(--border); flex: 1; text-align: left; user-select: text;">TG WS Proxy (tg-ws-proxy)

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
Если это не помогло, полностью очистите это поле.</div>
    <div class="modal-actions">
      <button onclick="hideModal('readme-modal')">Закрыть</button>
    </div>
  </div>
</div>

<!-- Toast Notice -->
<div class="toast-msg" id="toast"></div>

<script>
var stateCache = null;
var userScrolledUp = false;

function $(id) { return document.getElementById(id); }

function toast(msg, isError) {
  var t = $('toast');
  t.textContent = msg;
  t.className = 'toast-msg ' + (isError ? 'error' : 'success');
  t.style.display = 'block';
  window.clearTimeout(window.toastTimer);
  window.toastTimer = window.setTimeout(function() {
    t.style.display = 'none';
  }, 2000);
}

function api(url, method, payload, onSuccess, onError) {
  var xhr = new XMLHttpRequest();
  xhr.open(method || 'GET', url, true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onreadystatechange = function() {
    if (xhr.readyState !== 4) return;
    var data = {};
    try { data = JSON.parse(xhr.responseText); } catch(e) {}
    if (xhr.status >= 200 && xhr.status < 300) {
      if (onSuccess) onSuccess(data);
    } else {
      var err = data.error || data.message || ('Ошибка ' + xhr.status);
      if (onError) onError(err);
      else toast(err, true);
    }
  };
  xhr.onerror = function() {
    if (onError) onError('Нет связи с сервером');
  };
  xhr.send(payload ? JSON.stringify(payload) : null);
}

function run(action, data) {
  api('/api/action', 'POST', { action: action, data: data || {} }, function(res) {
    toast(res.message || 'Действие выполнено', false);
    poll();
  }, function(err) {
    toast(err, true);
    poll();
  });
}

function showModal(id) {
  var m = $(id);
  if (m) m.style.display = 'flex';
}

function hideModal(id) {
  var m = $(id);
  if (m) m.style.display = 'none';
}

function saveAlt(val) {
  if (val) run('save_alt', { strategy: val });
}

function generateSecret() {
  var chars = '0123456789abcdef';
  var sec = '';
  for (var i = 0; i < 32; i++) {
    sec += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  $('cfg-secret').value = sec;
  toast('Сгенерирован новый 32-hex ключ', false);
}

function copySecret() {
  var val = $('cfg-secret').value;
  if (!val) { toast('Ключ пустой', true); return; }
  if (navigator.clipboard) {
    navigator.clipboard.writeText(val);
    toast('Secret скопирован в буфер', false);
  }
}

function copyLogs() {
  var text = $('terminal-log').innerText;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text);
    toast('Логи скопированы в буфер', false);
  }
}

function loadConfig() {
  api('/api/config', 'GET', null, function(res) {
    var c = res.config || {};
    $('cfg-host').value = c.host || '127.0.0.1';
    $('cfg-port').value = c.port || 1443;
    $('cfg-secret').value = c.secret || '';
    $('cfg-pool').value = c.pool_size || 4;
    $('cfg-buf').value = c.buf_kb || 256;
    $('cfg-cfproxy').checked = !!c.cfproxy;
  });
}

function submitConfig() {
  var c = {
    host: $('cfg-host').value.trim(),
    port: parseInt($('cfg-port').value, 10),
    secret: $('cfg-secret').value.trim(),
    pool_size: parseInt($('cfg-pool').value, 10),
    buf_kb: parseInt($('cfg-buf').value, 10),
    cfproxy: $('cfg-cfproxy').checked
  };
  api('/api/config', 'POST', c, function() {
    toast('Настройки tg-ws-proxy сохранены', false);
    poll();
  }, function(err) {
    toast(err, true);
  });
}

function renderUI(d) {
  stateCache = d;

  // Header status & live count badge
  var runningCount = 0;
  var z = d.projects.zapret || {};
  var zapRunning = !!(z.winws || z.running || z.service === 'running');
  if (zapRunning) runningCount++;
  var t = d.projects.tgproxy || {};
  var tgRunning = !!t.running;
  if (tgRunning) runningCount++;

  var countBadge = $('live-count-badge');
  if (countBadge) {
    countBadge.textContent = runningCount + '/2';
    countBadge.className = 'badge ' + (runningCount === 2 ? 'badge-green' : (runningCount === 1 ? 'badge-yellow' : ''));
  }

  var dot = $('live-dot');
  var txt = $('live-text');
  if (d.busy) {
    dot.className = 'pulse-dot busy';
    txt.textContent = d.status || 'Выполняется…';
  } else {
    dot.className = 'pulse-dot';
    txt.textContent = 'Подключено';
  }

  // zapret details
  var z = d.projects.zapret || {};
  var zapRunning = !!(z.winws || z.running || z.service === 'running');
  $('zapret-card').className = 'service-card' + (zapRunning ? ' running' : '');
  $('zap-state-text').textContent = zapRunning ? 'РАБОТАЕТ' : 'ОСТАНОВЛЕН';
  $('zap-hint').textContent = zapRunning ? 'Нажмите, чтобы остановить' : 'Нажмите, чтобы запустить';
  $('zap-ver-badge').textContent = d.state.zapret_version || 'не установлен';
  $('zap-winws-val').innerHTML = z.winws ? '<span style="color:var(--green)">Запущен</span>' : '<span style="color:var(--red)">Остановлен</span>';
  
  var svcText = z.service === 'running' ? '<span style="color:var(--green)">Работает</span>' : (z.service === 'stopped' ? 'Остановлена' : (z.service === 'missing' ? 'Не установлена' : escapeHtml(z.service)));
  $('zap-service-val').innerHTML = svcText;
  $('zap-count-val').textContent = z.strategy_count || 0;

  if (d.latest.zapret && d.state.zapret_version && d.latest.zapret !== '…' && d.state.zapret_version !== d.latest.zapret) {
    $('zap-update-badge').style.display = 'inline-block';
  } else {
    $('zap-update-badge').style.display = 'none';
  }

  // tg-ws-proxy details
  var t = d.projects.tgproxy || {};
  var tgRunning = !!t.running;
  $('tg-card').className = 'service-card' + (tgRunning ? ' running' : '');
  $('tg-state-text').textContent = tgRunning ? 'РАБОТАЕТ' : 'ОСТАНОВЛЕН';
  $('tg-hint').textContent = tgRunning ? 'Нажмите, чтобы остановить' : 'Нажмите, чтобы запустить';
  $('tg-ver-badge').textContent = d.state.tgproxy_version || 'не установлен';
  $('tg-proc-val').innerHTML = tgRunning ? '<span style="color:var(--green)">Запущен</span>' : '<span style="color:var(--red)">Остановлен</span>';
  $('tg-port-val').innerHTML = (t.port || 1443) + (t.port_open ? ' <span style="color:var(--green)">(открыт)</span>' : ' <span style="color:var(--red)">(закрыт)</span>');
  $('tg-auto-val').innerHTML = t.autostart ? '<span style="color:var(--green)">Включён</span>' : '<span style="color:var(--text-dim)">Выключен</span>';

  if (d.latest.tgproxy && d.state.tgproxy_version && d.latest.tgproxy !== '…' && d.state.tgproxy_version !== d.latest.tgproxy) {
    $('tg-update-badge').style.display = 'inline-block';
  } else {
    $('tg-update-badge').style.display = 'none';
  }

  // Auto-update checkboxes
  var autoTg = $('auto-update-tg-toggle');
  if (autoTg) autoTg.checked = d.state.auto_update_tg !== false;
  var autoZap = $('auto-update-zapret-toggle');
  if (autoZap) autoZap.checked = Boolean(d.state.auto_update_zapret);

  // Active alt & Strategies select
  var activeAlt = d.state.zapret_strategy || '';
  $('active-alt-badge').textContent = activeAlt || 'не выбран';
  $('active-alt-badge').className = 'badge ' + (activeAlt ? 'badge-green' : 'badge-yellow');

  var sel = $('alt-select');
  var strats = d.strategies || [];
  if (sel.options.length !== strats.length + 1) {
    sel.innerHTML = '<option value="">Выберите стратегию...</option>';
    for (var i = 0; i < strats.length; i++) {
      var opt = document.createElement('option');
      opt.value = strats[i];
      opt.textContent = strats[i];
      sel.appendChild(opt);
    }
  }
  sel.value = activeAlt;

  // Alt results list
  var resBox = $('alt-results-list');
  var items = d.alt_results || [];
  if (items.length > 0) {
    var htmlStr = '';
    for (var j = 0; j < items.length; j++) {
      var it = items[j];
      var cls = 'alt-item' + (it.best ? ' best' : '') + (it.ok ? ' ok' : ' bad');
      htmlStr += '<div class="' + cls + '"><span>' + (it.best ? '[топ] ' : '') + escapeHtml(it.name) + '</span><span>' + escapeHtml(it.summary) + '</span></div>';
    }
    resBox.innerHTML = htmlStr;
  } else {
    resBox.innerHTML = '<div style="color:var(--text-dim); text-align:center; padding:10px;">Результаты тестирования пока отсутствуют</div>';
  }

  // Diagnostics Table
  var diagBody = $('diag-rows');
  var diagItems = d.diagnostics || [];
  var dHtml = '';
  for (var k = 0; k < diagItems.length; k++) {
    var dg = diagItems[k];
    var stCls = dg.ok ? 'badge-green' : 'badge-red';
    var msVal = parseInt(dg.ms, 10);
    var msCls = isNaN(msVal) ? '' : (msVal < 120 ? 'color:var(--green)' : (msVal < 350 ? 'color:var(--yellow)' : 'color:var(--red)'));
    dHtml += '<tr><td><strong>' + escapeHtml(dg.name) + '</strong></td>' +
      '<td><span class="badge ' + stCls + '">' + escapeHtml(dg.status) + '</span></td>' +
      '<td style="' + msCls + '">' + escapeHtml(dg.ms) + '</td>' +
      '<td>' + escapeHtml(dg.http) + '</td></tr>';
  }
  diagBody.innerHTML = dHtml;

  // Logs Terminal
  var logBox = $('terminal-log');
  if (logBox) {
    var newLogText = (d.logs || []).join('\n');
    if (logBox.textContent !== newLogText) {
      var wasAtBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 40;
      logBox.textContent = newLogText;
      if (!userScrolledUp || wasAtBottom) {
        logBox.scrollTop = logBox.scrollHeight;
      }
    }
  }
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, function(c) {
    return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c];
  });
}

function poll() {
  api('/api/state', 'GET', null, function(data) {
    renderUI(data);
  }, function() {
    $('live-dot').className = 'pulse-dot error';
    $('live-text').textContent = 'Нет связи';
  });
}

function switchReadme(tab) {
  if (tab === 'zap') {
    $('readme-tab-zap').className = 'btn-primary';
    $('readme-tab-tg').className = '';
    $('readme-content-zap').style.display = 'block';
    $('readme-content-tg').style.display = 'none';
  } else {
    $('readme-tab-zap').className = '';
    $('readme-tab-tg').className = 'btn-primary';
    $('readme-content-zap').style.display = 'none';
    $('readme-content-tg').style.display = 'block';
  }
}

// Initial setup
loadConfig();
var logEl = $('terminal-log');
if (logEl) {
  logEl.addEventListener('scroll', function() {
    var dist = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight;
    userScrolledUp = dist > 40;
  });
}
poll();
window.setInterval(poll, 800);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    backend = None

    def log_message(self, _format, *_args):
        return

    def send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/state":
            self.send_json(self.backend.snapshot())
            return
        if self.path == "/api/config":
            self.send_json({"config": load_tg_config()})
            return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
        except (ValueError, UnicodeDecodeError):
            self.send_json({"error": "Некорректные данные"}, 400)
            return
        if self.path == "/api/action":
            action = data.get("action")
            if action not in self.backend._actions:
                self.send_json({"error": "Неизвестная операция"}, 400)
                return
            self.send_json(self.backend.submit(action, data.get("data") or {}))
            return
        if self.path == "/api/config":
            try:
                config = save_tg_config(data)
                self.send_json({"config": config})
            except (ValueError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        self.send_json({"error": "Not found"}, 404)


def _refresh_on_start(backend):
    try:
        backend.refresh({})
        backend.startup_followup()
    except Exception as exc:
        with backend.lock:
            backend.status = "Не удалось обновить версии"
            backend.message = str(exc)
            backend._log("Ошибка обновления версий: " + str(exc))


def main(hide_console_on_start=True):
    core.ensure_admin()
    backend = Backend()
    Handler.backend = backend
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    backend.server = server
    url = f"http://127.0.0.1:{server.server_port}/"
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.2), daemon=True).start()

    window = webview.create_window(
        "ru Helper",
        url,
        width=1300,
        height=840,
        min_size=(1060, 680),
        resizable=True,
        background_color="#0b0e14",
    )
    backend.window = window

    threading.Thread(target=lambda: _refresh_on_start(backend), daemon=True).start()

    if hide_console_on_start:
        hide_console()

    try:
        webview.start()
    except KeyboardInterrupt:
        pass
    except Exception:
        try:
            webview.start(gui="mshtml")
        except Exception:
            pass
    finally:
        backend.stop_zapret_tasks()
        server.shutdown()
        server.server_close()

    return backend.switch_to_console


if __name__ == "__main__":
    switched = main()
    if switched:
        show_console()
        import ru_helper
        ru_helper.main()
    else:
        sys.exit(0)
