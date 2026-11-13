$ErrorActionPreference = "Continue"
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WA {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$script:rows = @()
$cb = [WA+EnumProc]{
  param($h, $l)
  $sb = New-Object System.Text.StringBuilder 512
  [WA]::GetWindowText($h, $sb, 512) | Out-Null
  $r = New-Object WA+RECT
  [WA]::GetWindowRect($h, [ref]$r) | Out-Null
  $w = $r.Right - $r.Left
  $hh = $r.Bottom - $r.Top
  if ($hh -gt 400) {
    $cls = New-Object System.Text.StringBuilder 256
    [WA]::GetClassName($h, $cls, 256) | Out-Null
    $script:rows += ("hwnd={0} vis={1} rect={2},{3} {4}x{5} cls={6} title={7}" -f $h, ([WA]::IsWindowVisible($h)), $r.Left, $r.Top, $w, $hh, $cls.ToString(), $sb.ToString())
  }
  return $true
}
[WA]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
foreach ($row in $script:rows) { Write-Output $row }
Write-Output ("TOTAL=" + $script:rows.Count)
