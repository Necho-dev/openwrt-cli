from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from openwrt_cli.app import app, hoist_global_options
from openwrt_cli.core.config import MASKED_SECRET

runner = CliRunner()


def _parse(stdout: str) -> dict:
    return json.loads(stdout)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_legacy_file_is_not_rewritten_on_show(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    original = "host: 192.0.2.1\nuser: root\npassword: secret\nlanguage: en\nmcp:\n  mode: readwrite\n"
    _write(cfg, original)
    shown = runner.invoke(app, ["--config", str(cfg), "--json", "config", "show"])
    assert shown.exit_code == 0
    body = _parse(shown.stdout)
    assert body["active"] == "default"
    assert body["language"] == "en"
    assert body["mcp"]["mode"] == "readwrite"
    assert body["mcp"]["effective"] == "readwrite"
    assert "host" not in body
    assert "password" not in json.dumps(body)
    assert cfg.read_text(encoding="utf-8") == original


def test_users_add_list_activate_update_and_config_keeps_override(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "language: en\nmcp:\n  mode: readonly\n")
    added = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "--json", "profiles", "add", "home",
        "-H", "192.0.2.1", "-u", "root", "--ssh", "--password", "secret",
        "--mcp-mode", "readwrite",
    ]))
    assert added.exit_code == 0, added.stdout
    listed = runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "list"])
    body = _parse(listed.stdout)
    assert body["active"] == "home"
    assert body["profiles"][0]["host"] == "192.0.2.1"
    assert body["profiles"][0]["password"] == MASKED_SECRET
    assert body["profiles"][0]["mcp"]["mode"] == "readwrite"
    assert body["profiles"][0]["mcp_effective"] == "readwrite"

    second = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "--json", "profiles", "add", "lab",
        "-H", "192.0.2.2", "--https",
    ]))
    assert second.exit_code == 0, second.stdout
    shown = runner.invoke(app, ["--config", str(cfg), "--json", "config", "show"])
    assert _parse(shown.stdout)["active"] == "home"
    assert _parse(shown.stdout)["mcp"]["effective"] == "readwrite"

    activated = runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "use", "lab"])
    assert activated.exit_code == 0
    assert _parse(runner.invoke(app, ["--config", str(cfg), "--json", "config", "show"]).stdout)["mcp"]["effective"] == "readonly"

    renamed = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "--json", "profiles", "update", "home",
        "--name", "house", "--mcp-mode", "inherit",
    ]))
    assert renamed.exit_code == 0, renamed.stdout
    again = _parse(runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "list"]).stdout)
    names = [item["name"] for item in again["profiles"]]
    assert names == ["house", "lab"]
    house = again["profiles"][0]
    assert "mcp" not in house
    assert house["mcp_effective"] == "readonly"

    mode = runner.invoke(app, ["--config", str(cfg), "--json", "config", "set", "--mcp-mode", "readwrite"])
    assert mode.exit_code == 0
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert disk["mcp"]["mode"] == "readwrite"
    assert "mcp" not in disk["profiles"][0]
    assert disk["profiles"][1]["host"] == "192.0.2.2"
    assert "host" not in disk


def test_config_set_host_is_rejected_and_root_host_does_not_save(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "active: home\nmcp:\n  mode: readonly\nusers:\n- name: home\n  host: 192.0.2.1\n  user: root\n  transport: ssh\n  port: 22\n")
    before = cfg.read_text(encoding="utf-8")
    rejected = runner.invoke(app, ["--config", str(cfg), "config", "set", "-H", "198.51.100.1"])
    assert rejected.exit_code != 0
    assert cfg.read_text(encoding="utf-8") == before
    shown = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "-H", "198.51.100.1", "--json", "config", "show",
    ]))
    assert shown.exit_code == 0
    assert "198.51.100.1" not in shown.stdout
    assert cfg.read_text(encoding="utf-8") == before


def test_first_setup_suggests_home_not_the_login_user():
    from openwrt_cli.commands.setup import profile_prompt

    first, name = profile_prompt({"profiles": [], "user": "root"})
    assert first is True
    assert name == "home"
    again, name = profile_prompt({
        "profiles": [{"name": "lab", "user": "root"}],
        "active": "lab",
        "user": "root",
    })
    assert again is False
    assert name == "lab"


def test_save_config_without_a_profile_requires_a_chosen_name(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "language: en\nmcp:\n  mode: readonly\n")
    saved = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "-H", "192.0.2.1", "-u", "root", "--save-config", "--json", "config", "path",
    ]))
    assert saved.exit_code != 0
    assert _parse(saved.stdout)["error"] == "need_name"
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    assert "profiles" not in disk
    assert "root" not in cfg.read_text(encoding="utf-8")


def test_save_config_updates_active_account(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "active: home\nmcp:\n  mode: readonly\nusers:\n- name: home\n  host: 192.0.2.1\n  user: root\n  transport: ssh\n  port: 22\n")
    saved = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "-H", "198.51.100.9", "--save-config", "--json", "config", "path",
    ]))
    assert saved.exit_code == 0, saved.stdout
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert disk["profiles"][0]["host"] == "198.51.100.9"
    assert disk["active"] == "home"
    assert "host" not in disk
    assert "users" not in disk


def test_profiles_add_without_host_needs_a_terminal(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "language: en\nmcp:\n  mode: readonly\n")
    missing = runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "add"])
    assert missing.exit_code == 2
    assert _parse(missing.stdout)["error"] == "need_name"
    plain = runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "add", "shop-rpa"])
    assert plain.exit_code == 1
    assert _parse(plain.stdout)["error"] == "profile_need_host"
    assert "profiles" not in (yaml.safe_load(cfg.read_text(encoding="utf-8")) or {})


def test_profiles_add_guided_saves_the_named_profile(tmp_path: Path, monkeypatch):
    from openwrt_cli.i18n import t

    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "language: en\nmcp:\n  mode: readonly\n")

    def fake_text(message, default=None, validate=None):
        value = "10.0.0.8" if message == t("setup.host") else (default or "shop-rpa")
        if validate is not None:
            assert validate(value) is True
        return value

    def fake_select(message, choices, default=None):
        return default or choices[0]

    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd._can_pick_profile", lambda app: True)
    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd.ask_text", fake_text)
    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd.ask_select", fake_select)
    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd.ask_password", lambda message: "secret")
    added = runner.invoke(app, ["--config", str(cfg), "profiles", "add", "shop-rpa"])
    assert added.exit_code == 0, added.stdout
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    profile = disk["profiles"][0]
    assert disk["active"] == "shop-rpa"
    assert profile["name"] == "shop-rpa"
    assert profile["host"] == "10.0.0.8"
    assert profile["user"] == "root"
    assert profile["transport"] == "ssh"
    assert profile["password"] == "secret"
    assert "mcp" not in profile


def test_profiles_update_without_fields_needs_a_terminal(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    original = "active: home\nmcp:\n  mode: readonly\nprofiles:\n- name: home\n  host: 192.0.2.1\n  user: root\n  transport: ssh\n  port: 22\n"
    _write(cfg, original)
    missing = runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "update"])
    assert missing.exit_code == 2
    assert _parse(missing.stdout)["error"] == "need_name"
    plain = runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "update", "home"])
    assert plain.exit_code == 2
    assert _parse(plain.stdout)["error"] == "need_fields"
    assert cfg.read_text(encoding="utf-8") == original


def test_profiles_update_guided_edits_the_chosen_profile(tmp_path: Path, monkeypatch):
    from openwrt_cli.i18n import t

    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, (
        "active: home\n"
        "mcp:\n  mode: readonly\n"
        "profiles:\n"
        "- name: home\n  host: 192.0.2.1\n  user: root\n  transport: ssh\n  port: 22\n  password: secret\n"
        "  mcp:\n    mode: readwrite\n"
    ))

    def fake_text(message, default=None, validate=None):
        value = "198.51.100.9" if message == t("setup.host") else (default or "")
        if validate is not None:
            assert validate(value) is True
        return value

    def fake_select(message, choices, default=None):
        return default or choices[0]

    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd._can_pick_profile", lambda app: True)
    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd.ask_text", fake_text)
    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd.ask_select", fake_select)
    edited = runner.invoke(app, ["--config", str(cfg), "profiles", "update", "home"])
    assert edited.exit_code == 0, edited.stdout
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    profile = disk["profiles"][0]
    assert profile["name"] == "home"
    assert profile["host"] == "198.51.100.9"
    assert profile["user"] == "root"
    assert profile["password"] == "secret"
    assert profile["mcp"]["mode"] == "readwrite"


def test_accounts_use_without_name_needs_a_terminal(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "active: home\nmcp:\n  mode: readonly\nusers:\n- name: home\n  host: 192.0.2.1\n  user: root\n  transport: ssh\n  port: 22\n")
    missing = runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "use"])
    assert missing.exit_code == 2
    assert _parse(missing.stdout)["error"] == "need_name"


def test_accounts_use_picker_confirms_highlight(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, (
        "active: home\n"
        "mcp:\n  mode: readonly\n"
        "users:\n"
        "- name: home\n  host: 192.0.2.1\n  user: root\n  transport: ssh\n  port: 22\n"
        "- name: lab\n  host: 192.0.2.2\n  user: root\n  transport: http\n  scheme: https\n  port: 443\n"
    ))
    seen: dict = {}

    class _Question:
        def ask(self):
            labels = seen["choices"]
            assert any(label.startswith("● home") for label in labels)
            assert seen["default"].startswith("● home")
            assert seen["use_arrow_keys"] is True
            return next(label for label in labels if "lab" in label)

    def fake_select(message, choices, **kwargs):
        seen["message"] = message
        seen["choices"] = list(choices)
        seen["default"] = kwargs.get("default")
        seen["use_arrow_keys"] = kwargs.get("use_arrow_keys")
        return _Question()

    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd.questionary.select", fake_select)
    monkeypatch.setattr("openwrt_cli.commands.profiles_cmd._can_pick_profile", lambda app: True)
    picked = runner.invoke(app, ["--config", str(cfg), "profiles", "use"])
    assert picked.exit_code == 0, picked.stdout
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert disk["active"] == "lab"


def test_delete_last_account_keeps_globals(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "language: en\nmcp:\n  mode: readwrite\nactive: only\nusers:\n- name: only\n  host: 192.0.2.1\n  user: root\n  transport: ssh\n  port: 22\n")
    denied = runner.invoke(app, ["--config", str(cfg), "--json", "profiles", "del", "only"])
    assert denied.exit_code == 2
    removed = runner.invoke(app, ["--config", str(cfg), "--json", "--yes", "profiles", "del", "only"])
    assert removed.exit_code == 0, removed.stdout
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert disk["language"] == "en"
    assert disk["mcp"]["mode"] == "readwrite"
    assert "profiles" not in disk
    assert "users" not in disk
    assert "active" not in disk
    assert "host" not in disk
    need = runner.invoke(app, ["--config", str(cfg), "--json", "system", "status"])
    assert need.exit_code == 1
    assert _parse(need.stdout)["error"] == "need_host"


def test_delete_active_promotes_the_next_account(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, (
        "active: home\n"
        "mcp:\n  mode: readonly\n"
        "users:\n"
        "- name: home\n  host: 192.0.2.1\n  user: root\n  transport: ssh\n  port: 22\n"
        "- name: lab\n  host: 192.0.2.2\n  user: root\n  transport: ssh\n  port: 22\n"
        "- name: last\n  host: 192.0.2.3\n  user: root\n  transport: ssh\n  port: 22\n"
    ))
    gone = runner.invoke(app, ["--config", str(cfg), "--json", "--yes", "profiles", "del", "home"])
    assert gone.exit_code == 0, gone.stdout
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert disk["active"] == "lab"
    tail = runner.invoke(app, ["--config", str(cfg), "--json", "--yes", "profiles", "del", "last"])
    assert tail.exit_code == 0, tail.stdout
    disk = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert disk["active"] == "lab"
    assert [item["name"] for item in disk["profiles"]] == ["lab"]
    assert "users" not in disk


def test_profiles_commands_accept_json_and_show_masks_the_password(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    _write(cfg, "language: en\nmcp:\n  mode: readonly\n")
    added = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "profiles", "add", "home",
        "-H", "192.0.2.1", "-u", "root", "--ssh", "--password", "secret",
        "--mcp-mode", "readwrite", "--json",
    ]))
    assert added.exit_code == 0, added.stdout
    assert _parse(added.stdout)["saved"] is True

    listed = runner.invoke(app, hoist_global_options(["--config", str(cfg), "profiles", "list", "--json"]))
    assert listed.exit_code == 0, listed.stdout
    body = _parse(listed.stdout)
    assert body["ok"] is True
    assert body["profiles"][0]["password"] == MASKED_SECRET
    assert "secret" not in listed.stdout

    shown = runner.invoke(app, hoist_global_options(["--config", str(cfg), "profiles", "show", "--json"]))
    assert shown.exit_code == 0, shown.stdout
    current = _parse(shown.stdout)
    assert current["current"] is True
    assert current["profile"]["name"] == "home"
    assert current["profile"]["host"] == "192.0.2.1"
    assert current["profile"]["password"] == MASKED_SECRET
    assert current["profile"]["mcp"]["mode"] == "readwrite"
    assert current["profile"]["mcp_effective"] == "readwrite"
    assert "secret" not in shown.stdout

    second = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "profiles", "add", "lab",
        "-H", "192.0.2.2", "--https", "--json",
    ]))
    assert second.exit_code == 0, second.stdout
    other = runner.invoke(app, hoist_global_options(["--config", str(cfg), "profiles", "show", "lab", "--json"]))
    assert other.exit_code == 0, other.stdout
    lab = _parse(other.stdout)
    assert lab["current"] is False
    assert lab["profile"]["name"] == "lab"
    assert lab["profile"]["scheme"] == "https"
    assert "password" not in lab["profile"]

    text = runner.invoke(app, ["--config", str(cfg), "profiles", "show", "home"])
    assert text.exit_code == 0, text.stdout
    assert "home" in text.stdout
    assert "secret" not in text.stdout
    assert MASKED_SECRET in text.stdout

    missing = runner.invoke(app, hoist_global_options(["--config", str(cfg), "profiles", "show", "nope", "--json"]))
    assert missing.exit_code == 1
    assert _parse(missing.stdout)["error"] == "profile_missing"

    used = runner.invoke(app, hoist_global_options(["--config", str(cfg), "profiles", "use", "lab", "--json"]))
    assert used.exit_code == 0
    assert _parse(used.stdout)["saved"] is True
    updated = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "profiles", "update", "home", "--name", "house", "--json",
    ]))
    assert updated.exit_code == 0
    assert _parse(updated.stdout)["ok"] is True
    removed = runner.invoke(app, hoist_global_options([
        "--config", str(cfg), "profiles", "del", "house", "--yes", "--json",
    ]))
    assert removed.exit_code == 0
    assert _parse(removed.stdout)["saved"] is True

    empty = tmp_path / "empty.yaml"
    _write(empty, "language: en\n")
    none = runner.invoke(app, hoist_global_options(["--config", str(empty), "profiles", "show", "--json"]))
    assert none.exit_code == 1
    assert _parse(none.stdout)["error"] == "no_profiles"
