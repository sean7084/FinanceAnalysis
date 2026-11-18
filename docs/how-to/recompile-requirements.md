# Recompile the requirement lockfiles

`requirements/base.txt` and `requirements/local.txt` are **generated**. Never hand-edit them:
`scripts/check_requirements_lockfile.py` compares each `.txt` against its `.in` and fails CI
before anything is installed.

There are exactly two pairs, `base` and `local`. `production.in`/`production.txt` were removed
in #149 — nothing installed them (CI and the only Docker image both install `local.txt`) and no
process invokes an ASGI/WSGI server, so their `daphne`/`gunicorn`/`uvicorn` pins described a
deployment that does not happen. `gunicorn` and `uvicorn` still appear in `local.txt` as mlflow
transitive dependencies; that is a closure fact, not a deployment claim.

## Why the lockfiles exist

A transitive dependency must not be allowed to float between CI runs. `celery` only constrains
`kombu` to `<6.0`, and kombu 5.7.0's `redis` extra rejects the `redis==5.0.4` pinned in `base.in`,
so a fresh resolve on 2026-11-03 turned every branch red without a code change. The lockfile pins
the whole closure and carries a hash per wheel, so a package released overnight cannot turn an
unrelated PR red either.

## Interpreter alignment

Both lockfiles must be compiled for the interpreter CI uses. `.github/workflows/ci.yml` sets
`python-version: "3.14"`; the `.txt` headers record which Python `pip-compile` ran under. A
mismatch is not cosmetic: marker evaluation can differ, and a hashed install on a different
interpreter may legitimately reject a marker-excluded package.

If the CI interpreter is not installed locally, cross-compile with `--python-version` rather
than installing a system Python. **Use `uv pip compile`**:

```bash
uv pip compile --generate-hashes --python-version 3.14 \
  -o requirements/base.txt requirements/base.in
uv pip compile --generate-hashes --python-version 3.14 \
  -o requirements/local.txt requirements/local.in
```

Compile `base.in` first — `local.in` starts with `-r base.in`. uv emits no index URL by default,
so the pins stay index-agnostic; pass `--index-url` on the command line if the default index is
unreachable (see `docs/how-to/local-setup.md` for the mirror this host uses), never in the `.in`.

`pip-compile` is the historical generator and still works:

```bash
.venv/bin/pip-compile --generate-hashes --allow-unsafe \
  --no-emit-index-url --no-emit-trusted-host \
  --python-version 3.14 requirements/base.in
```

`--allow-unsafe` pins `setuptools`, which pip otherwise refuses to hash-check; uv has no
equivalent because it does not emit build backends into the lock. Prefer uv for a reason that
is not speed: on 2026-11-18 a `pip-compile --generate-hashes` run for this exact graph wedged on
a stalled TLS read to the mirror CDN — **0.09s of CPU in 70 minutes**, sleeping in `poll` on one
ESTAB socket while the mirror itself answered a fresh request in 0.13s. It was not backtracking
and it was never going to finish. uv resolved the same graph in 55s. If you do use pip-compile,
watch its CPU time, not just its elapsed time: a resolver that is thinking burns CPU, a resolver
that is hung does not.

Both generators write a header naming the target interpreter — pip-compile as
`with Python 3.14`, uv as the `--python-version 3.14` command line it ran.
`scripts/check_pin_drift.py` accepts either and rejects a header naming a different version.

## The pin-drift gate

Re-resolution can silently move a critical pin, and the lockfile checker cannot see it: that
checker compares the `.txt` against the `.in`, and a transitive pin was never named in the `.in`.
Run the drift gate against the committed lockfiles **before committing**:

```bash
python scripts/check_pin_drift.py --self-test   # proves the gate can fail
python scripts/check_pin_drift.py               # worktree vs origin/main
```

It watches `torch`, `mlflow`, `akshare`, `lightgbm`, `channels`, every `nvidia-*`, and `daphne`
(watched because #149 deleted it — a recompile that reintroduces it has undone the decision).
Any move, addition or removal is a non-zero exit. It also requires the generated header to name
the target interpreter.

If a pin moved, either pin it explicitly with `==` in the `.in` and re-resolve, or escalate — do
not accept a silent upgrade because CI happens to stay green. `torch` in particular carries the
CUDA closure (`nvidia-*`, `cuda-bindings`, `triton`); moving it changes what GPU runtime is
possible, and moving `lightgbm` changes whether a GPU device exists at all.

Also confirm the wheel you need actually exists for the target interpreter (e.g. `torch==2.12.0`
ships `cp314-manylinux_2_28_x86_64`).

## Verify

```bash
python scripts/check_requirements_lockfile.py
python scripts/check_pin_drift.py
uv venv --python 3.14 /tmp/verify314
uv pip install --python /tmp/verify314/bin/python --require-hashes -r requirements/local.txt
/tmp/verify314/bin/python manage.py test --settings=config.settings.test
/tmp/verify314/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

The fresh-venv install is the real test: it proves the hashed closure resolves and installs on
the target interpreter, which the text comparison alone cannot show. Run the suite **in that
venv**, not in the dev `.venv` — the dev interpreter here is 3.12, so a suite run there says
nothing about the 3.14 lockfile CI will install.

## After changing an `.in`

Adding or removing a direct dependency means editing the `.in`, recompiling, and committing both
files in the same change. Removing a whole pair additionally means updating `PAIRS` in
`scripts/check_requirements_lockfile.py` and the `cache-dependency-path` list in
`.github/workflows/ci.yml` — the checker iterates `PAIRS` and reports a missing side, and
`setup-python` fails if a listed cache path does not exist.
