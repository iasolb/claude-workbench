# Desktop notification when Claude Code is waiting on input or has finished.
# Windows counterpart to notify.sh: same stdin JSON contract, same
# notification_type -> message mapping, so behavior matches across machines.

$stdin = [Console]::In.ReadToEnd()
try {
    $type = ($stdin | ConvertFrom-Json).notification_type
} catch {
    $type = ""
}

switch ($type) {
    "permission_prompt" { $message = "Waiting on you" }
    "idle_prompt"       { $message = "Waiting on you" }
    "agent_needs_input" { $message = "Waiting on you" }
    "agent_completed"   { $message = "Task finished" }
    default             { $message = "Notification" }
}

Add-Type -AssemblyName System.Windows.Forms
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Visible = $true
$notify.BalloonTipTitle = "Claude Code"
$notify.BalloonTipText = $message
$notify.ShowBalloonTip(4000)
Start-Sleep -Seconds 3
$notify.Dispose()
