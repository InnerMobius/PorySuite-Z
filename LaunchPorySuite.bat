@echo off
REM ── Self-hide: if not already running hidden, delegate to the VBS launcher ─
REM    PorySuite.vbs runs this bat with window-style 0 (invisible) and passes
REM    _hidden_ so we skip this block on the second pass.
REM    EXCEPTION: first-time setup (no venv yet) stays visible so the user
REM    can see pip installing packages instead of staring at nothing.
if /i not "%~1"=="_hidden_" (
    if exist "%~dp0cleanenv\Scripts\python.exe" (
        wscript //nologo "%~dp0PorySuite.vbs"
        exit /b 0
    )
    if exist "%~dp0.venv\Scripts\python.exe" (
        wscript //nologo "%~dp0PorySuite.vbs"
        exit /b 0
    )
    REM No venv yet — stay visible for first-time setup feedback
)
REM ──────────────────────────────────────────────────────────────────────────
setlocal enableextensions
REM Ensure working directory is the script directory
pushd "%~dp0"

REM Log file for crash diagnosis (overwrites each launch)
set "LAUNCH_LOG=%~dp0launch.log"
echo PorySuite-Z launch started %DATE% %TIME% > "%LAUNCH_LOG%"

REM ============================================================
REM  1. Locate a Python interpreter
REM ============================================================

set "PYTHON_EXE="

REM Prefer cleanenv (pre-built bundled venv)
if exist "%~dp0cleanenv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0cleanenv\Scripts\python.exe"
    set "PYQT6_BIN=%~dp0cleanenv\Lib\site-packages\PyQt6\Qt6\bin"
    echo Using cleanenv python >> "%LAUNCH_LOG%"
    goto :qt_env
)

REM Use the auto-created local venv if it already exists
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
    set "PYQT6_BIN=%~dp0.venv\Lib\site-packages\PyQt6\Qt6\bin"
    echo Using .venv python >> "%LAUNCH_LOG%"
    goto :qt_env
)

REM No ready-to-go venv -- find system Python to bootstrap one
where python >nul 2>&1
if %ERRORLEVEL%==0 (
    set "BOOTSTRAP_PYTHON=python"
    for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo Bootstrap Python: %%i >> "%LAUNCH_LOG%"
    echo Found system python for bootstrapping >> "%LAUNCH_LOG%"
    goto :create_venv
)

REM Nothing at all
echo ERROR: Python not found >> "%LAUNCH_LOG%"
echo.
echo ============================================================
echo  Python is not installed or not in your PATH.
echo  Download it from https://www.python.org/downloads/
echo  Make sure to tick "Add Python to PATH" during install.
echo ============================================================
echo.
pause
goto :done_error

REM ============================================================
REM  2. Create isolated venv and install packages
REM     (only runs once; subsequent launches jump straight to :qt_env)
REM ============================================================
:create_venv
echo Creating isolated Python environment...
echo Creating .venv >> "%LAUNCH_LOG%"

"%BOOTSTRAP_PYTHON%" -m venv "%~dp0.venv"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: venv creation failed >> "%LAUNCH_LOG%"
    echo.
    echo ============================================================
    echo  Could not create a virtual environment.
    echo  Make sure your Python installation is not corrupted.
    echo ============================================================
    echo.
    pause
    goto :done_error
)

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "PYQT6_BIN=%~dp0.venv\Lib\site-packages\PyQt6\Qt6\bin"

echo.
echo ============================================================
echo  First-time setup: installing required packages.
echo  This only happens once and may take a minute or two.
echo ============================================================
echo.

"%PYTHON_EXE%" -m pip install --upgrade pip >nul 2>&1
"%PYTHON_EXE%" -m pip install -r "%~dp0requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: pip install failed >> "%LAUNCH_LOG%"
    echo.
    echo ============================================================
    echo  Could not install dependencies.
    echo  Check your internet connection and try again.
    echo ============================================================
    echo.
    REM Clean up broken venv so next launch tries again
    rmdir /s /q "%~dp0.venv" >nul 2>&1
    pause
    goto :done_error
)

echo Packages installed successfully >> "%LAUNCH_LOG%"

REM ============================================================
REM  3. Configure Qt environment
REM ============================================================
:qt_env
set "QT_QPA_PLATFORM=windows"
REM Uncomment for detailed Qt plugin diagnostics:
REM set "QT_DEBUG_PLUGINS=1"

REM Add Qt6\bin to PATH so the bundled MSVC runtime DLLs are found
REM before any older system-wide copies. This prevents DLL version mismatches.
if exist "%PYQT6_BIN%" (
    set "PATH=%PYQT6_BIN%;%PATH%"
    echo Added Qt6\bin to PATH: %PYQT6_BIN% >> "%LAUNCH_LOG%"
) else (
    echo WARNING: Qt6\bin not found at %PYQT6_BIN% >> "%LAUNCH_LOG%"
)

REM Point Qt at the bundled platform plugins
set "_QT_PLUGINS="
for %%V in (cleanenv .venv) do (
    if not defined _QT_PLUGINS (
        if exist "%~dp0%%V\Lib\site-packages\PyQt6\Qt6\plugins\platforms\qwindows.dll" (
            set "_QT_PLUGINS=%~dp0%%V\Lib\site-packages\PyQt6\Qt6\plugins"
        )
    )
)
if defined _QT_PLUGINS (
    set "QT_QPA_PLATFORM_PLUGIN_PATH=%_QT_PLUGINS%\platforms"
    set "QT_PLUGIN_PATH=%_QT_PLUGINS%"
    echo Qt plugins: %_QT_PLUGINS% >> "%LAUNCH_LOG%"
) else (
    echo Qt plugins: using PyQt6 defaults >> "%LAUNCH_LOG%"
)

REM Font directory
set "QT_FONT_DIR="
for %%V in (cleanenv .venv) do (
    if not defined QT_FONT_DIR (
        if exist "%~dp0%%V\Lib\site-packages\PyQt6\Qt6\lib\fonts" (
            set "QT_FONT_DIR=%~dp0%%V\Lib\site-packages\PyQt6\Qt6\lib\fonts"
        )
    )
)
if not defined QT_FONT_DIR (
    if exist "%WINDIR%\Fonts" set "QT_FONT_DIR=%WINDIR%\Fonts"
)
if defined QT_FONT_DIR (
    set "QT_QPA_FONTDIR=%QT_FONT_DIR%"
    echo Qt fonts: %QT_FONT_DIR% >> "%LAUNCH_LOG%"
) else (
    echo WARNING: Qt font directory not found >> "%LAUNCH_LOG%"
)

REM ============================================================
REM  4. Verify Qt can actually load (catches DLL issues early)
REM ============================================================
:verify_qt

REM Log Python and Windows version for diagnostics
for /f "tokens=*" %%i in ('"%PYTHON_EXE%" --version 2^>^&1') do echo Python: %%i >> "%LAUNCH_LOG%"
for /f "tokens=*" %%i in ('ver') do echo Windows: %%i >> "%LAUNCH_LOG%"

echo Verifying Qt... >> "%LAUNCH_LOG%"
"%PYTHON_EXE%" -c "from PyQt6.QtCore import Qt" 2>>"%LAUNCH_LOG%"
if %ERRORLEVEL%==0 (
    echo Qt verified OK >> "%LAUNCH_LOG%"
    goto :launch
)

REM Qt DLL failed -- capture more detail and try VC++ runtime install
echo Qt DLL check failed >> "%LAUNCH_LOG%"

REM Check if this is a Windows version issue (Qt6 requires Windows 10 1809+)
for /f "tokens=4-5 delims=. " %%a in ('ver') do (
    set "WIN_MAJOR=%%a"
    set "WIN_BUILD=%%b"
)
if defined WIN_MAJOR (
    if %WIN_MAJOR% LSS 10 (
        echo ERROR: Windows version too old for PyQt6 >> "%LAUNCH_LOG%"
        echo.
        echo ============================================================
        echo  PorySuite-Z requires Windows 10 or later.
        echo  Qt 6 does not support this version of Windows.
        echo ============================================================
        echo.
        pause
        goto :done_error
    )
)

REM Try installing / updating the VC++ runtime
echo.
echo ============================================================
echo  Qt runtime check failed. Installing Visual C++ runtime...
echo  (This is a one-time fix.)
echo ============================================================
echo.

where winget >nul 2>&1
if %ERRORLEVEL%==0 (
    winget install --id Microsoft.VCRedist.2015+.x64 -e --silent --accept-package-agreements --accept-source-agreements
    if %ERRORLEVEL% NEQ 0 (
        echo winget VC++ install FAILED -- directing to manual download >> "%LAUNCH_LOG%"
        goto :vcredist_manual
    )
    echo winget VC++ install done >> "%LAUNCH_LOG%"
) else (
    echo winget not available -- directing to manual download >> "%LAUNCH_LOG%"
    goto :vcredist_manual
)
goto :vcredist_done

:vcredist_manual
echo.
echo ============================================================
echo  Could not install Visual C++ runtime automatically.
echo  Please download and install it manually:
echo  https://aka.ms/vs/17/release/vc_redist.x64.exe
echo.
echo  After installing, RESTART YOUR PC and run this launcher again.
echo ============================================================
echo.
pause
goto :done_error

:vcredist_done

REM VC++ installs often require a reboot to take effect.
REM Do NOT try to relaunch immediately -- the old DLL is still resident.
echo.
echo ============================================================
echo  Visual C++ runtime has been installed.
echo.
echo  PLEASE RESTART YOUR PC, then run this launcher again.
echo  (Windows needs a reboot for the new runtime to take effect.)
echo ============================================================
echo.
pause
goto :done_error

REM ============================================================
REM  5. Launch the app
REM ============================================================
:launch
echo Launching app.py >> "%LAUNCH_LOG%"
set "PYTHONW=%PYTHON_EXE:python.exe=pythonw.exe%"
if not exist "%PYTHONW%" set "PYTHONW=%PYTHON_EXE%"
start "" /b "%PYTHONW%" "%~dp0app.py" %*
popd
endlocal
exit /b 0

:done_error
popd
endlocal
exit /b 1
