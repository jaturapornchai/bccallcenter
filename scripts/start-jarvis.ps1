$ErrorActionPreference = "Stop"
$JarvisDir = Join-Path $PSScriptRoot "..\jarvis"
$VenvPython = Join-Path $JarvisDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
  $FallbackPython = "D:\bctest01\.venv\Scripts\python.exe"
  if (Test-Path $FallbackPython) {
    $VenvPython = $FallbackPython
  } else {
    $VenvPython = "python"
  }
}
Push-Location $JarvisDir
try {
  Write-Host "[J.A.R.V.I.S] Starting voice_agent.py from $JarvisDir ..."
  & $VenvPython -X utf8 -W ignore voice_agent.py
} finally {
  Pop-Location
}
