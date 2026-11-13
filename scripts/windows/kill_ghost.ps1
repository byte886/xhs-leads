$ErrorActionPreference = "Continue"
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WZ {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$script:ghosts = @()
$script:collect = [IntPtr]::Zero
$cb = [WZ+EnumProc]{
  param($h, $l)
  $cls = New-Object System.Text.StringBuilder 256
  [WZ]::GetClassName($h, $cls, 256) | Out-Null
  $c = $cls.ToString()
  $r = New-Object WZ+RECT
  [WZ]::GetWindowRect($h, [ref]$r) | Out-Null
  $w = $r.Right - $r.Left
  $hh = $r.Bottom - $r.Top
  if ($c -eq "Chrome_WidgetWin_0" -and $w -gt 400 -and $hh -gt 400) { $script:ghosts += $h }
  if ($c -eq "Chrome_WidgetWin_1" -and $hh -gt 700 -and $r.Left -gt 500) { $script:collect = $h }
  return $true
}
[WZ]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

foreach ($g in $script:ghosts) {
  [WZ]::PostMessage($g, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
  Write-Output ("closed-ghost hwnd=" + $g)
}
Start-Sleep -Milliseconds 1500

if ($script:collect -ne [IntPtr]::Zero) {
  [WZ]::SetWindowPos($script:collect, [IntPtr](-1), 605, 0, 640, 782, 0x0040) | Out-Null
  [WZ]::ShowWindow($script:collect, 9) | Out-Null
  Write-Output ("collect-topmost hwnd=" + $script:collect)
} else {
  Write-Output "collect-not-found"
}
