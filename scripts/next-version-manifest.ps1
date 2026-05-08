Set-StrictMode -Version Latest

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

function Get-RequiredManifestValue {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Manifest,

        [Parameter(Mandatory = $true)]
        [string]$FieldName
    )

    $property = $Manifest.PSObject.Properties[$FieldName]
    if ($null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "Manifest must contain a non-empty $FieldName value."
    }

    return [string]$property.Value
}

function Read-NextVersionManifest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,

        [Parameter(Mandatory = $true)]
        [string]$ManifestPath,

        [string]$ExpectedReleaseVersion
    )

    $resolvedRepositoryRoot = Resolve-CanonicalPath -LiteralPath $RepositoryRoot -Label 'Repository root'
    $resolvedManifestPath = Resolve-CanonicalPath -LiteralPath $ManifestPath -Label 'Manifest path'

    Assert-PathInsideRepository -RepositoryRoot $resolvedRepositoryRoot -Path $resolvedManifestPath -Label 'Manifest path'

    $manifest = Read-JsonObject -Path $resolvedManifestPath -Label 'Manifest'
    $releaseVersion = Normalize-Version -InputVersion (Get-RequiredManifestValue -Manifest $manifest -FieldName 'release_version') -Label 'release_version'
    $nextVersion = Normalize-Version -InputVersion (Get-RequiredManifestValue -Manifest $manifest -FieldName 'next_version') -Label 'next_version'

    if ($releaseVersion -eq $nextVersion) {
        throw 'next_version must differ from release_version'
    }

    if (-not [string]::IsNullOrWhiteSpace($ExpectedReleaseVersion)) {
        $normalizedExpectedReleaseVersion = Normalize-Version -InputVersion $ExpectedReleaseVersion -Label 'release_version'
        if ($releaseVersion -ne $normalizedExpectedReleaseVersion) {
            throw "release_version '$releaseVersion' does not match tag version '$normalizedExpectedReleaseVersion'"
        }
    }

    return [pscustomobject]@{
        RepositoryRoot = $resolvedRepositoryRoot
        ManifestPath = $resolvedManifestPath
        ReleaseVersion = $releaseVersion
        NextVersion = $nextVersion
    }
}

function Write-NextVersionRequestArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string]$OutputDirectory,

        [Parameter(Mandatory = $true)]
        [string]$ReleaseVersion,

        [Parameter(Mandatory = $true)]
        [string]$NextVersion
    )

    if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
        throw 'Output directory must not be empty.'
    }

    $resolvedOutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
    $null = New-Item -ItemType Directory -Path $resolvedOutputDirectory -Force

    $artifactPath = Join-Path $resolvedOutputDirectory 'next-version.json'
    $payload = @{
        release_version = $ReleaseVersion
        next_version = $NextVersion
    } | ConvertTo-Json

    Set-Content -LiteralPath $artifactPath -Value ($payload + [Environment]::NewLine) -Encoding UTF8
    return [System.IO.Path]::GetFullPath($artifactPath)
}