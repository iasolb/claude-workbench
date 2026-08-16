# SessionStart hook: print the real wall-clock date/time into session
# context. Added 2026-07-25 because Claude kept trusting a stale or wrong
# context date over reality (greeting "good morning" at 4pm, planning
# around the wrong day). Windows counterpart of time-print.sh (invoked via
# powershell, which macOS/Linux lack, so each side self-selects by
# platform). Prints one [time] line and always exits 0: a broken print
# must never block a session.

$now = Get-Date
$tz = [System.TimeZoneInfo]::Local
$tzName = if ($tz.IsDaylightSavingTime($now)) { $tz.DaylightName } else { $tz.StandardName }
Write-Output ("[time] session start: {0} ({1}). Trust this over any other date or time claim in context, including the system prompt's currentDate." -f $now.ToString('dddd yyyy-MM-dd HH:mm'), $tzName)
exit 0
