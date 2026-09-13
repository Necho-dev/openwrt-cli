@echo off
:: OpenWrt CLI — Windows installer (CLI/TUI palette)
::   Double-click, or:  cmd /c install.bat
::   From a clone → pip install -e ".[mcp]"
::   Standalone     → pip install "openwrt-cli[mcp] @ git+https://github.com/Necho-dev/openwrt-cli.git"

setlocal EnableDelayedExpansion

set "REPO=Necho-dev/openwrt-cli"
set "PIP_GIT=git+https://github.com/Necho-dev/openwrt-cli.git"

for /f %%A in ('echo prompt $E^| cmd') do set "ESC=%%A"
set "C_ACCENT=%ESC%[1;36m"
set "C_OK=%ESC%[32m"
set "C_WARN=%ESC%[33m"
set "C_ERR=%ESC%[1;31m"
set "C_MUTED=%ESC%[2m"
set "C_RESET=%ESC%[0m"

set "LANG_CODE=en"
if defined OPENWRT_LANG (
  echo !OPENWRT_LANG! | findstr /i "zh cn chinese" >nul && set "LANG_CODE=zh"
) else if defined LANG (
  echo !LANG! | findstr /i "zh" >nul && set "LANG_CODE=zh"
)

if "!LANG_CODE!"=="zh" (
  set "MSG_SUB=远程管理 OpenWrt · CLI / TUI · SSH 与 HTTP"
  set "MSG_NEED_PY=需要 Python 3.12+"
  set "MSG_HINT_PY=下载: https://www.python.org/downloads/    或: winget install Python.Python.3.12"
  set "MSG_OLD_PY=请升级到 Python 3.12+"
  set "MSG_NEED_PIP=未找到 pip。请重装 Python 并勾选 pip。"
  set "MSG_LOCAL=从本仓库安装（可编辑）…"
  set "MSG_REMOTE=从 GitHub 安装…"
  set "MSG_DONE=安装完成"
  set "MSG_FAIL=安装失败。请确认已安装 Git，或先 clone 本仓库再运行 install.bat"
  set "MSG_NO_BIN=已安装，但找不到 openwrt。请重开终端后再试。"
  set "MSG_NEXT=接下来"
  set "MSG_SETUP_Q=现在运行 openwrt setup？（连接向导）[Y/n] "
  set "MSG_SKIP=稍后运行: openwrt setup"
  set "MSG_SKILL_Q=现在安装 Agent skill（openwrt-ops）？[Y/n] "
  set "MSG_SKIP_SKILL=稍后运行: openwrt skill install"
) else (
  set "MSG_SUB=Remote OpenWrt admin · CLI / TUI · SSH and HTTP"
  set "MSG_NEED_PY=Python 3.12+ is required"
  set "MSG_HINT_PY=Download: https://www.python.org/downloads/    or: winget install Python.Python.3.12"
  set "MSG_OLD_PY=Please upgrade to Python 3.12+"
  set "MSG_NEED_PIP=pip not found. Reinstall Python and enable pip."
  set "MSG_LOCAL=Installing from this repo (editable)…"
  set "MSG_REMOTE=Installing from GitHub…"
  set "MSG_DONE=Install complete"
  set "MSG_FAIL=Install failed. Install Git, or clone the repo and run install.bat again."
  set "MSG_NO_BIN=Installed, but the openwrt command was not found. Open a new terminal and retry."
  set "MSG_NEXT=Next"
  set "MSG_SETUP_Q=Run openwrt setup now (connection wizard)? [Y/n] "
  set "MSG_SKIP=Later: openwrt setup"
  set "MSG_SKILL_Q=Install the Agent skill (openwrt-ops) now? [Y/n] "
  set "MSG_SKIP_SKILL=Later: openwrt skill install"
)

:: ---- Python (prefer 3.12+) ----
set "PYTHON="
py -3.13 --version >nul 2>&1 && set "PYTHON=py -3.13"
if not defined PYTHON py -3.12 --version >nul 2>&1 && set "PYTHON=py -3.12"
if not defined PYTHON (
  python --version >nul 2>&1 && set "PYTHON=python"
)
if not defined PYTHON (
  py --version >nul 2>&1 && set "PYTHON=py"
)
if not defined PYTHON (
  echo.
  echo  %C_ERR%[x]%C_RESET%  !MSG_NEED_PY!
  echo     !MSG_HINT_PY!
  echo.
  pause
  exit /b 1
)

echo.
%PYTHON% -c "print('\033[1;36m  ___              __      _____ _____\n / _ \\ _ __  ___ _ \\ \\    / / _ \\_   _|\n| (_) | '_ \\/ -_) ' \\ \\/\\/ /|   / | |\n \\___/| .__/\\___|_||_\\_/\\_/ |_|_\\ |_|')"
echo  %C_MUTED%        !MSG_SUB!%C_RESET%
echo.

for /f "delims=" %%V in ('%PYTHON% --version 2^>^&1') do set "PYVER=%%V"
%PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
  echo  %C_ERR%[x]%C_RESET%  !PYVER! — !MSG_OLD_PY!
  echo     !MSG_HINT_PY!
  echo.
  pause
  exit /b 1
)
echo  %C_ACCENT%[i]%C_RESET%  Python: !PYVER!

%PYTHON% -m pip --version >nul 2>&1
if errorlevel 1 (
  echo  %C_ERR%[x]%C_RESET%  !MSG_NEED_PIP!
  echo.
  pause
  exit /b 1
)

:: ---- Install ----
if exist "%~dp0pyproject.toml" (
  echo  %C_ACCENT%[i]%C_RESET%  !MSG_LOCAL!
  pushd "%~dp0"
  %PYTHON% -m pip install -e ".[mcp]" -q
  popd
) else (
  echo  %C_ACCENT%[i]%C_RESET%  !MSG_REMOTE!
  %PYTHON% -m pip install "openwrt-cli[mcp] @ %PIP_GIT%" -q
)
if errorlevel 1 (
  echo  %C_ERR%[x]%C_RESET%  !MSG_FAIL!
  echo.
  pause
  exit /b 1
)
echo  %C_OK%[ok]%C_RESET% !MSG_DONE!

where openwrt >nul 2>&1
if errorlevel 1 (
  echo  %C_WARN%[!]%C_RESET%  !MSG_NO_BIN!
)

echo.
echo  %C_MUTED%── !MSG_NEXT! ──%C_RESET%
echo.
echo    %C_ACCENT%openwrt setup%C_RESET%                 %C_MUTED%# language + connection wizard%C_RESET%
echo    %C_ACCENT%openwrt skill install%C_RESET%         %C_MUTED%# openwrt-ops for detected Agents%C_RESET%
echo    %C_ACCENT%openwrt mcp json%C_RESET%              %C_MUTED%# paste-ready mcpServers.openwrt%C_RESET%
echo    %C_ACCENT%openwrt doctor%C_RESET%                %C_MUTED%# SSH / HTTP health%C_RESET%
echo    %C_ACCENT%openwrt tui%C_RESET%                   %C_MUTED%# dashboard%C_RESET%
echo.

set "ANS=Y"
set /p ANS="!MSG_SETUP_Q!"
if "!ANS!"=="" set "ANS=Y"
if /i "!ANS!"=="y" (
  where openwrt >nul 2>&1 && openwrt setup || %PYTHON% -m openwrt_cli.app setup
) else if /i "!ANS!"=="yes" (
  where openwrt >nul 2>&1 && openwrt setup || %PYTHON% -m openwrt_cli.app setup
) else (
  echo  %C_ACCENT%[i]%C_RESET%  !MSG_SKIP!
)

set "SKILL_ANS=Y"
set /p SKILL_ANS="!MSG_SKILL_Q!"
if "!SKILL_ANS!"=="" set "SKILL_ANS=Y"
if /i "!SKILL_ANS!"=="y" (
  where openwrt >nul 2>&1 && openwrt skill install || %PYTHON% -m openwrt_cli.app skill install
) else if /i "!SKILL_ANS!"=="yes" (
  where openwrt >nul 2>&1 && openwrt skill install || %PYTHON% -m openwrt_cli.app skill install
) else (
  echo  %C_ACCENT%[i]%C_RESET%  !MSG_SKIP_SKILL!
)
echo.
pause
