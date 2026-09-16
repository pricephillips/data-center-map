# Pending: run the restriction probe on a machine with egress

`docs/pending_probe_workflow.patch` adds `.github/workflows/restriction-probe.yml`,
which runs `restriction_probe.py --backfill` on a GitHub Actions runner.

Same situation as `docs/pending_ci_wiring.patch`: written, tested, verified to
apply, and not committed because the agent sessions that wrote it cannot commit
under `.github/workflows/`.

## The machine already exists

Nothing needs provisioning. GitHub Actions runners have unrestricted outbound
network, and this repository already relies on that: `fetch_permits.py`,
`bill_sync.py`, `fetch_pudl.py`, `signal_harvest.py` and `census_geocode.py`
all reach hosts (OpenStates, the Census geocoder, GDELT, S3) that a sandboxed
agent session cannot. The municipal code libraries are ordinary public web
sites; a runner reaches them the same way.

So the reason `evidence_grade` is `U` for roughly 3,000 counties is not that no
machine can do the work. It is that nothing had been pointed at the work yet.

## Why it is a separate workflow

The backfill is rate-limited on purpose (`THROTTLE_S` between requests), so a
full pass over 3,222 counties takes hours. That cannot sit inside the
ten-minute feed build. It is resumable: a county already holding a result for
an adapter is skipped, so each run advances the frontier and a run that dies
costs only what it had not reached.

It starts on `workflow_dispatch` only. The schedule is commented out
deliberately, and should stay that way until one manual run has confirmed the
adapter works against the live host.

## The part that is not verified, and why shipping it anyway is safe

`probe_municipal_code()` has never run against a live municipal code library.
Nobody has watched it. Its URL template and response markers in
`configs/restriction_evidence_sources.json` are starting points, and
`implemented` is `false`, so `--backfill` currently probes nothing.

Normally that would make it unshippable. A scraper whose parsing has drifted
returns "no matches found", which looks exactly like a genuine clean check, and
in this layer that marks a county verified that nobody ever read.

The adapter is written to invert that failure mode. **`clear` is the only
result that marks a county checked-and-empty, so it is the only result the
prober refuses to infer.** It returns `clear` only on positive confirmation of
a zero-result search. Every other outcome, including every outcome it did not
anticipate, degrades to `unreachable`, which costs a re-probe and asserts
nothing about the county.

The self-test pins this against an empty body, unrecognized HTML, a login wall,
a JSON error payload, a 404, a 500, a network failure, and an adapter with no
zero-result markers. All eight return `unreachable`. Being wrong is cheap
rather than corrupting.

That is what makes the first live run a verification step rather than a risk.

## Verifying it, which is one cycle

1. Apply the patch and commit:
   ```sh
   git apply docs/pending_probe_workflow.patch
   git add .github/workflows/ && git commit -m "Add restriction probe workflow"
   ```
2. In `configs/restriction_evidence_sources.json`, set the municode adapter's
   `implemented` to `true`.
3. Run the workflow from the Actions tab with `limit: 5`.
4. Read the output. Three cases:
   - **clear and hit results appear** - the markers are right. Raise the limit,
     then uncomment the schedule.
   - **everything is `unreachable`** - expected on a first run. The module says
     so explicitly rather than reporting a clean pass. Each row records why in
     its `detail`; correct `hit_markers` / `zero_result_markers` /
     `search_url_template` against that evidence and re-run.
   - **everything is `not_covered`** - the county slug does not match the
     host's URL shape. Fix `search_url_template`.
5. Once real results land, set `verified_against_live_host` to `true` on the
   adapter so the next reader knows it was confirmed rather than assumed.

## What a successful run changes

Each county that comes back `clear` from the municipal code family gains a
`primary_law` check and moves `U` to `B`, with `label_state` becoming
`asserted_clear` - the first counties in the frame whose absence of a
restriction is a checked claim rather than an inference from silence.

Adding a second independent class on top (a `proceeding` source) takes those to
`A`. See the note below on why the existing meeting-portal feed is not that
second class.

## Why the meeting feed does not help here

`local_meeting_feed.py` already probes five portal platforms and already runs
in CI against live network, and it looks like a ready-made `proceeding`-class
source. It is not, for two reasons worth recording so nobody wires it up
expecting grades to move:

1. **It reads recent and upcoming agendas.** A moratorium adopted in 2024 does
   not appear in a 2026 agenda feed. A clean scan is therefore not evidence of
   absence, and wiring it as `clear` would manufacture exactly the false
   confidence the evidence layer exists to prevent. It can produce `hit`, never
   `clear`.
2. **Its coverage is tiny.** Discovery resolves a platform for 3 of 798
   counties; the rest are `none` (the slug guess missed) or `ambiguous` (the
   bare county name is shared across states and the source APIs expose no state
   field to disambiguate). The live feed currently holds 81 rows across 5
   counties, and none of them is a genuine restriction item.

Improving portal discovery is worthwhile for finding restrictions the tracker
is missing, which is a breadth problem. It is not a route to clearing counties.
