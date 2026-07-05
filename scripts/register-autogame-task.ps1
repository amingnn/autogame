param(
    [string]$TaskName = "AutoGame",
    [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$uv = (Get-Command uv -ErrorAction Stop).Source
$action = New-ScheduledTaskAction `
    -Execute $uv `
    -Argument "run python main.py" `
    -WorkingDirectory $ProjectDir

$triggers = @(
    New-ScheduledTaskTrigger -Daily -At 09:00
    New-ScheduledTaskTrigger -Daily -At 21:00
)

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Run AutoGame at 09:00 and 21:00, waking the computer if asleep." `
    -Force