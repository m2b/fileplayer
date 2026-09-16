<#
.SYNOPSIS
Syncs this directory to a remote host over ssh.

.DESCRIPTION
Prompts for anything not supplied as a parameter or environment variable.

This deliberately only ever syncs FILES - it never runs `docker` on the
remote host itself. Instead it prints the `docker compose up -d --build`
command at the end for you to run there yourself. The ssh user doing the
file sync may not be in that host's `docker` group (or have passwordless
sudo for it) even when it can write to the deploy directory fine - a
common split on hosts where container lifecycle is deliberately gated
behind a login/sudo prompt, and non-interactive `ssh host "docker ..."`
often can't see docker even when an interactive login can (PATH/profile
differences, or a stale group membership that only refreshes on a fresh
login). Rather than chase every way that can fail non-interactively, this
just doesn't try.

Auth: tries key-based ssh first (fast, silent); if that's not set up, you
get a normal interactive password prompt instead - same as running ssh
directly. Unlike deploy.sh, this does NOT multiplex the connectivity check
and the file transfer over one shared connection - Windows' OpenSSH port
has never properly supported ControlMaster/ControlPath (incomplete AF_UNIX
socket support; fails with "getsockname failed: Not a socket"), so if
you're on password auth you'll be prompted twice.

Transport is `tar` piped over `ssh` (no rsync dependency, since Windows
doesn't ship one) - the same approach the reference scripts above use, and
for the same reason: it's one ssh connection for the whole sync instead of
one per file/directory.

Requires locally: ssh and tar (Windows' optional OpenSSH Client feature,
or Git for Windows' bundled versions; tar has shipped in Windows itself
since 10 1803). Requires on the remote host: ssh access (key or password)
for the given user, `tar`, and (for you to run yourself afterwards) docker
with the compose plugin.

A .env file (see .env.example - at minimum WEBSOCKET_URL) must exist next
to this script before deploying; it's copied over so compose has it on the
remote side too.

.PARAMETER RemoteTarget
SSH target, e.g. someuser@192.168.1.50. Prompted for if omitted.

.PARAMETER RemoteDir
Remote directory to deploy into. Defaults to "fileplayer" if omitted and
not supplied via prompt.

.EXAMPLE
.\deploy.ps1
.\deploy.ps1 -RemoteTarget someuser@192.168.1.50 -RemoteDir fileplayer
#>
param(
    [string]$RemoteTarget,
    [string]$RemoteDir
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $RemoteTarget) { $RemoteTarget = $env:REMOTE_TARGET }
if (-not $RemoteTarget) { $RemoteTarget = Read-Host "Remote target (user@host)" }
if (-not $RemoteTarget) {
    Write-Error "a remote target is required"
    exit 1
}

if (-not $RemoteDir) { $RemoteDir = $env:REMOTE_DIR }
if (-not $RemoteDir) { $RemoteDir = Read-Host "Remote directory [fileplayer]" }
if (-not $RemoteDir) { $RemoteDir = "fileplayer" }

$EnvFile = Join-Path $ScriptDir ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Error "$EnvFile not found - copy .env.example to .env and fill it in first"
    exit 1
}

Write-Host "==> checking ssh access to $RemoteTarget"
& ssh -o BatchMode=yes -o ConnectTimeout=10 $RemoteTarget "exit" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "    key-based auth OK"
} else {
    Write-Host "    key-based auth not available - enter your password when prompted (you'll be asked again for the file transfer)"
    & ssh -o ConnectTimeout=30 $RemoteTarget "exit"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "could not authenticate to $RemoteTarget"
        exit 1
    }
}

$ExcludeDirs = @(".git", "__pycache__", ".pytest_cache", "tests", "demo", "logs")

Push-Location $ScriptDir
try {
    $files = Get-ChildItem -Recurse -File -Force | Where-Object {
        $rel = $_.FullName.Substring($ScriptDir.Length).TrimStart('\', '/')
        $relParts = $rel -split '[\\/]'
        -not ($ExcludeDirs | Where-Object { $relParts -contains $_ }) -and
        ($_.Name -notlike "fileplayer_state*.json")
    } | ForEach-Object {
        ($_.FullName.Substring($ScriptDir.Length).TrimStart('\', '/')) -replace '\\', '/'
    }

    if (-not $files) {
        throw "no files found to sync under $ScriptDir"
    }

    Write-Host "==> syncing $($files.Count) file(s) to ${RemoteTarget}:${RemoteDir}/"
    & tar cf - $files | & ssh $RemoteTarget "mkdir -p '$RemoteDir' && tar xf - -C '$RemoteDir'"
    if ($LASTEXITCODE -ne 0) {
        throw "tar | ssh transfer to ${RemoteTarget}:${RemoteDir} failed (exit $LASTEXITCODE)"
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "==> files synced. Run this on the remote host to build the image and start the container"
Write-Host "    (prefix with sudo if your remote user is not in the docker group):"
Write-Host ""
Write-Host "    ssh $RemoteTarget"
Write-Host "    cd $RemoteDir && docker compose up -d --build"
Write-Host ""
Write-Host "    then tail logs with:"
Write-Host "    docker compose logs -f"
