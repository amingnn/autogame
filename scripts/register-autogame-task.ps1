param(
    [string]$TaskName = "AutoGame",
    [string]$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$TimesCsv = "07:00,19:00",
    [string]$UvPath = "",
    [switch]$List
)

$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path $ProjectDir).Path
$existing = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($List) {
    $times = @()
    if ($null -ne $existing) {
        $times = @($existing.Triggers | ForEach-Object {
            if ($_.StartBoundary) { ([datetime]$_.StartBoundary).ToString("HH:mm") }
        } | Where-Object { $_ } | Sort-Object -Unique)
    }
    ConvertTo-Json -InputObject @($times) -Compress
    exit 0
}

$uv = if ($UvPath -and (Test-Path $UvPath)) {
    $UvPath
} elseif ($env:MY_ENV -and (Test-Path (Join-Path $env:MY_ENV 'uv\uv.exe'))) {
    (Join-Path $env:MY_ENV 'uv\uv.exe')
} else {
    (Get-Command uv -ErrorAction Stop).Source
}

$times = @($TimesCsv -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($times.Count -eq 0 -or @($times | Where-Object { $_ -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$' }).Count -gt 0) {
    throw "TimesCsv must contain one or more valid HH:mm values."
}
$times = @($times | Sort-Object -Unique)
$triggers = @($times | ForEach-Object {
    $hour, $minute = $_ -split ':'
    New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour ([int]$hour) -Minute ([int]$minute) -Second 0)
})

$action = New-ScheduledTaskAction `
    -Execute $uv `
    -Argument "run python main.py -a -f -s" `
    -WorkingDirectory $ProjectDir

if ($null -ne $existing) {
    # 已存在任务时更新执行入口和触发时间，保留账户和其他设置。
    Set-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action -Trigger $triggers | Out-Null
    Write-Output ("已更新 {0}：每日 {1} 执行 python main.py -a -f -s。" -f $TaskName, ($times -join ', '))
    exit 0
}

# 新电脑没有任务时，按页面提交的全部时间创建触发器。
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
    -Description ("Run AutoGame daily at {0}, waking the computer if asleep." -f ($times -join ', '))
$xml = $task | Export-ScheduledTask
$xml = $xml -replace '<MultipleInstancesPolicy>[^<]+</MultipleInstancesPolicy>', '<MultipleInstancesPolicy>StopExisting</MultipleInstancesPolicy>'
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Xml $xml -Force | Out-Null
Write-Output ("已创建 {0}：每日 {1} 执行 python main.py -a -f -s。" -f $TaskName, ($times -join ', '))
