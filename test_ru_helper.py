import tempfile
import unittest
import zipfile
from pathlib import Path

import ru_helper
from ru_helper_gui import _DiscussionDetailParser, _DiscussionListParser
from project_adapters import ZapretAdapter, find_tg_executable, select_tgproxy_asset, tg_proxy_url


class HelperTests(unittest.TestCase):
    def test_service_bat_runs_tests_inline_and_removes_second_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = root / "service.bat"
            service.write_text(
                "@echo off\n"
                ":run_tests\n"
                "echo Starting configuration tests in PowerShell window...\n"
                "start \"\" powershell -NoProfile -File \"%~dp0utils\\test zapret.ps1\"\n"
                "pause\n"
                "goto menu\n"
                ":get_strategy_name\n",
                encoding="utf-8",
            )

            patched = ru_helper.patch_service_bat(root)
            result = service.read_text(encoding="utf-8")

            self.assertEqual(patched, [service])
            self.assertIn(ru_helper.SERVICE_PATCH_MARKER, result)
            self.assertNotIn('start "" powershell', result.lower())
            self.assertIn("powershell -NoProfile", result)
            self.assertIn("in this window", result)
            self.assertIn("rem Esc возвращает в меню service.bat", result)
            run_tests = result.split(":run_tests", 1)[1].split(":get_strategy_name", 1)[0]
            self.assertNotIn("\npause", run_tests.lower())

    def test_safe_extract_rejects_parent_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            destination = root / "out"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../../outside.txt", "must not escape")

            with self.assertRaises(ValueError):
                ru_helper.safe_extract_zip(archive, destination)

            self.assertFalse((root.parent / "outside.txt").exists())

    def test_zapret_adapter_only_accepts_local_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "general (ALT).bat").write_text("", encoding="utf-8")
            (root / "general (ALT9).bat").write_text("", encoding="utf-8")
            (root / "general.bat").write_text("", encoding="utf-8")
            adapter = ZapretAdapter(root)
            self.assertEqual(len(adapter.strategies()), 3)
            self.assertIsNotNone(adapter.normalize_strategy("general (ALT)"))
            self.assertIsNotNone(adapter.normalize_strategy("general (ALT 9)"))
            self.assertIsNotNone(adapter.normalize_strategy("ALT 9"))
            self.assertIsNone(adapter.normalize_strategy("..\\outside"))

    def test_tg_asset_selection_and_link(self):
        assets = [
            {"name": "TgWsProxy_windows_arm64.exe"},
            {"name": "TgWsProxy_windows.exe"},
        ]
        selected = select_tgproxy_asset(assets)
        self.assertIsNotNone(selected)
        self.assertIn("windows", selected["name"].lower())
        link = tg_proxy_url({"host": "127.0.0.1", "port": 1443, "secret": "a" * 32})
        self.assertEqual(link, "tg://proxy?server=127.0.0.1&port=1443&secret=dd" + "a" * 32)

    def test_discussions_parsers_extract_list_and_comments(self):
        listing = (
            '<a class="markdown-title discussion-Link--secondary" '
            'href="/Flowseal/zapret-discord-youtube/discussions/42">'
            '  Нужна настройка <strong>DNS</strong>  </a>'
        )
        list_parser = _DiscussionListParser()
        list_parser.feed(listing)
        self.assertEqual(list_parser.items[0]["number"], "42")
        self.assertEqual(list_parser.items[0]["title"], "Нужна настройка DNS")

        detail = (
            '<span class="js-issue-title markdown-title">Проблема #42</span>'
            '<td class="comment-body markdown-body js-comment-body"><p>Первый пост</p></td>'
            '<td class="comment-body markdown-body js-comment-body"><p>Ответ</p></td>'
        )
        detail_parser = _DiscussionDetailParser()
        detail_parser.feed(detail)
        self.assertEqual(detail_parser.title, "Проблема #42")
        self.assertEqual(detail_parser.bodies, ["Первый пост", "Ответ"])

    def test_backend_console_switch_and_snapshot(self):
        from ru_helper_gui import Backend
        backend = Backend()
        snapshot = backend.snapshot()
        self.assertIn("projects", snapshot)
        self.assertIn("strategies", snapshot)
        self.assertIn("diagnostics", snapshot)
        self.assertFalse(backend.switch_to_console)
        backend.console({})
        self.assertTrue(backend.switch_to_console)


if __name__ == "__main__":
    unittest.main()
