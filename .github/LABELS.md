# GitHub Labels Configuration for FinanceAnalysis

## Overview

This document defines the labeling strategy for the FinanceAnalysis repository
to streamline issue tracking, pull request management, and project workflow.

---

## Label Categories

### Status Labels (4)

| Label Name | Color | Description |
|------------|-------|-------------|
| `triage` | #E9D75F | Needs initial review and categorization |
| `awaiting response` | #0E8A16 | Waiting on author feedback |
| `blocked` | #BDBDBD | Cannot proceed due to dependency |
| `stale` | #EEEEEE | No activity for 30 days |

---

### Priority Labels (4)

| Label Name | Color | Description |
|------------|-------|-------------|
| `P0 - Critical` | #B60205 | Blocks release; needs immediate fix |
| `P1 - High` | #D93F0B | Important bug/feature for next sprint |
| `P2 - Medium` | #FBCA04 | Regular backlog item |
| `P3 - Low` | #EDEDED | Nice-to-have, low priority |

---

### Type Labels (7)

| Label Name | Color | Description |
|------------|-------|-------------|
| `bug` | #D73A4A | Something isn't working correctly |
| `enhancement` | #A2EEEF | New feature request |
| `documentation` | #0075CA | Improvements to docs |
| `good first issue` | #7057FF | Easy for newcomers |
| `help wanted` | #008672 | Contribution needed |
| `question` | #CC3147 | More information needed |
| `discussion` | #CCE329 | Topics requiring community input |

---

### Component Labels (12)

| Label Name | Color | Description |
|------------|-------|-------------|
| `component: markets` | #1D76EB | Stock data, prices, technical indicators |
| `component: analytics` | #0052CC | WebSocket alerts, real-time analytics |
| `component: prediction` | #5457FF | LightGBM/LSTM model training & inference |
| `component: backtest` | #FBE75F | Backtesting engine and comparison |
| `component: sentiment` | #FBF29D | News sentiment analysis |
| `component: macro` | #5791EF | Macro-economic data (yield curves, FX) |
| `component: factors` | #A2EEEF | Fundamental factor materialization |
| `component: frontend` | #79CB23 | React/Vite dashboard and UI |
| `component: ml-pipeline` | #6F42C1 | Feature engineering, label generation |
| `component: celery-tasks` | #D8744E | Celery workers, queues, Beat schedule |
| `component: docker-infra` | #C6CFCF | Docker Compose, container config |
| `component: api-auth` | #EB6420 | DRF endpoints, JWT, API-key auth |

---

### Impact Labels (3)

| Label Name | Color | Description |
|------------|-------|-------------|
| `breaking change` | #B60205 | Incompatible API/model changes |
| `deprecation` | #D8744E | Deprecated feature scheduled for removal |
| `migration-required` | #BF5D73 | Database migration required |

---

### Testing Labels (3)

| Label Name | Color | Description |
|------------|-------|-------------|
| `needs testing` | #FFEBD6 | Awaiting validation |
| `test-passed` | #84b6ef | Verified in staging |
| `e2e-test-needed` | #7057FF | End-to-end test coverage gap |

---

### Deployment/Release Labels (3)

| Label Name | Color | Description |
|------------|-------|-------------|
| `deployment` | #C2E0C6 | Ready for deployment |
| `release-notes` | #0366D6 | Must be included in changelog |
| `security` | #EB6420 | Security-related fix |

---

## Usage Guidelines

### Label Combinations

**For Bug Reports:**
- Priority: `P0`, `P1`, `P2`, or `P3`
- Type: `bug`
- Component: `component: [area]`
- Status: `needs testing` or `test-passed`

**For Feature Requests:**
- Priority: `P0`, `P1`, `P2`, or `P3`
- Type: `enhancement`
- Component: `component: [area]`
- Planning: `discussion` if still being evaluated

**For Data Pipeline Issues:**
- Type: `bug`
- Component: `component: celery-tasks` or specific data app
- Priority based on data freshness impact

**For Model Regressions:**
- Type: `bug`
- Component: `component: prediction` or `component: ml-pipeline`
- Priority: `P0` if predictions are live, `P1` otherwise

**For Pull Requests:**
- Status: `awaiting response` (if waiting for review)
- Testing: `e2e-test-needed` (if automation tests missing)
- Approval: `test-passed` (after verification)

### Automation Integration

These labels work seamlessly with GitHub Projects automation rules:
- Automatically move cards based on status changes
- Trigger notifications for high-priority items
- Archive stale issues after 30 days

---

## Setup Script

Use `scripts/setup-github-labels.ps1` to automatically create all labels via
GitHub API.

```powershell
.\scripts\setup-github-labels.ps1
```

---

*Last Updated: September 15, 2026*
