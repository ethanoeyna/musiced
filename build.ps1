#!/usr/bin/env pwsh
$ErrorActionPreference = 'Stop'
uv run --group dev python -m PyInstaller --clean --noconfirm musiced.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
Write-Output "Built: dist/Musiced.exe"
