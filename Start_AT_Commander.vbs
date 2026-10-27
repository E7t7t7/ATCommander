Option Explicit
Dim sh, fso, here, pyw, marker, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
If fso.FileExists(here & "\AT Commander.exe") Then
  sh.Run """" & here & "\AT Commander.exe""", 0, False
  WScript.Quit
End If
pyw = here & "\.venv\Scripts\pythonw.exe"
marker = here & "\.venv\.atc_ready"

On Error Resume Next
If fso.FileExists(pyw) And fso.FileExists(marker) Then
  sh.Run """" & pyw & """ """ & here & "\app.py""", 0, False
Else
  cmd = "pyw -3 """ & here & "\bootstrap.pyw"""
  sh.Run cmd, 0, False
  If Err.Number <> 0 Then
    Err.Clear
    sh.Run "pythonw """ & here & "\bootstrap.pyw""", 0, False
  End If
End If
