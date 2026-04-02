# ============================================
# MZ1312 DRIFTER — One-Command Deploy from Windows
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: .\deploy.ps1 -PiHost <ip_or_hostname> [-PiUser kali]
#
# Pushes the entire repo to your Pi over SSH and runs install.sh.
# No git, no DNS, no internet needed on the Pi.
# ============================================

param(
    [Parameter(Mandatory=$true, HelpMessage="Pi IP address or hostname (e.g. 192.168.1.50)")]
    [string]$PiHost,

    [Parameter(HelpMessage="SSH username on the Pi (default: kali)")]
    [string]$PiUser = "kali",

    [Parameter(HelpMessage="Skip the install step (just copy files)")]
    [switch]$CopyOnly,

    [Parameter(HelpMessage="SSH port (default: 22)")]
    [int]$Port = 22
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  DRIFTER DEPLOY" -ForegroundColor Cyan
Write-Host "  MZ1312 UNCAGED TECHNOLOGY" -ForegroundColor DarkGray
Write-Host ""

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RemoteDir = "/home/$PiUser/drifter"
$SshTarget = "$PiUser@$PiHost"
$SshOpts = @("-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", "-p", "$Port")

# ── Preflight ──
Write-Host "[1/4] Testing SSH connection to $SshTarget..." -ForegroundColor Yellow
try {
    $result = ssh @SshOpts $SshTarget "echo ok" 2>&1
    if ($result -ne "ok") { throw "SSH failed" }
    Write-Host "  Connected" -ForegroundColor Green
} catch {
    Write-Host "  Cannot reach $SshTarget on port $Port" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Make sure:" -ForegroundColor Yellow
    Write-Host "    - Pi is powered on and connected to the same network"
    Write-Host "    - SSH is enabled (sudo systemctl enable ssh --now)"
    Write-Host "    - IP address is correct (check with: hostname -I on the Pi)"
    Write-Host ""
    exit 1
}

# ── Copy files ──
Write-Host "[2/4] Copying DRIFTER files to Pi..." -ForegroundColor Yellow

# Create target directory
ssh @SshOpts $SshTarget "mkdir -p $RemoteDir"

# Use scp to transfer the repo (exclude .git, __pycache__, .venv)
# Build a list of files/folders to send
$items = @(
    "install.sh", "README.md", "LICENSE", "AGENTS.md", "conftest.py",
    "src", "services", "config", "realdash", "scripts", "tests", "docs"
)

foreach ($item in $items) {
    $localPath = Join-Path $RepoDir $item
    if (Test-Path $localPath) {
        $scpOpts = @("-o", "StrictHostKeyChecking=no", "-P", "$Port", "-r")
        scp @scpOpts "$localPath" "${SshTarget}:${RemoteDir}/" 2>&1 | Out-Null
        Write-Host "  Copied: $item" -ForegroundColor DarkGray
    }
}

# Clean __pycache__ on remote
ssh @SshOpts $SshTarget "find $RemoteDir -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true"

Write-Host "  All files deployed to $RemoteDir" -ForegroundColor Green

# ── Make executable ──
Write-Host "[3/4] Setting permissions..." -ForegroundColor Yellow
ssh @SshOpts $SshTarget "chmod +x $RemoteDir/install.sh $RemoteDir/scripts/*.sh 2>/dev/null; true"
Write-Host "  Done" -ForegroundColor Green

# ── Run installer ──
if ($CopyOnly) {
    Write-Host "[4/4] Skipping install (-CopyOnly)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Files are on the Pi at: $RemoteDir" -ForegroundColor Cyan
    Write-Host "  SSH in and run:" -ForegroundColor Cyan
    Write-Host "    ssh $SshTarget" -ForegroundColor White
    Write-Host "    cd $RemoteDir && sudo ./install.sh" -ForegroundColor White
} else {
    Write-Host "[4/4] Running installer on Pi (this takes a few minutes)..." -ForegroundColor Yellow
    Write-Host "  ─────────────────────────────────────────" -ForegroundColor DarkGray
    ssh @SshOpts -t $SshTarget "cd $RemoteDir && sudo ./install.sh"
    $exitCode = $LASTEXITCODE

    Write-Host "  ─────────────────────────────────────────" -ForegroundColor DarkGray

    if ($exitCode -eq 0) {
        Write-Host ""
        Write-Host "  DRIFTER DEPLOYED SUCCESSFULLY" -ForegroundColor Green
        Write-Host ""
        Write-Host "  Next steps:" -ForegroundColor Cyan
        Write-Host "    1. Reboot the Pi:  ssh $SshTarget 'sudo reboot'" -ForegroundColor White
        Write-Host "    2. Connect phone to Wi-Fi: MZ1312_DRIFTER / uncaged1312" -ForegroundColor White
        Write-Host "    3. Open RealDash -> TCP CAN -> 10.42.0.1:35000" -ForegroundColor White
        Write-Host "    4. Wire OBD-II pigtail into USB2CANFD terminals" -ForegroundColor White
        Write-Host "    5. After warm-up drive, calibrate:" -ForegroundColor White
        Write-Host "       ssh $SshTarget 'sudo /opt/drifter/venv/bin/python3 /opt/drifter/calibrate.py --auto'" -ForegroundColor White
    } else {
        Write-Host ""
        Write-Host "  Install failed (exit code: $exitCode)" -ForegroundColor Red
        Write-Host "  SSH in to debug: ssh $SshTarget" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  1312 — ZERO CLOUD — TOTAL SOVEREIGNTY" -ForegroundColor DarkRed
Write-Host ""
