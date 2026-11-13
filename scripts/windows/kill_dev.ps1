Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*launcher.py*' } |
  ForEach-Object {
    Write-Output ("killing python " + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Milliseconds 800
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  ForEach-Object {
    Write-Output ("killing chrome " + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Write-Output "done"
