#Requires -Version 5.1
<#
.SYNOPSIS
    删除计划任务 "aiher-meme-intake"（install-task.ps1 的反操作）。
.DESCRIPTION
    - 先 schtasks /Query 确认存在，不存在则直接返回（幂等，可重复运行）。
    - 先打印任务定义，经确认（ShouldProcess）后才 schtasks /Delete /F。
    - 支持 -WhatIf 干跑：只打印，不动系统。
    - 只删计划任务，不动 services/meme-intake/data/ 下的数据库与日志。
.NOTES
    删除系统计划任务属系统动作，需 站点所有者批准后再执行：
        powershell -ExecutionPolicy Bypass -File uninstall-task.ps1
        powershell -ExecutionPolicy Bypass -File uninstall-task.ps1 -WhatIf   # 只看计划
    日志文件为 UTF-8 BOM：Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按 ANSI
    解码，中文注释会变乱码甚至破坏解析，故两个 .ps1 都带 BOM。
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = 'aiher-meme-intake'
)

$ErrorActionPreference = 'Stop'

$null = & schtasks /Query /TN $TaskName 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "计划任务不存在：$TaskName（无需删除）"
    return
}

Write-Host "将删除计划任务：$TaskName"
& schtasks /Query /TN $TaskName /FO LIST

if (-not $PSCmdlet.ShouldProcess("计划任务 '$TaskName'", '删除')) {
    Write-Host '已取消：未做任何更改。（确认无误后重跑，或先加 -WhatIf 干跑一遍。）'
    return
}

& schtasks /Delete /TN $TaskName /F
if ($LASTEXITCODE -ne 0) {
    throw "schtasks /Delete 失败，退出码 $LASTEXITCODE"
}

Write-Host "已删除：$TaskName"
Write-Host '注意：数据库与 intake.log 未动，如需清理请手工处理 services/meme-intake/data/。'
