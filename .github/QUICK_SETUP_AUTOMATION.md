# Quick Setup Guide - Project Automation Rules

**Date:** September 15, 2026
**Repository:** sean7084/FinanceAnalysis
**Time Required:** 3-5 minutes

---

## What You Need to Do

Your GitHub project board is created. Now add automation rules to make it work
automatically.

### Step-by-Step Instructions:

1. **Open Your Project Board**
   - Go to your project URL (see PROJECT_WORKFLOW.md for the link)

2. **Find the Workflow Settings**
   - Click the **"..."** menu at the top-right corner
   - Select **"Workflows"**

3. **Add Automation Rules**

   Configure these built-in workflows:

---

## Recommended Automation Rules

### Rule 1: Pull Request Created
```
Trigger: When pull request is opened
Action: Set status to "Code Review"
```

### Rule 2: Pull Request Merged
```
Trigger: When pull request is merged
Action: Set status to "Done"
```

### Rule 3: Issue Closed
```
Trigger: When issue is closed
Action: Set status to "Done"
```

---

## Pro Tips

- You can modify rules anytime
- Rules work for issues AND pull requests
- Multiple rules can trigger on the same event
- Deactivate rules without deleting them

---

## After Setting Up Automation

Add custom fields to track important information:

1. Click **+ New field** on your project board
2. Add these fields:
   - **Priority** (Single select): P0-Critical, P1-High, P2-Medium, P3-Low
   - **Component** (Multiple select): markets, analytics, prediction, backtest,
     sentiment, macro, factors, frontend, ml-pipeline, celery-tasks,
     docker-infra, api-auth
   - **Story Points** (Number): 1, 2, 3, 5, 8, 13

---

## That's It!

Your project board is now fully configured with:
- Automated workflow rules
- Proper labeling system
- Custom tracking fields

**Next Steps:**
- Start moving tasks through the workflow
- Create issues using the templates in `.github/ISSUE_TEMPLATE/`

---

## Additional Resources

- **Full Workflow Guide:** `.github/PROJECT_WORKFLOW.md`
- **Label Definitions:** `.github/LABELS.md`
- **Automation Script:** `scripts/setup-github-project.ps1`

---

*Last Updated: September 15, 2026*
