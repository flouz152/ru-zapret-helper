# ru Helper

Менеджер `zapret-discord-youtube` и `tg-ws-proxy` с двумя режимами в одном приложении:

- `ru-helper.exe` или `run_console.bat` — консольный режим;
- `ru-helper.exe --gui` или `run_gui.bat` — HTML-интерфейс в браузере.

HTML-интерфейс поднимается на loopback-адресе и открывается отдельным desktop-окном через встроенный Windows MSHTML, а не вкладкой браузера. WebView2 Runtime не требуется. Он использует тот же процесс Python, что и консольный режим. Для переключения из консоли используется пункт `8`.

Для сборки одного Windows-файла:

```powershell
.\build_exe.ps1
```

Результат будет находиться в `dist\ru-helper.exe`.

Проверки проекта:

```text
python -m unittest -v test_ru_helper.py
python -m py_compile ru_helper.py ru_helper_gui.py project_adapters.py
```
