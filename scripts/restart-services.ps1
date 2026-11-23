<#
Restart BC Call Center services for an unattended deploy (Dev on the Fly), only while no call is live.

Processes are created through WMI, so they belong to no console or job of the caller: a review session that
ends right after a deploy must not take the production server down with it. Output goes to log files because
such a process has no visible console (server: data\logs\server.log, J.A.R.V.I.S: jarvis\logs\voice_agent.log).

Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restart-services.ps1 -Target all|server|jarvis [-Force]
Prints one JSON line. Exit: 0 healthy, 1 unhealthy after restart, 2 skipped because a call is live.
#>
param(
  [ValidateSet("all", "server", "jarvis")][string]$Target = "all",
  [switch]$Force,
  [int]$JarvisTimeoutSec = 300,
  [int]$ServerTimeoutSec = 90,
  [string]$PublicUrl = "https://callcenter.bcsmethai.com/"
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$result = [ordered]@{ ok = $false; target = $Target }

function Get-HttpStatus([string]$Url) {
  $code = & curl.exe -s -o NUL -w "%{http_code}" --max-time 8 $Url 2>$null
  if ($code -match "^\d+$") { return [int]$code }
  return 0
}

function Wait-Http([string]$Url, [int]$TimeoutSec) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if ((Get-HttpStatus $Url) -eq 200) { return $true }
    Start-Sleep -Seconds 2
  }
  return $false
}

function Stop-Listeners([int[]]$Ports) {
  $owners = Get-NetTCPConnection -LocalPort $Ports -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($owner in $owners) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
}

function Start-Detached([string]$CommandLine) {
  $hidden = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
  $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $CommandLine; CurrentDirectory = $Root; ProcessStartupInformation = $hidden
  }
  if ($created.ReturnValue -ne 0) { throw "Win32_Process.Create failed ($($created.ReturnValue)): $CommandLine" }
  return $created.ProcessId
}

function Complete([int]$ExitCode) {
  $result | ConvertTo-Json -Compress
  exit $ExitCode
}

if (-not $Force) {
  $idle = & node (Join-Path $Root "scripts\dev-on-the-fly-scan.mjs") --check-idle | Out-String | ConvertFrom-Json
  if (-not $idle.idle) {
    $result.reason = "call in progress"
    $result.activeCalls = $idle.activeCalls
    Complete 2
  }
}

$healthy = $true

if ($Target -eq "all" -or $Target -eq "jarvis") {
  Stop-Listeners 7860, 7861
  $shell = Join-Path $PSHOME "powershell.exe"
  $result.jarvisPid = Start-Detached "`"$shell`" -NoProfile -ExecutionPolicy Bypass -File `"$Root\scripts\start-jarvis.ps1`""
  $result.jarvis = (Wait-Http "http://127.0.0.1:7861/" $JarvisTimeoutSec) -and (Wait-Http "http://127.0.0.1:7860/" 30)
  $healthy = $healthy -and $result.jarvis
}

if ($Target -eq "all" -or $Target -eq "server") {
  $logDir = Join-Path $Root "data\logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $log = Join-Path $logDir "server.log"
  if ((Test-Path $log) -and (Get-Item $log).Length -gt 10MB) { Move-Item -Force $log "$log.1" }
  $node = (Get-Command node).Source
  Stop-Listeners 3000
  $result.serverPid = Start-Detached "cmd.exe /d /s /c `"set NODE_ENV=production&& `"$node`" server.mjs >> `"$log`" 2>&1`""
  $result.server = Wait-Http "http://127.0.0.1:3000/" $ServerTimeoutSec
  $healthy = $healthy -and $result.server
}

$result.public = Wait-Http $PublicUrl 40
$result.ok = $healthy -and $result.public
if ($result.ok) { Complete 0 } else { Complete 1 }
