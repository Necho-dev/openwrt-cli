"""OpenSSH 单行公钥（Dropbear / OpenSSH authorized_keys 兼容）。"""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

from openwrt_cli.i18n import t

DEFAULT_KEY_PATH = Path.home() / ".ssh" / "id_ed25519_openwrt"

OPENSSH_KEY_TYPES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@",
    "sk-ecdsa-sha2-nistp256@",
)

REMOTE_AUTH_FILES = (
    "/etc/dropbear/authorized_keys",
    "/root/.ssh/authorized_keys",
)


def normalize_openssh_pubkey(text: str) -> str:
    """校验并返回一行 OpenSSH 公钥。拒绝 PEM / RFC4716。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError(t("err.pubkey_empty"))

    if raw.startswith("-----"):
        raise ValueError(t("err.pubkey_pem"))

    line = next((ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.startswith("#")), "")
    if not line:
        raise ValueError(t("err.pubkey_line"))

    key_type = line.split(None, 1)[0]
    if not any(key_type == typ or key_type.startswith(typ) for typ in OPENSSH_KEY_TYPES):
        raise ValueError(t("err.pubkey_type", key_type=key_type))
    if len(line.split()) < 2:
        raise ValueError(t("err.pubkey_data"))
    return line


def generate_ed25519(private_path: Path | None = None, comment: str | None = None) -> Path:
    """生成 OpenSSH ed25519 密钥对，返回私钥路径。"""
    private_path = Path(private_path or DEFAULT_KEY_PATH).expanduser()
    if private_path.exists():
        return private_path

    comment = comment or f"openwrt-cli@{socket.gethostname()}"
    private_path.parent.mkdir(mode=0o700, exist_ok=True)
    subprocess.run(
        [
            "ssh-keygen",
            "-t", "ed25519",
            "-a", "64",
            "-f", str(private_path),
            "-N", "",
            "-C", comment,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    os.chmod(private_path, 0o600)
    pub = private_path.with_suffix(private_path.suffix + ".pub") if private_path.suffix else Path(str(private_path) + ".pub")
    if not pub.exists():
        pub = Path(str(private_path) + ".pub")
    os.chmod(pub, 0o644)
    return private_path


def public_key_path(private_path: Path) -> Path:
    pub = Path(str(private_path) + ".pub")
    if pub.exists():
        return pub
    alt = private_path.with_suffix(".pub")
    return alt if alt.exists() else pub


def load_or_create_default_pubkey(comment: str | None = None) -> tuple[str, Path, bool]:
    """返回 (pubkey, private_path, created)。"""
    private_path = DEFAULT_KEY_PATH
    created = not private_path.exists()
    if created:
        generate_ed25519(private_path, comment=comment)
    pubkey = normalize_openssh_pubkey(public_key_path(private_path).read_text(encoding="utf-8"))
    return pubkey, private_path, created


def install_remote_pubkey(ssh, pubkey: str) -> list[str]:
    """写入 Dropbear / OpenSSH authorized_keys，已存在则跳过。"""
    key = normalize_openssh_pubkey(pubkey)
    safe = key.replace("'", "'\"'\"'")
    written = []
    for path in REMOTE_AUTH_FILES:
        parent = os.path.dirname(path)
        ssh.exec(f"mkdir -p '{parent}' && chmod 700 '{parent}'")
        ssh.exec(f"touch '{path}' && chmod 600 '{path}'")
        exists = ssh.exec(f"grep -qxF '{safe}' '{path}' && echo yes || echo no").strip()
        if exists != "yes":
            ssh.exec(f"printf '%s\\n' '{safe}' >> '{path}'")
            written.append(path)
        else:
            written.append(f"{path} (already present)")
    return written
