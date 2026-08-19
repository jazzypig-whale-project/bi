# Upstream research: Metabase dashboards-as-code

Research notes from 2026-08-18, written before this tool existed. Kept as a
record of what upstream Metabase offers, what it costs, and why we ended up
writing our own tool instead of using any of it.

**This document is historical.** For how the repository works today, see the
top-level `README.md` and `docs/FORMAT.md`. Where the notes below describe an
intended approach, a "Superseded" paragraph records what we actually did.

Question: can we describe Metabase dashboards as code (create / modify /
delete), instead of clicking the UI or driving MCP interactively?

Answer: yes. Metabase supports a code-first workflow officially. Part of it is
paid, but the part we need works on the Open Source edition.

## 1. What exists upstream

### 1.1 Metabase Representation Format (MBRF)

Metabase represents user-created content as a tree of YAML files — one file per entity (collection, card, dashboard, model, transform). Numeric database IDs are replaced by human-readable names and entity IDs, so the files are portable between instances.

* Spec, JSON Schemas per entity type, examples, NPM validation package: <https://github.com/metabase/representations>
* Used by serialization and Remote Sync (both paid — see 1.3).

### 1.2 Official CLI: `mb`

* Repo: <https://github.com/metabase/metabase-cli>
* Install: `npm install --global @metabase/cli`
* Auth: `mb auth login` (browser OAuth on v63+) or API key for scripting/CI. Credentials stored per named profile in the OS keychain when available.
* Minimum Metabase version: **v0.58**. Most commands work on **OSS v0.58+**.
* Relevant commands:
  * `mb db list`, `mb db schemas <id>`, `mb table get <id>` — schema discovery
  * `mb card` — CRUD on questions / models / metrics
  * `mb dashboard` — create and modify dashboards
  * `mb collection` — manage collections
  * `mb snippet` — reusable SQL fragments
* Request bodies are JSON, passed via `--body` (inline), `--file <path>` or stdin.
* Enterprise-only subcommands: `mb git-sync` (Remote Sync), `mb library publish`.

### 1.3 Paid-only pieces (not available to us)

| Feature | Availability |
| --- | --- |
| Serialization (`java -jar metabase.jar export/import`, `POST /api/ee/serialization/export`, `POST /api/ee/serialization/import`) | Pro / Enterprise only |
| Remote Sync (Metabase pushes/pulls content YAML to a git repo, dev→prod, branch management in the UI) | Pro / Enterprise only |
| "Agent-driven / file-based development" workflow as documented by Metabase | Pro / Enterprise only — it requires Remote Sync |

Docs:
* Serialization: <https://www.metabase.com/docs/latest/installation-and-operation/serialization>
* Remote sync: <https://www.metabase.com/docs/latest/installation-and-operation/remote-sync>
* Agent-driven development: <https://www.metabase.com/docs/latest/ai/file-based-development>
* Feature announcement ("dashboards-as-code"), Metabase 61, May 2026: <https://www.metabase.com/releases/metabase-61>
* Official agent skill: <https://github.com/metabase/agent-skills/tree/main/skills/metabase-representation-format>

Remote Sync limitations, for reference if we ever buy Pro: one branch at a time per Metabase instance, unidirectional dev→prod (read-only instances cannot push back), synced collections must be self-contained (all dependencies inside the synced content), only admins manage branches, table metadata does not sync.

### 1.4 Terraform providers (alternative, community)

* <https://github.com/flovouin/terraform-provider-metabase> — collections, cards, dashboards; ships `mbtf`, which imports existing dashboards/cards from a live instance into Terraform definitions. Supports Metabase .57, .58, .60, .61, .62, .63.
* <https://registry.terraform.io/providers/bnjns/metabase/latest/docs>
* <https://registry.terraform.io/providers/getniagra/metabase/latest/docs>

Caveat stated by the flovouin author: the Metabase API is not versioned and is subject to breaking changes, so provider/Metabase version pairs must be tracked.

## 2. Decision for our OSS instance

Code-first, without Remote Sync:

* **Source of truth**: files in git in this directory.
* **Apply mechanism**: `mb` CLI against the Metabase API (fallback: plain `curl` with `X-API-KEY`).
* **MCP**: used only for exploration and verification (browsing schema, checking the result), never as the way changes are made.

Note on format: MBRF YAML is consumed by serialization / Remote Sync, which we do not have. On OSS the `mb card` / `mb dashboard` commands take **JSON bodies matching the API shape**. So our files are authored as YAML for readability and converted to that JSON by the apply script — the MBRF schemas are a useful reference for structure, but not the wire format in our case.

Proposed layout:

```
metabase/
  INIT.md                 # this file
  .env                    # METABASE_URL, METABASE_API_KEY (git-ignored)
  cards/<name>.yaml       # questions
  dashboards/<name>.yaml  # dashboards, referencing cards by name
  apply.sh                # idempotent create-or-update against the instance
```

Trade-offs accepted:

* No PR-gated deploy inside Metabase itself; review happens on our repo, `apply.sh` is the deploy step.
* Drift is possible — anyone editing a dashboard in the UI diverges from the files. Mitigation: treat the repo as authoritative and re-apply; optionally add a `diff` mode later.
* The Metabase API is unversioned; an upgrade may break the payload shape. Pin the `@metabase/cli` version and record the Metabase version this was built against.

**Superseded.** The overall decision held — files in git are the source of
truth, MCP is for exploration only — but two parts of it did not survive
contact with the instance:

* The `mb` CLI (`@metabase/cli@0.3.0`) cannot reach our instance at all. It
  offers no way to send an extra `Authorization` header, and our Metabase sits
  behind an nginx reverse proxy that requires HTTP Basic auth on top of the
  API key. We talk to the HTTP API directly instead, from the `mbc` tool in
  this repository.
* MBRF was rejected as the file format: it is keyed on `entity_id`, and OSS
  v0.60 silently drops `entity_id` on create. Our own YAML format is
  documented in `docs/FORMAT.md`.

The layout also grew: `collections/` alongside `cards/` and `dashboards/`, a
committed `.state/<host>.yaml` mapping logical keys to server ids, and
`export` / `validate` / `diff` / `apply` subcommands of `mbc` rather than a
single `apply.sh`. The `diff` mode floated as optional above turned out to be
essential, since drift detection is what makes the repo authoritative in
practice.

## 3. Open items before implementation

1. Metabase base URL.
2. API key location — keep it in `metabase/.env` (git-ignored) or the shell environment, not in the repo or in chat.
3. Confirm the instance version and edition from `/api/session/properties` (`version.tag`, `token-features`) once URL and key are available; `mb` requires v0.58+.
4. Decide which dashboards to build first and against which database.

**Resolved.** The instance is `https://bi.apple-certificate.solutions:8443`,
Metabase v0.60.7, Open Source (Community) edition. Credentials live in `.env`
(git-ignored) — base URL, Basic auth username/password, and API key. Existing
content was adopted with `mbc export` rather than authored from scratch.
