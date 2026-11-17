Option Explicit
Dim shell, fso, folder, command, pathFile, stream, python, pythonw
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
pathFile = folder & "\python-path.txt"
python = ""
If fso.FileExists(pathFile) And fso.FileExists(folder & "\startup_bootstrap.py") Then
    On Error Resume Next
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2
    stream.Charset = "utf-8"
    stream.Open
    stream.LoadFromFile pathFile
    python = Trim(Replace(Replace(stream.ReadText, vbCr, ""), vbLf, ""))
    stream.Close
    If Len(python) > 0 Then
        If Not fso.FileExists(python) Then
            Set stream = fso.OpenTextFile(pathFile, 1, False, -2)
            python = Trim(stream.ReadLine)
            stream.Close
        End If
    End If
    On Error GoTo 0
End If
If Len(python) > 0 And InStr(LCase(python), "\.dsh\dsh-runtimes\") = 0 Then
    If fso.FileExists(python) Then
        pythonw = fso.BuildPath(fso.GetParentFolderName(python), "pythonw.exe")
        If Not fso.FileExists(pythonw) Then pythonw = python
        command = """" & pythonw & """ """ & folder & "\startup_bootstrap.py"""
        shell.Run command, 0, False
        WScript.Quit 0
    End If
End If
command = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & folder & "\launch.ps1"""
shell.Run command, 0, False
