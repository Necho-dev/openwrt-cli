from __future__ import annotations

from openwrt_cli.i18n import normalize_language, set_language, t
from openwrt_cli.i18n.catalog import CATALOGS, DEFAULT_LANG, EN, SUPPORTED
from openwrt_cli.tui.app import format_svc_detail


def test_catalog_key_parity():
    assert DEFAULT_LANG in CATALOGS
    assert set(SUPPORTED) == set(CATALOGS)
    source = set(EN)
    for lang, table in CATALOGS.items():
        assert set(table) == source, lang


def test_normalize_language():
    assert normalize_language("zh_CN.UTF-8") == "zh"
    assert normalize_language("zh-Hans") == "zh"
    assert normalize_language("en_US") == "en"
    assert normalize_language("cn") == "zh"
    assert normalize_language("de_DE") is None
    assert normalize_language("") is None


def test_language_help_lists_supported():
    set_language("en")
    assert "en" in t("err.language")
    assert "zh" in t("help.opt.language")
    set_language("en")


def test_frozen_glossary():
    set_language("en")
    assert t("tab.neighbors") == "Neighbors"
    assert t("col.received") == "Received"
    assert t("col.startup") == "Startup"
    assert t("legend.download") == "Download"
    set_language("zh")
    assert t("tab.neighbors") == "邻居"
    assert t("col.received") == "收到"
    assert t("section.rules") == "规则"
    assert t("col.rule") == "规则"
    set_language("en")


def test_svc_detail_note_follows_language():
    payload = {
        "service": "network",
        "priority": 20,
        "running": True,
        "enabled": True,
        "uci": {"package": "network", "sections": 4, "types": {"interface": 3}},
    }
    payload["note"] = "节点/规则等业务配置在 UCI 中，本命令只做摘要，不展开"
    set_language("en")
    en = str(format_svc_detail(payload))
    assert "summary" in en.lower()
    assert "摘要" not in en
    assert t("col.priority") in en
    set_language("zh")
    zh = str(format_svc_detail(payload))
    assert "摘要" in zh
    assert t("col.priority") in zh
    set_language("en")
