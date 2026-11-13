$ErrorActionPreference = "Continue"
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WH {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$script:rows = @()
$cb = [WH+EnumProc]{
  param($h, $l)
  $cls = New-Object System.Text.StringBuilder 256
  [WH]::GetClassName($h, $cls, 256) | Out-Null
  $c = $cls.ToString()
  if ($c -eq "ConsoleWindowClass" -or $c -like "*CASCADIA*" -or $c -eq "PseudoConsoleWindow") {
    $sb = New-Object System.Text.StringBuilder 512
    [WH]::GetWindowText($h, $sb, 512) | Out-Null
    $r = New-Object WH+RECT
    [WH]::GetWindowRect($h, [ref]$r) | Out-Null
    $vis = [WH]::IsWindowVisible($h)
    $script:rows += ("cls={0} vis={1} rect={2},{3} {4}x{5} title={6}" -f $c, $vis, $r.Left, $r.Top, ($r.Right-$r.Left), ($r.Bottom-$r.Top), $sb.ToString())
    if ($vis) { [WH]::ShowWindow($h, 0) | Out-Null; $script:rows += "  -> hidden" }
  }
  return $true
}
[WH]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
foreach ($row in $script:rows) { Write-Output $row }
