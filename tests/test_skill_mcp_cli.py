from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openwrt_cli.app import app
from openwrt_cli.core.config import MASKED_SECRET
from openwrt_cli.mcp.packaging import skill_text
from openwrt_cli.commands.skill_cmd import (
    _client_choices,
    _collect_targets,
    _install_intro,
    _plan_rows,
)
from openwrt_cli.mcp.targets import SKILL_NAME, client_ids, forbidden_skill_path

runner = CliRunner()


def _parse(stdout: str) -> dict:
    return json.loads(stdout)


def test_skill_detect_and_list_differ():
    detected = runner.invoke(app, ["--json", "skill", "detect"])
    listed = runner.invoke(app, ["--json", "skill", "list"])
    assert detected.exit_code == 0
    assert listed.exit_code == 0
    detect_data = _parse(detected.stdout)
    list_data = _parse(listed.stdout)
    assert detect_data["ok"] is True
    assert list_data["ok"] is True
    assert "clients" in detect_data
    assert "copies" not in detect_data
    assert "bundled" not in detect_data
    assert "via" in detect_data["clients"][0]
    assert detect_data["clients"][0]["installed"] in {"present", "missing"}
    listed_ids = {row["id"] for row in detect_data["clients"]}
    assert listed_ids == set(client_ids())
    assert {"windsurf", "qoder", "opencode"} <= listed_ids
    assert list_data["bundled"][0]["name"] == SKILL_NAME
    assert "SKILL.md" in list_data["bundled"][0]["files"]
    assert "clients" not in list_data
    assert {row["status"] for row in list_data["copies"]} <= {"present", "missing"}
    assert {row["project"] for row in list_data["copies"]} <= {"present", "missing"}
    assert all("scope" not in row for row in list_data["copies"])
    workspace = str(Path.cwd())
    assert all(not str(row["path"]).startswith(workspace + "/") for row in list_data["copies"])


def test_skill_list_and_show_json():
    listed = runner.invoke(app, ["--json", "skill", "list"])
    assert listed.exit_code == 0
    data = _parse(listed.stdout)
    assert data["ok"] is True
    assert data["bundled"][0]["name"] == SKILL_NAME
    assert "copies" in data
    shown = runner.invoke(app, ["--json", "skill", "show"])
    assert shown.exit_code == 0
    body = _parse(shown.stdout)
    assert "do not" in body["text"].lower() or "禁止" in body["text"]
    assert "--yes" in body["text"]
    assert "openwrt-ops" in skill_text()
    front = skill_text().split("---", 2)[1]
    assert not re.search(r"[\u4e00-\u9fff]", front)


def test_skill_install_dir_and_forbidden(tmp_path: Path):
    dest_root = tmp_path / "skills"
    result = runner.invoke(app, ["--json", "skill", "install", "--dir", str(dest_root), "--yes"])
    assert result.exit_code == 0, result.stdout
    data = _parse(result.stdout)
    assert data["installed"][0]["status"] in {"created", "updated"}
    skill_dir = dest_root / SKILL_NAME
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "USAGE.md").is_file()
    again = runner.invoke(app, ["--json", "skill", "install", "--dir", str(dest_root), "--yes"])
    assert _parse(again.stdout)["installed"][0]["status"] == "unchanged"
    gone = runner.invoke(app, ["--json", "skill", "uninstall", "--dir", str(dest_root), "--yes"])
    assert gone.exit_code == 0
    assert not skill_dir.exists()
    reserved = Path.home() / ".cursor" / "skills-cursor" / SKILL_NAME
    assert forbidden_skill_path(reserved) is True


def test_skill_install_project_not_global_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--json", "skill", "install", "--agent", "cursor", "--project", "--yes"])
    assert result.exit_code == 0, result.stdout
    skill_dir = tmp_path / ".cursor" / "skills" / SKILL_NAME
    assert (skill_dir / "SKILL.md").is_file()
    listed = runner.invoke(app, ["--json", "skill", "list"])
    cursor = next(row for row in _parse(listed.stdout)["copies"] if row["client"] == "cursor")
    assert cursor["project"] == "present"
    assert not str(cursor["path"]).startswith(str(tmp_path))
    conflict = runner.invoke(app, ["--json", "skill", "install", "--global", "--project", "--yes"])
    assert conflict.exit_code != 0


def test_install_intro_is_one_line(monkeypatch: pytest.MonkeyPatch):
    from openwrt_cli.mcp.targets import client_by_id

    found = [client_by_id("cursor")]
    monkeypatch.setattr("openwrt_cli.commands.skill_cmd.detected_clients", lambda: found)
    monkeypatch.setattr(
        "openwrt_cli.commands.skill_cmd._copy_rows",
        lambda: [{"client": "cursor", "status": "missing", "project": "missing"}],
    )
    text = _install_intro()
    assert SKILL_NAME in text
    assert "\n" not in text


def test_client_choices_detected_only_simple_label(monkeypatch: pytest.MonkeyPatch):
    from openwrt_cli.mcp.targets import client_by_id

    found = [client_by_id("cursor"), client_by_id("codex")]
    monkeypatch.setattr("openwrt_cli.commands.skill_cmd.detected_clients", lambda: found)
    choices = _client_choices()
    assert [item.value for item in choices] == ["cursor", "codex", "custom"]
    assert choices[0].title == "Cursor (cursor)"
    assert choices[1].title == "Codex (codex)"


def test_collect_targets_custom_skips_named_scope(tmp_path: Path):
    only = _collect_targets(agents=[], project=True, directory=str(tmp_path / "mine"))
    assert only == [("custom", tmp_path.resolve() / "mine" / SKILL_NAME)]
    mixed = _collect_targets(agents=["cursor"], project=True, directory=str(tmp_path / "mine"))
    assert mixed[0][0] == "custom"
    assert mixed[1][0] == "cursor"
    assert mixed[1][1].parts[-3:] == (".cursor", "skills", SKILL_NAME)


def test_skill_plan_rows_check_dest(tmp_path: Path):
    dest = tmp_path / "skills" / SKILL_NAME
    rows = _plan_rows([("custom", dest)])
    assert rows[0]["status"] == "ready"
    assert rows[0]["path"] == str(dest)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("# x\n", encoding="utf-8")
    again = _plan_rows([("custom", dest)])
    assert again[0]["status"] == "exists"


def test_skill_install_json_needs_yes(tmp_path: Path):
    result = runner.invoke(app, ["--json", "skill", "install", "--dir", str(tmp_path / "x")])
    assert result.exit_code == 2
    assert _parse(result.stdout).get("error") == "need_yes"


def test_mcp_overview_json():
    result = runner.invoke(app, ["--json", "mcp"])
    assert result.exit_code == 0
    data = _parse(result.stdout)
    assert data["ok"] is True
    assert data["writes_config"] is False
    assert data["next"] == ["json", "prompt", "path"]


def test_mcp_json_and_prompt():
    js = runner.invoke(app, ["--json", "mcp", "json", "--command", "openwrt-mcp"])
    assert js.exit_code == 0
    data = _parse(js.stdout)
    assert data["ok"] is True
    assert data["snippet"]["mcpServers"]["openwrt"]["command"] == "openwrt-mcp"
    assert "password" not in js.stdout.lower() or MASKED_SECRET in js.stdout
    toml = runner.invoke(app, ["--json", "mcp", "json", "--client", "codex", "--command", "openwrt-mcp"])
    body = _parse(toml.stdout)
    assert body["format"] == "toml"
    assert "mcp_servers.openwrt" in body["snippet"]
    prompt = runner.invoke(app, ["--json", "mcp", "prompt"])
    text = _parse(prompt.stdout)["prompt"]
    assert "readonly" in text
    assert "--yes" in text
    paths = runner.invoke(app, ["--json", "mcp", "path", "--client", "cursor"])
    assert _parse(paths.stdout)["paths"][0]["id"] == "cursor"
    oc = runner.invoke(app, ["--json", "mcp", "json", "--client", "opencode", "--command", "openwrt-mcp"])
    oc_body = _parse(oc.stdout)
    assert oc_body["format"] == "opencode"
    assert oc_body["snippet"]["mcp"]["openwrt"]["command"] == ["openwrt-mcp"]
    wind = runner.invoke(app, ["--json", "mcp", "json", "--client", "windsurf", "--command", "openwrt-mcp"])
    assert _parse(wind.stdout)["snippet"]["mcpServers"]["openwrt"]["command"] == "openwrt-mcp"


def test_config_set_mcp_mode(tmp_path: Path):
    cfg = tmp_path / "openwrt-cli.yaml"
    cfg.write_text("host: 192.0.2.1\nuser: root\n", encoding="utf-8")
    bad = runner.invoke(app, ["--config", str(cfg), "config", "set", "--mcp-mode", "full"])
    assert bad.exit_code != 0
    ok = runner.invoke(app, ["--config", str(cfg), "--json", "config", "set", "--mcp-mode", "readwrite"])
    assert ok.exit_code == 0
    shown = runner.invoke(app, ["--config", str(cfg), "--json", "config", "show"])
    body = _parse(shown.stdout)
    assert body["mcp"]["mode"] == "readwrite"
    assert "password" not in json.dumps(body).replace(MASKED_SECRET, "")
