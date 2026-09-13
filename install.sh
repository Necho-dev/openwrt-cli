#!/usr/bin/env bash
# OpenWrt CLI — Linux / macOS installer (CLI/TUI palette)
#   curl -fsSL https://raw.githubusercontent.com/Necho-dev/openwrt-cli/main/install.sh | bash
#   ./install.sh   (from a clone → pip install -e ".[mcp]")

set -euo pipefail

REPO="Necho-dev/openwrt-cli"
GIT_URL="https://github.com/${REPO}.git"
PIP_GIT="git+${GIT_URL}"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_ACCENT='\033[1;36m'
  C_OK='\033[32m'
  C_WARN='\033[33m'
  C_ERR='\033[1;31m'
  C_MUTED='\033[2m'
  C_RESET='\033[0m'
else
  C_ACCENT="" C_OK="" C_WARN="" C_ERR="" C_MUTED="" C_RESET=""
fi

info()  { printf "${C_ACCENT}[i]${C_RESET}  %s\n" "$*"; }
ok()    { printf "${C_OK}[ok]${C_RESET} %s\n" "$*"; }
warn()  { printf "${C_WARN}[!]${C_RESET}  %s\n" "$*"; }
error() { printf "${C_ERR}[x]${C_RESET}  %s\n" "$*" >&2; }

_norm_lang() {
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | tr '-' '_'
}

detect_lang() {
  local raw
  raw="$(_norm_lang "${OPENWRT_LANG:-}")"
  case "$raw" in
    zh*|cn|chinese) echo zh; return ;;
    en|en_*|english) echo en; return ;;
  esac
  for raw in \
    "$(_norm_lang "${LC_ALL:-}")" \
    "$(_norm_lang "${LC_MESSAGES:-}")" \
    "$(_norm_lang "${LANG:-}")"
  do
    case "$raw" in
      zh*|cn|chinese) echo zh; return ;;
      en|en_*) echo en; return ;;
    esac
  done
  echo en
}

LANG_CODE="$(detect_lang)"

_() {
  if [ "$LANG_CODE" = zh ]; then
    case "$1" in
      sub) echo "远程管理 OpenWrt · CLI / TUI · SSH 与 HTTP" ;;
      need_py) echo "需要 Python 3.12+" ;;
      hint_py) echo "Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv" ;;
      hint_mac) echo "macOS: brew install python@3.12" ;;
      found_py) echo "Python: $2" ;;
      need_pip) echo "未找到 pip。请安装：python3 -m ensurepip --upgrade" ;;
      old_py) echo "当前 $2，请升级到 Python 3.12+" ;;
      local) echo "从本仓库安装（可编辑）…" ;;
      remote) echo "从 GitHub 安装…" ;;
      done) echo "安装完成" ;;
      path) echo "openwrt 不在 PATH。请把下面目录加入 PATH 后重开终端：" ;;
      next) echo "接下来" ;;
      setup_q) echo "现在运行 openwrt setup？（连接向导）[Y/n] " ;;
      skip_setup) echo "稍后运行: openwrt setup" ;;
      skill_q) echo "现在安装 Agent skill（openwrt-ops）？[Y/n] " ;;
      skip_skill) echo "稍后运行: openwrt skill install" ;;
      no_bin) echo "已安装，但找不到 openwrt 命令。请重开终端后再试。" ;;
      fail) echo "安装失败。可改用: pipx install '$PIP_GIT'" ;;
      *) echo "$1" ;;
    esac
  else
    case "$1" in
      sub) echo "Remote OpenWrt admin · CLI / TUI · SSH and HTTP" ;;
      need_py) echo "Python 3.12+ is required" ;;
      hint_py) echo "Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv" ;;
      hint_mac) echo "macOS: brew install python@3.12" ;;
      found_py) echo "Python: $2" ;;
      need_pip) echo "pip not found. Try: python3 -m ensurepip --upgrade" ;;
      old_py) echo "Found $2 — please upgrade to Python 3.12+" ;;
      local) echo "Installing from this repo (editable)…" ;;
      remote) echo "Installing from GitHub…" ;;
      done) echo "Install complete" ;;
      path) echo "openwrt is not on PATH. Add this directory, then open a new terminal:" ;;
      next) echo "Next" ;;
      setup_q) echo "Run openwrt setup now (connection wizard)? [Y/n] " ;;
      skip_setup) echo "Later: openwrt setup" ;;
      skill_q) echo "Install the Agent skill (openwrt-ops) now? [Y/n] " ;;
      skip_skill) echo "Later: openwrt skill install" ;;
      no_bin) echo "Installed, but the openwrt command was not found. Open a new terminal and retry." ;;
      fail) echo "Install failed. Try: pipx install '$PIP_GIT'" ;;
      *) echo "$1" ;;
    esac
  fi
}

banner() {
  printf "\n"
  printf "${C_ACCENT}  ___              __      _____ _____${C_RESET}\n"
  printf "${C_ACCENT} / _ \\ _ __  ___ _ \\ \\    / / _ \\_   _|${C_RESET}\n"
  printf "${C_ACCENT}| (_) | '_ \\/ -_) ' \\ \\/\\/ /|   / | |${C_RESET}\n"
  printf "${C_ACCENT} \\___/| .__/\\___|_||_\\_/\\_/ |_|_\\ |_|${C_RESET}\n"
  printf "${C_MUTED}        %s${C_RESET}\n\n" "$(_ sub)"
}

detect_python() {
  local cand
  for cand in python3.13 python3.12 python3 python; do
    if command -v "$cand" >/dev/null 2>&1 \
      && "$cand" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}

repo_root() {
  local src="${BASH_SOURCE[0]:-}"
  if [ -n "$src" ] && [ -f "$src" ] && [ -r "$src" ]; then
    local dir
    dir="$(cd "$(dirname "$src")" && pwd)"
    if [ -f "${dir}/pyproject.toml" ]; then
      printf '%s' "$dir"
      return 0
    fi
  fi
  return 1
}

user_bin() {
  "$PYTHON" -c "import os, site; print(os.path.join(site.USER_BASE, 'bin'))" 2>/dev/null || true
}

resolve_openwrt() {
  if command -v openwrt >/dev/null 2>&1; then
    command -v openwrt
    return 0
  fi
  local bin
  bin="$(user_bin)"
  if [ -n "$bin" ] && [ -x "${bin}/openwrt" ]; then
    printf '%s' "${bin}/openwrt"
    return 0
  fi
  return 1
}

pip_install() {
  local extra=()
  if [ -z "${VIRTUAL_ENV:-}" ] && [ -z "${CONDA_PREFIX:-}" ]; then
    extra+=(--user)
  fi
  if "$PYTHON" -m pip install -q "${extra[@]}" "$@"; then
    return 0
  fi
  if command -v pipx >/dev/null 2>&1; then
    if [ "${1:-}" = "-e" ]; then
      local target="${2%%\[*}"
      pipx install -e "$target" || return 1
      pipx inject openwrt-cli mcp >/dev/null 2>&1 || true
    else
      pipx install "$1" || return 1
      pipx inject openwrt-cli mcp >/dev/null 2>&1 || true
    fi
    return 0
  fi
  return 1
}

has_tty() {
  [ -e /dev/tty ] && [ -r /dev/tty ]
}

print_next() {
  printf "\n${C_MUTED}── %s ──${C_RESET}\n\n" "$(_ next)"
  printf "  ${C_ACCENT}openwrt setup${C_RESET}                 ${C_MUTED}# language + connection wizard${C_RESET}\n"
  printf "  ${C_ACCENT}openwrt skill install${C_RESET}         ${C_MUTED}# openwrt-ops for detected Agents${C_RESET}\n"
  printf "  ${C_ACCENT}openwrt mcp json${C_RESET}              ${C_MUTED}# paste-ready mcpServers.openwrt${C_RESET}\n"
  printf "  ${C_ACCENT}openwrt doctor${C_RESET}                ${C_MUTED}# SSH / HTTP health${C_RESET}\n"
  printf "  ${C_ACCENT}openwrt tui${C_RESET}                   ${C_MUTED}# dashboard${C_RESET}\n"
  printf "\n"
}

main() {
  banner

  if ! PYTHON="$(detect_python)"; then
    error "$(_ need_py)"
    if command -v python3 >/dev/null 2>&1; then
      error "$(_ old_py "$(python3 --version 2>&1)")"
    fi
    printf "  %s\n  %s\n" "$(_ hint_py)" "$(_ hint_mac)"
    exit 1
  fi

  local ver
  ver="$("$PYTHON" --version 2>&1)"
  info "$(_ found_py "$ver")"

  if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    error "$(_ need_pip)"
    exit 1
  fi

  local root=""
  root="$(repo_root || true)"
  if [ -n "$root" ]; then
    info "$(_ local)"
    if ! pip_install -e "${root}[mcp]"; then
      error "$(_ fail)"
      exit 1
    fi
  else
    info "$(_ remote)"
    if ! pip_install "openwrt-cli[mcp] @ ${PIP_GIT}"; then
      error "$(_ fail)"
      exit 1
    fi
  fi
  ok "$(_ done)"

  local bin=""
  if bin="$(resolve_openwrt)"; then
    if [ "$(command -v openwrt 2>/dev/null || true)" != "$bin" ]; then
      warn "$(_ path)"
      printf "  ${C_ACCENT}%s${C_RESET}\n" "$(dirname "$bin")"
    fi
  else
    warn "$(_ no_bin)"
    local ubin
    ubin="$(user_bin)"
    [ -n "$ubin" ] && printf "  ${C_ACCENT}%s${C_RESET}\n" "$ubin"
  fi

  print_next

  if has_tty && [ -n "${bin:-}" ]; then
    local ans=""
    printf "${C_ACCENT}%s${C_RESET}" "$(_ setup_q)"
    IFS= read -r ans < /dev/tty || true
    case "$ans" in
      ""|y|Y|yes|YES) "$bin" setup ;;
      *) info "$(_ skip_setup)" ;;
    esac
    printf "${C_ACCENT}%s${C_RESET}" "$(_ skill_q)"
    IFS= read -r ans < /dev/tty || true
    case "$ans" in
      ""|y|Y|yes|YES) "$bin" skill install ;;
      *) info "$(_ skip_skill)" ;;
    esac
  fi
}

main "$@"
