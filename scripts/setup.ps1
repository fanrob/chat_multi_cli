<#
.SYNOPSIS
    Подготовка окружения разработки: виртуальная среда + зависимости.

.EXAMPLE
    .\scripts\setup.ps1
    .\scripts\setup.ps1 -Dev
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"

Write-Host "==> Создание .venv (Python $PythonVersion)" -ForegroundColor Cyan
if (Test-Path $venv) {
    Write-Host "    .venv уже существует, пропускаем" -ForegroundColor DarkGray
} else {
    & py "-$PythonVersion" -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось создать venv. Проверьте, что установлен Python $PythonVersion."
    }
}

$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "python.exe не найден в $venv"
}

Write-Host "==> Обновление pip" -ForegroundColor Cyan
& $python -m pip install --upgrade pip

$req = if ($Dev) { "requirements-dev.txt" } else { "requirements.txt" }
Write-Host "==> Установка зависимостей ($req)" -ForegroundColor Cyan
& $python -m pip install -r (Join-Path $root $req)

Write-Host "==> Установка пакета в режиме редактирования" -ForegroundColor Cyan
& $python -m pip install -e $root

$exe = Join-Path $venv "Scripts\chat-multi-cli.exe"
Write-Host ""
Write-Host "Готово." -ForegroundColor Green
Write-Host "  Активация:  $venv\Scripts\Activate.ps1"
Write-Host "  Запуск:     .\.venv\Scripts\chat-multi-cli.exe"
if (Test-Path $exe) { Write-Host "  Проверка:   $exe" -ForegroundColor DarkGray }
Write-Host "  Тесты:      .\.venv\Scripts\python.exe -m pytest"
Write-Host "  Линт:       .\.venv\Scripts\python.exe -m ruff check ."