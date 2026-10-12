# Run from PowerShell: .\scripts\setup_windows.ps1 [-InstallTools]
param([switch]$InstallTools)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($InstallTools) {
    & winget install --exact --id Python.Python.3.12 --source winget
    & winget install --exact --id EclipseAdoptium.Temurin.21.JDK --source winget
    Write-Host 'PATH更新のため新しいPowerShellで、このスクリプトを -InstallTools なしで再実行してください。'
    exit 0
}
if (!(Get-Command python -ErrorAction SilentlyContinue) -or !(Get-Command javac -ErrorAction SilentlyContinue)) {
    throw 'Python 3.10+ とJDK 21が必要です。-InstallTools または公式インストーラーで導入してください。'
}
$JavaVersion = & javac -version 2>&1
if ("$JavaVersion" -notmatch '^javac 21\.') { throw 'この手順はJDK 21が必要です。JAVA_HOMEとPATHを確認してください。' }
if (!(Test-Path '.venv\Scripts\python.exe')) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'venv作成失敗' }
}
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw 'Python依存インストール失敗' }
$Tools = Join-Path (Get-Location) 'output\tools'
New-Item -ItemType Directory -Force $Tools | Out-Null
$Maven = Join-Path $Tools 'apache-maven-3.9.9\bin\mvn.cmd'
if (!(Test-Path $Maven)) {
    $Zip = Join-Path $Tools 'maven.zip'
    $Url = 'https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.9/apache-maven-3.9.9-bin.zip'
    Invoke-WebRequest $Url -OutFile $Zip
    $Expected = ((Invoke-WebRequest "$Url.sha512").Content.Trim() -split '\s+')[0]
    if ((Get-FileHash $Zip -Algorithm SHA512).Hash.ToLower() -ne $Expected.ToLower()) { throw 'Mavenチェックサム不一致' }
    Expand-Archive $Zip -DestinationPath $Tools -Force
}
& $Maven -f paper/pom.xml package
if ($LASTEXITCODE -ne 0) { throw 'Paperプラグインのビルド失敗' }
& .\.venv\Scripts\python.exe scripts/setup_paper.py
if ($LASTEXITCODE -ne 0) { throw 'セットアップ停止。既存サーバーは上書きしません。docs/PAPER.mdを確認してください。' }
