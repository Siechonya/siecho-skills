<#
  sync-skills.ps1 - propagate one canonical skill directory into several agents.

  Edit the CONFIG block, then run (powershell.exe ships with every Windows install;
  pwsh works too if PowerShell 7 is available):

    powershell -File scripts/sync-skills.ps1 -DryRun
    powershell -File scripts/sync-skills.ps1

  Safety: this script only creates, repoints, and removes *links*. It never deletes a real
  directory. Entries listed in Keep are treated as that agent's private skills and are left
  completely alone.
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$AllowNonWindows
)

# ------------------------------- CONFIG -------------------------------
$Canonical = Join-Path $HOME '.agents/skills'

# Roots where the WHOLE skills directory should BE a link to $Canonical.
# Use this when the agent has no skills of its own.
$WholeLinkRoots = @(
  # (Join-Path $HOME '.config/opencode/skills')
)

# Roots that hold the canonical skills as PER-SKILL links, plus agent-private entries
# listed in Keep. Use this when the agent ships or keeps its own skills.
$PerSkillRoots = @(
  # @{ Path = (Join-Path $HOME '.codex/skills');  Keep = @('.system') }
  # @{ Path = (Join-Path $HOME '.claude/skills'); Keep = @('cc-web', 'gemini-vision') }
)
# ----------------------------------------------------------------------

if (-not (Test-Path $Canonical)) { throw "canonical skill directory not found: $Canonical" }

if ($env:OS -ne 'Windows_NT' -and -not $AllowNonWindows) {
  throw @"
Junctions are a Windows feature. On macOS/Linux create symlinks yourself, e.g.
  ln -s "$Canonical" <agent-skills-dir>
or use one symlink per skill for agents that also own private skills. The link-management
logic below has not been exercised off Windows.
"@
}

$names = @(Get-ChildItem $Canonical -Directory -Force | Select-Object -ExpandProperty Name | Sort-Object)
Write-Host "canonical : $Canonical"
Write-Host "skills    : $($names.Count)"
Write-Host ("mode      : " + ($(if ($DryRun) { 'DryRun (no changes)' } else { 'APPLY' })))
Write-Host ""

$script:warnings = 0

function Resolve-Link([string]$Link, [string]$Target) {
  # -> created | ok | repointed | warn-real | missing-target | failed
  if (-not (Test-Path $Target)) { return 'missing-target' }

  if (Test-Path $Link) {
    $item = Get-Item $Link -Force
    if ($item.LinkType) {
      $current = (($item.Target | ForEach-Object { $_.ToString().TrimEnd('\') }) -join ';')
      if ($current -eq $Target.TrimEnd('\')) { return 'ok' }
      if ($DryRun) { return 'repointed' }
      [System.IO.Directory]::Delete($Link, $false)
    } else {
      $script:warnings++
      Write-Warning "real directory where a canonical skill is expected; left untouched: $Link"
      return 'warn-real'
    }
  }

  if ($DryRun) { return 'created' }
  $null = cmd /c "mklink /J `"$Link`" `"$Target`"" 2>&1
  if (-not (Test-Path $Link)) {
    $script:warnings++
    Write-Warning "mklink failed: $Link"
    return 'failed'
  }
  return 'created'
}

foreach ($root in $WholeLinkRoots) {
  if (-not (Test-Path $root)) { Write-Host ("[whole]     {0,-46} absent, skipped" -f $root); continue }
  Write-Host ("[whole]     {0,-46} {1}" -f $root, (Resolve-Link $root $Canonical))
}

foreach ($entry in $PerSkillRoots) {
  $root = $entry.Path
  $keep = @($entry.Keep)
  if (-not (Test-Path $root)) {
    if ($DryRun) { Write-Host ("[per-skill] {0,-46} absent (would be created)" -f $root); continue }
    New-Item -ItemType Directory -Path $root -Force | Out-Null
  }

  $changed = 0
  foreach ($name in $names) {
    if ($keep -contains $name) {
      # A canonical skill that shares a name with this entry's private skill: ownership is
      # ambiguous, so touch nothing and let the operator decide. Linking it anyway replaced
      # the private directory.
      $script:warnings++
      Write-Warning "canonical skill '$name' collides with a private Keep entry; left untouched"
      continue
    }
    $action = Resolve-Link (Join-Path $root $name) (Join-Path $Canonical $name)
    if ($action -in 'created', 'repointed') { $changed++ }
  }

  $stale = 0
  foreach ($child in @(Get-ChildItem $root -Force -Directory -ErrorAction SilentlyContinue)) {
    if ($names -contains $child.Name) { continue }
    if ($keep -contains $child.Name) {
      if ($child.LinkType) {
        $script:warnings++
        Write-Warning "private entry should be a real directory but is a link: $($child.FullName)"
      } else {
        Write-Host ("            kept (private): {0}" -f $child.Name)
      }
      continue
    }
    if ($child.LinkType) {
      if (-not $DryRun) { [System.IO.Directory]::Delete($child.FullName, $false) }
      Write-Host ("            stale link removed: {0}" -f $child.Name)
      $stale++
    } else {
      $script:warnings++
      Write-Warning "unexpected real directory (not canonical, not private); left untouched: $($child.FullName)"
    }
  }

  Write-Host ("[per-skill] {0,-46} changed={1} stale={2}" -f $root, $changed, $stale)
}

Write-Host ""
if ($script:warnings -gt 0) {
  Write-Host "finished with $($script:warnings) warning(s)." -ForegroundColor Yellow
} else {
  Write-Host "finished clean." -ForegroundColor Green
}