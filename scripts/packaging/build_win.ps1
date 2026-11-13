# XHSLeads Windows packaging script (run on Windows; PyInstaller cannot cross-compile)
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\packaging\build_win.ps1
# Optional:
#   -SkipDeps      skip pip install (deps already present)
#   -CftVersion    Chrome for Testing version (default matches repo mac engine)
#   -Format        dir | zip | installer | all      (default all)
#                    dir        only the onedir build (dev self-check)
#                    zip        onedir + Desktop\XHSLeads-win64.zip (portable)
#                    installer  onedir + Desktop\XHSLeads-Setup-x64.exe (Inno Setup, no admin)
#                    all        zip + installer
#
# Output: %LOCALAPPDATA%\xhs-leads-build\dist\XHSLeads\  (onedir)
#         Desktop\XHSLeads-win64.zip            (Format zip|all)
#         Desktop\XHSLeads-Setup-x64.exe        (Format installer|all)
# Log:    <repo>\build-win.log  (transcript; readable from the Mac side over the share)
#
# NOTE: installer requires Inno Setup on the build machine once:
#       winget install --id JRSoftware.InnoSetup -e

param(
    [switch]$SkipDeps,
    [string]$CftVersion = "158.0.8127.18",
    [ValidateSet("dir", "zip", "installer", "all")]
    [string]$Format = "all"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..")).ProviderPath
$LogPath   = Join-Path $RepoRoot "build-win.log"
$Work      = Join-Path $env:LOCALAPPDATA "xhs-leads-build"
$Venv      = Join-Path $Work ".venv"
$Desktop   = [Environment]::GetFolderPath("Desktop")

Start-Transcript -Path $LogPath -Force | Out-Null

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }

try {
    Write-Host "repo: $RepoRoot"
    Write-Host "work: $Work"

    Step "1/7 detect python"
    $pyExe = $null
    $cands = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { $cands += ,@($py.Source, @("-3.12")) }
    $pyt = Get-Command python -ErrorAction SilentlyContinue
    if ($pyt) { $cands += ,@($pyt.Source, @()) }
    $stdPy = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $stdPy) { $cands += ,@($stdPy, @()) }
    foreach ($c in $cands) {
        try {
            $ver = & $c[0] @($c[1]) -c "import sys;print('PYOK', sys.version.split()[0])" 2>$null
            if ($ver -match "PYOK") { $pyExe = $c; break }
        } catch {}
    }
    if (-not $pyExe) {
        $localInstaller = Join-Path $RepoRoot "build_resources\installer\python-3.12.6-amd64.exe"
        if (Test-Path $localInstaller) {
            Write-Host "using bundled installer: $localInstaller"
            $installer = $localInstaller
        } else {
            Write-Host "python not found, downloading official 3.12.6 installer"
            $installer = Join-Path $env:TEMP "python-3.12.6-amd64.exe"
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe" -OutFile $installer -UseBasicParsing
        }
        Start-Process -FilePath $installer -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0" -Wait
        $pyPath = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        if (-not (Test-Path $pyPath)) { throw "python install failed: $pyPath missing" }
        $pyExe = @($pyPath, @())
    }
    & $pyExe[0] @($pyExe[1]) -c "import sys;print('Python', sys.version)"
    if ($LASTEXITCODE -ne 0) { throw "python not usable" }

    Step "2/7 check frontend dist"
    if (-not (Test-Path (Join-Path $RepoRoot "frontend\dist\index.html"))) {
        throw "frontend\dist missing: run npm run build on the Mac first"
    }

    Step "3/7 venv and dependencies"
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
        & $pyExe[0] @($pyExe[1]) -m venv $Venv
    }
    $venvPy = Join-Path $Venv "Scripts\python.exe"
    if (-not $SkipDeps) {
        $wheelDir = Join-Path $RepoRoot "build_resources\wheels-win"
        $pkgs = @("fastapi","uvicorn","playwright","pydantic","httpx","cryptography","psutil","pyinstaller","pillow","python-multipart")
        if ((Test-Path $wheelDir) -and ((Get-ChildItem $wheelDir -Filter *.whl).Count -gt 0)) {
            # pip cannot use a UNC path directly: copy wheels to local disk first
            $localWheels = Join-Path $Work "wheels-win"
            if (Test-Path $localWheels) { Remove-Item $localWheels -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $localWheels | Out-Null
            Write-Host "staging wheels to local disk: $localWheels"
            Copy-Item -Path (Join-Path $wheelDir "*.whl") -Destination $localWheels -Force
            Write-Host "installing from local wheels (offline): $localWheels"
            & $venvPy -m pip install --no-index --find-links $localWheels @pkgs
            if ($LASTEXITCODE -ne 0) {
                Write-Host "offline wheels failed, falling back to online install"
                & $venvPy -m pip install --upgrade pip wheel --quiet
                & $venvPy -m pip install --quiet @pkgs
            }
        } else {
            & $venvPy -m pip install --upgrade pip wheel --quiet
            & $venvPy -m pip install --quiet @pkgs
            if ($LASTEXITCODE -ne 0) {
                Write-Host "default index failed, retry with tsinghua mirror"
                & $venvPy -m pip install --quiet -i https://pypi.tuna.tsinghua.edu.cn/simple @pkgs
            }
        }
        if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }
    }
    & $venvPy -c "import fastapi, uvicorn, playwright, psutil, PyInstaller; print('deps ok')"

    Step "4/7 windows engine CFT $CftVersion"
    $engineDir = Join-Path $RepoRoot "build_resources\engine\chrome-win64"
    $chromeExe = Join-Path $engineDir "chrome.exe"
    if (-not (Test-Path $chromeExe)) {
        $zip = Join-Path $env:TEMP "chrome-win64.zip"
        $urls = @(
            "https://cdn.npmmirror.com/binaries/chrome-for-testing/$CftVersion/win64/chrome-win64.zip",
            "https://storage.googleapis.com/chrome-for-testing-public/$CftVersion/win64/chrome-win64.zip"
        )
        $ok = $false
        foreach ($u in $urls) {
            try {
                Write-Host "download: $u"
                Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing -TimeoutSec 900
                $ok = $true; break
            } catch {
                Write-Host "failed, trying next mirror: $($_.Exception.Message)"
            }
        }
        if (-not $ok) { throw "engine CFT download failed on all mirrors" }
        $src = Join-Path $env:TEMP "chrome-win64-src"
        if (Test-Path $src) { Remove-Item $src -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $src -Force
        if (Test-Path $engineDir) { Remove-Item $engineDir -Recurse -Force }
        Copy-Item -Path (Join-Path $src "chrome-win64") -Destination $engineDir -Recurse
        Remove-Item $zip -Force
    } else {
        Write-Host "engine present, skip download: $chromeExe"
    }
    if (-not (Test-Path $chromeExe)) { throw "engine CFT missing: $chromeExe" }

    Step "5/7 pyinstaller"
    $distPath = Join-Path $Work "dist"
    $workPath = Join-Path $Work "build"

    # 先清掉残留进程与旧产物：chrome.exe 可能被杀软/上次运行占用，删除会 Access Denied
    Get-Process XHSLeads -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    foreach ($proc in (Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if ($proc.Path -and $proc.Path.StartsWith($Work)) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
        } catch {}
    }
    Start-Sleep -Milliseconds 500
    foreach ($path in @($distPath, $workPath)) {
        for ($i = 1; $i -le 5; $i++) {
            try {
                if (Test-Path $path) { Remove-Item $path -Recurse -Force -ErrorAction Stop }
                break
            } catch {
                Write-Host "clean retry $i : $path"
                Start-Sleep -Seconds 2
            }
        }
        if (Test-Path $path) { throw "cannot clean leftover path: $path" }
    }

    Push-Location $RepoRoot
    try {
        & $venvPy -m PyInstaller --noconfirm --clean "scripts\packaging\xhs_leads_win.spec" --distpath $distPath --workpath $workPath
        if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed with code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }

    Step "6/7 copy engine and self-check"
    $outDir = Join-Path $distPath "XHSLeads"
    $mpDir = Join-Path $outDir "_internal"
    if (-not (Test-Path $mpDir)) { $mpDir = $outDir }
    $outEngine = Join-Path $mpDir "engine"
    if (Test-Path $outEngine) { Remove-Item $outEngine -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $outEngine | Out-Null
    Copy-Item -Path $engineDir -Destination $outEngine -Recurse -Force
    $checks = @(
        (Join-Path $outDir "XHSLeads.exe"),
        (Join-Path $outEngine "chrome-win64\chrome.exe"),
        (Join-Path $mpDir "frontend\dist\index.html")
    )
    foreach ($c in $checks) {
        if (-not (Test-Path $c)) { throw "artifact missing: $c" }
        Write-Host "OK  $c"
    }
    # Shared asset manifest with macOS (scripts/packaging/check_pack.py):
    # extension trio (manifest/background/extract), frontend dist, engine, main exe.
    # NOTE: keep this file UTF-8 *with BOM*. Windows PowerShell 5.1 decodes BOM-less
    # UTF-8 as GBK; a CJK char at line end can swallow the newline and silently turn
    # the following command line into part of the comment.
    & $venvPy (Join-Path $RepoRoot "scripts\packaging\check_pack.py") $outDir --platform win32
    if ($LASTEXITCODE -ne 0) { throw "artifact self-check failed" }

    Step "7/7 distribute ($Format)"

    if ($Format -eq "zip" -or $Format -eq "all") {
        $zipOut = Join-Path $Desktop "XHSLeads-win64.zip"
        # Compress-Archive writes backslash entry names (breaks non-Windows extractors),
        # so build the archive with Python zipfile instead.
        & $venvPy (Join-Path $RepoRoot "scripts\packaging\make_zip.py") $outDir $zipOut "XHSLeads"
        if ($LASTEXITCODE -ne 0) { throw "zip creation failed" }
        Write-Host "zip:   $zipOut"
    }

    if ($Format -eq "installer" -or $Format -eq "all") {
        # Inno Setup: installs into %LOCALAPPDATA%\Programs\XHSLeads (no admin rights).
        $iscc = $null
        foreach ($c in @(
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"))) {
            if ($c -and (Test-Path $c)) { $iscc = $c; break }
        }
        if (-not $iscc) { throw "Inno Setup not found: winget install --id JRSoftware.InnoSetup -e" }
        $issPath = Join-Path $RepoRoot "scripts\packaging\xhs_leads.iss"
        $icoPath = Join-Path $RepoRoot "build_resources\icon\XHSLeadsIcon.ico"
        Write-Host "iscc:  $iscc"
        & $iscc "/DMyDistDir=$outDir" "/DMyOutDir=$Desktop" "/DMyIcon=$icoPath" "/Qp" $issPath
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed with code $LASTEXITCODE" }
        $setupOut = Join-Path $Desktop "XHSLeads-Setup-x64.exe"
        if (-not (Test-Path $setupOut)) { throw "installer not produced: $setupOut" }
        Write-Host "setup: $setupOut"
    }

    Write-Host "BUILD_OK"
}
catch {
    Write-Host ""
    Write-Host "BUILD_FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}
