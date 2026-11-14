# Windows media session (what Spotify, browsers and media players report to the volume flyout).
# Usage: media.ps1 [status|play|pause|toggle|next|previous]. Prints JSON.
param([string]$Action = "status")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result
}
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$s = $mgr.GetCurrentSession()
if ($null -eq $s) { '{"session": false}'; exit 0 }
$ok = $true
switch ($Action) {
    "play"     { $ok = Await ($s.TryPlayAsync()) ([bool]) }
    "pause"    { $ok = Await ($s.TryPauseAsync()) ([bool]) }
    "toggle"   { $ok = Await ($s.TryTogglePlayPauseAsync()) ([bool]) }
    "next"     { $ok = Await ($s.TrySkipNextAsync()) ([bool]) }
    "previous" { $ok = Await ($s.TrySkipPreviousAsync()) ([bool]) }
}
if ($Action -ne "status") { Start-Sleep -Milliseconds 400 }
$p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
[pscustomobject]@{
    session = $true; ok = $ok; app = $s.SourceAppUserModelId
    title = $p.Title; artist = $p.Artist; album = $p.AlbumTitle
    status = $s.GetPlaybackInfo().PlaybackStatus.ToString()
} | ConvertTo-Json -Compress
