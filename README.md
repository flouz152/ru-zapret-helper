# ru Helper by EleanorMay

Менеджер `zapret-discord-youtube` и `tg-ws-proxy` с двумя режимами в одном приложении:

- `ru-helper.exe` или `run_console.bat` — консольный режим;
- `ru-helper.exe --gui` или `run_gui.bat` — HTML интерфейс в браузере.


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
