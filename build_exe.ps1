param(
    [string]$PythonExecutable = (Join-Path $PSScriptRoot '.build-venv/Scripts/python.exe'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist')
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw 'Create the isolated build environment first; see README.md.'
}
& $PythonExecutable (Join-Path $PSScriptRoot 'build_exe.py') --output $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw 'Single-file build failed' }
