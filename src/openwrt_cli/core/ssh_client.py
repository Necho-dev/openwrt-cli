# -*- coding: utf-8 -*-
"""
SSH 客户端封装 - 支持更好的错误提示和诊断
"""
import logging
import os
import socket
import sys
import time

import paramiko
from paramiko.ssh_exception import (
    AuthenticationException,
    NoValidConnectionsError,
    SSHException,
)

from openwrt_cli.core.errors import DeviceCommandError, DeviceConnectionError
from openwrt_cli.i18n import t

SSHConnectionError = DeviceConnectionError
SSHCommandError = DeviceCommandError


def diagnose(host: str, port: int, timeout: int = 5) -> dict:
    """
    连接前的本地诊断，返回诊断结果
    """
    results = {
        "host": host,
        "port": port,
        "dns_ok": False,
        "ping_ok": False,
        "tcp_ok": False,
        "ping_ms": None,
        "suggestions": [],
    }
    
    # DNS 解析
    try:
        socket.gethostbyname(host)
        results["dns_ok"] = True
    except socket.gaierror:
        results["suggestions"].append(f"❌ {t('diag.dns', host=host)}")
        return results
    
    # Ping 检测
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        # Windows 不支持 ICMP，用 TCP 端口检测代替
        sock.close()
        # 尝试 TCP 连接到 SSH 端口
        s = socket.socket()
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        results["ping_ok"] = True
        results["ping_ms"] = round((time.time() - start) * 1000)
    except socket.timeout:
        results["suggestions"].append(f"❌ {t('diag.timeout', host=host, port=port, timeout=timeout)}")
        results["suggestions"].append(f"💡 {t('diag.timeout_hint')}")
    except socket.error as e:
        results["suggestions"].append(f"❌ {t('diag.tcp_fail', host=host, port=port, error=e)}")
        results["suggestions"].append(f"💡 {t('diag.tcp_hint')}")
    except Exception as e:
        results["suggestions"].append(f"⚠️  {t('diag.exception', error=e)}")
    
    return results


class SSHClient:
    def __init__(self, host, user="root", port=22, password=None,
                 identity_file=None, timeout=10, verbose=False):
        self.host = host
        self.user = user
        self.port = port
        self.verbose = verbose
        self.client = None
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # 诊断
        if verbose:
            diag = diagnose(host, port, timeout=3)
            if not diag["dns_ok"]:
                raise SSHConnectionError(t("err.dns", host=host))
            if not diag["ping_ok"]:
                print(f"⚠️  {t('warn.host_unreachable', host=host, port=port)}", file=sys.stderr)
                for s in diag["suggestions"]:
                    print(f"   {s}", file=sys.stderr)

        # Dropbear 认证次数很少。已指定密码或密钥时不要再扫 agent / 默认密钥，
        # 否则会被提前断开，表现为 Error reading SSH protocol banner。
        use_explicit_auth = bool(password or identity_file)
        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": timeout,
            "banner_timeout": max(timeout, 30),
            "auth_timeout": timeout,
            "look_for_keys": not use_explicit_auth,
            "allow_agent": not use_explicit_auth,
        }
        if password:
            connect_kwargs["password"] = password
        if identity_file:
            identity_file = os.path.expanduser(identity_file)
            if os.path.exists(identity_file):
                connect_kwargs["key_filename"] = identity_file
            else:
                raise SSHConnectionError(t("err.key_file_missing", path=identity_file))

        self._connect_kwargs = connect_kwargs
        transport_log = logging.getLogger("paramiko.transport")
        prev_level = transport_log.level
        transport_log.setLevel(logging.CRITICAL)
        try:
            self.client.connect(**self._connect_kwargs)
        except AuthenticationException:
            raise SSHConnectionError(t("err.ssh_auth", host=host, port=port, user=user))
        except socket.timeout:
            raise SSHConnectionError(t("err.ssh_timeout", timeout=timeout, host=host, port=port))
        except NoValidConnectionsError as e:
            raise SSHConnectionError(t("err.ssh_connect", host=host, port=port, error=e))
        except socket.error as e:
            err_msg = str(e)
            if "Connection refused" in err_msg:
                raise SSHConnectionError(t("err.ssh_refused", host=host, port=port))
            elif "No route to host" in err_msg:
                raise SSHConnectionError(t("err.ssh_no_route", host=host))
            elif "Network is unreachable" in err_msg:
                raise SSHConnectionError(t("err.ssh_net_unreach"))
            else:
                raise SSHConnectionError(t("err.ssh_net", host=host, port=port, error=e))
        except SSHException as e:
            err_msg = str(e)
            if "banner" in err_msg.lower():
                raise SSHConnectionError(t("err.ssh_banner", host=host, port=port, user=user))
            raise SSHConnectionError(t("err.ssh_proto", error=e))
        except Exception as e:
            raise SSHConnectionError(
                t("err.ssh_fail", error=f"{type(e).__name__}: {e}", host=host, port=port, user=user)
            )
        finally:
            transport_log.setLevel(prev_level)
        self._enable_keepalive()

    def _enable_keepalive(self) -> None:
        transport = self.client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)

    def reconnect(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        transport_log = logging.getLogger("paramiko.transport")
        prev = transport_log.level
        transport_log.setLevel(logging.CRITICAL)
        try:
            self.client.connect(**self._connect_kwargs)
        finally:
            transport_log.setLevel(prev)
        self._enable_keepalive()

    def keepalive(self) -> None:
        """协议层已有 set_keepalive；这里只在传输死掉时重连，不额外 exec。"""
        transport = self.client.get_transport() if self.client else None
        if transport is None or not transport.is_active():
            self.reconnect()

    def exec_iter(self, cmd: str):
        """逐行产出 stdout，用于 logread -f 一类长驻命令。Ctrl+C 由调用方中断。"""
        if self.client is None:
            raise SSHConnectionError(t("err.ssh_disconnected"))
        try:
            _, stdout, _ = self.client.exec_command(cmd, timeout=None)
            stdout.channel.settimeout(None)
            while True:
                line = stdout.readline()
                if not line:
                    break
                yield line.rstrip("\r\n")
        except socket.error as e:
            raise SSHConnectionError(t("err.ssh_exec_drop", error=e)) from e

    def exec(self, cmd: str, timeout=30, check=False) -> str:
        """
        执行命令并返回输出
        :param cmd: 命令
        :param timeout: 超时秒数
        :param check: 是否检查返回码（非0则抛异常）
        """
        try:
            stdin, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            stderr_text = stderr.read().decode("utf-8", errors="replace").strip()
            stdout_text = stdout.read().decode("utf-8", errors="replace").strip()

            if check and exit_code != 0:
                raise SSHCommandError(t("err.ssh_cmd_fail", code=exit_code, cmd=cmd, stderr=stderr_text))
            return stdout_text
        except socket.timeout:
            raise SSHCommandError(t("err.ssh_cmd_timeout", timeout=timeout, cmd=cmd))
        except socket.error as e:
            raise SSHConnectionError(t("err.ssh_exec_drop", error=e))
        except Exception as e:
            if "not connected" in str(e).lower():
                raise SSHConnectionError(t("err.ssh_disconnected"))
            raise

    def exec_json(self, cmd: str, timeout=30):
        """执行命令并尝试解析为 JSON"""
        import json
        output = self.exec(cmd, timeout=timeout)
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            raise ValueError(t("err.ssh_not_json", output=output[:500]))

    def exec_sudo(self, cmd: str, timeout=30) -> str:
        """使用 sudo 执行命令（适用于非 root 用户场景）"""
        sudo_cmd = f"sudo {cmd}"
        return self.exec(sudo_cmd, timeout=timeout)

    def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        result = self.exec(f'test -e "{path}" && echo "1" || echo "0"')
        return result.strip() == "1"

    def read_file(self, path: str) -> str:
        """读取远程文件内容"""
        return self.exec(f"cat {path}", timeout=10)

    def write_file(self, path: str, content: str) -> str:
        """写入远程文件"""
        # 用 sftp 更安全，但这里用 heredoc 简单实现
        safe_content = content.replace("'", "'\"'\"'")
        return self.exec(f"cat > {path} << 'EOF'\n{content}\nEOF")

    def close(self):
        if self.client:
            self.client.close()
            self.client = None
