function Invoke-TargetRegexReplace {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Content,

        [Parameter(Mandatory = $true)]
        [string]$Pattern,

        [Parameter(Mandatory = $true)]
        [string]$Replacement
    )

    $regex = [System.Text.RegularExpressions.Regex]::new($Pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    $updatedContent = $regex.Replace($Content, $Replacement, 1)

    return [pscustomobject]@{
        IsMatch = $regex.IsMatch($Content)
        Content = $updatedContent
    }
}

function Get-SupportedVersionTargetStrategies {
    return @(
        'python_dunder_version',
        'pyproject_project_version',
        'npm_package_version',
        'dotnet_project_version',
        'plain_version',
        'readme_badge'
    )
}

function Assert-VersionTargetStrategySupported {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Strategy
    )

    if ((Get-SupportedVersionTargetStrategies) -notcontains $Strategy) {
        throw "Unsupported target strategy: $Strategy"
    }
}

function Update-TargetContentByStrategy {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Content,

        [Parameter(Mandatory = $true)]
        [string]$Strategy,

        [Parameter(Mandatory = $true)]
        [string]$NextVersion
    )

    Assert-VersionTargetStrategySupported -Strategy $Strategy

    switch ($Strategy) {
        'python_dunder_version' {
            return Invoke-TargetRegexReplace -Content $Content -Pattern '(__version__\s*=\s*["''])([^"'']+)(["''])' -Replacement ('${1}' + $NextVersion + '${3}')
        }
        'pyproject_project_version' {
            return Invoke-TargetRegexReplace -Content $Content -Pattern '(?s)(^\[project\].*?^version\s*=\s*")([^"]+)(")' -Replacement ('${1}' + $NextVersion + '${3}')
        }
        'npm_package_version' {
            return Invoke-TargetRegexReplace -Content $Content -Pattern '("version"\s*:\s*")([^"]+)(")' -Replacement ('${1}' + $NextVersion + '${3}')
        }
        'dotnet_project_version' {
            return Invoke-TargetRegexReplace -Content $Content -Pattern '(<Version>)([^<]+)(</Version>)' -Replacement ('${1}' + $NextVersion + '${3}')
        }
        'plain_version' {
            return [pscustomobject]@{
                IsMatch = $true
                Content = $NextVersion + [Environment]::NewLine
            }
        }
        'readme_badge' {
            return Invoke-TargetRegexReplace -Content $Content -Pattern 'version-(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?-green' -Replacement ('version-' + $NextVersion + '-green')
        }
    }
}

function Get-TargetVersionFromContent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Content,

        [Parameter(Mandatory = $true)]
        [string]$Strategy
    )

    Assert-VersionTargetStrategySupported -Strategy $Strategy

    switch ($Strategy) {
        'python_dunder_version' {
            $match = [System.Text.RegularExpressions.Regex]::Match($Content, '__version__\s*=\s*["''](?<version>[^"'']+)["'']', [System.Text.RegularExpressions.RegexOptions]::Multiline)
            break
        }
        'pyproject_project_version' {
            $match = [System.Text.RegularExpressions.Regex]::Match($Content, '(?s)^\[project\].*?^version\s*=\s*"(?<version>[^"]+)"', [System.Text.RegularExpressions.RegexOptions]::Multiline)
            break
        }
        'npm_package_version' {
            $match = [System.Text.RegularExpressions.Regex]::Match($Content, '"version"\s*:\s*"(?<version>[^"]+)"', [System.Text.RegularExpressions.RegexOptions]::Multiline)
            break
        }
        'dotnet_project_version' {
            $match = [System.Text.RegularExpressions.Regex]::Match($Content, '<Version>(?<version>[^<]+)</Version>', [System.Text.RegularExpressions.RegexOptions]::Multiline)
            break
        }
        'plain_version' {
            $trimmedContent = $Content.Trim()
            if ([string]::IsNullOrWhiteSpace($trimmedContent)) {
                return $null
            }

            return $trimmedContent
        }
        'readme_badge' {
            $match = [System.Text.RegularExpressions.Regex]::Match(
                $Content,
                'version-(?:v)?(?<version>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)-green',
                [System.Text.RegularExpressions.RegexOptions]::Multiline
            )
            break
        }
    }

    if (-not $match.Success) {
        return $null
    }

    return $match.Groups['version'].Value
}