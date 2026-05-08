[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ExpectedReleaseVersion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSCommandPath) 'next-version-manifest.ps1')

$validatedManifest = Read-NextVersionManifest `
    -RepositoryRoot $RepositoryRoot `
    -ManifestPath $ManifestPath `
    -ExpectedReleaseVersion $ExpectedReleaseVersion

$artifactPath = Write-NextVersionRequestArtifact `
    -OutputDirectory $OutputDirectory `
    -ReleaseVersion $validatedManifest.ReleaseVersion `
    -NextVersion $validatedManifest.NextVersion

return [pscustomobject]@{
    RepositoryRoot = $validatedManifest.RepositoryRoot
    ManifestPath = $validatedManifest.ManifestPath
    ArtifactPath = $artifactPath
    ReleaseVersion = $validatedManifest.ReleaseVersion
    NextVersion = $validatedManifest.NextVersion
}