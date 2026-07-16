' PoGo Stats launcher.
'
' Double-click this to start everything. Unlike a .bat file (which always
' flashes a console window briefly, even if it just launches something else
' invisibly - cmd.exe needs a console host to run at all), a .vbs script
' runs via wscript.exe, which has no console of its own - so this produces
' zero window flash. If the venv/.env aren't set up yet, you get a normal
' Windows message box explaining what's missing instead.
'
' All this does is check that .venv and .env exist, then hand off to
' launcher.py (run via the venv's own pythonw.exe, so it has access to the
' packages from requirements.txt) completely hidden.

Dim fso, shell, scriptDir, pythonwPath, launcherPath

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = scriptDir & "\.venv\Scripts\pythonw.exe"
launcherPath = scriptDir & "\launcher.py"

If Not fso.FileExists(pythonwPath) Then
    MsgBox "Virtual environment not found (.venv). Please set it up first:" & vbCrLf & vbCrLf & _
           "  python -m venv .venv" & vbCrLf & _
           "  .venv\Scripts\activate" & vbCrLf & _
           "  pip install -r requirements.txt", vbCritical, "PoGo Stats"
    WScript.Quit 1
End If

If Not fso.FileExists(scriptDir & "\.env") Then
    MsgBox "No .env file found. Please set DISCORD_TOKEN and CHANNEL_ID first " & _
           "(copy .env.example to .env and fill it in).", vbCritical, "PoGo Stats"
    WScript.Quit 1
End If

' 0 = hidden window style, False = don't wait for it to exit.
shell.Run """" & pythonwPath & """ """ & launcherPath & """", 0, False
