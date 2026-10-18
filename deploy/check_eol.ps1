# Line-ending guard for the shell scripts that get uploaded to the server.
#
# Why this exists: a CRLF shell script is silently fatal.  The kernel reads
# "#!/usr/bin/env bash" plus a trailing CR as an interpreter named "bash\r",
# fails to find it, and falls back to /bin/sh (dash) - where "set -o pipefail"
# aborts with "set: pipefail: invalid option name".  The deploy then dies
# before doing anything, and the error is easy to misread as a server problem.
#
# Windows editors and some PowerShell rewrites love CRLF, so check bytes here.
#
#   .\deploy\check_eol.ps1              # checks deploy\ next to this script
#   .\deploy\check_eol.ps1 -Dir <path>  # checks another directory of .sh files
#
# Exits 0 when every .sh is LF-only, BOM-free and newline-terminated,
# exits 1 with one line per problem otherwise.  Pure ASCII on purpose:
# Windows PowerShell 5.1 decodes a BOM-less .ps1 as ANSI.
param(
    [string]$Dir
)

$ErrorActionPreference = "Stop"
if (-not $Dir) { $Dir = $PSScriptRoot }
$Dir = (Resolve-Path -LiteralPath $Dir).Path

$scripts = @(Get-ChildItem -LiteralPath $Dir -Filter "*.sh" -File | Sort-Object Name)
if ($scripts.Count -eq 0) {
    Write-Output "eol check: no .sh files under $Dir"
    exit 0
}

$problems = @()
foreach ($file in $scripts) {
    $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
    if ($bytes.Length -eq 0) {
        $problems += ("empty file: " + $file.Name)
        continue
    }
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) {
        $problems += ("UTF-8 BOM in " + $file.Name)
    }
    $cr = 0
    foreach ($value in $bytes) {
        if ($value -eq 13) { $cr++ }
    }
    if ($cr -gt 0) {
        $problems += ("CR bytes in " + $file.Name + " (x" + $cr + ") - rewrite it with LF line endings")
    }
    if ($bytes[$bytes.Length - 1] -ne 10) {
        $problems += ("missing final newline in " + $file.Name)
    }
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 35 -and $bytes[1] -eq 33) {
        # Shebang present: the interpreter path must not carry CR (covered above)
        # and should be one of the shells the server actually has.
        $firstLine = ""
        foreach ($value in $bytes) {
            if ($value -eq 10) { break }
            if ($value -ne 13) { $firstLine += [char]$value }
        }
        if ($firstLine -notmatch "^#!\s*/(usr/)?bin/(env\s+)?(ba)?sh") {
            $problems += ("unexpected shebang in " + $file.Name + ": " + $firstLine)
        }
    }
}

if ($problems.Count -gt 0) {
    Write-Output ("eol check FAILED: " + $problems.Count + " problem(s), do not upload these scripts")
    foreach ($item in $problems) { Write-Output ("  - " + $item) }
    exit 1
}
Write-Output ("eol check OK: " + $scripts.Count + " shell scripts are LF-only, BOM-free and newline-terminated")
exit 0
