import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from xlcli.cli import _select_engine, run
from xlcli.models import OfflineTask


class CLITests(unittest.TestCase):
    def test_bare_magnet_uses_local_thunder_without_constructing_cloud_auth(self):
        local = Mock()
        local.add.return_value = [
            OfflineTask("xl-123456789abc:1", "sample", "获取种子", 0)
        ]
        with (
            patch("xlcli.cli.LocalThunder", return_value=local),
            patch(
                "xlcli.cli.Auth",
                side_effect=AssertionError("cloud auth must stay idle"),
            ),
        ):
            self.assertEqual(run(["magnet:?xt=urn:btih:abc"]), 0)
        local.add.assert_called_once_with(["magnet:?xt=urn:btih:abc"], False)

    def test_auto_prefers_installed_local_engine_even_if_cloud_is_logged_in(self):
        local = Mock()
        local.status.return_value = SimpleNamespace(installed=True)
        with (
            patch("xlcli.cli.LocalThunder", return_value=local),
            patch("xlcli.cli.TokenStore") as token_store,
        ):
            self.assertEqual(_select_engine("auto"), "local")
        token_store.assert_not_called()

    def test_auto_falls_back_to_cloud_when_local_app_is_missing(self):
        local = Mock()
        local.status.return_value = SimpleNamespace(installed=False)
        tokens = Mock()
        tokens.load.return_value = object()
        with (
            patch("xlcli.cli.LocalThunder", return_value=local),
            patch("xlcli.cli.TokenStore", return_value=tokens),
        ):
            self.assertEqual(_select_engine("auto"), "cloud")

    def test_explicit_engine_is_never_second_guessed(self):
        with patch("xlcli.cli.LocalThunder") as local:
            self.assertEqual(_select_engine("cloud"), "cloud")
        local.assert_not_called()

    def test_local_get_waits_for_completion_without_cloud_auth(self):
        native = Mock()
        task = OfflineTask("xl-123456789abc:1", "file", "下载中", 0)
        native.add.return_value = [task]
        native.wait.return_value = OfflineTask(task.id, task.name, "已完成", 1)
        with (
            patch("xlcli.cli._select_engine", return_value="local"),
            patch("xlcli.cli.NativeThunder", return_value=native),
            patch("xlcli.cli.Auth", side_effect=AssertionError("no cloud auth")),
        ):
            self.assertEqual(
                run(["get", "https://example.org/file", "--timeout", "12"]), 0
            )
        native.wait.assert_called_once_with(task.id, 12)

    def test_local_get_failure_has_nonzero_exit_status(self):
        native = Mock()
        task = OfflineTask("xl-123456789abc:1", "file", "下载中", 0)
        native.add.return_value = [task]
        native.wait.return_value = OfflineTask(
            task.id, task.name, "失败", 0, message="下载失败"
        )
        with (
            patch("xlcli.cli._select_engine", return_value="local"),
            patch("xlcli.cli.NativeThunder", return_value=native),
        ):
            self.assertEqual(run(["get", "https://example.org/file"]), 2)

    def test_wait_auto_preserves_cloud_task_routing(self):
        api = Mock()
        api.wait_task.return_value = OfflineTask("cloud-task", "file", "已完成", 1)
        with (
            patch("xlcli.cli.Auth"),
            patch("xlcli.cli.DriveAPI", return_value=api),
            patch("xlcli.cli.NativeThunder") as native,
        ):
            self.assertEqual(run(["wait", "cloud-task"]), 0)
        api.wait_task.assert_called_once_with("cloud-task", 3600)
        native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
