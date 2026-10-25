Option Explicit
Dim sh, fso, folder, desktop, target, exe, shortcut
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
desktop = sh.SpecialFolders("Desktop")
exe = folder & "\dist\Yus Browser Pro 2.3\Yus Browser Pro 2.3.exe"
If fso.FileExists(exe) Then
  target = exe
Else
  target = folder & "\START_YUS_BROWSER_NO_CONSOLE.vbs"
End If
Set shortcut = sh.CreateShortcut(desktop & "\Yus Browser Pro 2.3.lnk")
shortcut.TargetPath = target
shortcut.WorkingDirectory = folder
shortcut.Description = "Yus Browser Pro 2.3 — запуск без окна командной строки"
If fso.FileExists(folder & "\assets\yus_browser_icon.ico") Then shortcut.IconLocation = folder & "\assets\yus_browser_icon.ico"
shortcut.Save
MsgBox "Ярлык Yus Browser Pro 2.3 создан на рабочем столе." & vbCrLf & vbCrLf & "Если EXE ещё не собран, ярлык запускает браузер скрыто через Python. Для отдельного EXE один раз запустите BUILD_STANDALONE_APP.bat.", 64, "Yus Browser Pro 2.3"
