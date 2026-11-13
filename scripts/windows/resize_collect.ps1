Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WinEnum {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hh, bool repaint);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

$target = $null
$rows = @()
$cb = [WinEnum+EnumProc]{
  param($h, $l)
  if ([WinEnum]::IsWindowVisible($h)) {
    $sb = New-Object System.Text.StringBuilder 512
    [WinEnum]::GetWindowText($h, $sb, 512) | Out-Null
    $t = $sb.ToString()
    if ($t -ne '') {
      $r = New-Object WinEnum+RECT
      [WinEnum]::GetWindowRect($h, [ref]$r) | Out-Null
      $w = $r.Right - $r.Left
      $hh = $r.Bottom - $r.Top
      if ($w -gt 300 -and $hh -gt 300) {
        $script:rows += ("{0},{1} {2}x{3}  hwnd={4}  {5}" -f $r.Left, $r.Top, $w, $hh, $h, $t)
        if ($t -match '小红书' -and $null -eq $script:target) { $script:target = $h }
      }
    }
  }
  return $true
}
[WinEnum]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

Write-Output "--- windows ---"
$rows | ForEach-Object { Write-Output $_ }
Write-Output "--- target ---"
if ($null -ne $target) {
  [WinEnum]::MoveWindow($target, 605, 0, 640, 782, $true) | Out-Null
  Start-Sleep -Milliseconds 900
  $r = New-Object WinEnum+RECT
  [WinEnum]::GetWindowRect($target, [ref]$r) | Out-Null
  Write-Output ("resized to {0},{1} {2}x{3}" -f $r.Left, $r.Top, ($r.Right-$r.Left), ($r.Bottom-$r.Top))
} else {
  Write-Output "no xiaohongshu window found"
}
