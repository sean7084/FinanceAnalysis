import {
  Callout,
  CollapsibleSection,
  Divider,
  Grid,
  H1,
  H2,
  Stack,
  Stat,
  Table,
  Tag,
  Text,
} from "qoder/canvas";

export default function WslDirectGithubMigrationReport() {
  return (
    <Stack gap={20}>
      <H1>WSL Direct-to-GitHub Migration — Completion Report</H1>
      <Text tone="secondary">
        FinanceAnalysis · spec: WSL_direct_GitHub_migration_task-ccb · October 8, 2026
      </Text>

      <Grid columns={4} gap={16}>
        <Stat value="1" label="Git remote (was 2-hop)" tone="success" description="origin = GitHub" />
        <Stat value="3" label="PRs merged" tone="success" description="#33 · #34 · #35" />
        <Stat value="0 / 0" label="main vs origin/main" tone="success" description="in sync" />
        <Stat value="0" label="CRLF blobs in index" tone="success" description="endings normalized" />
      </Grid>

      <Divider />

      <H2>Accomplishment Summary</H2>
      <Text>
        Retired the two-hop git topology in which the WSL clone's origin was the Windows clone
        (/mnt/c/Users/sean_/Documents/FinanceAnalysis) relaying to GitHub. The WSL clone at
        ~/FinanceAnalysis-wsl2 now talks to GitHub directly. The migration also surfaced and fixed
        drift the hop had hidden: GitHub main was 8 commits ahead (including our own already-merged
        fix da5138a via PR #32), so local main was rebased onto it rather than force-pushed;
        divergent WSL work was preserved; the repo's mixed CRLF/LF line endings were normalized; and
        the docs describing the dead two-hop were corrected.
      </Text>

      <H2>Key Steps</H2>
      <Table
        headers={["Phase", "Action", "Outcome"]}
        rows={[
          ["0 · Connectivity + auth", "Probed GitHub (direct + PassWall localhost:10808; router .117 ports filtered from WSL); set repo-local github.com proxy; wired gh (sean7084) via gh auth setup-git", "Authenticated; proxy scoped to github.com"],
          ["0.5 · Backup", "git bundle create tmp/fa-backup-2026-10-08.bundle --all", "49 MB safety net, all branches"],
          ["1 · Repoint", "origin renamed to windows-peer; added origin=GitHub; fetch --prune (public repo, anonymous read)", "origin/main = b8cc8dd"],
          ["2 · Reconcile", "main had diverged (GitHub +8 incl. merged fix; ours +2 chores on a stale base); rebased our 2 commits onto GitHub main", "Clean rebase, no force-push"],
          ["3 · Land chores", "PR #33: active 2020-01-01 model artifacts + *.code-workspace ignore; CI green; merged --admin", "9e81813 on main"],
          ["4 · Preserve", "Pushed wsl-inline-backtest (2 unique WIP commits) to GitHub", "Divergent work backed up"],
          ["5 · Cleanup", "Deleted merged fix/* + redundant chore/repo-hygiene; removed windows-peer remote", "Two-hop severed"],
          ["6 · Verify", "Audited remotes, branches, sync, connectivity", "One-hop confirmed"],
          ["Hardening", "PR #34: root .gitattributes (* text=auto eol=lf, CRLF for .bat/.cmd, binary markers) + renormalize", "32 CRLF files -> LF; 6d126fd"],
          ["Docs", "PR #35: wsl-services.md + local-setup.md rewritten for the direct flow", "eebba6c on main"],
        ]}
      />

      <Divider />

      <H2>Changed Files & Artifacts</H2>
      <CollapsibleSection title="Git topology & config" defaultOpen>
        <Table
          headers={["Item", "Change"]}
          rows={[
            ["origin remote", "/mnt/c/.../FinanceAnalysis  ->  https://github.com/sean7084/FinanceAnalysis.git"],
            ["windows-peer remote", "added as a safety net, then removed"],
            ["http.https://github.com.proxy", "repo-local = http://localhost:10808 (PassWall)"],
            ["credential helper", "gh auth git-credential (via gh auth setup-git)"],
            ["local branches", "deleted fix/export-docs-connection-retry, fix/coverage-scan-order-by, chore/repo-hygiene"],
          ]}
        />
      </CollapsibleSection>
      <CollapsibleSection title="Committed via PRs #33 / #34 / #35">
        <Table
          headers={["PR", "Files", "Change"]}
          rows={[
            ["#33 · 9e81813", ".gitignore", "broadened to *.code-workspace"],
            ["#33 · 9e81813", "models/lightgbm|lstm/*2020-01-01/", "19 active model artifacts tracked"],
            ["#34 · 6d126fd", ".gitattributes (new)", "* text=auto eol=lf + CRLF for .bat/.cmd + binary markers"],
            ["#34 · 6d126fd", "32 source/config files", "renormalized CRLF -> LF (EOL-only, content unchanged)"],
            ["#35 · eebba6c", "docs/how-to/wsl-services.md, local-setup.md", "two-hop -> direct GitHub flow"],
          ]}
        />
      </CollapsibleSection>

      <Divider />

      <H2>Verification Evidence (final audit)</H2>
      <Table
        headers={["Check", "Result"]}
        rows={[
          ["git remote -v", "only origin = GitHub (windows-peer removed)"],
          ["git branch -vv", "main (eebba6c) + wsl-inline-backtest, both tracking origin"],
          ["main vs origin/main", "0  0 (in sync)"],
          [".gitattributes / EOL", "i/lf w/lf; CRLF blobs in index = 0"],
          ["active models on main", "19 files tracked (2020-01-01 families)"],
          ["gh pr list --open", "none open"],
          ["git fetch --dry-run", "exit 0 via proxy"],
          ["working tree", "clean except 4 intentional untracked 2026-09-21 model dirs"],
        ]}
        rowTone={["success", "success", "success", "success", "success", "success", "success", undefined]}
      />

      <Callout tone="info" title="main history after migration">
        <Stack gap={4}>
          <Text size="small">eebba6c · Merge PR #35 — docs: direct WSL-to-GitHub flow</Text>
          <Text size="small">6d126fd · Merge PR #34 — .gitattributes line-ending normalization</Text>
          <Text size="small">9e81813 · Merge PR #33 — active models + *.code-workspace ignore</Text>
          <Text size="small">b8cc8dd · Merge PR #32 — prior GitHub main tip</Text>
        </Stack>
      </Callout>

      <Divider />

      <H2>Final Outcome</H2>
      <Text>
        <Tag tone="success">Complete</Tag> The two-hop git structure is fully retired: the WSL clone
        is a first-class GitHub client (fetch, push, and PR directly), main is in sync at eebba6c,
        line endings are normalized so the Windows peer stops producing phantom CRLF diffs, and the
        documentation matches the new topology. The Windows clone remains an independent peer (kept,
        not deleted, per your choice).
      </Text>

      <Callout tone="warning" title="Remaining / out of scope (flagged, not done)">
        <Stack gap={6}>
          <Text size="small">
            Windows clone: ~122 files of CRLF churn (self-heals now that .gitattributes is on main)
            plus orphaned commit d172317 on the dead branch feat/serve-spa-from-django — Windows-side cleanup.
          </Text>
          <Text size="small">wsl-inline-backtest (2 WIP commits) still needs reconciliation with main.</Text>
          <Text size="small">Backup bundle tmp/fa-backup-2026-10-08.bundle can be deleted once you are confident.</Text>
          <Text size="small">
            Model artifacts remain tracked in git (grows per retrain); git-LFS or an external store is
            an open BACKLOG item.
          </Text>
        </Stack>
      </Callout>

      <Text tone="secondary" size="small">
        Generated for the WSL_direct_GitHub_migration_task-ccb goal · every claim verified against live
        git and GitHub state.
      </Text>
    </Stack>
  );
}
