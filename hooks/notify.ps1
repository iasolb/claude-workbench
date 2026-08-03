# Notification hook, Windows side (notify.sh is the macOS/Linux counterpart,
# same stdin JSON contract). Two jobs:
#   1. Local toast so the state change is visible at the desk.
#   2. One-way ntfy push to the phone when a session is WAITING on the user
#      (permission prompt / needs input), so they know to open the Claude
#      app and answer there. Notify-only by design: no action buttons, no
#      response topic, the approval itself only ever happens in the app UI.
# Topics come from ~/.claude/ntfy-topics.json (never committed): uses the
# "request" topic and optional "server" (default https://ntfy.sh).
# Always exits 0: a broken notifier must never block a session.

$stdin = [Console]::In.ReadToEnd()
try {
    $type = ($stdin | ConvertFrom-Json).notification_type
} catch {
    $type = ""
}

$waiting = @("permission_prompt", "idle_prompt", "agent_needs_input")
switch ($type) {
    "permission_prompt" { $message = "Waiting on an approval" }
    "idle_prompt"       { $message = "Waiting on you" }
    "agent_needs_input" { $message = "Waiting on your input" }
    "agent_completed"   { $message = "Task finished" }
    default             { $message = "Notification" }
}

# Phone push, waiting states only. Completions travel by email instead.
if ($waiting -contains $type) {
    try {
        $cfgPath = Join-Path $env:USERPROFILE ".claude\ntfy-topics.json"
        if (Test-Path $cfgPath) {
            $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
            if ($cfg.request) {
                $server = if ($cfg.server) { $cfg.server } else { "https://ntfy.sh" }
                $body = @{
                    topic    = $cfg.request
                    title    = "Claude PC: $message"
                    message  = "Open the Claude app to respond."
                    priority = 4
                    tags     = @("hourglass_flowing_sand")
                } | ConvertTo-Json
                Invoke-RestMethod -Method Post -Uri $server -Body $body `
                    -ContentType "application/json" -TimeoutSec 10 | Out-Null
            }
        }
    } catch {}
}

try {
    Add-Type -AssemblyName System.Windows.Forms
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.Visible = $true
    $notify.BalloonTipTitle = "Claude Code"
    $notify.BalloonTipText = $message
    $notify.ShowBalloonTip(4000)
    Start-Sleep -Seconds 3
    $notify.Dispose()
} catch {}
exit 0
