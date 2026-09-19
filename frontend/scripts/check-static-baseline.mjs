#!/usr/bin/env node
/**
 * Ratchet gate for the frontend's two static checks.
 *
 * Both commands are clean of errors on the committed tree today: `npm run build`
 * type-checks and bundles, and `npm run lint` reports nine `react-hooks/exhaustive-deps`
 * warnings and no errors. Six TypeScript errors and one ESLint error were once
 * catalogued in BACKLOG.md, and the CI job that ran these commands was made non-blocking
 * with `continue-on-error` so the tree would not be red on arrival. Commit 5883eaf fixed
 * the seven errors and folded both steps into the blocking `frontend` job, but the nine
 * warnings came along for the ride and nothing gates them: `npm run lint` exits 0 with
 * all nine present, so a tenth would arrive the same way they did -- silently.
 *
 * This script is that missing ceiling. It records the current diagnostics in
 * static-baseline.json and fails only when something beyond that baseline appears, so
 * the tree stays green on arrival while any new diagnostic turns it red. With the error
 * counts at zero the build gate is exactly as strict as the bare `npm run build` it
 * replaces; the lint gate keeps the nine known warnings from becoming ten.
 *
 * Usage, from the frontend/ directory:
 *
 *   node scripts/check-static-baseline.mjs build           gate `npm run build`
 *   node scripts/check-static-baseline.mjs lint            gate `npm run lint`
 *   node scripts/check-static-baseline.mjs build --update  re-record this one gate
 *
 * A diagnostic is identified by file, rule code, and a 40-character fingerprint of its
 * message -- deliberately not by line and column. Line numbers shift whenever code is
 * edited above them, so keying on position would turn every unrelated change into a
 * false regression and teach people to ignore the gate. Forty characters is enough to
 * separate every diagnostic currently recorded and short enough to stop before a
 * TypeScript type dump, whose field order churns independently of the error it belongs
 * to.
 *
 * `--update` refuses to raise a count or add a key unless `--allow-increase` is also
 * passed, so absorbing a new failure stays a deliberate, reviewable act rather than a
 * reflex. Lowering the baseline needs no flag.
 *
 * Once the nine remaining warnings are cleared both gates report zero and this script is
 * pure overhead: delete it and static-baseline.json, and put the plain `npm run build`
 * and `npm run lint` back into the two steps of the `frontend` job in
 * .github/workflows/ci.yml.
 */

import { spawnSync } from 'node:child_process'
import { readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const BASELINE_PATH = join(PROJECT_ROOT, 'static-baseline.json')

const ABOUT =
  'Recorded baseline for scripts/check-static-baseline.mjs. A diagnostic is keyed by ' +
  'file, rule code and a 40-character message fingerprint, never by line and column, ' +
  'so the baseline survives edits that merely move code. Regenerate one gate with ' +
  '`node scripts/check-static-baseline.mjs <build|lint> --update`; raising a count or ' +
  'adding a key also requires --allow-increase. Delete this file and this script, and ' +
  'restore the plain npm scripts in the frontend CI job, once the warnings listed here ' +
  'are cleared too.'

// See the header for why 40 and not the whole message.
const FINGERPRINT_LENGTH = 40

const SEVERITIES = ['error', 'warning']

const GATES = {
  build: {
    command: 'npm run build',
    parser: 'tsc',
    // The tsc/vite output is the diagnosis, so it goes to the log verbatim.
    echoRawOutput: true,
    // `tsc --build` is incremental and caches its state in `.tsbuildinfo` (configured
    // here as node_modules/.tmp/tsconfig.{app,node}.tsbuildinfo). A warm run is not a
    // measurement of the current tree: it can skip a project it considers up to date,
    // or replay diagnostics recorded against an older revision of the source. This is
    // not hypothetical -- recording this baseline began with `npm run build` reporting
    // six TypeScript errors, and the same command reporting none once the build-info
    // files were removed, with `tsc -p tsconfig.app.json --noEmit` and `tsc -b --force`
    // both exiting 0 in confirmation. A gate built on that first reading would have
    // recorded six phantom errors. Clearing the cache makes every run cold and
    // deterministic, which is also exactly what CI gets from a fresh `npm ci`. Both
    // locations are covered: the configured `node_modules/.tmp/` and the project root,
    // where tsc writes the file when `tsBuildInfoFile` is unset.
    staleCacheRoots: ['node_modules/.tmp', '.'],
  },
  lint: {
    // JSON rather than stylish: a gate must not depend on a formatter's column layout.
    // The script prints its own digest instead of the raw report.
    command: 'npm run lint -- --format json',
    parser: 'eslint-json',
    echoRawOutput: false,
    // eslint caches nothing here (`--cache` is not passed), so there is nothing to clear.
    staleCacheRoots: [],
  },
}

// tsc prints `path(line,col): error TS1234: message`, and reports a few project-level
// failures with no file at all.
const TSC_POSITIONAL = /^(.+?)\((\d+),(\d+)\): (error|warning) (TS\d+): (.*)$/
const TSC_BARE = /^(error|warning) (TS\d+): (.*)$/

function usage(message) {
  if (message) process.stderr.write(`${message}\n\n`)
  process.stderr.write(
    'Usage: node scripts/check-static-baseline.mjs <build|lint> [--update] [--allow-increase]\n',
  )
  process.exit(2)
}

function toPosix(path) {
  return path.split(sep).join('/')
}

// tsc emits paths relative to the frontend root; eslint's JSON report emits absolute
// ones. Both are normalised so a baseline recorded on one platform matches a run on
// another.
function relativeToRoot(path) {
  return toPosix(relative(PROJECT_ROOT, resolve(PROJECT_ROOT, path)))
}

function fingerprint(message) {
  const flat = String(message).replace(/\s+/g, ' ').trim()
  return flat.length > FINGERPRINT_LENGTH
    ? flat.slice(0, FINGERPRINT_LENGTH).trimEnd()
    : flat
}

function parseTsc(output) {
  const found = []
  for (const line of output.split(/\r?\n/)) {
    const positioned = TSC_POSITIONAL.exec(line.trimEnd())
    if (positioned) {
      found.push({
        file: relativeToRoot(positioned[1]),
        severity: positioned[4],
        code: positioned[5],
        message: positioned[6],
      })
      continue
    }
    // Continuation lines of a multi-line tsc error are indented and carry no code, so
    // they fall through here and are skipped.
    const bare = TSC_BARE.exec(line.trim())
    if (bare) {
      found.push({ file: '<compiler>', severity: bare[1], code: bare[2], message: bare[3] })
    }
  }
  return found
}

function parseEslintJson(stdout) {
  // npm prints its `> frontend@0.0.0 lint` preamble ahead of the report, and can print
  // an error footer after it, so the array is sliced out rather than assumed to be the
  // whole stream.
  const open = stdout.indexOf('[')
  const close = stdout.lastIndexOf(']')
  if (open < 0 || close <= open) return []

  let report
  try {
    report = JSON.parse(stdout.slice(open, close + 1))
  } catch {
    return []
  }
  if (!Array.isArray(report)) return []

  const found = []
  for (const file of report) {
    const relativeFile = relativeToRoot(file.filePath ?? '')
    for (const message of file.messages ?? []) {
      found.push({
        file: relativeFile,
        severity: message.severity === 2 ? 'error' : 'warning',
        // A fatal parse error carries no ruleId; it must still be keyed, not dropped.
        code: message.ruleId ?? '<fatal>',
        message: message.message ?? '',
      })
    }
  }
  return found
}

function keyOf(diagnostic) {
  return `${diagnostic.file}::${diagnostic.code}::${fingerprint(diagnostic.message)}`
}

function aggregate(diagnostics) {
  const counts = { error: 0, warning: 0 }
  const byKey = new Map()

  for (const diagnostic of diagnostics) {
    counts[diagnostic.severity] = (counts[diagnostic.severity] ?? 0) + 1
    const key = keyOf(diagnostic)
    const entry = byKey.get(key) ?? { key, severity: diagnostic.severity, count: 0 }
    entry.count += 1
    byKey.set(key, entry)
  }

  return {
    counts,
    total: diagnostics.length,
    // Sorted so two runs of --update produce an identical file and a reviewable diff.
    diagnostics: [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key)),
  }
}

function readBaselineFile() {
  let text
  try {
    text = readFileSync(BASELINE_PATH, 'utf8')
  } catch (error) {
    if (error.code === 'ENOENT') return { about: ABOUT, gates: {} }
    throw error
  }
  const parsed = JSON.parse(text)
  return { about: parsed.about ?? ABOUT, gates: parsed.gates ?? {} }
}

function serializeGate(gateName, observed) {
  return {
    command: GATES[gateName].command,
    counts: { error: observed.counts.error ?? 0, warning: observed.counts.warning ?? 0 },
    diagnostics: observed.diagnostics.map((entry) => ({
      key: entry.key,
      severity: entry.severity,
      count: entry.count,
    })),
  }
}

function countBySeverity(entries) {
  const counts = { error: 0, warning: 0 }
  for (const entry of entries) counts[entry.severity] = (counts[entry.severity] ?? 0) + entry.count
  return counts
}

function describeCounts(counts) {
  return `${counts.error ?? 0} error(s), ${counts.warning ?? 0} warning(s)`
}

// Deliberately narrow: two known directories, one extension, no recursion into the
// wider node_modules tree. A build info file is a cache and is always safe to drop.
function clearStaleCache(roots) {
  const removed = []
  for (const root of roots) {
    const directory = join(PROJECT_ROOT, root)
    let entries
    try {
      entries = readdirSync(directory, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.endsWith('.tsbuildinfo')) continue
      const path = join(directory, entry.name)
      rmSync(path, { force: true })
      removed.push(relativeToRoot(path))
    }
  }
  return removed
}

function runGate(gate) {
  const cleared = clearStaleCache(gate.staleCacheRoots ?? [])
  if (cleared.length > 0) {
    process.stdout.write(`Cleared stale incremental build cache: ${cleared.join(', ')}\n`)
  }

  const result = spawnSync(gate.command, {
    cwd: PROJECT_ROOT,
    // shell:true so `npm` resolves to npm.cmd on Windows and to npm on the ubuntu runner
    // without this script having to care.
    shell: true,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  if (result.error) {
    process.stderr.write(`Could not run \`${gate.command}\`: ${result.error.message}\n`)
    process.exit(2)
  }
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
  }
}

function fail(message) {
  // ::error:: is a GitHub Actions annotation; it is plain text anywhere else.
  process.stdout.write(`::error::${message}\n`)
  process.exit(1)
}

function notice(message) {
  process.stdout.write(`::notice::${message}\n`)
}

function printDigest(gateName, gate, run, observed, baseline) {
  process.stdout.write(`\n${gateName} gate\n`)
  process.stdout.write(`  command    ${gate.command}\n`)
  process.stdout.write(`  exit code  ${run.status}\n`)
  process.stdout.write(`  parsed     ${describeCounts(observed.counts)} in ${observed.diagnostics.length} distinct diagnostic(s)\n`)
  if (baseline) {
    process.stdout.write(`  baseline   ${describeCounts(countBySeverity(baseline.diagnostics ?? []))} in ${(baseline.diagnostics ?? []).length} distinct diagnostic(s)\n`)
  }

  if (observed.diagnostics.length > 0) {
    process.stdout.write('\n  observed diagnostics:\n')
    for (const entry of observed.diagnostics) {
      const recorded = (baseline?.diagnostics ?? []).find((candidate) => candidate.key === entry.key)
      const mark = !recorded ? 'NEW' : entry.count > recorded.count ? 'OVER' : 'ok  '
      process.stdout.write(`    [${mark}] x${entry.count} ${entry.severity} ${entry.key}\n`)
    }
  }
  process.stdout.write('\n')
}

// --- main -------------------------------------------------------------------------

const argv = process.argv.slice(2)
const positional = argv.filter((arg) => !arg.startsWith('--'))
const flags = argv.filter((arg) => arg.startsWith('--'))

if (positional.length !== 1) usage('Expected exactly one gate name.')
const gateName = positional[0]
if (!Object.hasOwn(GATES, gateName)) usage(`Unknown gate '${gateName}'.`)

const update = flags.includes('--update')
const allowIncrease = flags.includes('--allow-increase')
for (const flag of flags) {
  if (flag !== '--update' && flag !== '--allow-increase') usage(`Unknown option '${flag}'.`)
}
if (allowIncrease && !update) usage('--allow-increase only applies to --update.')

const gate = GATES[gateName]
const run = runGate(gate)

if (gate.echoRawOutput) {
  if (run.stdout) process.stdout.write(run.stdout)
  if (run.stderr) process.stderr.write(run.stderr)
}

const parsed = gate.parser === 'tsc' ? parseTsc(`${run.stdout}\n${run.stderr}`) : parseEslintJson(run.stdout)
const observed = aggregate(parsed)
const baselineFile = readBaselineFile()
const baseline = baselineFile.gates[gateName]

// The load-bearing guard, and the same lesson as the MIN_TEST_COUNT floors elsewhere in
// ci.yml: a gate that parses nothing from a failing run would otherwise report success.
// `vite build` blowing up after a clean type-check lands here too, which is the point --
// this gate owns the whole `npm run build` exit status, not just the tsc half of it.
if (run.status !== 0 && observed.total === 0) {
  printDigest(gateName, gate, run, observed, baseline)
  fail(
    `${gateName}: \`${gate.command}\` exited ${run.status} but this gate parsed no diagnostics from it. ` +
      'Either the failure is not a recorded TypeScript/ESLint diagnostic (a bundler, config or spawn ' +
      'error), or the parser no longer matches the tool output. Both are real failures; neither may pass silently.',
  )
}

printDigest(gateName, gate, run, observed, baseline)

if (update) {
  const previous = baselineFile.gates[gateName]
  const increases = []
  if (previous) {
    const previousCounts = countBySeverity(previous.diagnostics ?? [])
    for (const severity of SEVERITIES) {
      const now = observed.counts[severity] ?? 0
      const before = previousCounts[severity] ?? 0
      if (now > before) increases.push(`${severity} count ${before} -> ${now}`)
    }
    const previousByKey = new Map((previous.diagnostics ?? []).map((entry) => [entry.key, entry.count]))
    for (const entry of observed.diagnostics) {
      const before = previousByKey.get(entry.key) ?? 0
      if (entry.count > before) increases.push(`x${entry.count - before} beyond baseline: ${entry.key}`)
    }
  }

  if (increases.length > 0 && !allowIncrease) {
    fail(
      `${gateName}: refusing to record a looser baseline (${increases.join('; ')}). ` +
        'Fix the new diagnostics, or re-run with --allow-increase and state the reason in the commit.',
    )
  }

  baselineFile.about = ABOUT
  baselineFile.gates[gateName] = serializeGate(gateName, observed)
  writeFileSync(BASELINE_PATH, `${JSON.stringify(baselineFile, null, 2)}\n`, 'utf8')
  process.stdout.write(
    `Recorded ${relativeToRoot(BASELINE_PATH)} [${gateName}]: ${describeCounts(observed.counts)} ` +
      `in ${observed.diagnostics.length} distinct diagnostic(s).\n`,
  )
  process.exit(0)
}

if (!baseline) {
  fail(
    `${gateName}: no baseline recorded in ${relativeToRoot(BASELINE_PATH)}. ` +
      `Create one with \`node scripts/check-static-baseline.mjs ${gateName} --update\`.`,
  )
}

const regressions = []
const baselineByKey = new Map((baseline.diagnostics ?? []).map((entry) => [entry.key, entry.count]))
for (const entry of observed.diagnostics) {
  const allowed = baselineByKey.get(entry.key) ?? 0
  if (entry.count > allowed) regressions.push({ ...entry, allowed })
}

// Counts are gated independently of the keys: a diagnostic that keeps its identity but
// changes severity, or a flood of identical new failures in a file already on the list,
// must not slip through a per-key comparison alone.
const countRegressions = []
const baselineCounts = countBySeverity(baseline.diagnostics ?? [])
for (const severity of SEVERITIES) {
  const now = observed.counts[severity] ?? 0
  const allowed = baselineCounts[severity] ?? 0
  if (now > allowed) countRegressions.push(`${severity}s ${allowed} -> ${now}`)
}

const cleared = []
for (const [key, allowed] of baselineByKey) {
  const now = observed.diagnostics.find((entry) => entry.key === key)?.count ?? 0
  if (now < allowed) cleared.push(`${key} (${allowed} -> ${now})`)
}

if (regressions.length > 0 || countRegressions.length > 0) {
  for (const entry of regressions) {
    process.stdout.write(`  beyond baseline: x${entry.count} (allowed ${entry.allowed}) ${entry.severity} ${entry.key}\n`)
  }
  fail(
    `${gateName}: static-check regression. ${
      countRegressions.length > 0 ? `Counts moved past the baseline (${countRegressions.join(', ')}). ` : ''
    }${regressions.length} diagnostic(s) are new or more frequent than ${relativeToRoot(BASELINE_PATH)} records. ` +
      'Fix them; do not re-record the baseline to make this pass.',
  )
}

if (cleared.length > 0) {
  notice(
    `${gateName}: ${cleared.length} recorded diagnostic(s) no longer occur at their recorded count: ${cleared.join('; ')}. ` +
      `Tighten the baseline with \`node scripts/check-static-baseline.mjs ${gateName} --update\`.`,
  )
}

if (observed.total === 0) {
  process.stdout.write(
    `${gateName}: clean. Nothing left to ratchet -- if the lint gate is clean too, delete this script and ` +
      'static-baseline.json, and put the plain `npm run build` and `npm run lint` back into the frontend job.\n',
  )
} else {
  process.stdout.write(`${gateName}: within the recorded baseline.\n`)
}

process.exit(0)
