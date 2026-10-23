#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = 'aiher-meme-intake',
    [string]$Python = 'python',
    [Parameter(Mandatory=$true)][string]$EnvFile,
    [Parameter(Mandatory=$true)][string]$Database
)
$ErrorActionPreference = 'Stop'
$PythonExe = (Get-Command $Python -ErrorAction Stop).Source
$Pipeline = Join-Path $PSScriptRoot 'pipeline.py'
$EnvFile = (Resolve-Path -LiteralPath $EnvFile).Path
$Database = [IO.Path]::GetFullPath($Database)
$Arguments = '-X utf8 "{0}" --env-file "{1}" --database "{2}" --log' -f $Pipeline,$EnvFile,$Database
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $Arguments -WorkingDirectory $PSScriptRoot
# Resume pending PR/deployment every ten minutes; pipeline paces fresh collection to 20h.
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) -RepetitionInterval (New-TimeSpan -Minutes 10)
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
if ($PSCmdlet.ShouldProcess($TaskName, 'Register bounded collection and publication task')) {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
}
