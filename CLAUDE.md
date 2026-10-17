# Conventions for this repository

An async client for Hunter Douglas / Luxaflex PowerView Gen 3 gateways, over their local HTTP API
and event stream. What a real gateway was measured to do, and what the design takes from it:
`docs/protocol.md`. Measured behaviour is the source of truth: a move sends only its own rails,
the event stream says when a move starts and stops, and a shade is polled only while it moves.

## Comments and docstrings

Based on PEP 8 (Comments), PEP 257 (Docstring Conventions) and the Google Python Style Guide
(3.8 Comments and Docstrings).

**Docstrings describe the unit's own contract, and nothing else.**
- Every public module, class, function and method has one.
- The first line is a one-sentence summary. Add more only when a caller needs it: arguments,
  return value, exceptions raised, side effects, invariants.
- Use Google style sections (`Args:`, `Returns:`, `Raises:`) only when they say something the
  signature and type hints do not. Never repeat types; the hints carry them.
- Describe what the unit does, not who calls it, how other classes use its data, or where its
  result ends up. That knowledge belongs to those other units and goes stale here.

**Comments explain why, never what.**
- Write one only where the code cannot say it: a protocol quirk, a non-obvious constraint, the
  reason for an order of operations.
- No history: no "used to", "previously", "the old code", "this was a bug". Version control
  holds history; a regression test's name holds the reason it exists.
- No references to other files, classes, line numbers or design-document sections. They rot the
  moment the other side changes.
- No commented-out code. No TODO without an issue reference.

**Keep them short.** A comment longer than a few lines is design rationale; it belongs in
`docs/`, where it can cover several modules at once.

**Tests.** The test name states the behaviour. Add a docstring only when the reason is not
obvious from the name - one or two lines, no narrative.

## Other conventions

- pyright strict must report 0 errors (`pyright`); the configuration is in `pyproject.toml`.
  The release scripts are a project of their own: `pyright -p .github/scripts`.
  No `# type: ignore` and no rule turned off.
- Every line and branch of the package is covered by the tests, and stays so: `pytest` fails
  below 100 % (`--cov`, configured in `pyproject.toml`).
- Every problem found gets a test that fails without the fix.
- aiohttp is the only runtime dependency. No Home Assistant imports.
- The tests run the real client against the simulated gateway (`powerview_connect.testing`), an
  HTTP server on 127.0.0.1 that behaves as a real gateway was measured to; captures from a real
  gateway are the fixtures of the parsing and motion tests. Every capture is kept whole,
  anonymized as `captures/README.md` says, in `captures/`. A change to what the gateway is
  known to do changes `docs/protocol.md`, the simulated gateway and the captures together.
- Live tests (`tests/test_live_*.py`) only read - `GET /home` and the event stream, with a
  read-only client - never move a shade, and skip themselves unless a gateway is configured.
  CI runs `pytest -m "not live"`.
- Test tools are pinned in `requirements-test.txt`: `pip install -e . -r requirements-test.txt`.
- Tests import the installed package, as its users do, never `src.<package>`: a packaging
  mistake then fails a test before a release.
- No real IP addresses, hostnames, serial numbers or email addresses in tracked files; real
  values live in the gitignored `mysecrets.py` or in environment variables. Examples use the
  documentation ranges, such as 192.0.2.0/24.

## Releasing

**The version is the release's tag, and the draft release is never published by hand.** The
package has no version in a file: setuptools-scm works it out from the tags when it is built, so a
checkout between releases says a development version such as `0.2.1.dev3+g1a2b3c4`, and
`__version__` reads it from the installed package.

1. Merge pull requests into `main`. Release Drafter keeps a draft release proposing the next
   version: a minor bump for the label `breaking-change` or `minor`, a patch otherwise.
   **A new major version is never worked out**, as a label placed wrongly must not release one;
   type it into Release as the override.
2. Run the **Release** workflow from the Actions tab, on `main`, and choose **release
   candidate** or **final release**. It works out the version: the next release candidate of the
   open series (`0.2.0rc1` -> `0.2.0rc2`), or the final release that ends it (`0.2.0`); a draft
   naming a higher version starts a new series. Type a version only for a new major, or to
   override one worked out wrongly: canonical PEP 440, of the kind chosen.

Release refuses a version with no pull request merged since the release before it - for a
final release, the final release before it.

Release refuses any version not higher than every version tagged or on PyPI, yanked ones
included. It runs the tests on the commit it releases, tags that commit, builds, installs the
wheel and checks that `__version__` and the metadata say the version, and only then pushes the
tag - nothing is pushed to `main`. Then PyPI, then the GitHub release, marked as a pre-release
where it is one.

A release candidate's notes are written by GitHub from the pull requests merged since the
previous release candidate or final release, grouped by label as `.github/release.yml` says. A
final release's notes are the draft's: edit the draft - it lists what was merged since the last
final release - and run Release before anything else is merged, as a merge rewrites the draft.
Without a draft, GitHub writes them from the pull requests since the previous final release.

PyPI trusted publishing is bound to this repository, the file `release.yml` and the `pypi`
environment: renaming either breaks the upload. Actions are referred to by their major version;
Dependabot proposes a new major, and updates to the test tools, as pull requests.

## Branches and pull requests

- Every change goes through a branch and a pull request: `main` takes no push. A ruleset
  (`.github/rulesets/main.json`) enforces it; its only bypass is the owner's, and only for
  merging a pull request. A change to what a user gets - the library's code or documentation -
  comes with its tests.
- The Home Assistant integration (`HairingX/ha_powerview_connect`) is developed together with this
  library on a branch of the same name in each repository: its CI then tests with this
  library's branch.
- Name every branch by the kind of change, as the release draft's labels follow the name:
  `feature/<what>` is labelled `feature request`, `fix/<what>` is labelled `bug`,
  `docs/<what>` is labelled `documentation`, and `chore/<what>` - CI, tooling, anything that
  stays in the repository - is labelled `chore`. The release notes list features and fixes;
  documentation, chores and Dependabot's `dependencies` are left out. A change to what a user
  installs is a `fix/` or a `feature/`, never a `chore/`.
- A pull request's text describes only its own change: what it does and how it was tested. Never
  releases to come, merge order, or other repositories.
- Put `breaking-change` on a pull request that breaks the API - a `feature/` or `fix/` one, as a
  chore is left out of the notes - and `minor` on one that needs a minor bump; both give a minor
  bump, and a patch is the default. No label gives a major.
