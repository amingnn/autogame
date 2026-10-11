Option Explicit

Dim shell, projectDir, pythonExe, scriptPath, command

projectDir = "D:\project\autogame"
pythonExe = projectDir & "\.venv\Scripts\pythonw.exe"
scriptPath = projectDir & "\main.py"
command = """" & pythonExe & """ """ & scriptPath & """"

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = projectDir
shell.Run command, 0, False
Set shell = Nothing
