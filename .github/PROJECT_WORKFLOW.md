# GitHub Projects Workflow Setup for FinanceAnalysis

## Overview

This document describes how to configure and use GitHub Projects for managing
the FinanceAnalysis development lifecycle.

---

## Project Board Location

**Project URL:** https://github.com/users/sean7084/projects/2

**Status:** Created via `scripts/setup-github-project.ps1`

---

## Recommended Views

GitHub Projects supports multiple views. Configure these for optimal workflow:

### 1. Kanban Board View (Primary)

**Columns:**
```
Backlog -> In Progress -> Code Review -> Done
```

**Setup Steps:**
1. Go to your project board
2. Click "Customize columns" (or the status field options)
3. Rename default columns to match the four stages above
4. Enable auto-mapping for issues/PRs

### 2. Backlog Board (Planning Mode)

**Columns:**
```
Icebox -> Next Sprint -> Current Sprint -> In Progress -> Completed
```

**Use Case:** Sprint planning and backlog grooming sessions.

### 3. Table View (Detailed Filtering)

Group by priority or component. Useful for cross-board analysis.

---

## Custom Fields Configuration

Define these custom fields for each issue/task:

| Field Name | Type | Options | Purpose |
|------------|------|---------|---------|
| **Priority** | Single select | P0-Critical, P1-High, P2-Medium, P3-Low | Impact assessment |
| **Component** | Multiple select | markets, analytics, prediction, backtest, sentiment, macro, factors, frontend, ml-pipeline, celery-tasks, docker-infra, api-auth | Area of code affected |
| **Story Points** | Number | 1, 2, 3, 5, 8, 13 | Estimation metric |

**How to Add Custom Fields:**
1. Click "+ New field" on your project board
2. Select field type from dropdown
3. Configure options and visibility

---

## Automation Rules

### Accessing Automation Rules:
1. Open your project board
2. Click the **"..."** menu at the top-right
3. Select **"Workflows"** to configure automation

### Rule Templates to Add:

#### Rule 1: Pull Request Creation
```
When: Pull request is created
Then: Move card to status "Code Review"
```

#### Rule 2: Pull Request Merged
```
When: Pull request is merged
Then: Move card to status "Done"
```

#### Rule 3: Stale Issues
```
When: No activity for 30 days AND label="stale"
Then: Archive card (do NOT delete)
```

---

## Workflow Stages Defined

### Backlog
- **Definition:** Issue/PR identified but not yet started
- **Required Fields:** Priority, Component labels
- **Actions:** Assign owner, add story points

### In Progress
- **Definition:** Work actively being performed
- **Requirements:** At least one assignee
- **Rules:** Maximum 3 tasks concurrently

### Code Review
- **Definition:** PR submitted, awaiting approval
- **Required:** Minimum 1 reviewer
- **Timeout:** Self-review after 48h if no external reviewer available

### Done
- **Definition:** Merged to main, CI green
- **Activities:** Update CHANGELOG.md, close milestone

---

## Best Practices

### Card Hygiene
- Close/archived cards older than 90 days
- Keep descriptions concise but informative
- Use checkboxes for multi-step actions
- Attach screenshots/mockups directly to cards

### Communication
- Reference issue numbers in commit messages
- Link sub-tasks to parent epic using GitHub's linking syntax
- Move cards through the workflow as work progresses

### Continuous Improvement
Monthly retrospective questions:
- What slowed us down?
- Which processes worked well?
- How to reduce cycle time next month?

---

## Quick Start Checklist

- [ ] Visit project board URL (see above)
- [ ] Configure columns: Backlog, In Progress, Code Review, Done
- [ ] Add custom fields: Priority, Component, Story Points
- [ ] Set up automation rules
- [ ] Create first sprint board view (if using sprint workflow)

---

## Troubleshooting

**Issue:** Can't find automation settings
- **Solution:** GitHub Projects (v2) uses "Workflows" instead of "Automate"
- **Alternative:** Check if using the new or classic Projects UI

**Issue:** Automation rules not triggering
- **Solution:** Verify status field names match exactly (case-sensitive)
- **Check:** Ensure labels are applied correctly

---

*Last Updated: September 15, 2026*
