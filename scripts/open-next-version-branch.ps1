[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,

    [Parameter(Mandatory = $true)]
    [string]$VersionTargetsPath,

    [Parameter(Mandatory = $true)]
    [string]$ReadmeTargetsPath,

    [string]$BaseBranch = 'main'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'version-target-strategies.ps1')
. (Join-Path (Split-Path -Parent $PSCommandPath) 'next-version-manifest.ps1')

function Get-RelativePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BasePath,

        [Parameter(Mandatory = $true)]
        [string]$TargetPath
    )

    $normalizedBasePath = [System.IO.Path]::GetFullPath($BasePath)
    if (-not $normalizedBasePath.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $normalizedBasePath += [System.IO.Path]::DirectorySeparatorChar
    }

    $baseUri = New-Object System.Uri($normalizedBasePath)
    $targetUri = New-Object System.Uri([System.IO.Path]::GetFullPath($TargetPath))
    $relativePath = [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString())

    return $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [switch]$AllowFailure
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'git'
    $startInfo.WorkingDirectory = $RepositoryRoot
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true
    $startInfo.Arguments = [string]::Join(' ', ($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') {
            $escapedArgument = $_ -replace '(\\*)"', '$1$1\\"'
            $escapedArgument = $escapedArgument -replace '(\\+)$', '$1$1'
            '"' + $escapedArgument + '"'
        }
        else {
            $_
        }
    }))

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $null = $process.Start()
    $standardOutput = $process.StandardOutput.ReadToEnd()
    $standardError = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    $exitCode = $process.ExitCode

    $output = @()
    if (-not [string]::IsNullOrWhiteSpace($standardOutput)) {
        $output += ($standardOutput -split "`r?`n" | Where-Object { $_ -ne '' })
    }
    if (-not [string]::IsNullOrWhiteSpace($standardError)) {
        $output += ($standardError -split "`r?`n" | Where-Object { $_ -ne '' })
    }

    if ((-not $AllowFailure) -and $exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = @($output)
    }
}

function Read-JsonArray {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $parsedValue = Read-JsonObject -Path $Path -Label $Label
    if ($null -eq $parsedValue) {
        Write-Output -NoEnumerate -InputObject @()
        return
    }

    Write-Output -NoEnumerate -InputObject @($parsedValue)
}

function Update-TargetFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,

        [Parameter(Mandatory = $true)]
        [pscustomobject]$Target,

        [Parameter(Mandatory = $true)]
        [string]$NextVersion
    )

    if ([string]::IsNullOrWhiteSpace($Target.path)) {
        throw 'Target path must not be empty.'
    }

    if ([string]::IsNullOrWhiteSpace($Target.strategy)) {
        throw "Target strategy must not be empty for $($Target.path)."
    }

    $targetPath = Resolve-CanonicalPath -LiteralPath (Join-Path $RepositoryRoot $Target.path) -Label 'Target file'
    Assert-PathInsideRepository -RepositoryRoot $RepositoryRoot -Path $targetPath -Label 'Target file'

    $originalContent = Get-Content -LiteralPath $targetPath -Raw -Encoding UTF8
    $updateResult = Update-TargetContentByStrategy -Content $originalContent -Strategy $Target.strategy -NextVersion $NextVersion

    if (-not $updateResult.IsMatch) {
        throw "Unable to update target using strategy $($Target.strategy): $($Target.path)"
    }

    if ($updateResult.Content -ne $originalContent) {
        $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($targetPath, $updateResult.Content, $utf8NoBom)
        return [pscustomobject]@{
            Changed = $true
            RelativePath = Get-RelativePath -BasePath $RepositoryRoot -TargetPath $targetPath
        }
    }

    return [pscustomobject]@{
        Changed = $false
        RelativePath = Get-RelativePath -BasePath $RepositoryRoot -TargetPath $targetPath
    }
}

$validatedManifest = Read-NextVersionManifest -RepositoryRoot $RepositoryRoot -ManifestPath $ManifestPath

$resolvedRepositoryRoot = $validatedManifest.RepositoryRoot
$resolvedVersionTargetsPath = Resolve-CanonicalPath -LiteralPath $VersionTargetsPath -Label 'Version targets path'
$resolvedReadmeTargetsPath = Resolve-CanonicalPath -LiteralPath $ReadmeTargetsPath -Label 'Readme targets path'

Assert-PathInsideRepository -RepositoryRoot $resolvedRepositoryRoot -Path $resolvedVersionTargetsPath -Label 'Version targets path'
Assert-PathInsideRepository -RepositoryRoot $resolvedRepositoryRoot -Path $resolvedReadmeTargetsPath -Label 'Readme targets path'

$versionTargets = Read-JsonArray -Path $resolvedVersionTargetsPath -Label 'Version targets'
$readmeTargets = Read-JsonArray -Path $resolvedReadmeTargetsPath -Label 'Readme targets'

$releaseVersion = $validatedManifest.ReleaseVersion
$nextVersion = $validatedManifest.NextVersion

$branchExistsLocally = (Invoke-Git -RepositoryRoot $resolvedRepositoryRoot -Arguments @('show-ref', '--verify', '--quiet', "refs/heads/$nextVersion") -AllowFailure).ExitCode -eq 0
$originRemotes = (Invoke-Git -RepositoryRoot $resolvedRepositoryRoot -Arguments @('remote')).Output | ForEach-Object { $_.ToString().Trim() } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
$originExists = @($originRemotes) -contains 'origin'
$branchExistsRemotely = $false

if ($originExists) {
    $remoteLookup = Invoke-Git -RepositoryRoot $resolvedRepositoryRoot -Arguments @('ls-remote', '--heads', 'origin', $nextVersion) -AllowFailure
    $branchExistsRemotely = $remoteLookup.ExitCode -eq 0 -and $remoteLookup.Output.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace(($remoteLookup.Output -join ''))
}

if ($branchExistsLocally -or $branchExistsRemotely) {
    return [pscustomobject]@{
        Status = 'skipped_existing_branch'
        ReleaseVersion = $releaseVersion
        NextVersion = $nextVersion
        BranchName = $nextVersion
        CommitCreated = $false
        UpdatedFiles = @()
    }
}

Invoke-Git -RepositoryRoot $resolvedRepositoryRoot -Arguments @('checkout', $BaseBranch) | Out-Null
Invoke-Git -RepositoryRoot $resolvedRepositoryRoot -Arguments @('checkout', '-b', $nextVersion) | Out-Null

$updatedFiles = New-Object System.Collections.Generic.List[string]

foreach ($target in (@($versionTargets) + @($readmeTargets))) {
    $updateResult = Update-TargetFile -RepositoryRoot $resolvedRepositoryRoot -Target $target -NextVersion $nextVersion
    if ($updateResult.Changed) {
        $updatedFiles.Add($updateResult.RelativePath)
    }
}

$commitCreated = $false
$commitMessage = "Start $nextVersion cycle after $releaseVersion release"

if ($updatedFiles.Count -gt 0) {
    Invoke-Git -RepositoryRoot $resolvedRepositoryRoot -Arguments (@('add', '--') + $updatedFiles.ToArray()) | Out-Null
    Invoke-Git -RepositoryRoot $resolvedRepositoryRoot -Arguments @('commit', '-m', $commitMessage) | Out-Null
    $commitCreated = $true
}

$headSha = (Invoke-Git -RepositoryRoot $resolvedRepositoryRoot -Arguments @('rev-parse', 'HEAD')).Output[-1].Trim()

return [pscustomobject]@{
    Status = if ($commitCreated) { 'updated' } else { 'skipped_no_changes' }
    ReleaseVersion = $releaseVersion
    NextVersion = $nextVersion
    BranchName = $nextVersion
    CommitCreated = $commitCreated
    CommitMessage = if ($commitCreated) { $commitMessage } else { $null }
    HeadSha = $headSha
    UpdatedFiles = $updatedFiles.ToArray()
}