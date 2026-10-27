param([switch]$Refresh)
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$workspaceRoot = Split-Path -Parent $sourceRoot
$releaseRoot = Join-Path $workspaceRoot 'ATCommander_1.7.26'
$archivePath = Join-Path $workspaceRoot 'ATCommander_1.7.26.zip'
if (-not $Refresh -and ((Test-Path -LiteralPath $releaseRoot) -or (Test-Path -LiteralPath $archivePath))) {
    throw 'Release target already exists; preserve it instead of replacing it.'
}
$rootFiles = @('app.py','bootstrap.pyw','AT Commander.exe','Start_AT_Commander.vbs','run.bat',
    'Start_CM_Discord_Bot.bat','requirements.txt','requirements-discord.txt','DISCORD_CM_BOT_SETUP.md',
    'README.md','COMMAND_REFERENCE.md','BENDER_PARITY.md','build_launcher.ps1','build_release_1.7.26.ps1',
    'GITHUB_UPDATES.md','commander-update.json','.gitignore','tools\make_update_manifest.py')
$rootFiles += @(Get-ChildItem -LiteralPath $sourceRoot -File | Where-Object {$_.Name -match '^README_1\.7(?:\.\d+)?_CHANGES\.md$'} | ForEach-Object {$_.Name})
$files = @($rootFiles)
foreach ($folder in @('atcommander','tests')) {
    $files += @(Get-ChildItem -LiteralPath (Join-Path $sourceRoot $folder) -File -Filter '*.py' | ForEach-Object {Join-Path $folder $_.Name})
}
$files += @(Get-ChildItem -LiteralPath (Join-Path $sourceRoot 'launcher') -File -Filter '*.cs' | ForEach-Object {Join-Path 'launcher' $_.Name})
$files += @('tests\fixtures\public_combat_signing.json','tests\fixtures\public_first_free_mom.json')
$files += @('tests\native_research_ui_fixture.js')
Add-Type -AssemblyName System.IO.Compression.FileSystem
$candidateArchive = $archivePath + '.new.zip'
if (Test-Path -LiteralPath $candidateArchive) {throw 'An unverified candidate ZIP already exists; preserve it for inspection.'}
$writer = [IO.Compression.ZipFile]::Open($candidateArchive,[IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($relative in $files) {
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($writer,(Join-Path $sourceRoot $relative),
            ('ATCommander_1.7.26/'+$relative.Replace('\','/')),[IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally {$writer.Dispose()}
$archive = [IO.Compression.ZipFile]::OpenRead($candidateArchive)
try {
    $expected = @($files | ForEach-Object {'ATCommander_1.7.26/'+$_.Replace('\','/')})
    $actual = @($archive.Entries | Where-Object {$_.Length -gt 0} | ForEach-Object {$_.FullName.Replace('\','/')})
    if (@(Compare-Object ($expected | Sort-Object) ($actual | Sort-Object)).Count) {throw 'ZIP content differs from the public-file allowlist.'}
    foreach ($entry in $archive.Entries | Where-Object {$_.Length -gt 0}) {
        $relative = $entry.FullName.Replace('\','/').Substring('ATCommander_1.7.26/'.Length)
        $sourceHash = (Get-FileHash -LiteralPath (Join-Path $sourceRoot $relative) -Algorithm SHA256).Hash
        $hasher = [Security.Cryptography.SHA256]::Create(); $stream = $entry.Open()
        try {$archiveHash = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-','')}
        finally {$stream.Dispose();$hasher.Dispose()}
        if ($sourceHash -ne $archiveHash) {throw 'A ZIP entry differs from the current working source.'}
    }
    Write-Output "Verified $($actual.Count) public files with matching source hashes; runtime/private folders excluded."
} finally {$archive.Dispose()}
foreach ($relative in $files) {
    $target = [IO.Path]::GetFullPath((Join-Path $releaseRoot $relative))
    if (-not $target.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'A release destination escaped the expected release folder.'
    }
    $parent = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $parent)) {New-Item -ItemType Directory -Path $parent | Out-Null}
    $sourceFile = [IO.Path]::GetFullPath((Join-Path $sourceRoot $relative))
    if (-not $sourceFile.Equals($target,[StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -LiteralPath $sourceFile -Destination $target
    }
}
if (Test-Path -LiteralPath $archivePath) {
    $previousArchive = Join-Path $workspaceRoot ('ATCommander_1.7.26_previous_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.zip')
    Move-Item -LiteralPath $archivePath -Destination $previousArchive
}
Move-Item -LiteralPath $candidateArchive -Destination $archivePath
Write-Output "Archive: $archivePath"

