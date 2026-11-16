# Runs discovery and then re-scans the CV library, once a day.
#
# Register it (no admin needed, runs as you, when you are logged on):
#
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_discovery.ps1
#
# Remove it:
#
#   Unregister-ScheduledTask -TaskName "Jobhunter discovery" -Confirm:$false
#
# The point of a scheduled run is not speed, it is that the dashboard is current
# when you open it. Boards change daily; a stale list is worse than a short one.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "-m jobhunter discover" `
    -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -Daily -At 08:30

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName "Jobhunter discovery" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Read employer job boards, screen new postings, update the local dashboard." `
    -Force | Out-Null

Write-Host "registered 'Jobhunter discovery' for 08:30 daily"
Write-Host "run it now with: Start-ScheduledTask -TaskName 'Jobhunter discovery'"
