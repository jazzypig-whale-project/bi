# Metabase as code

Dashboards and questions on our Metabase instance are managed as files in this
repository. The YAML files under `collections/`, `cards/` and `dashboards/`
are the source of truth; the live Metabase instance is a deployment target,
not a place to make lasting changes by hand.

The instance is Metabase **v0.60.7, Open Source (Community) edition**, sitting
behind an nginx reverse proxy. The proxy requires HTTP Basic auth in addition
to Metabase's own API key — every request carries both an `Authorization:
Basic ...` header and an `X-API-KEY` header.

Because this is the OSS edition, Metabase's own code-first tooling is not
available to us: serialization (`export`/`import`), Remote Sync, and the
agent-driven file-based development workflow documented by Metabase are all
Pro/Enterprise features. The official `mb` CLI (`@metabase/cli@0.3.0`) was
evaluated and rejected: it has no flag or environment variable to send an
extra `Authorization` header, and Node's `fetch` refuses credentials embedded
in a URL, so it simply cannot reach an instance sitting behind Basic auth.
The tool in this repository (`mbc`) therefore talks to the Metabase HTTP API
directly.

The Metabase Representation Format (MBRF, see
<https://github.com/metabase/representations>) was evaluated as the file
format and also rejected: it is keyed on `entity_id`, and OSS Metabase v0.60
silently drops `entity_id` on create, which would leave us with a translation
layer and no actual portability benefit. MBRF remains a useful structural
reference for what a well-formed card or dashboard looks like, but our own
YAML format (documented in `docs/FORMAT.md`) is the wire format we author
against.

The tool is pinned to Metabase v0.60.7. The Metabase API is unversioned, so
an upgrade of the instance may change payload shapes without notice.

## Prerequisites

- Python 3.12
- PyYAML — install with `pip install --requirement requirements.txt`, or use
  the system package (`python3-yaml` on Debian/Ubuntu) if you prefer not to
  use a virtualenv.
- A populated `.env` file (see Configuration below).

## Configuration

Configuration lives in `.env` at the repository root, alongside the bi-stack
secrets (`MB_DB_PASS`, `BI_BACKUP_ENCRYPT_PASSWORD`, `SPACESHIP_API_*`) — see
`.env.example`:

```
METABASE_BASE_URL=https://metabase.example.internal
METABASE_BASIC_USERNAME=...
METABASE_BASIC_PASSWORD=...
METABASE_API_KEY=...
```

`.env` must never be committed — it is `.gitignore`d.

Gotcha: if `METABASE_BASE_URL` has a trailing slash, nginx answers every
request with `400 Ambiguous URI empty segment`. The tool strips a trailing
slash before use, but keep the value clean anyway.

## Repository layout

```
mbc                    entrypoint script
mbcode/                 implementation
collections/            collection definitions, one file per collection
cards/                   question/model/metric definitions
dashboards/             dashboard definitions
.state/<host>.yaml      logical key -> server id mapping, meant to be committed
docs/FORMAT.md          YAML format reference
docs/RESEARCH.md        upstream research notes that led to this tool
```

### Version control

This tool lives inside the `bi` repository (merged in with its own commit
history). Two rules matter: `.state/<host>.yaml` must be committed — losing
it means the next `apply` cannot recognize existing entities and recreates
everything as duplicates — and `.env` must never be, since it holds the API
key and the Basic auth credentials alongside the bi-stack secrets.

## Commands

### `./mbc export [--overwrite]`

Pulls the live instance into YAML under `collections/`, `cards/` and
`dashboards/`, and writes the state file. Read-only against Metabase — it
never modifies the instance. Used once to adopt existing content into the
repository, and afterwards whenever you deliberately want to adopt a change
made in the UI.

Without `--overwrite`, export refuses to clobber files that already exist.
With `--overwrite`, it replaces them with what the instance currently has.

Example:

```
./mbc export --overwrite
```

### `./mbc validate`

Fully offline structural and reference checks: file/key consistency, allowed
fields, valid references between collections, cards and dashboards, dashboard
grid constraints, and so on. Makes no network calls.

Exit codes: `0` clean, `1` on validation error.

```
./mbc validate
```

### `./mbc diff [--json] [--fail-on-orphans]`

Compares the files against the live instance and reports what would change.

Exit codes: **`0` = no difference, `2` = difference found, `1` = error**
(for example: instance unreachable, auth failure). This is what CI keys on —
a build should treat exit code `2` as "drift detected, review needed", not
as a tool failure.

Orphans (entities that exist on the instance but have no file) are reported
but do not affect the exit code by default. Pass `--fail-on-orphans` to make
`diff` exit `2` when orphans are present too, for CI that wants to catch
drift caused by UI-created content, not just changes to managed entities.

```
./mbc diff --json
./mbc diff --fail-on-orphans
```

### `./mbc apply [--yes] [--dry-run] [--allow-duplicate-names]`

Makes the instance match the files: creates, updates, and archives entities
marked `archived: true`. Prompts for confirmation before making changes
unless `--yes` is given; refuses to run unattended (e.g. in CI) without
`--yes`. `--dry-run` shows what would be done without doing it.

If the plan contains a CREATE whose name matches an existing orphan of the
same kind — the signature of an unsafe key rename (see "Renaming a logical
key" below) — `apply` refuses to run and exits `1` with `aborted: refusing
to create duplicates (use --allow-duplicate-names to proceed)`. Pass
`--allow-duplicate-names` to proceed anyway, once you've confirmed the
name collision is intentional and not an unrenamed state-file entry.

```
./mbc apply --yes
```

## Safety guarantees

- The tool never issues `DELETE` against Metabase.
- It never archives anything that isn't explicitly marked `archived: true`
  in the YAML files.
- Whole cards, dashboards and collections that exist on the instance but
  have no corresponding file are reported, never pruned or touched.
- Secrets (`METABASE_API_KEY`, Basic auth credentials) are never printed,
  including under `--verbose`.

**Dashcards are the exception to the guarantee above.** `PUT
/api/dashboard/:id` replaces a dashboard's `dashcards` array wholesale, so
`apply` always sends the full set of dashcards from the file — there is no
way to leave an unrecognized dashcard alone. If someone adds a dashcard to a
managed dashboard through the Metabase UI, the next `apply` deletes it,
because it never existed in the file. To keep a UI-added dashcard, adopt it
first with `./mbc export --overwrite` before running `apply` again.

## Performance

`export`, `diff` and `apply`'s plan-building step read every collection, card
and dashboard the tree tracks. Two things keep that fast:

- **Keep-alive connections.** The HTTP client reuses one connection per
  thread instead of opening a fresh TCP+TLS connection for every request. A
  connection the peer silently closed (idle keep-alive timeout) is retried
  once on a fresh connection for `GET`/`PUT`; `POST` is never retried, since
  retrying a create could double-post the same card or dashboard.
- **Parallel reads.** `export`'s and `diff`'s GETs run through a small thread
  pool (`--jobs`, default 8) instead of one request at a time.

`--yes`/`--dry-run` writes in `apply` (`POST`/`PUT`) always stay strictly
sequential and ordered — only the read phase parallelizes. Pass `--jobs 1` to
force fully sequential reads too (useful with `--verbose`, whose request
tracing interleaves across threads otherwise):

```
./mbc diff --jobs 1
```

## Typical workflows

**Initial adoption.** Run `./mbc export`, review the generated YAML and the
new state file, commit both.

**Making a change.** Edit the relevant YAML file(s), then
`./mbc validate`, `./mbc diff`, and `./mbc apply` once the diff looks right.

**Detecting drift after someone edited in the UI.** Run `./mbc diff`. If the
files should win, run `./mbc apply` to push them back over the UI change. If
the UI change should be kept, run `./mbc export --overwrite` to adopt it into
the files, then commit.

**CI check.** Run `./mbc validate` and `./mbc diff` in the pipeline; fail the
build on exit code `2` (or `1`). Add `--fail-on-orphans` to `diff` if the
pipeline should also fail on UI-created content that isn't in the files.

**Renaming a logical key.** Never rename just the file or just the `key:`
field. The tool identifies entities by logical key via the state file, so a
key rename it doesn't know about looks like a brand new entity plus an
orphaned old one: the next `apply` creates a duplicate under the new key and
leaves the old server object in place, reported as an orphan, instead of
renaming it. To rename safely, in the same commit: rename the file, edit its
`key:` field, and rename the matching entry in `.state/<host>.yaml` from the
old key to the new one. The same applies to dashcard and tab keys inside a
dashboard's state entry — rename the file's `key:`/`tab:` value and the
matching key under that dashboard's `dashcards`/`tabs` map in the state
file. A tab-key rename additionally recreates the tab server-side if the
state file isn't updated to match, since tabs are identified the same way.

If you forget to update the state file, `apply` refuses to run: a CREATE
under the new key with the same name as the now-orphaned old key is
detected as a duplicate-name conflict and blocked unless you pass
`--allow-duplicate-names` (see `./mbc apply` above). Treat that block as a
prompt to fix the state file, not to pass the flag.

## Known limitations

- `collection_position` (the ordering of items inside a collection) is
  deliberately not managed. Metabase reshuffles the position of sibling
  items whenever one item moves, which makes it impractical to author or
  diff reliably.
- `result_metadata` is computed by the server and is never authored in the
  files.
- Dashcard identity depends on the committed state file — see below.
- `lib/uuid` values inside `dataset_query` are regenerated by the server on
  every write and are ignored when comparing files to the instance.
- The Metabase API is unversioned. This tool is pinned to v0.60.7; an
  instance upgrade may require changes to the tool.
- Out of scope entirely: personal collections, permissions, users,
  subscriptions/alerts, snippets, and segments.
- `apply` is not atomic. It makes many HTTP calls, and a mid-run failure can
  leave the instance partially updated. The state file is written after
  each successful create, so re-running `apply` converges, but check the
  diff after any failed run.

## How it works

The tool reads the YAML files, resolves logical references (collection,
card, tab, dashcard keys) to server-assigned ids via the state file,
normalizes both the file content and the instance's current state by
dropping server-generated fields, and issues the minimal set of `POST`/`PUT`
calls needed to reconcile the two.
