Option Explicit
Dim sh, fso, folder, pythonw, script, candidates, i, cmd, pyw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
script = Chr(34) & folder & "\main.py" & Chr(34)
sh.CurrentDirectory = folder
sh.Environment("PROCESS")("PYTHONUTF8") = "1"
candidates = Array( _
 sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"), _
 sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"), _
 sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"), _
 sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"), _
 sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"), _
 sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"))
For i = 0 To UBound(candidates)
 If fso.FileExists(candidates(i)) Then
  pythonw = candidates(i)
  Exit For
 End If
Next
If pythonw <> "" Then
 cmd = Chr(34) & pythonw & Chr(34) & " " & script
 sh.Run cmd, 0, False
Else
 pyw = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Microsoft\WindowsApps\pyw.exe")
 If fso.FileExists(pyw) Then
  sh.Run Chr(34) & pyw & Chr(34) & " -3 " & script, 0, False
 Else
  MsgBox "Python with PySide6 is not found. Run INSTALL_DEPENDENCIES.bat first.", 48, "Yus Browser Pro 2.3"
 End If
End If
