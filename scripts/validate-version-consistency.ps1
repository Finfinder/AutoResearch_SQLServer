[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$VersionTargetsPath,

    [Parameter(Mandatory = $true)]
    [string]$ReadmeTargetsPath,

    [string]$ExpectedVersion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'version-target-strategies.ps1')

function Resolve-CanonicalPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        throw "$Label does not exist: $LiteralPath"
    }

    $resolvedPath = Resolve-Path -LiteralPath $LiteralPath
    return [System.IO.Path]::GetFullPath($resolvedPath.ProviderPath)
}

function Test-IsChildPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ParentPath,

        [Parameter(Mandatory = $true)]
        [string]$ChildPath
    )

    $normalizedParent = [System.IO.Path]::GetFullPath($ParentPath)
    $normalizedChild = [System.IO.Path]::GetFullPath($ChildPath)

    if ($normalizedParent.Length -gt 3) {
        $normalizedParent = $normalizedParent.TrimEnd('\', '/')
    }

    if ($normalizedChild.Length -gt 3) {
        $normalizedChild = $normalizedChild.TrimEnd('\', '/')
    }

    if ($normalizedChild.Equals($normalizedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $parentWithSeparator = $normalizedParent + [System.IO.Path]::DirectorySeparatorChar
    return $normalizedChild.StartsWith($parentWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-PathInsideRepository {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-IsChildPath -ParentPath $RepositoryRoot -ChildPath $Path)) {
        throw "$Label must be located inside repository root: $Path"
    }
}

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

    return $relativePath
}

function Normalize-Version {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InputVersion,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $match = [System.Text.RegularExpressions.Regex]::Match(
        $InputVersion,
        '^(?:v)?(?<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)$'
    )

    if (-not $match.Success) {
        throw "$Label must be a semver value such as 1.2.3 or v1.2.3. Received: $InputVersion"
    }

    return $match.Groups['version'].Value
}

function Read-JsonObject {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($content)) {
        throw "$Label must not be empty: $Path"
    }

    return $content | ConvertFrom-Json
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

function Read-TargetVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,

        [Parameter(Mandatory = $true)]
        [pscustomobject]$Target,

        [Parameter(Mandatory = $true)]
        [string]$TargetLabel
    )

    if ([string]::IsNullOrWhiteSpace($Target.path)) {
        throw "$TargetLabel path must not be empty."
    }

    if ([string]::IsNullOrWhiteSpace($Target.strategy)) {
        throw "$TargetLabel strategy must not be empty for $($Target.path)."
    }

    $targetPath = Resolve-CanonicalPath -LiteralPath (Join-Path $RepositoryRoot $Target.path) -Label $TargetLabel
    Assert-PathInsideRepository -RepositoryRoot $RepositoryRoot -Path $targetPath -Label $TargetLabel

    $content = Get-Content -LiteralPath $targetPath -Raw -Encoding UTF8
    $rawVersion = Get-TargetVersionFromContent -Content $content -Strategy $Target.strategy

    if ([string]::IsNullOrWhiteSpace($rawVersion)) {
        throw "Could not read version using strategy $($Target.strategy): $($Target.path)"
    }

    return [pscustomobject]@{
        RelativePath = Get-RelativePath -BasePath $RepositoryRoot -TargetPath $targetPath
        Strategy = $Target.strategy
        Version = Normalize-Version -InputVersion $rawVersion -Label $Target.path
    }
}

function Assert-TargetCollectionNotEmpty {
    param(
        [object[]]$Targets,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if ($null -eq $Targets -or @($Targets).Count -eq 0) {
        throw "$Label must not be empty."
    }
}

function Assert-AllVersionsMatch {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Targets,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedVersion
    )

    foreach ($target in $Targets) {
        if ($target.Version -ne $ExpectedVersion) {
            throw "Inconsistent version in $($target.RelativePath): expected $ExpectedVersion, found $($target.Version)"
        }
    }
}

$resolvedRepositoryRoot = Resolve-CanonicalPath -LiteralPath $RepositoryRoot -Label 'Repository root'
$resolvedVersionTargetsPath = Resolve-CanonicalPath -LiteralPath $VersionTargetsPath -Label 'Version targets path'
$resolvedReadmeTargetsPath = Resolve-CanonicalPath -LiteralPath $ReadmeTargetsPath -Label 'Readme targets path'

Assert-PathInsideRepository -RepositoryRoot $resolvedRepositoryRoot -Path $resolvedVersionTargetsPath -Label 'Version targets path'
Assert-PathInsideRepository -RepositoryRoot $resolvedRepositoryRoot -Path $resolvedReadmeTargetsPath -Label 'Readme targets path'

$versionTargets = @(Read-JsonArray -Path $resolvedVersionTargetsPath -Label 'Version targets')
$readmeTargets = @(Read-JsonArray -Path $resolvedReadmeTargetsPath -Label 'Readme targets')

Assert-TargetCollectionNotEmpty -Targets $versionTargets -Label 'Version targets'
Assert-TargetCollectionNotEmpty -Targets $readmeTargets -Label 'Readme targets'

$resolvedVersionTargets = @($versionTargets | ForEach-Object {
    Read-TargetVersion -RepositoryRoot $resolvedRepositoryRoot -Target $_ -TargetLabel 'Target file'
})
$resolvedReadmeTargets = @($readmeTargets | ForEach-Object {
    Read-TargetVersion -RepositoryRoot $resolvedRepositoryRoot -Target $_ -TargetLabel 'Target file'
})

$canonicalVersion = $resolvedVersionTargets[0].Version
Assert-AllVersionsMatch -Targets $resolvedVersionTargets -ExpectedVersion $canonicalVersion
Assert-AllVersionsMatch -Targets $resolvedReadmeTargets -ExpectedVersion $canonicalVersion

if (-not [string]::IsNullOrWhiteSpace($ExpectedVersion)) {
    $normalizedExpectedVersion = Normalize-Version -InputVersion $ExpectedVersion -Label 'expected_version'
    if ($canonicalVersion -ne $normalizedExpectedVersion) {
        throw "Inconsistent version in expected_version: expected $normalizedExpectedVersion, found $canonicalVersion"
    }
}

return [pscustomobject]@{
    Version = $canonicalVersion
    VersionTargets = $resolvedVersionTargets
    ReadmeTargets = $resolvedReadmeTargets
    ValidatedTargetCount = $resolvedVersionTargets.Count + $resolvedReadmeTargets.Count
}