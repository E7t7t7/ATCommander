param([string]$OutputDirectory = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$sourcePath = Join-Path $PSScriptRoot 'launcher\ATCommanderLauncher.cs'
$compilerCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$compilerPath = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compilerPath) { throw 'The Windows .NET Framework C# compiler was not found.' }
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $resolvedOutput)) { New-Item -ItemType Directory -Path $resolvedOutput | Out-Null }
$targetPath = Join-Path $resolvedOutput 'AT Commander.exe'
& $compilerPath /nologo /target:winexe /platform:anycpu /optimize+ /r:System.Windows.Forms.dll "/out:$targetPath" $sourcePath
if ($LASTEXITCODE -ne 0) { throw 'The native Windows launcher did not compile.' }
Write-Output "Built GUI launcher: $targetPath"
