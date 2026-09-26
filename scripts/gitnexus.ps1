[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$GitNexusArgs
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$allowedBuildPackages = @(
    '@ladybugdb/core'
    'gitnexus'
    'tree-sitter'
    'tree-sitter-c-sharp'
    'tree-sitter-cpp'
    'tree-sitter-go'
    'tree-sitter-java'
    'tree-sitter-javascript'
    'tree-sitter-php'
    'tree-sitter-python'
    'tree-sitter-ruby'
    'tree-sitter-rust'
    'tree-sitter-typescript'
    '@scarf/scarf'
    'onnxruntime-node'
    'protobufjs'
)

$pnpmArgs = @($allowedBuildPackages | ForEach-Object { "--allow-build=$_" })
$pnpmArgs += @('dlx', 'gitnexus@1.6.12')
if ($GitNexusArgs) {
    $pnpmArgs += $GitNexusArgs
}

$isFtsRepair = $GitNexusArgs.Count -ge 2 -and
    $GitNexusArgs[0] -eq 'analyze' -and
    $GitNexusArgs -contains '--repair-fts'
$hadInstallMode = Test-Path Env:\GITNEXUS_LBUG_EXTENSION_INSTALL
$previousInstallMode = $env:GITNEXUS_LBUG_EXTENSION_INSTALL
if ($isFtsRepair) {
    $env:GITNEXUS_LBUG_EXTENSION_INSTALL = 'auto'
}

Push-Location -LiteralPath $repoRoot
try {
    & pnpm @pnpmArgs
    $exitCode = $LASTEXITCODE
} catch {
    Write-Error "Could not start GitNexus. Verify that pnpm is installed and available on PATH. $_"
    $exitCode = 1
} finally {
    Pop-Location
    if ($isFtsRepair) {
        if ($hadInstallMode) {
            $env:GITNEXUS_LBUG_EXTENSION_INSTALL = $previousInstallMode
        } else {
            Remove-Item Env:\GITNEXUS_LBUG_EXTENSION_INSTALL -ErrorAction SilentlyContinue
        }
    }
}

exit $exitCode
