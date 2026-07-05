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

# The PowerShell ScheduledTasks enum on some Windows builds does not expose
# StopExisting, even though Task Scheduler supports it in XML and the GUI.
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

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Run AutoGame at 09:00 and 21:00, waking the computer if asleep."

$xml = $task | Export-ScheduledTask
$xml = $xml -replace '<MultipleInstancesPolicy>[^<]+</MultipleInstancesPolicy>', '<MultipleInstancesPolicy>StopExisting</MultipleInstancesPolicy>'

Register-ScheduledTask `
    -TaskName $TaskName `
    -Xml $xml `
    -Force
