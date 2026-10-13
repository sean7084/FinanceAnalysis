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

export default function MinioArtifactStoreMigrationReport() {
  return (
    <Stack gap={20}>
      <H1>MinIO Artifact-Store Migration — Completion Report</H1>
      <Text tone="secondary">
        FinanceAnalysis · spec: MinIO_artifact_store_migration_task-ccb · October 10, 2026
      </Text>

      <Grid columns={4} gap={16}>
        <Stat value="170" label="Artifacts in MinIO" tone="success" description="38 families · ~86 MB" />
        <Stat value="151 → 0" label="Model files tracked in git" tone="success" description="untracked + gitignored" />
        <Stat value="7" label="PRs merged (#37–#43)" tone="success" description="main = dcca4ae" />
        <Stat value="exit 0" label="verify_artifact_store" tone="success" description="all ACTIVE resolve" />
      </Grid>

      <Divider />

      <H2>Accomplishment Summary</H2>
      <Text>
        Closed the git-bloat half of the artifact-storage backlog: trained-model artifacts no longer
        live in the repository. A MinIO S3 object store was deployed on the HomeNAS and the app was
        cut over to it. All 170 on-disk artifacts (38 families, ~86 MB) were migrated to a versioned,
        ZFS-backed bucket behind a least-privilege scoped key; models/ is now an untracked, retrainable
        local cache; the app runs ARTIFACT_STORE_BACKEND=s3. Because existing history keeps its blobs
        (forward-only, per your decision), .git simply stops growing one retained family per retrain.
        This resolves the item the WSL-migration report flagged as open.
      </Text>

      <H2>Key Steps</H2>
      <Table
        headers={["Stage", "Action", "Outcome"]}
        rows={[
          ["Deploy · Recon", "Drove the NAS read-only via scripts/run-ssh.ps1 (plink + full-path powershell.exe from WSL): docker group, ZFS free, 9000/9001 free, live DOCKER-USER pattern", "SSH auth works (no TOTP block); assumptions confirmed"],
          ["Deploy · Infra", "zfs create DefaultMirror/minio (atime=off); /var/docks/minio compose (br-minio pin, mem_limit 1g); 0600 .env with a generated root password", "compose config valid (after moving the bridge pin to top level)"],
          ["Deploy · Image", "Docker Hub blocked at the proxy exit and docker.m.daocloud.io now denies org images → pulled minio/minio + minio/mc via docker.1panel.live, retagged", "finance_artifacts_minio up · RELEASE.2025-09-07"],
          ["Deploy · Firewall", "Backed up the ruleset, then append-only LAN-scoped DOCKER-USER + INPUT rules on br-minio; netfilter-persistent save", "host-local /minio/health/live OK · no SSH impact"],
          ["Deploy · Bucket + key", "mc: bucket finance-analysis-artifacts + versioning + a scoped service account with an inline bucket-only policy", "least-privilege key; secret never printed"],
          ["Cutover · Wire", "Set ARTIFACT_STORE_BACKEND=s3 + endpoint/bucket/key in .env; store smoke test (put/get/exists/list/delete)", "is_remote True against live MinIO"],
          ["Cutover · Migrate", "migrate_artifacts_to_store --execute --normalize-registry", "170 files / 38 families uploaded"],
          ["Cutover · Verify", "verify_artifact_store; cold-load via ensure_dir_local into a temp dir", "exit 0, all ACTIVE resolve; cold-load OK"],
          ["Cutover · Untrack", "git rm -r --cached models/ (151) + gitignore; models.md header → 'Present in store?' + regen; CI-sim (models/ absent)", "PR #42 · 373 tests pass without models/"],
          ["Docs", "FA artifact-store/BACKLOG/CHANGELOG; HomeServer runbook → DEPLOYED + inventory/live-state/secrets rows", "PRs #41/#43 · HomeServer fa5c183"],
        ]}
      />

      <Divider />

      <H2>Changed Files & Artifacts</H2>
      <CollapsibleSection title="NAS (live state, not in git)" defaultOpen>
        <Table
          headers={["Item", "Detail"]}
          rows={[
            ["ZFS dataset", "DefaultMirror/minio (atime=off), on the mirrored pool"],
            ["Compose", "/var/docks/minio/{docker-compose.yml,.env} — dir + .env cliu:cliu, .env 0600"],
            ["Container", "finance_artifacts_minio · bridge br-minio (172.19.0.0/16) · mem_limit 1g · restart unless-stopped"],
            ["Firewall", "DOCKER-USER + INPUT LAN-scoped; backup /root/iptables.bak-minio-20261009-235221"],
            ["Bucket + key", "finance-analysis-artifacts (versioning on); scoped access key id fa8edec8… (bucket-only policy)"],
          ]}
        />
      </CollapsibleSection>
      <CollapsibleSection title="FinanceAnalysis repo (PRs #37–#43)">
        <Table
          headers={["Area", "Change"]}
          rows={[
            ["apps/prediction/artifact_store.py", "local + S3 backends; object key = BASE_DIR-relative artifact_path (#37)"],
            ["tasks_lightgbm.py / tasks_lstm.py", "save→upload_dir, load→ensure_dir_local seams (#37)"],
            ["migrate/sync/verify commands", "3 management commands (#37, #39)"],
            ["config/settings/base.py · requirements · .env.example", "ARTIFACT_* settings + boto3 pin (#37)"],
            ["deploy/minio/ kit", "compose + init-minio.sh + README; NAS-accurate (#38, #41, #43)"],
            ["export_documentation_facts.py · models.md", "store-aware presence + 'Present in store?' header, regenerated (#38, #42)"],
            [".gitignore · models/", "models/ ignored; 151 files untracked (#42)"],
            ["docs + BACKLOG + CHANGELOG", "artifact-store.md, retrain.md, local-setup.md; 0.1.16 (#38, #40, #42)"],
          ]}
        />
      </CollapsibleSection>
      <CollapsibleSection title="HomeServer repo (local commits)">
        <Table
          headers={["File", "Change"]}
          rows={[
            ["docs/runbooks/minio.md", "new runbook (b176b66) → DEPLOYED + deployment log (fa5c183)"],
            ["docs/reference/inventory.md", "9000/9001 rows (fa5c183)"],
            ["docs/reference/live-state.md", "MinIO bullet (fa5c183)"],
            ["docs/reference/secrets-inventory.md", "/var/docks/minio/.env row (fa5c183)"],
            ["README.md", "doc-map row (fa5c183)"],
          ]}
        />
      </CollapsibleSection>

      <Divider />

      <H2>Verification Evidence (final audit)</H2>
      <Table
        headers={["Check", "Result"]}
        rows={[
          ["WSL → MinIO health/live", "OK (LAN path, same NAT route as Postgres)"],
          ["verify_artifact_store", "exit 0 — all ACTIVE resolve; 28 legacy inactive flagged (dead paths, expected)"],
          ["store smoke test", "put/get/exists/list/exists_prefix/delete OK; is_remote True"],
          ["cold-load (ensure_dir_local)", "4 files pulled from MinIO into a temp dir"],
          ["sync_artifacts_from_store", "151 downloaded (19 cached) → cache restored to 32+6"],
          ["migration", "170 files / 38 families / ~86 MB uploaded"],
          ["CI safety", "373 tests pass with models/ absent on the local backend"],
          ["export_documentation_facts --check", "green"],
          ["git", "models/ tracked = 0, gitignored = yes, main == origin (0 0), 0 open PRs"],
          ["secret hygiene", "scoped key never printed; root creds NAS-only (0600 cliu:cliu)"],
        ]}
        rowTone={["success", "success", "success", "success", "success", "success", "success", "success", "success", "success"]}
      />

      <Divider />

      <H2>Final Outcome</H2>
      <Text>
        <Tag tone="success">Complete</Tag> Model artifacts are externalized to a MinIO S3 bucket on the
        NAS — versioned, ZFS-backed, LAN-scoped, least-privilege — and the app reads/writes through the
        store abstraction with a verified cold-load and sync path. models/ is an untracked cache that a
        fresh clone repopulates via sync_artifacts_from_store. The migration is forward-only, so history
        keeps its blobs but .git stops growing per retrain. Every step was verified against the live NAS,
        MinIO, and git state.
      </Text>

      <Callout tone="warning" title="Recommended follow-ups (NAS hardening, flagged not done)">
        <Stack gap={6}>
          <Text size="small">
            homenas-monitor: add a finance_artifacts_minio container check + a :9000/minio/health/live
            probe (runbook §6) so an outage alerts.
          </Text>
          <Text size="small">
            sanoid: add a template for DefaultMirror/minio (runbook §1). The ZFS mirror gives disk-failure
            redundancy but the new dataset has no snapshots yet; artifacts are retrainable, so this is
            hardening rather than critical.
          </Text>
          <Text size="small">
            28 legacy inactive registry rows (dead absolute paths from other hosts) remain — harmless
            historical entries; could be purged.
          </Text>
          <Text size="small">
            Unrelated, still open: the Windows clone's CRLF churn and the wsl-inline-backtest branch
            reconciliation.
          </Text>
        </Stack>
      </Callout>

      <Text tone="secondary" size="small">
        Generated for the MinIO_artifact_store_migration_task-ccb goal · no screenshots (SSH/git/CLI work) ·
        every claim verified against live state.
      </Text>
    </Stack>
  );
}
