$ErrorActionPreference = "Continue"
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WQ {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$script:rows = @()
$cb = [WQ+EnumProc]{
  param($h, $l)
  if ([WQ]::IsWindowVisible($h)) {
    $sb = New-Object System.Text.StringBuilder 512
    [WQ]::GetWindowText($h, $sb, 512) | Out-Null
    $cls = New-Object System.Text.StringBuilder 256
    [WQ]::GetClassName($h, $cls, 256) | Out-Null
    $r = New-Object WQ+RECT
    [WQ]::GetWindowRect($h, [ref]$r) | Out-Null
    $w = $r.Right - $r.Left
    $hh = $r.Bottom - $r.Top
    if ($w -gt 400 -and $hh -gt 300) {
      $procId = [uint32]0
      [WQ]::GetWindowThreadProcessId($h, [ref]$procId) | Out-Null
      $pname = "?"
      try { $pname = (Get-Process -Id $procId -ErrorAction Stop).ProcessName } catch { }
      $script:rows += ("hwnd={0} pid={1} proc={2} rect={3},{4} {5}x{6} cls={7} title={8}" -f $h, $procId, $pname, $r.Left, $r.Top, $w, $hh, $cls.ToString(), $sb.ToString())
    }
  }
  return $true
}
[WQ]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
foreach ($row in $script:rows) { Write-Output $row }
