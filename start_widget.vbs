' Launches the widget via pythonw.exe so no console window appears.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir
shell.Run "pythonw """ & scriptDir & "\widget.py""", 0, False
