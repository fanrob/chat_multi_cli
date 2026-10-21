@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
title Установка chat_multi_cli

set "PYVER=3.13.16"
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do set "PYDIR=Python%%a%%b"

echo ================================================================
echo      Установка chat_multi_cli - клиент мастера
echo ================================================================
echo.
echo  Нужен доступ в интернет: если Python не установлен, он будет
echo  скачан с python.org и поставлен без диалогов.
echo  Права администратора не требуются - всё ставится в профиль
echo  текущего пользователя Windows.
echo.

if not exist "%~dp0pyproject.toml" (
    set "ERR=Рядом с install.bat нет pyproject.toml. Положите скрипт в корень папки проекта."
    goto :error
)

fltmc >nul 2>&1
if "%ERRORLEVEL%"=="0" (
    echo  [!] Скрипт запущен от имени администратора - обычно это лишнее.
    echo      Установка пойдёт в профиль того пользователя, под которым
    echo      запущен Windows. Если это не тот человек - перезапустите
    echo      скрипт от его имени, обычным двойным щелчком.
    echo.
)

echo  Ответ по умолчанию показан в скобках, просто Enter - принять его.
echo.

set "DEFAULT_DIR=%LOCALAPPDATA%\Programs\chat_multi_cli"
set "INSTALL_DIR=%DEFAULT_DIR%"
set /p "INSTALL_DIR=Куда установить [%DEFAULT_DIR%]: "

set "ANSWER=Y"
set /p "ANSWER=Создать ярлык на рабочем столе? [Y/n]: "
set "DO_DESKTOP=1"
if /i "%ANSWER%"=="n" set "DO_DESKTOP=0"
if /i "%ANSWER%"=="no" set "DO_DESKTOP=0"
if "%ANSWER%"=="н" set "DO_DESKTOP=0"
if "%ANSWER%"=="Н" set "DO_DESKTOP=0"
if "%ANSWER%"=="нет" set "DO_DESKTOP=0"
if "%ANSWER%"=="Нет" set "DO_DESKTOP=0"
if "%ANSWER%"=="НЕТ" set "DO_DESKTOP=0"

set "ANSWER=Y"
set /p "ANSWER=Запускать программу при входе в Windows? [Y/n]: "
set "DO_AUTOSTART=1"
if /i "%ANSWER%"=="n" set "DO_AUTOSTART=0"
if /i "%ANSWER%"=="no" set "DO_AUTOSTART=0"
if "%ANSWER%"=="н" set "DO_AUTOSTART=0"
if "%ANSWER%"=="Н" set "DO_AUTOSTART=0"
if "%ANSWER%"=="нет" set "DO_AUTOSTART=0"
if "%ANSWER%"=="Нет" set "DO_AUTOSTART=0"
if "%ANSWER%"=="НЕТ" set "DO_AUTOSTART=0"

set "ANSWER=Y"
set /p "ANSWER=Запустить программу сразу после установки? [Y/n]: "
set "DO_RUN=1"
if /i "%ANSWER%"=="n" set "DO_RUN=0"
if /i "%ANSWER%"=="no" set "DO_RUN=0"
if "%ANSWER%"=="н" set "DO_RUN=0"
if "%ANSWER%"=="Н" set "DO_RUN=0"
if "%ANSWER%"=="нет" set "DO_RUN=0"
if "%ANSWER%"=="Нет" set "DO_RUN=0"
if "%ANSWER%"=="НЕТ" set "DO_RUN=0"

set "INSTALL_DIR=%INSTALL_DIR:"=%"
for %%I in ("%INSTALL_DIR%") do set "INSTALL_DIR=%%~fI"
if "%INSTALL_DIR:~-1%"=="\" if not "%INSTALL_DIR:~-2%"==":\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"
if not defined INSTALL_DIR set "INSTALL_DIR=%DEFAULT_DIR%"

echo.
echo  Каталог установки: "%INSTALL_DIR%"
echo.

echo [1/4] Поиск Python 3.11 или новее...

set "PY="
set "CAND="
for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do set "CAND=%%P"
if defined CAND call :try_python "%CAND%"

set "CAND="
for /f "usebackq delims=" %%P in (`python -c "import sys;print(sys.executable)" 2^>nul`) do set "CAND=%%P"
if defined CAND call :try_python "%CAND%"

for %%V in (314 313 312 311) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" call :try_python "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    if exist "%ProgramFiles%\Python%%V\python.exe" call :try_python "%ProgramFiles%\Python%%V\python.exe"
    if exist "%ProgramFiles(x86)%\Python%%V\python.exe" call :try_python "%ProgramFiles(x86)%\Python%%V\python.exe"
)

if defined PY (
    echo       Найден установленный Python:
    "%PY%" --version
)

if defined PY goto :python_ready

echo       Подходящий Python не найден.
set "ARCH=amd64"
set "NATIVE=%PROCESSOR_ARCHITECTURE%"
if defined PROCESSOR_ARCHITEW6432 set "NATIVE=%PROCESSOR_ARCHITEW6432%"
if /i "%NATIVE%"=="ARM64" set "ARCH=arm64"
if /i "%NATIVE%"=="x86" set "ARCH=x86"
set "SETUP_NAME=python-%PYVER%-%ARCH%.exe"
if /i "%ARCH%"=="x86" set "SETUP_NAME=python-%PYVER%.exe"
set "SETUP=%TEMP%\%SETUP_NAME%"

echo       Скачиваю %SETUP_NAME% с python.org, подождите...
call :download "https://www.python.org/ftp/python/%PYVER%/%SETUP_NAME%" "%SETUP%"
if not "%ERRORLEVEL%"=="0" (
    set "ERR=Не удалось скачать Python с python.org. Проверьте доступ в интернет и запустите установку заново."
    goto :error
)

echo       Устанавливаю Python %PYVER% без диалогов, для текущего пользователя...
"%SETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0 AssociateFiles=0 Shortcuts=0 TargetDir="%LOCALAPPDATA%\Programs\Python\%PYDIR%"
if not "%ERRORLEVEL%"=="0" if not "%ERRORLEVEL%"=="3010" (
    set "ERR=Установщик Python завершился с ошибкой. Установите Python вручную с python.org и запустите install.bat заново."
    goto :error
)
del /q "%SETUP%" 2>nul

set "PY=%LOCALAPPDATA%\Programs\Python\%PYDIR%\python.exe"
if not exist "%PY%" (
    set "ERR=Python установился, но файл %PY% не найден. Установите Python вручную с python.org и запустите install.bat заново."
    goto :error
)
call :python_ok
if not "%ERRORLEVEL%"=="0" (
    set "ERR=Python %PYVER% установлен, но не запускается. Проверьте систему вручную и запустите установку заново."
    goto :error
)

:python_ready
echo       Python готов:
"%PY%" --version

echo.
echo [2/4] Виртуальное окружение...

mkdir "%INSTALL_DIR%" 2>nul
if not exist "%INSTALL_DIR%\" (
    set "ERR=Не удалось создать каталог %INSTALL_DIR%. Проверьте права на запись или укажите другой путь."
    goto :error
)

set "VPY=%INSTALL_DIR%\venv\Scripts\python.exe"
if exist "%VPY%" (
    echo       Окружение уже есть - переиспользую
) else (
    "%PY%" -m venv "%INSTALL_DIR%\venv"
)
if not exist "%VPY%" (
    set "ERR=Не удалось создать виртуальное окружение в %INSTALL_DIR%\venv."
    goto :error
)

echo.
echo [3/4] Установка chat_multi_cli и зависимостей...
echo       Скачаются PySide6 и httpx, это займёт несколько минут.
echo.

"%VPY%" -m pip install --upgrade pip --no-input --disable-pip-version-check
if not "%ERRORLEVEL%"=="0" (
    set "ERR=Не удалось обновить pip. Проверьте доступ в интернет и запустите установку заново."
    goto :error
)

"%VPY%" -m pip install --upgrade --no-input --disable-pip-version-check "%~dp0."
if not "%ERRORLEVEL%"=="0" (
    set "ERR=Не удалось установить приложение. Посмотрите сообщения pip выше и запустите установку заново."
    goto :error
)

set "APP_EXE=%INSTALL_DIR%\venv\Scripts\chat-multi-cli.exe"
if not exist "%APP_EXE%" (
    set "ERR=Установка завершилась, но chat-multi-cli.exe не найден в %INSTALL_DIR%\venv\Scripts."
    goto :error
)

echo.
echo [4/4] Ярлыки и автозагрузка...

set "LNK_TARGET=%APP_EXE%"
set "LNK_WORKDIR=%INSTALL_DIR%"
set "LNK_NAME=Chat Multi CLI"

if "%DO_DESKTOP%"=="0" (
    echo       Ярлык на рабочем столе пропущен
    goto :desktop_done
)
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; $p=[Environment]::GetFolderPath('Desktop'); $s=$w.CreateShortcut((Join-Path $p ($env:LNK_NAME+'.lnk'))); $s.TargetPath=$env:LNK_TARGET; $s.WorkingDirectory=$env:LNK_WORKDIR; $s.IconLocation=$env:LNK_TARGET+',0'; $s.Description='chat_multi_cli'; $s.Save()"
if not "%ERRORLEVEL%"=="0" (
    echo       [!] Не удалось создать ярлык на рабочем столе - продолжаем
    goto :desktop_done
)
echo       Ярлык на рабочем столе готов
:desktop_done

if "%DO_AUTOSTART%"=="0" (
    echo       Автозагрузка пропущена
    goto :autostart_done
)
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; $p=[Environment]::GetFolderPath('Startup'); $s=$w.CreateShortcut((Join-Path $p ($env:LNK_NAME+'.lnk'))); $s.TargetPath=$env:LNK_TARGET; $s.WorkingDirectory=$env:LNK_WORKDIR; $s.IconLocation=$env:LNK_TARGET+',0'; $s.Description='chat_multi_cli'; $s.Save()"
if not "%ERRORLEVEL%"=="0" (
    echo       [!] Не удалось добавить программу в автозагрузку - продолжаем
    goto :autostart_done
)
echo       Автозагрузка готова
:autostart_done

echo.
echo ================================================================
echo  Установка завершена.
echo.
echo    Программа : "%APP_EXE%"
echo    Профиль   : "%USERPROFILE%\.chat_multi_cli"
echo                config.json, база chat.db, логи в logs\app.log
echo.
echo  При первом запуске программа покажет instance_id - его нужно
echo  добавить в белый список на сервере, затем указать ФИО и цех.
echo ================================================================
echo.

if "%DO_RUN%"=="0" goto :finished
echo       Запускаю chat_multi_cli...
start "" /d "%INSTALL_DIR%" "%APP_EXE%"

:finished
echo  Нажмите любую клавишу, чтобы закрыть окно...
pause >nul
exit /b 0

:error
echo.
echo ================================================================
echo  ОШИБКА: "%ERR%"
echo ================================================================
echo.
echo  Нажмите любую клавишу, чтобы закрыть окно...
pause >nul
exit /b 1

:try_python
if defined PY exit /b 0
set "PY=%~1"
call :python_ok
if not "%ERRORLEVEL%"=="0" set "PY="
exit /b 0

:python_ok
if not defined PY exit /b 1
"%PY%" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul && exit /b 0
exit /b 1

:download
where curl.exe >nul 2>nul
if not "%ERRORLEVEL%"=="0" goto :download_ps
curl.exe -fL --progress-bar --retry 2 -o "%~2" "%~1"
if "%ERRORLEVEL%"=="0" exit /b 0
:download_ps
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2'"
exit /b %ERRORLEVEL%
