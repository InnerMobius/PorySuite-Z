'' Launches PorySuite-Z without any visible console/CMD window.
'' Double-click this file instead of the .bat to start the app.
'' Also called by the .bat itself when launched directly, so either way
'' the setup runs invisibly.
Dim sDir
sDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
CreateObject("WScript.Shell").Run Chr(34) & sDir & "LaunchPorySuite.bat" & Chr(34) & " _hidden_", 0, False
