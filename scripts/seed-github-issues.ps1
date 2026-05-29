[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$SeedPath = (Join-Path (Split-Path -Parent $PSScriptRoot) '.github/issue-seed.json'),
    [string]$Repo
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$mode = if ($Apply) { 'apply' } else { 'dry-run' }

function Write-Log {
    param([string]$Message)

    Write-Host "[seed-github-issues] $Message"
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

function Resolve-PathFromRepoRoot {
    param([string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }

    return Join-Path $repoRoot $Path
}

function Get-DefaultRepoSlug {
    $configPath = Join-Path $repoRoot '.github/gh-sync.json'
    if (-not (Test-Path $configPath)) {
        throw "Default repo config not found: $configPath"
    }

    $config = Get-Content -Raw -Path $configPath | ConvertFrom-Json -Depth 100
    if (-not $config.repo.slug) {
        throw "Missing repo.slug in $configPath"
    }

    return [string]$config.repo.slug
}

function Get-NormalizedSourceIds {
    param([string[]]$SourceIds)

    return @(
        $SourceIds |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } |
            ForEach-Object { ([string]$_).Trim().ToUpperInvariant() } |
            Sort-Object -Unique
    )
}

function Get-SourceIdKey {
    param([string[]]$SourceIds)

    return (Get-NormalizedSourceIds -SourceIds $SourceIds) -join '|'
}

function Get-SourceIdsFromBody {
    param([AllowNull()][string]$Body)

    if ([string]::IsNullOrWhiteSpace($Body)) {
        return @()
    }

    $match = [regex]::Match($Body, '(?im)^\s*Original backlog IDs:\s*(?<ids>.+?)\s*$')
    if (-not $match.Success) {
        return @()
    }

    $matches = [regex]::Matches($match.Groups['ids'].Value, 'ARSS-\d+', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $matches -or $matches.Count -eq 0) {
        return @()
    }

    return Get-NormalizedSourceIds -SourceIds @($matches | ForEach-Object { $_.Value })
}

function Get-IssueSeed {
    param([string]$Path)

    $resolvedPath = Resolve-PathFromRepoRoot -Path $Path
    if (-not (Test-Path $resolvedPath)) {
        throw "Seed file not found: $resolvedPath"
    }

    $seed = Get-Content -Raw -Path $resolvedPath | ConvertFrom-Json -Depth 100
    if (-not ($seed.PSObject.Properties.Name -contains 'issues')) {
        throw "Missing issues section in $resolvedPath"
    }
    if (-not $seed.issues -or $seed.issues.Count -eq 0) {
        throw "No issues found in $resolvedPath"
    }

    foreach ($issue in $seed.issues) {
        if ([string]::IsNullOrWhiteSpace([string]$issue.title)) {
            throw "Every seed issue requires a title."
        }
        if ([string]::IsNullOrWhiteSpace([string]$issue.body)) {
            throw "Seed issue '$([string]$issue.title)' is missing body."
        }
        if ([string]::IsNullOrWhiteSpace([string]$issue.milestone)) {
            throw "Seed issue '$([string]$issue.title)' is missing milestone."
        }
        if (-not $issue.labels -or $issue.labels.Count -eq 0) {
            throw "Seed issue '$([string]$issue.title)' requires at least one label."
        }
        if (-not $issue.sourceIds -or $issue.sourceIds.Count -eq 0) {
            throw "Seed issue '$([string]$issue.title)' requires at least one source id."
        }

        $declaredSourceIds = Get-NormalizedSourceIds -SourceIds @($issue.sourceIds | ForEach-Object { [string]$_ })
        $bodySourceIds = Get-SourceIdsFromBody -Body ([string]$issue.body)
        if ($bodySourceIds.Count -eq 0) {
            throw "Seed issue '$([string]$issue.title)' body must contain 'Original backlog IDs: ...'."
        }
        if ((Get-SourceIdKey -SourceIds $declaredSourceIds) -ne (Get-SourceIdKey -SourceIds $bodySourceIds)) {
            throw "Seed issue '$([string]$issue.title)' has mismatched source ids between sourceIds and body."
        }
    }

    return [pscustomobject]@{
        Path = $resolvedPath
        Data = $seed
    }
}

function Get-ExistingIssueMaps {
    param([string]$Slug)

    $issues = Invoke-GhJson @('issue', 'list', '--repo', $Slug, '--state', 'all', '--limit', '500', '--json', 'number,title,body,labels')
    $titleMap = @{}
    $sourceIdMap = @{}
    if ($issues) {
        foreach ($issue in $issues) {
            $titleMap[[string]$issue.title] = $issue

            $sourceIdKey = Get-SourceIdKey -SourceIds (Get-SourceIdsFromBody -Body ([string]$issue.body))
            if (-not [string]::IsNullOrWhiteSpace($sourceIdKey)) {
                if ($sourceIdMap.ContainsKey($sourceIdKey)) {
                    $existingNumber = [string]$sourceIdMap[$sourceIdKey].number
                    Write-Log "WARN: duplicate sourceIds '$sourceIdKey' found on #$existingNumber and #$([string]$issue.number); using the first match."
                }
                else {
                    $sourceIdMap[$sourceIdKey] = $issue
                }
            }
        }
    }

    return [pscustomobject]@{
        ByTitle = $titleMap
        BySourceIds = $sourceIdMap
    }
}

function Get-ExistingMilestoneMap {
    param([string]$Slug)

    $milestones = Invoke-GhJson @('api', "repos/$Slug/milestones?state=all&per_page=100")
    $map = @{}
    if ($milestones) {
        foreach ($milestone in $milestones) {
            $map[[string]$milestone.title] = $milestone
        }
    }

    return $map
}

function Format-List {
    param([object[]]$Items)

    $rendered = @(
        $Items |
            ForEach-Object { [string]$_ } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )

    if ($rendered.Count -eq 0) {
        return 'none'
    }

    return ($rendered -join ', ')
}

function Get-LabelNamesFromIssue {
    param($Issue)

    if (-not $Issue -or -not $Issue.labels) {
        return @()
    }

    return @($Issue.labels | ForEach-Object { [string]$_.name } | Sort-Object -Unique)
}

function Set-IssueInMaps {
    param(
        $Maps,
        [int]$Number,
        [string]$Title,
        [string]$Body,
        [string[]]$Labels,
        [string]$PreviousTitle,
        [string[]]$PreviousSourceIds
    )

    if (-not [string]::IsNullOrWhiteSpace($PreviousTitle) -and $Maps.ByTitle.ContainsKey($PreviousTitle)) {
        [void]$Maps.ByTitle.Remove($PreviousTitle)
    }

    $previousSourceIdKey = Get-SourceIdKey -SourceIds $PreviousSourceIds
    if (-not [string]::IsNullOrWhiteSpace($previousSourceIdKey) -and $Maps.BySourceIds.ContainsKey($previousSourceIdKey)) {
        if ([string]$Maps.BySourceIds[$previousSourceIdKey].number -eq [string]$Number) {
            [void]$Maps.BySourceIds.Remove($previousSourceIdKey)
        }
    }

    $issueSnapshot = [pscustomobject]@{
        number = $Number
        title = $Title
        body = $Body
        labels = @($Labels | ForEach-Object { [pscustomobject]@{ name = $_ } })
    }

    $Maps.ByTitle[$Title] = $issueSnapshot

    $sourceIdKey = Get-SourceIdKey -SourceIds (Get-SourceIdsFromBody -Body $Body)
    if (-not [string]::IsNullOrWhiteSpace($sourceIdKey)) {
        $Maps.BySourceIds[$sourceIdKey] = $issueSnapshot
    }
}

$seed = Get-IssueSeed -Path $SeedPath
$repoSlug = if ([string]::IsNullOrWhiteSpace($Repo)) { Get-DefaultRepoSlug } else { $Repo }

Write-Log "Using seed '$($seed.Path)'"
Write-Log "Target repo '$repoSlug'"

Invoke-GhReadyCheck

$existingIssues = Get-ExistingIssueMaps -Slug $repoSlug
$existingMilestones = Get-ExistingMilestoneMap -Slug $repoSlug

foreach ($issue in $seed.Data.issues) {
    $title = [string]$issue.title
    $body = [string]$issue.body
    $milestone = [string]$issue.milestone
    $labels = @($issue.labels | ForEach-Object { [string]$_ })
    $sourceIds = Get-NormalizedSourceIds -SourceIds @($issue.sourceIds | ForEach-Object { [string]$_ })

    if (-not $existingMilestones.ContainsKey($milestone)) {
        throw "Milestone '$milestone' does not exist in '$repoSlug'. Run 'scripts/sync-github-meta.ps1 -Apply' first."
    }

    $matchedIssue = $null
    $matchReason = $null
    $sourceIdKey = Get-SourceIdKey -SourceIds $sourceIds

    if ($existingIssues.ByTitle.ContainsKey($title)) {
        $matchedIssue = $existingIssues.ByTitle[$title]
        $matchReason = 'exact title'
    }
    elseif (-not [string]::IsNullOrWhiteSpace($sourceIdKey) -and $existingIssues.BySourceIds.ContainsKey($sourceIdKey)) {
        $matchedIssue = $existingIssues.BySourceIds[$sourceIdKey]
        $matchReason = 'sourceIds'
    }

    if ($matchedIssue) {
        $existingNumber = [string]$matchedIssue.number
        $existingLabels = Get-LabelNamesFromIssue -Issue $matchedIssue
        $labelsToAdd = @($labels | Where-Object { $existingLabels -notcontains $_ })
        $labelsToRemove = @($existingLabels | Where-Object { $labels -notcontains $_ })

        if (-not $Apply) {
            Write-Log "DRY-RUN: would update #$existingNumber via $matchReason to '$title' [milestone: $milestone] [add labels: $(Format-List -Items $labelsToAdd)] [remove labels: $(Format-List -Items $labelsToRemove)] [sourceIds: $(Format-List -Items $sourceIds)]"
            continue
        }

        $arguments = @('issue', 'edit', $existingNumber, '--repo', $repoSlug, '--title', $title, '--body', $body, '--milestone', $milestone)
        foreach ($label in $labelsToAdd) {
            $arguments += @('--add-label', $label)
        }
        foreach ($label in $labelsToRemove) {
            $arguments += @('--remove-label', $label)
        }

        $previousTitle = [string]$matchedIssue.title
        $previousSourceIds = Get-SourceIdsFromBody -Body ([string]$matchedIssue.body)
        Invoke-GhText -Arguments $arguments | Out-Null
        Set-IssueInMaps -Maps $existingIssues -Number ([int]$matchedIssue.number) -Title $title -Body $body -Labels $labels -PreviousTitle $previousTitle -PreviousSourceIds $previousSourceIds
        Write-Log "Updated issue #$existingNumber via ${matchReason}: '$title'"
        continue
    }

    if (-not $Apply) {
        Write-Log "DRY-RUN: would create '$title' [milestone: $milestone] [labels: $(Format-List -Items $labels)] [sourceIds: $(Format-List -Items $sourceIds)]"
        continue
    }

    $arguments = @('issue', 'create', '--repo', $repoSlug, '--title', $title, '--body', $body, '--milestone', $milestone)
    foreach ($label in $labels) {
        $arguments += @('--label', $label)
    }

    $result = Invoke-GhText -Arguments $arguments
    if ($result -match '/issues/(?<number>\d+)$') {
        Set-IssueInMaps -Maps $existingIssues -Number ([int]$Matches.number) -Title $title -Body $body -Labels $labels -PreviousTitle '' -PreviousSourceIds @()
    }

    Write-Log "Created issue '$title'"
}

Write-Log "Completed in $mode mode."
if (-not $Apply) {
    Write-Log "Use -Apply to create missing issues after milestones are synced."
}