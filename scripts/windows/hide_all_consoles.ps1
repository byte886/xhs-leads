Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WinHide {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
}
"@
$classes = @('CASCADIA_HOSTING_WINDOW_CLASS','PseudoConsoleWindow','ConsoleWindowClass')
$script:done = @()
[WinHide]::EnumWindows({ param($h,$l)
  if ([WinHide]::IsWindowVisible($h)) {
    $sb = New-Object System.Text.StringBuilder 256
    [WinHide]::GetClassName($h,$sb,256) | Out-Null
    $cls = $sb.ToString()
    if ($classes -contains $cls) {
      [WinHide]::ShowWindow($h,0) | Out-Null
      $script:done += $cls
    }
  }
  return $true
}, [IntPtr]::Zero) | Out-Null
if ($script:done.Count -eq 0) { Write-Output "NO_CONSOLE_VISIBLE" }
else { $script:done | ForEach-Object { Write-Output ("HID " + $_) } }
