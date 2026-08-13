param(
    [string]$TaskName = "AutoGame",
    [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path $ProjectDir).Path
$uv = (Get-Command uv -ErrorAction Stop).Source
$action = New-ScheduledTaskAction `
    -Execute $uv `
    -Argument "run python main.py --automation" `
    -WorkingDirectory $ProjectDir

$existing = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    # 已存在任务时只更新执行动作，保留电脑上的触发器、账户和其他设置。
    Set-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action | Out-Null
    Write-Output ("已更新 {0} 的执行入口：python main.py --automation；其他计划任务配置保持不变。" -f $TaskName)
    exit 0
}

# 新电脑没有任务时，使用当前电脑采用的两个时间点创建任务。
$triggers = @(
    New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour 7 -Minute 0 -Second 0)
    New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour 19 -Minute 0 -Second 0)
)
$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
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
    -Description "Run AutoGame at 07:00 and 19:00, waking the computer if asleep."
$xml = $task | Export-ScheduledTask
$xml = $xml -replace '<MultipleInstancesPolicy>[^<]+</MultipleInstancesPolicy>', '<MultipleInstancesPolicy>StopExisting</MultipleInstancesPolicy>'
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Xml $xml -Force | Out-Null
Write-Output ("已创建 {0}：07:00 和 19:00 执行 python main.py --automation。" -f $TaskName)
