[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$ConfigPath = (Join-Path (Split-Path -Parent $PSScriptRoot) '.github/gh-sync.json')
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$mode = if ($Apply) { 'apply' } else { 'dry-run' }

function Write-Log {
    param([string]$Message)

    Write-Host "[sync-github-meta] $Message"
}

function Invoke-GhText {
    param([string[]]$Arguments)

    $output = & gh @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $rendered = ($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        throw "gh command failed: gh $($Arguments -join ' ')$([Environment]::NewLine)$rendered"
    }

    return (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
}

function Invoke-GhJson {
    param([string[]]$Arguments)

    $text = Invoke-GhText -Arguments $Arguments
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }

    return $text | ConvertFrom-Json -Depth 100
}

function Invoke-GhReadyCheck {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI ('gh') was not found in PATH."
    }

    & gh --version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI ('gh') could not be started."
    }

    & gh auth status *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI is not authenticated. Run 'gh auth login' first."
    }
}

function Invoke-Step {
    param(
        [string]$Message,
        [scriptblock]$Action
    )

    if ($Apply) {
        Write-Log "APPLY: $Message"
        & $Action
        return
    }

    Write-Log "DRY-RUN: $Message"
}

function Get-SyncConfig {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "Config file not found: $Path"
    }

    $config = Get-Content -Raw -Path $Path | ConvertFrom-Json -Depth 100
    if (-not $config.repo.slug) {
        throw "Missing repo.slug in $Path"
    }
    if (-not $config.features) {
        throw "Missing features section in $Path"
    }
    if (-not $config.labels) {
        throw "Missing labels section in $Path"
    }
    if (-not $config.roadmapIssue) {
        throw "Missing roadmapIssue section in $Path"
    }

    return $config
}

function Get-ExistingLabels {
    param([string]$Slug)

    $labels = Invoke-GhJson @('api', "repos/$Slug/labels?per_page=100")
    $map = @{}
    if ($labels) {
        foreach ($label in $labels) {
            $map[[string]$label.name] = $label
        }
    }

    return $map
}

function Sync-Features {
    param($Config)

    $values = @{
        has_issues = [bool]$Config.features.issues
        has_projects = [bool]$Config.features.projects
        has_wiki = [bool]$Config.features.wiki
        has_discussions = [bool]$Config.features.discussions
    }

    $requestParts = @('api', '--method', 'PATCH', "repos/$($Config.repo.slug)")
    foreach ($key in $values.Keys) {
        $requestParts += @('-f', "$key=$([string]$values[$key].ToString().ToLowerInvariant())")
    }

    Invoke-Step "sync repository feature flags" { Invoke-GhText -Arguments $requestParts | Out-Null }
}

function Sync-Labels {
    param($Config)

    $existingLabels = Get-ExistingLabels -Slug $Config.repo.slug

    foreach ($label in $Config.labels) {
        $name = [string]$label.name
        $color = [string]$label.color
        $description = [string]$label.description

        if ([string]::IsNullOrWhiteSpace($name) -or [string]::IsNullOrWhiteSpace($color)) {
            throw "Every label requires name and color."
        }

        if ($existingLabels.ContainsKey($name)) {
            $encodedName = [uri]::EscapeDataString($name)
            $requestParts = @(
                'api', '--method', 'PATCH', "repos/$($Config.repo.slug)/labels/$encodedName",
                '-f', "new_name=$name",
                '-f', "color=$color",
                '-f', "description=$description"
            )
            Invoke-Step "update label '$name'" { Invoke-GhText -Arguments $requestParts | Out-Null }
        }
        else {
            $requestParts = @(
                'api', '--method', 'POST', "repos/$($Config.repo.slug)/labels",
                '-f', "name=$name",
                '-f', "color=$color",
                '-f', "description=$description"
            )
            Invoke-Step "create label '$name'" { Invoke-GhText -Arguments $requestParts | Out-Null }
        }
    }
}

function Sync-Milestones {
    param($Config)

    if (-not ($Config.PSObject.Properties.Name -contains 'milestones')) {
        return
    }
    if (-not $Config.milestones) {
        return
    }

    $existingMilestones = Invoke-GhJson @('api', "repos/$($Config.repo.slug)/milestones?state=all&per_page=100")
    $existingByTitle = @{}
    if ($existingMilestones) {
        foreach ($milestone in $existingMilestones) {
            $existingByTitle[[string]$milestone.title] = $milestone
        }
    }

    foreach ($milestone in $Config.milestones) {
        $title = [string]$milestone.title
        if ([string]::IsNullOrWhiteSpace($title)) {
            continue
        }

        $description = [string]$milestone.description
        $state = if ([string]::IsNullOrWhiteSpace([string]$milestone.state)) { 'open' } else { [string]$milestone.state }

        if ($existingByTitle.ContainsKey($title)) {
            $number = [int]$existingByTitle[$title].number
            $requestParts = @(
                'api', '--method', 'PATCH', "repos/$($Config.repo.slug)/milestones/$number",
                '-f', "title=$title",
                '-f', "description=$description",
                '-f', "state=$state"
            )
            Invoke-Step "update milestone '$title'" { Invoke-GhText -Arguments $requestParts | Out-Null }
        }
        else {
            $requestParts = @(
                'api', '--method', 'POST', "repos/$($Config.repo.slug)/milestones",
                '-f', "title=$title",
                '-f', "description=$description",
                '-f', "state=$state"
            )
            Invoke-Step "create milestone '$title'" { Invoke-GhText -Arguments $requestParts | Out-Null }
        }
    }
}

function Find-IssueByTitle {
    param(
        [string]$Slug,
        [string]$Title
    )

    $issues = Invoke-GhJson @('issue', 'list', '--repo', $Slug, '--state', 'all', '--search', $Title, '--json', 'number,title,id')
    if (-not $issues) {
        return $null
    }

    return $issues | Where-Object { $_.title -eq $Title } | Select-Object -First 1
}

function Sync-RoadmapIssue {
    param($Config)

    if (-not [bool]$Config.roadmapIssue.enabled) {
        return
    }

    $title = [string]$Config.roadmapIssue.title
    if ([string]::IsNullOrWhiteSpace($title)) {
        throw "roadmapIssue.title is required when roadmapIssue.enabled is true."
    }

    $bodyRelativePath = [string]$Config.roadmapIssue.bodyPath
    $bodyPath = Join-Path $repoRoot $bodyRelativePath
    if (-not (Test-Path $bodyPath)) {
        throw "Roadmap issue body file not found: $bodyPath"
    }

    $labels = @()
    if ($Config.roadmapIssue.labels) {
        $labels = @($Config.roadmapIssue.labels | ForEach-Object { [string]$_ })
    }

    $existingIssue = Find-IssueByTitle -Slug $Config.repo.slug -Title $title
    if ($existingIssue) {
        $requestParts = @('issue', 'edit', [string]$existingIssue.number, '--repo', $Config.repo.slug, '--title', $title, '--body-file', $bodyPath)
        foreach ($label in $labels) {
            $requestParts += @('--add-label', $label)
        }
        Invoke-Step "update roadmap issue '$title'" { Invoke-GhText -Arguments $requestParts | Out-Null }
    }
    else {
        $requestParts = @('issue', 'create', '--repo', $Config.repo.slug, '--title', $title, '--body-file', $bodyPath)
        foreach ($label in $labels) {
            $requestParts += @('--label', $label)
        }
        Invoke-Step "create roadmap issue '$title'" { Invoke-GhText -Arguments $requestParts | Out-Null }
    }

    if (-not [bool]$Config.roadmapIssue.pin) {
        return
    }

    if (-not $Apply) {
        Write-Log "DRY-RUN: pin roadmap issue '$title'"
        return
    }

    $issueToPin = Find-IssueByTitle -Slug $Config.repo.slug -Title $title
    if (-not $issueToPin -or [string]::IsNullOrWhiteSpace([string]$issueToPin.id)) {
        throw "Could not resolve roadmap issue id for pinning."
    }

    try {
        Invoke-GhText -Arguments @(
            'api', 'graphql',
            '-f', 'query=mutation($issueId:ID!){pinIssue(input:{issueId:$issueId}){issue{id number}}}',
            '-f', "issueId=$([string]$issueToPin.id)"
        ) | Out-Null
        Write-Log "Pinned roadmap issue '$title'"
    }
    catch {
        Write-Log "Pin skipped for '$title': $($_.Exception.Message)"
    }
}

$config = Get-SyncConfig -Path $ConfigPath
Write-Log "Using config '$ConfigPath'"
Invoke-GhReadyCheck
Sync-Features -Config $config
Sync-Labels -Config $config
Sync-Milestones -Config $config
Sync-RoadmapIssue -Config $config
Write-Log "Completed in $mode mode."
if (-not $Apply) {
    Write-Log "Use -Apply to perform remote updates."
}
