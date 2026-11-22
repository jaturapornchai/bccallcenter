<#
.SYNOPSIS
  สร้าง LiveKit Inbound SIP Trunk และ Dispatch Rule (Inbound-Only) ผ่าน LiveKit CLI (`lk`)
  ติดตั้ง LiveKit CLI ก่อน: winget install LiveKit.LiveKitCLI หรือ brew install livekit-cli
#>
param(
  [string]$LiveKitUrl = "http://localhost:7880",
  [string]$ApiKey = $env:LIVEKIT_API_KEY,
  [string]$ApiSecret = $env:LIVEKIT_API_SECRET
)

$ErrorActionPreference = "Stop"
if (-not $ApiKey -or -not $ApiSecret) {
  Write-Error "กรุณาตั้งค่า LIVEKIT_API_KEY และ LIVEKIT_API_SECRET ก่อนรันสคริปต์นี้"
}

$env:LIVEKIT_URL = $LiveKitUrl
$env:LIVEKIT_API_KEY = $ApiKey
$env:LIVEKIT_API_SECRET = $ApiSecret

Write-Host "[1/3] ตรวจสอบการเชื่อมต่อ LiveKit Server ($LiveKitUrl)..."
lk room list

Write-Host "[2/3] สร้าง Inbound SIP Trunk..."
$trunkJson = Join-Path $PSScriptRoot "inbound-trunk.json"
$trunkOut = lk sip inbound create $trunkJson --json | ConvertFrom-Json
$trunkId = $trunkOut.sip_trunk_id
Write-Host "สร้าง Inbound Trunk สำเร็จ: $trunkId"

Write-Host "[3/3] สร้าง Dispatch Rule ผูกกับ Agent 'jarvis-callcenter'..."
$dispatchJson = Join-Path $PSScriptRoot "dispatch-rule.json"
lk sip dispatch create $dispatchJson --trunks $trunkId

Write-Host "เสร็จสมบูรณ์! พร้อมรับสายเข้าจาก SIP Trunk แล้ว"
