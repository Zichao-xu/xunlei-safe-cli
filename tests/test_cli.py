import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from xlcli.cli import _select_engine, run


class CLITests(unittest.TestCase):
    def test_bare_magnet_uses_local_thunder_without_constructing_cloud_auth(self):
        local = Mock()
        local.add.return_value = ["magnet:?xt=urn:btih:abc"]
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


if __name__ == "__main__":
    unittest.main()
