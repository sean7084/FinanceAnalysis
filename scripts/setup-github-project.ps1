# GitHub Project Board Setup for FinanceAnalysis
# Uses the GitHub Projects (v2) GraphQL API to create a project board.

$repoOwner = "sean7084"
$repoName = "FinanceAnalysis"
$token = (Get-Content '.env' | Select-String '^GITHUB_PERSONAL_ACCESS_TOKEN=').ToString().Split('=', 2)[1].Trim()
$headers = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github.v3+json"
}
$graphqlUrl = "https://api.github.com/graphql"

Write-Host "=== Starting Project Board Setup ===" -ForegroundColor Cyan
Write-Host "Repository: $repoOwner/$repoName" -ForegroundColor Yellow

# Step 1: Get the owner (user) ID for project creation
Write-Host "`n[1/4] Resolving owner ID..." -ForegroundColor Yellow

$viewerQuery = @{
    query = '{ viewer { id login } }'
} | ConvertTo-Json

try {
    $viewerResult = Invoke-RestMethod `
        -Uri $graphqlUrl `
        -Method Post `
        -Headers $headers `
        -Body $viewerQuery `
        -ContentType "application/json"

    $ownerId = $viewerResult.data.viewer.id
    $ownerLogin = $viewerResult.data.viewer.login
    Write-Host "[OK] Owner: $ownerLogin (ID: $ownerId)" -ForegroundColor Green
} catch {
    Write-Host "[!!] Could not resolve owner: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "     Falling back to manual creation instructions..." -ForegroundColor Yellow
    $ownerId = $null
}

# Step 2: Create the project board
$projectId = $null
if ($ownerId) {
    Write-Host "`n[2/4] Creating project board..." -ForegroundColor Yellow

    $createProjectMutation = @{
        query = @"
mutation(`$ownerId: ID!) {
  createProjectV2(input: {
    ownerId: `$ownerId,
    title: "FinanceAnalysis Development"
  }) {
    projectV2 {
      id
      url
      number
    }
  }
}
"@
        variables = @{ ownerId = $ownerId }
    } | ConvertTo-Json -Depth 3

    try {
        $projectResult = Invoke-RestMethod `
            -Uri $graphqlUrl `
            -Method Post `
            -Headers $headers `
            -Body $createProjectMutation `
            -ContentType "application/json"

        if ($projectResult.data.createProjectV2.projectV2.id) {
            $projectId = $projectResult.data.createProjectV2.projectV2.id
            $projectUrl = $projectResult.data.createProjectV2.projectV2.url
            $projectNumber = $projectResult.data.createProjectV2.projectV2.number
            Write-Host "[OK] Project created!" -ForegroundColor Green
            Write-Host "     URL: $projectUrl" -ForegroundColor Gray
            Write-Host "     Number: $projectNumber" -ForegroundColor Gray
        } elseif ($projectResult.errors) {
            $errorMsg = ($projectResult.errors | ForEach-Object { $_.message }) -join "; "
            Write-Host "[!!] GraphQL errors: $errorMsg" -ForegroundColor Red
            Write-Host "     The project may need to be created manually." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "[!!] Could not create project: $($_.Exception.Message)" -ForegroundColor Red
    }
}

if (-not $projectId) {
    Write-Host "`n[2/4] Manual project creation required:" -ForegroundColor Yellow
    Write-Host "  1. Go to https://github.com/$repoOwner/$repoName" -ForegroundColor Gray
    Write-Host "  2. Click the 'Projects' tab" -ForegroundColor Gray
    Write-Host "  3. Click 'New project'" -ForegroundColor Gray
    Write-Host "  4. Select 'Board' template" -ForegroundColor Gray
    Write-Host "  5. Name it: 'FinanceAnalysis Development'" -ForegroundColor Gray
}

# Step 3: Configure columns (status field)
Write-Host "`n[3/4] Column configuration..." -ForegroundColor Yellow
Write-Host "Configure these status columns in your project board:" -ForegroundColor Gray
Write-Host "  Backlog -> In Progress -> Code Review -> Done" -ForegroundColor Cyan
Write-Host ""
Write-Host "Steps:" -ForegroundColor Gray
Write-Host "  1. Open the project board" -ForegroundColor Gray
Write-Host "  2. Click '...' menu -> 'Workflows' or 'Settings'" -ForegroundColor Gray
Write-Host "  3. Edit the 'Status' field options" -ForegroundColor Gray
Write-Host "  4. Rename/add columns to match: Backlog, In Progress, Code Review, Done" -ForegroundColor Gray

# Step 4: Custom fields
Write-Host "`n[4/4] Custom fields to add manually:" -ForegroundColor Yellow
Write-Host "Click '+ New field' on your project board:" -ForegroundColor Gray
Write-Host "  - Priority (Single select): P0-Critical, P1-High, P2-Medium, P3-Low" -ForegroundColor Gray
Write-Host "  - Component (Multiple select): markets, analytics, prediction," -ForegroundColor Gray
Write-Host "    backtest, sentiment, macro, factors, frontend, ml-pipeline," -ForegroundColor Gray
Write-Host "    celery-tasks, docker-infra, api-auth" -ForegroundColor Gray
Write-Host "  - Story Points (Number): 1, 2, 3, 5, 8, 13" -ForegroundColor Gray

# Summary
Write-Host "`n=== Summary ===" -ForegroundColor Cyan
if ($projectId) {
    Write-Host "Project board created and configured!" -ForegroundColor Green
    Write-Host "URL: $projectUrl" -ForegroundColor Gray
} else {
    Write-Host "Project board needs manual creation (see instructions above)." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Configure columns: Backlog, In Progress, Code Review, Done" -ForegroundColor Gray
Write-Host "  2. Add custom fields: Priority, Component, Story Points" -ForegroundColor Gray
Write-Host "  3. Set up automation rules (see .github/QUICK_SETUP_AUTOMATION.md)" -ForegroundColor Gray
Write-Host ""
Write-Host "Full documentation: .github/PROJECT_WORKFLOW.md" -ForegroundColor Gray
