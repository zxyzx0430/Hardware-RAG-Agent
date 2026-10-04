param(
    [string]$Python = 'python',
    [string[]]$TestPaths = @('tests'),
    [string]$TempRoot = [IO.Path]::GetTempPath()
)

$ErrorActionPreference = 'Stop'
$taskRepository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskTempRoot = [IO.Path]::GetFullPath($TempRoot)
New-Item -ItemType Directory -Path $taskTempRoot -Force | Out-Null
$taskCopy = Join-Path $taskTempRoot ('hardware-rag-verify-' + [guid]::NewGuid().ToString('N'))
$taskBackend = Join-Path $taskCopy 'backend'
New-Item -ItemType Directory -Path (Join-Path $taskBackend 'data') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $taskCopy 'runtime-temp') -Force | Out-Null

# Copy only tracked and non-ignored source/fixtures, never local credentials or databases.
$taskFiles = @(& git -C $taskRepository ls-files --cached --others --exclude-standard -- backend/app backend/src backend/tests backend/main.py data/test_docs)
if ($LASTEXITCODE -ne 0 -or $taskFiles.Count -eq 0) { throw 'Cannot enumerate repository source files.' }
foreach ($taskFile in ($taskFiles | Sort-Object -Unique)) {
    if ($taskFile -match '(^|/)(__pycache__|data)/' -and $taskFile -notlike 'data/test_docs/*.md') { continue }
    $taskSource = Join-Path $taskRepository $taskFile
    if (-not (Test-Path -LiteralPath $taskSource -PathType Leaf)) { continue }
    $taskDestination = [IO.Path]::GetFullPath((Join-Path $taskCopy $taskFile))
    if (-not $taskDestination.StartsWith($taskCopy + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source path escaped the isolated copy.'
    }
    if ((Get-Item -LiteralPath $taskSource).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing to copy a linked source file.'
    }
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($taskDestination)) -Force | Out-Null
    Copy-Item -LiteralPath $taskSource -Destination $taskDestination
    # Give disposable copies a fresh timestamp so age-based temp cleaners cannot
    # mistake newly copied source for old files. The original stays unchanged.
    (Get-Item -LiteralPath $taskDestination).LastWriteTimeUtc = [DateTime]::UtcNow
}

$taskEnvironment = @{
    SQLITE_DB_PATH = (Join-Path $taskBackend 'data/test.db')
    AGENT_CHECKPOINTER_TYPE = 'memory'
    PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
    HF_HUB_OFFLINE = '1'
    TRANSFORMERS_OFFLINE = '1'
    ANONYMIZED_TELEMETRY = 'False'
    TEMP = (Join-Path $taskCopy 'runtime-temp')
    TMP = (Join-Path $taskCopy 'runtime-temp')
}
$taskPrevious = @{}
foreach ($taskKey in $taskEnvironment.Keys) {
    $taskPrevious[$taskKey] = [Environment]::GetEnvironmentVariable($taskKey, 'Process')
}
$taskExit = 1
Push-Location $taskBackend
try {
    foreach ($taskKey in $taskEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($taskKey, $taskEnvironment[$taskKey], 'Process')
    }
    Write-Output ('Isolated source: ' + $taskCopy)
    # Preload PyArrow to avoid the observed native import-order crash on this Windows host.
    # The base is inside this invocation's fresh copy, never an existing user folder.
    $taskPytestBase = Join-Path $taskCopy 'pytest'
    & $Python -c 'import sys,pyarrow,pytest; raise SystemExit(pytest.main(["-p","pytest_asyncio.plugin","--basetemp",sys.argv[1],*sys.argv[2:],"-q","--tb=short","--maxfail=1","--disable-warnings"]))' $taskPytestBase @TestPaths
    $taskExit = $LASTEXITCODE
}
finally {
    foreach ($taskKey in $taskEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($taskKey, $taskPrevious[$taskKey], 'Process')
    }
    Pop-Location
}
# Preserve the disposable copy for failure diagnosis; never remove source or user data.
exit $taskExit
