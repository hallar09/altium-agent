# SPDX-License-Identifier: Apache-2.0
# Install Altium Agent into .venv in this checkout and register the AltiumAgent
# MCP server for Claude Code (user scope). Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File <checkout>\scripts\install_windows.ps1
# It backs up %USERPROFILE%\.claude.json before changing it, and changes only
# the "AltiumAgent" entry under mcpServers. Other servers are left alone.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "Altium Agent checkout: $Root"

# 1. Python 3.11+
$py = $null
foreach ($v in @("3.13", "3.12", "3.11")) {
    try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } } catch {}
}
if (-not $py) {
    try {
        $ok = & python -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
        if ($ok -eq "True") { $py = @("python") }
    } catch {}
}
if (-not $py) { throw "Python 3.11 or newer was not found. Install it from python.org (tick 'Add to PATH') and run this again." }
Write-Host "Python: $($py -join ' ')"

# 2. .venv and the package
$PyArgs = @()
if ($py.Length -gt 1) { $PyArgs = $py[1..($py.Length - 1)] }
if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
    & $py[0] @PyArgs -m venv "$Root\.venv"
    if ($LASTEXITCODE -ne 0) { throw "Creating .venv failed." }
}
$VPy = "$Root\.venv\Scripts\python.exe"
& $VPy -m pip install --upgrade pip
& $VPy -m pip install -e "$Root"
if ($LASTEXITCODE -ne 0) { throw "pip install failed; see the messages above." }
$Exe = "$Root\.venv\Scripts\altium-agent.exe"
if (-not (Test-Path $Exe)) { throw "Install finished but $Exe is missing." }

# Native commands below may print to stderr; that must not abort the script.
$ErrorActionPreference = "Continue"

# 3. The Altium side first: copy and stamp the script project to %USERPROFILE%\Altium Agent\scripts
& $Exe install-scripts --force
if ($LASTEXITCODE -ne 0) { throw "install-scripts failed; see the messages above." }
$Scripts = Join-Path $env:USERPROFILE "Altium Agent\scripts"
if (-not (Test-Path "$Scripts\AltiumAgent.PrjScr")) { throw "install-scripts ran but $Scripts\AltiumAgent.PrjScr is missing." }
Write-Host "Altium scripts installed in $Scripts"

# 4. Register the MCP server (user scope), with a backup of .claude.json.
# Done by editing the JSON directly (only the AltiumAgent entry), which works
# whether or not the claude command is on PATH.
$Cfg = Join-Path $env:USERPROFILE ".claude.json"
if (Test-Path $Cfg) {
    $Bak = "$Cfg.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Copy-Item $Cfg $Bak
    Write-Host "Backed up $Cfg to $Bak"
}
& $VPy -c @"
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
data = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
data.setdefault('mcpServers', {})['AltiumAgent'] = {'type': 'stdio', 'command': sys.argv[2], 'args': ['--toolset', 'core'], 'env': {}}
p.write_text(json.dumps(data, indent=2), encoding='utf-8')
print('Registered AltiumAgent in', p)
"@ $Cfg $Exe
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Registering in $Cfg failed. The Altium scripts are installed; register by hand with:"
    Write-Warning "  claude mcp add --scope user AltiumAgent -- `"$Exe`" --toolset core"
}

Write-Host ""
Write-Host "In Altium: stop and close any other script project (e.g. eda-agent's Altium_API.PrjScr), then"
Write-Host "File > Run Script... > browse to $Scripts\AltiumAgent.PrjScr"
Write-Host "> Dispatcher.pas > StartMCPServer > Run. The window is titled 'Altium Agent'."

# 5. Check
Write-Host ""
Write-Host "Doctor (needs Altium running with the AltiumAgent script started to pass fully):"
& $Exe doctor
Write-Host ""
Write-Host "Done. Restart Claude Code so it loads the AltiumAgent server."
Write-Host "While testing, turn off any other Altium MCP server (two bridges collide)."
