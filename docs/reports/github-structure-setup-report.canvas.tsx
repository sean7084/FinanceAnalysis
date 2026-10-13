import {
  Badge,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Stack,
  Stat,
  Table,
  Text,
} from "qoder/canvas";

export default function GitHubStructureSetupReport() {
  return (
    <Stack gap={20}>
      <H1>GitHub Structure Setup -- Completion Report</H1>
      <Text tone="secondary">
        sean7084/FinanceAnalysis | September 15, 2026
      </Text>

      <Divider />

      {/* ---- Summary Stats ---- */}
      <H2>Accomplishment Summary</H2>
      <Text>
        Fully implemented a fine-tuned GitHub governance structure for
        FinanceAnalysis, mirroring the HengjiAMS1 reference project but adapted
        for this quantitative finance platform (Django + Celery + React + ML
        stack) and solo-maintainer reality.
      </Text>

      <Grid columns={4} gap={16}>
        <Stat value="15" label="Files Created" tone="success" />
        <Stat value="36" label="Labels Configured" tone="success" />
        <Stat value="6" label="Issue Templates" />
        <Stat value="5" label="Automation Scripts" />
      </Grid>

      <Grid columns={3} gap={16}>
        <Stat value="9/9" label="Critical Labels Verified" tone="success" />
        <Stat value="10/10" label="Local Files Present" tone="success" />
        <Stat value="1" label="Project Board Created" tone="success" />
      </Grid>

      <Divider />

      {/* ---- Key Steps ---- */}
      <H2>Key Steps Completed</H2>
      <Table
        headers={["#", "Step", "Status"]}
        rows={[
          [
            "1",
            "Created 6 issue templates (incl. 2 unique: data_pipeline.md, model_regression.md)",
            "Done",
          ],
          [
            "2",
            "Created PR template with FinanceAnalysis-specific paths and makemigrations CI gate",
            "Done",
          ],
          [
            "3",
            "Defined 36 labels across 7 categories mapped to Django app modules",
            "Done",
          ],
          [
            "4",
            "Created project workflow docs with 4-column solo-lean kanban",
            "Done",
          ],
          [
            "5",
            "Built 5 PowerShell automation scripts (REST + GraphQL APIs)",
            "Done",
          ],
          [
            "6",
            "Executed all scripts: labels created, branch protection active, project board live",
            "Done",
          ],
          [
            "7",
            "Fixed 2 script bugs during execution, verified all green",
            "Done",
          ],
        ]}
        rowTone={[
          "success",
          "success",
          "success",
          "success",
          "success",
          "success",
          "success",
        ]}
      />

      <Divider />

      {/* ---- Files Created ---- */}
      <H2>Files Created</H2>

      <H3>.github/ (10 files)</H3>
      <Table
        headers={["File", "Purpose"]}
        rows={[
          ["ISSUE_TEMPLATE/bug_report.md", "Standard bug report with full-stack env section"],
          ["ISSUE_TEMPLATE/feature_request.md", "Feature request referencing Django apps and frontend"],
          ["ISSUE_TEMPLATE/task.md", "Epic / task tracker"],
          ["ISSUE_TEMPLATE/security.md", "Confidential vulnerability report"],
          [
            "ISSUE_TEMPLATE/data_pipeline.md",
            "NEW -- Tushare / Celery / indicator failures",
          ],
          [
            "ISSUE_TEMPLATE/model_regression.md",
            "NEW -- LightGBM / LSTM quality issues",
          ],
          ["PULL_REQUEST_TEMPLATE.md", "PR template with CI gate checklist"],
          ["LABELS.md", "36 labels across 7 categories"],
          ["PROJECT_WORKFLOW.md", "4-column kanban workflow guide"],
          ["QUICK_SETUP_AUTOMATION.md", "Automation quick-start guide"],
        ]}
      />

      <H3>scripts/ (5 files)</H3>
      <Table
        headers={["File", "Purpose"]}
        rows={[
          ["setup-github-labels.ps1", "Creates 36 labels via REST API"],
          [
            "setup-github-branch-protection.ps1",
            "Protects main (1 reviewer, CI required)",
          ],
          [
            "setup-github-project.ps1",
            "Creates project board via GraphQL API",
          ],
          [
            "verify-github-setup.ps1",
            "Verifies labels, branch protection, local files",
          ],
          ["run-github-setup.ps1", "Master runner (calls all scripts)"],
        ]}
      />

      <Divider />

      {/* ---- Verification Evidence ---- */}
      <H2>Verification Evidence</H2>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H3>Labels</H3>
          <Table
            headers={["Check", "Result"]}
            rows={[
              ["Total on GitHub", "39 (36 created + 3 pre-existing)"],
              ["Critical labels found", "9 / 9"],
              ["Idempotent re-run", "6 skipped (already existed)"],
            ]}
            rowTone={["success", "success", undefined]}
          />
        </Stack>

        <Stack gap={8}>
          <H3>Branch Protection (main)</H3>
          <Table
            headers={["Setting", "Value"]}
            rows={[
              ["PR required", "Yes (1 reviewer)"],
              ["CI checks", "Django + Frontend tests"],
              ["Strict mode", "Enabled"],
              ["Admins enforced", "True"],
              ["Force push", "Blocked"],
              ["Deletion", "Blocked"],
              ["Stale review dismiss", "True"],
            ]}
            rowTone={[
              "success",
              "success",
              "success",
              "success",
              "success",
              "success",
              "success",
            ]}
          />
        </Stack>
      </Grid>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H3>Project Board</H3>
          <Table
            headers={["Property", "Value"]}
            rows={[
              ["URL", "github.com/users/sean7084/projects/2"],
              ["Created via", "GraphQL API (setup-github-project.ps1)"],
              ["Columns", "Backlog / In Progress / Code Review / Done"],
            ]}
          />
        </Stack>

        <Stack gap={8}>
          <H3>Local Files</H3>
          <Table
            headers={["Check", "Result"]}
            rows={[
              [".github/ files", "10 / 10 present"],
              ["scripts/ files", "5 / 5 present"],
              ["All scripts execute", "No errors"],
            ]}
            rowTone={["success", "success", "success"]}
          />
        </Stack>
      </Grid>

      <Divider />

      {/* ---- Bugs Fixed ---- */}
      <H2>Bugs Fixed During Execution</H2>
      <Table
        headers={["Bug", "Root Cause", "Fix"]}
        rows={[
          [
            "Project board GraphQL mutation failed",
            "CreateProjectV2Input does not accept 'public' argument",
            "Removed 'public: false' from the mutation in setup-github-project.ps1",
          ],
          [
            "verify-github-setup.ps1 parser error",
            "PowerShell does not allow if/try expressions inside hashtable literals",
            "Computed values into variables first, then built an [ordered] hashtable",
          ],
        ]}
      />

      <Divider />

      {/* ---- Outcome ---- */}
      <H2>Final Outcome</H2>
      <Text>
        All plan requirements satisfied. The GitHub repository now has a
        complete governance structure with issue templates, standardized labels,
        PR workflow, project board, and branch protection -- all configured
        programmatically via PowerShell scripts reading the
        GITHUB_PERSONAL_ACCESS_TOKEN from .env.
      </Text>

      <Stack gap={4}>
        <Badge tone="success">All 15 files created</Badge>
        <Badge tone="success">36 labels live on GitHub</Badge>
        <Badge tone="success">Branch protection active on main</Badge>
        <Badge tone="success">Project board created at projects/2</Badge>
        <Badge tone="success">Verification script passes all checks</Badge>
      </Stack>

      <Text tone="secondary" size="small">
        Generated from plan: GitHub_Structure_Setup_task-3d0.md
      </Text>
    </Stack>
  );
}
