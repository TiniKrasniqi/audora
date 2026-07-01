<#
.SYNOPSIS
    Bootstraps the Audora application on Windows.
.DESCRIPTION
    The script installs Python if necessary, creates an isolated virtual
    environment and installs the application's dependencies. A helper script
    named run_app.ps1 is generated to launch the program afterwards.
#>

param(
    [switch]$ForcePythonInstall
)

$ErrorActionPreference = "Stop"

function Resolve-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @($pyLauncher.Source, "-3")
    }

    return $null
}

function Install-PythonIfNeeded {
    param(
        [switch]$Force
    )

    $pythonCmd = Resolve-PythonCommand
    if ($pythonCmd -and -not $Force) {
        return $pythonCmd
    }

    Write-Host "Python was not found. Downloading the official installer..."
    $pythonUrl = "https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe"
    $downloadPath = Join-Path $env:TEMP "python-installer.exe"

    Invoke-WebRequest -Uri $pythonUrl -OutFile $downloadPath
    Write-Host "Running the Python installer (this may take a while)..."
    $arguments = @("/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1")
    Start-Process -FilePath $downloadPath -ArgumentList $arguments -Wait

    Remove-Item $downloadPath -ErrorAction SilentlyContinue

    $pythonCmd = Resolve-PythonCommand
    if (-not $pythonCmd) {
        throw "Python installation failed. Please install Python 3.8+ manually and rerun this script."
    }

    return $pythonCmd
}

function Test-JavaScriptRuntime {
    foreach ($name in @("deno", "node", "bun", "qjs")) {
        if (Get-Command $name -ErrorAction SilentlyContinue) {
            return $true
        }
    }
    return $false
}

$VlcVersion = "3.0.23"
$VlcArchiveName = "vlc-$VlcVersion-win64.zip"
$VlcDownloadUrl = "https://download.videolan.org/pub/videolan/vlc/$VlcVersion/win64/$VlcArchiveName"
$VlcSha256 = "992d19dbd0b8a7cde9167d2f7780b1ef6f92acc8a71acfa736101a21f35181e1"
$FfmpegArchiveName = "ffmpeg-release-essentials.zip"
$FfmpegDownloadUrl = "https://www.gyan.dev/ffmpeg/builds/$FfmpegArchiveName"
$WebView2DownloadUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

function Get-AudoraRuntimeRoot {
    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    if (-not $localAppData) {
        $localAppData = $env:LOCALAPPDATA
    }
    if (-not $localAppData) {
        throw "Could not resolve the LocalAppData folder for the Audora runtime."
    }
    return (Join-Path $localAppData "Audora\runtime")
}

function Install-VlcRuntime {
    $runtimeRoot = Get-AudoraRuntimeRoot
    $vlcDir = Join-Path $runtimeRoot "vlc-$VlcVersion"
    $libVlc = Join-Path $vlcDir "libvlc.dll"
    $pluginsDir = Join-Path $vlcDir "plugins"

    if ((Test-Path $libVlc) -and (Test-Path $pluginsDir)) {
        Write-Host "VLC playback runtime already installed."
        return $vlcDir
    }

    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $archivePath = Join-Path $runtimeRoot $VlcArchiveName
    $extractDir = Join-Path $runtimeRoot "vlc-extract-$PID"
    $expectedExtractedDir = Join-Path $extractDir "vlc-$VlcVersion"

    $resolvedRoot = [IO.Path]::GetFullPath($runtimeRoot)
    $resolvedTarget = [IO.Path]::GetFullPath($vlcDir)
    if (-not $resolvedTarget.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to install VLC outside the Audora runtime folder."
    }

    Write-Host "Downloading VLC playback runtime..."
    Invoke-WebRequest -Uri $VlcDownloadUrl -OutFile $archivePath
    $actualHash = (Get-FileHash -Path $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $VlcSha256) {
        Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
        throw "VLC runtime checksum mismatch. Expected $VlcSha256 but got $actualHash."
    }

    if (Test-Path $extractDir) {
        Remove-Item -LiteralPath $extractDir -Recurse -Force
    }
    Expand-Archive -Path $archivePath -DestinationPath $extractDir -Force
    if (-not (Test-Path $expectedExtractedDir)) {
        throw "The VLC archive did not contain the expected folder: $expectedExtractedDir"
    }

    if (Test-Path $vlcDir) {
        Remove-Item -LiteralPath $vlcDir -Recurse -Force
    }
    Move-Item -LiteralPath $expectedExtractedDir -Destination $vlcDir

    Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue

    Write-Host "VLC playback runtime installed."
    return $vlcDir
}

function Install-FfmpegRuntime {
    $runtimeRoot = Get-AudoraRuntimeRoot
    $ffmpegDir = Join-Path $runtimeRoot "ffmpeg"
    $ffmpegBinDir = Join-Path $ffmpegDir "bin"
    $ffmpegExe = Join-Path $ffmpegBinDir "ffmpeg.exe"
    $ffprobeExe = Join-Path $ffmpegBinDir "ffprobe.exe"

    if ((Test-Path $ffmpegExe) -and (Test-Path $ffprobeExe)) {
        Write-Host "FFmpeg runtime already installed."
        return $ffmpegBinDir
    }

    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $archivePath = Join-Path $runtimeRoot $FfmpegArchiveName
    $extractDir = Join-Path $runtimeRoot "ffmpeg-extract-$PID"

    $resolvedRoot = [IO.Path]::GetFullPath($runtimeRoot)
    $resolvedTarget = [IO.Path]::GetFullPath($ffmpegDir)
    if (-not $resolvedTarget.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to install FFmpeg outside the Audora runtime folder."
    }

    Write-Host "Downloading FFmpeg runtime..."
    Invoke-WebRequest -Uri $FfmpegDownloadUrl -OutFile $archivePath

    if (Test-Path $extractDir) {
        Remove-Item -LiteralPath $extractDir -Recurse -Force
    }
    Expand-Archive -Path $archivePath -DestinationPath $extractDir -Force

    $downloadedFfmpeg = Get-ChildItem -Path $extractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $downloadedFfmpeg) {
        throw "The FFmpeg archive did not contain ffmpeg.exe."
    }

    $downloadedBinDir = Split-Path -Parent $downloadedFfmpeg.FullName
    $downloadedRoot = Split-Path -Parent $downloadedBinDir
    $downloadedFfprobe = Join-Path $downloadedBinDir "ffprobe.exe"
    if (-not (Test-Path $downloadedFfprobe)) {
        throw "The FFmpeg archive did not contain ffprobe.exe."
    }

    if (Test-Path $ffmpegDir) {
        Remove-Item -LiteralPath $ffmpegDir -Recurse -Force
    }
    Move-Item -LiteralPath $downloadedRoot -Destination $ffmpegDir

    Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue

    Write-Host "FFmpeg runtime installed."
    return $ffmpegBinDir
}

function Test-WebView2Runtime {
    $clientId = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    $registryPaths = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$clientId",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$clientId",
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$clientId"
    )
    foreach ($path in $registryPaths) {
        try {
            $runtime = Get-ItemProperty -Path $path -ErrorAction Stop
            if ($runtime.pv) {
                return $true
            }
        } catch {
            continue
        }
    }

    $installRoots = @()
    foreach ($root in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
        if ($root) {
            $installRoots += (Join-Path $root "Microsoft\EdgeWebView\Application")
        }
    }

    foreach ($root in $installRoots) {
        if (-not (Test-Path $root)) {
            continue
        }
        $runtimeExe = Get-ChildItem -Path $root -Recurse -Filter "msedgewebview2.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($runtimeExe) {
            return $true
        }
    }

    return $false
}

function Install-WebView2Runtime {
    if (Test-WebView2Runtime) {
        Write-Host "Microsoft Edge WebView2 runtime already installed."
        return
    }

    Write-Host "Downloading Microsoft Edge WebView2 runtime..."
    $installerPath = Join-Path $env:TEMP "MicrosoftEdgeWebview2Setup.exe"
    Invoke-WebRequest -Uri $WebView2DownloadUrl -OutFile $installerPath

    Write-Host "Installing Microsoft Edge WebView2 runtime..."
    $process = Start-Process -FilePath $installerPath -ArgumentList @("/silent", "/install") -Wait -PassThru
    Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue

    if ($process.ExitCode -ne 0 -and -not (Test-WebView2Runtime)) {
        throw "Microsoft Edge WebView2 runtime installation failed with exit code $($process.ExitCode)."
    }

    if (-not (Test-WebView2Runtime)) {
        throw "Microsoft Edge WebView2 runtime installation did not complete."
    }

    Write-Host "Microsoft Edge WebView2 runtime installed."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$appDir = Join-Path $scriptDir "app"
$venvDir = Join-Path $scriptDir "venv"

$pythonCommand = Install-PythonIfNeeded -Force:$ForcePythonInstall

function Invoke-Python {
    param(
        [string[]]$Arguments
    )

    $command = $pythonCommand[0]
    $extraArgs = @()
    if ($pythonCommand.Length -gt 1) {
        $extraArgs = $pythonCommand[1..($pythonCommand.Length - 1)]
    }

    & $command @extraArgs @Arguments
}

Write-Host "Creating virtual environment..."
Invoke-Python -Arguments @("-m", "venv", $venvDir)

$venvPython = Join-Path $venvDir "Scripts/python.exe"

Write-Host "Upgrading pip..."
& $venvPython -m pip install --upgrade pip

Write-Host "Installing project dependencies..."
& $venvPython -m pip install -r (Join-Path $appDir "requirements.txt")

$vlcRuntimeDir = Install-VlcRuntime
$ffmpegBinDir = Install-FfmpegRuntime
Install-WebView2Runtime

if (-not (Test-JavaScriptRuntime)) {
    Write-Warning "No JavaScript runtime was found on PATH. The app can still run, but current yt-dlp works best for YouTube when Deno 2.3+, Node.js 22+, Bun 1.2.11+, or QuickJS is installed."
}

$launcherPath = Join-Path $scriptDir "run_app.ps1"
$launcherContent = @"
`$env:AUDORA_VLC_DIR = '$vlcRuntimeDir'
`$env:AUDORA_FFMPEG_DIR = '$ffmpegBinDir'
`$env:PATH = "`$env:AUDORA_FFMPEG_DIR;`$env:PATH"
Write-Host "Starting Audora..."
`$audoraExe = Join-Path '$appDir' 'audora.exe'
if (Test-Path `$audoraExe) {
    & `$audoraExe @args
} else {
    & '$venvPython' (Join-Path '$appDir' 'main.py') @args
}
"@
$launcherContent | Set-Content -Path $launcherPath -Encoding UTF8

Write-Host "Installation complete. Run 'run_app.ps1' to launch the application."
