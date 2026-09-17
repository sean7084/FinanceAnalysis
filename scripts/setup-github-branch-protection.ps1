# Automated GitHub Branch Protection Setup for FinanceAnalysis
# Protects main with PR requirement (1 reviewer), CI status checks, and
# force-push / deletion guards.

$repoOwner = "sean7084"
$repoName = "FinanceAnalysis"
$token = (Get-Content '.env' | Select-String '^GITHUB_PERSONAL_ACCESS_TOKEN=').ToString().Split('=', 2)[1].Trim()
$headers = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github.v3+json"
}
$baseUrl = "https://api.github.com/repos/$repoOwner/$repoName"

Write-Host "=== Starting Branch Protection Setup ===" -ForegroundColor Cyan
Write-Host "Repository: $repoOwner/$repoName" -ForegroundColor Yellow

# Detect default branch
Write-Host "`nDetecting default branch..." -ForegroundColor Yellow
try {
    $repoInfo = Invoke-RestMethod -Uri "$baseUrl" -Method Get -Headers $headers
    $defaultBranch = $repoInfo.default_branch
    Write-Host "[OK] Default branch: '$defaultBranch'" -ForegroundColor Green
} catch {
    Write-Host "[!!] Cannot determine default branch: $($_.Exception.Message)" -ForegroundColor Red
    throw "Cannot determine default branch"
}

# Step 1: Required status checks (CI jobs from ci.yml)
Write-Host "`n[1/3] Setting up required status checks..." -ForegroundColor Yellow

# The CI workflow job names become the status check contexts.
# ci.yml defines: "Django checks and test suite" and "Frontend tests"
# GitHub uses the job `name:` field as the check context.
$statusCheckContexts = @(
    "Django checks and test suite",
    "Frontend tests, type-check, and lint"
)

$protectionPayload = @{
    required_status_checks = @{
        strict   = $true
        contexts = $statusCheckContexts
    }
    enforce_admins            = $true
    required_pull_request_reviews = @{
        required_approving_review_count = 1
        dismiss_stale_reviews           = $true
        require_code_owner_reviews      = $false
    }
    restrictions              = $null
    allow_force_pushes        = $false
    allow_deletions           = $false
    block_creations           = $false
    required_conversation_resolution = $false
} | ConvertTo-Json -Depth 5

try {
    Invoke-RestMethod `
        -Uri "$baseUrl/branches/$defaultBranch/protection" `
        -Method Put `
        -Headers $headers `
        -Body $protectionPayload `
        -ContentType "application/json"

    Write-Host "[OK] Branch protection configured:" -ForegroundColor Green
    Write-Host "   - Required status checks: $($statusCheckContexts -join ', ')" -ForegroundColor Gray
    Write-Host "   - Strict mode: enabled (must be up-to-date)" -ForegroundColor Gray
    Write-Host "   - Minimum 1 approving review" -ForegroundColor Gray
    Write-Host "   - Stale reviews dismissed on new commits" -ForegroundColor Gray
    Write-Host "   - Admins enforced" -ForegroundColor Gray
    Write-Host "   - Force pushes blocked" -ForegroundColor Gray
    Write-Host "   - Branch deletion blocked" -ForegroundColor Gray
} catch {
    Write-Host "[!!] Could not configure branch protection: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "     You may need to set this up through GitHub web UI:" -ForegroundColor Yellow
    Write-Host "     Settings -> Branches -> Add branch protection rule" -ForegroundColor Gray
    throw "Branch protection setup failed"
}

# Step 2: Verification
Write-Host "`n[2/3] Verifying current protection rules..." -ForegroundColor Yellow

try {
    $currentRules = Invoke-RestMethod `
        -Uri "$baseUrl/branches/$defaultBranch/protection" `
        -Method Get `
        -Headers $headers

    Write-Host "`nCurrent Branch Protection Settings:" -ForegroundColor Cyan
    Write-Host "  Required Status Checks:" -ForegroundColor Gray
    Write-Host "    Strict: $($currentRules.required_status_checks.strict)" -ForegroundColor Gray
    Write-Host "    Contexts: $($currentRules.required_status_checks.contexts -join ', ')" -ForegroundColor Gray
    Write-Host "  Pull Request Reviews:" -ForegroundColor Gray
    Write-Host "    Required approvals: $($currentRules.required_pull_request_reviews.required_approving_review_count)" -ForegroundColor Gray
    Write-Host "    Dismiss stale reviews: $($currentRules.required_pull_request_reviews.dismiss_stale_reviews)" -ForegroundColor Gray
    Write-Host "    Code owner reviews: $($currentRules.required_pull_request_reviews.require_code_owner_reviews)" -ForegroundColor Gray
    Write-Host "  Enforce Admins: $($currentRules.enforce_admins)" -ForegroundColor Gray
    Write-Host "  Allow Force Pushes: $($currentRules.allow_force_pushes)" -ForegroundColor Gray
    Write-Host "  Allow Deletions: $($currentRules.allow_deletions)" -ForegroundColor Gray
} catch {
    Write-Host "[!!] Could not verify: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Step 3: Summary
Write-Host "`n[3/3] Summary" -ForegroundColor Yellow
Write-Host "`n=== Branch Protection Configuration Complete ===" -ForegroundColor Cyan
Write-Host "  Branch: '$defaultBranch'" -ForegroundColor Gray
Write-Host "  CI checks required: Django checks and test suite, Frontend tests" -ForegroundColor Gray
Write-Host "  PR required: 1 reviewer" -ForegroundColor Gray
Write-Host "  Force pushes: blocked" -ForegroundColor Gray
Write-Host "  Deletions: blocked" -ForegroundColor Gray
