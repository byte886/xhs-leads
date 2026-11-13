Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WX {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hh, bool repaint);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$collect = $null
$hits = @()
$cb = [WX+EnumProc]{
  param($h, $l)
  if ([WX]::IsWindowVisible($h)) {
    $sb = New-Object System.Text.StringBuilder 512
    [WX]::GetWindowText($h, $sb, 512) | Out-Null
    $t = $sb.ToString()
    $r = New-Object WX+RECT
    [WX]::GetWindowRect($h, [ref]$r) | Out-Null
    $w = $r.Right - $r.Left
    $hh = $r.Bottom - $r.Top
    if ($t -like '*Google Chrome for Testing*' -and $w -gt 400) { $script:collect = $h }
    $isConsole = ($t -like '*Parallels*') -or ($t -like '*prl_tools*') -or ($t -like '*Windows Terminal*')
    if ($isConsole -and $w -gt 300 -and $hh -gt 200) {
      $script:hits += ("{0}`t{1}`t{2}`t{3}`t{4}`t{5}" -f $h, $r.Left, $r.Top, $w, $hh, $t)
    }
  }
  return $true
}
[WX]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

foreach ($row in $hits) {
  $p = $row -split "`t"
  $h = [IntPtr][long]$p[0]
  [WX]::ShowWindow($h, 6) | Out-Null
  [WX]::MoveWindow($h, -4300, 0, [int]$p[3], [int]$p[4], $true) | Out-Null
  Write-Output ("hidden hwnd=" + $p[0] + " was=" + $p[1] + "," + $p[2] + " title=" + $p[5].Substring(0, [Math]::Min(40, $p[5].Length)))
}
if ($collect -ne $null) {
  [WX]::BringWindowToTop($collect) | Out-Null
  [WX]::SetForegroundWindow($collect) | Out-Null
  Write-Output ("collect-front hwnd=" + $collect)
  Write-Output ("total-console-windows=" + $hits.Count)
}
