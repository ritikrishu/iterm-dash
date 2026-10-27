"""Made-up sessions so the UI can be built, checked and recorded without iTerm2.

Six Claude Code sessions work through scripted tasks (one stops to ask a question, two finish and
pick up again) next to one dev-server shell. Statuses follow a fixed timeline measured from server
start, so a recording made right after the server starts always shows the same story.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
import time
from dataclasses import dataclass

from iterm_dash.hub import Hub, ScreenFrame
from iterm_dash.registry import SessionInfo
from iterm_dash.render import Row
from iterm_dash.status import classify

_log = logging.getLogger(__name__)
COLS, ROWS = 214, 57
CYCLE_SECONDS = 48.0
_SPINNER = "·✢✳✶✻✽"
_FG, _BG = "#d8dee9", "#16181d"
_DIM, _ORANGE, _GREEN, _BLUE, _PINK, _CYAN = "#7d8590", "#d97757", "#4eba65", "#7aa2f7", "#e06c9f", "#56b6c2"
_ADD_BG, _DEL_BG, _USER_BG = "#1f3a28", "#45222a", "#262a33"
_BOLD, _FAINT, _STRIKE = 1, 8, 16


def _run(text: str, fg: str | None = None, bg: str | None = None, flags: int = 0) -> list:
    return [text, fg, bg, flags]


# -- transcript blocks ------------------------------------------------------------------------------
def _user(text: str) -> list[Row]:
    return [[_run(f"> {text}".ljust(COLS), _FG, _USER_BG)], []]


def _say(text: str) -> list[Row]:
    lines = textwrap.wrap(text, COLS - 4)
    rows = [[_run("● "), _run(lines[0])]] + [[_run("  " + line)] for line in lines[1:]]
    return rows + [[]]


def _tool(name: str, arg: str, result: str) -> list[Row]:
    return [
        [_run("● ", _GREEN), _run(name, None, None, _BOLD), _run(f"({arg})")],
        [_run("  ⎿  ", _DIM), _run(result, _DIM)],
        [],
    ]


def _diff(path: str, first: int, lines: list[str]) -> list[Row]:
    adds = sum(line.startswith("+") for line in lines)
    dels = sum(line.startswith("-") for line in lines)
    rows: list[Row] = [
        [_run("● ", _GREEN), _run("Update", None, None, _BOLD), _run(f"({path})")],
        [_run("  ⎿  ", _DIM), _run(f"Updated {path} with {adds} additions and {dels} removals", _DIM)],
    ]
    n = first
    for line in lines:
        sign, code = line[:1], line[1:]
        bg = {"+": _ADD_BG, "-": _DEL_BG}.get(sign)
        body = f" {sign if bg else ' '} {code}".ljust(COLS - 14)
        rows.append([_run(f"      {n:>5}", _DIM), _run(body, None, bg)])
        if sign != "-":
            n += 1
    return rows + [[]]


def _todos(items: list[tuple[str, str]]) -> list[Row]:
    rows: list[Row] = [[_run("● ", _GREEN), _run("Update Todos", None, None, _BOLD)]]
    for i, (state, text) in enumerate(items):
        lead = "  ⎿  " if i == 0 else "     "
        if state == "done":
            rows.append([_run(lead, _DIM), _run(f"☒ {text}", _DIM, None, _STRIKE)])
        elif state == "now":
            rows.append([_run(lead, _DIM), _run(f"☐ {text}", None, None, _BOLD)])
        else:
            rows.append([_run(lead, _DIM), _run(f"☐ {text}")])
    return rows + [[]]


def _ran(count: int) -> list[Row]:
    return [[_run(f"  Ran {count} shell commands", _DIM)], []]


@dataclass(frozen=True)
class _Question:
    topic: str
    text: str
    options: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Project:
    title: str
    verb: str
    pace: float  # seconds between transcript blocks while working
    started_ago: float  # how long the first status has been in effect at startup
    script: tuple[list[Row], ...]
    timeline: tuple[tuple[float, str], ...]  # (second within the cycle, status), first at 0
    summary: list[Row]
    resume: list[Row]
    question: _Question | None = None
    history: tuple[list[Row], ...] = ()  # an earlier turn, so the screen starts full


_PROJECTS = (
    _Project(
        title="Idempotent retries for payments API",
        history=(
            _user("run the payments test suite and tell me what's slow"),
            _tool("Bash", "pytest tests/payments --durations=5 -q", "38 passed in 4.12s · slowest: test_refund_flow 1.9s"),
            _say(
                "Most of the time goes to test_refund_flow, which sleeps while it waits for the webhook. I swapped the sleep"
                " for the fake clock, and the suite now runs in 2.3s."
            ),
            _diff("tests/payments/test_refunds.py", 64, ["-    time.sleep(1.5)", "+    clock.advance(seconds=1.5)"]),
        ),
        verb="Wiring retries",
        pace=2.6,
        started_ago=7 * 60 + 12,
        timeline=((0, "working"), (30, "idle"), (40, "working")),
        script=(
            _user("add idempotency keys to POST /v1/charges so a client retry can never double-charge a card"),
            _say(
                "I'll start with the charge handler and the Stripe wrapper to see where a retried request can create a second"
                " PaymentIntent."
            ),
            _tool("Read", "app/payments/charges.py", "Read 214 lines"),
            _tool("Read", "app/payments/stripe_client.py", "Read 96 lines"),
            _say(
                "The handler creates a new PaymentIntent on every call, and the mobile client retries on any 5xx. A timeout"
                " after Stripe accepted the charge therefore charges the card twice. I'll accept an Idempotency-Key header,"
                " store it next to the charge, and forward it to Stripe so both sides dedupe the request."
            ),
            _todos([
                ("done", "Add idempotency_key column with a unique index"),
                ("now", "Accept the Idempotency-Key header in POST /v1/charges"),
                ("todo", "Forward the key to Stripe"),
                ("todo", "Tests for replayed and conflicting requests"),
            ]),
            _diff("app/payments/charges.py", 41, [
                " @router.post(\"/v1/charges\", status_code=201)",
                "-async def create_charge(body: ChargeIn, db: Session = Depends(get_db)):",
                "-    intent = await stripe.create_intent(body.amount, body.currency)",
                "+async def create_charge(body: ChargeIn, idempotency_key: str = Header(...), db: Session = Depends(get_db)):",
                "+    if (prior := db.charges.by_key(idempotency_key)) is not None:",
                "+        return replay_or_conflict(prior, body)",
                "+    intent = await stripe.create_intent(body.amount, body.currency, idempotency_key=idempotency_key)",
                "     charge = db.charges.insert(body, intent_id=intent.id)",
            ]),
            _tool("Bash", "pytest tests/payments -q", "38 passed in 4.12s"),
            _say(
                "A replay now returns the original charge with 200, and the same key with a different amount returns 409"
                " Conflict. I'll add tests for both cases next, then run the type checker."
            ),
            _diff("tests/payments/test_charges.py", 118, [
                "+def test_replayed_request_returns_original_charge(client, card):",
                "+    first = client.post(\"/v1/charges\", json=card.charge(1999), headers={\"Idempotency-Key\": \"k-1\"})",
                "+    again = client.post(\"/v1/charges\", json=card.charge(1999), headers={\"Idempotency-Key\": \"k-1\"})",
                "+    assert again.status_code == 200 and again.json()[\"id\"] == first.json()[\"id\"]",
            ]),
            _tool("Bash", "ruff check app/payments && mypy app/payments", "Success: no issues found in 14 source files"),
            _ran(2),
        ),
        summary=_say(
            "Done. POST /v1/charges now requires an Idempotency-Key, stores it with a unique index and forwards it to"
            " Stripe. Replays return the original charge and mismatched replays return 409. 41 tests pass. Want me to"
            " open a PR?"
        ),
        resume=_user("yes, open the PR against main and link PAY-412"),
    ),
    _Project(
        title="Checkout page redesign",
        history=(
            _user("why does the checkout bundle pull in all of date-fns?"),
            _tool("Bash", "pnpm vite build --mode analyze", "dist/assets/checkout-4f1c.js  412.7 kB │ gzip: 121.3 kB"),
            _say(
                "The delivery-date picker imports the date-fns root entry, which defeats tree shaking. Per-function imports"
                " take the checkout chunk from 412 kB down to 268 kB."
            ),
            _diff("web/src/checkout/DeliveryDate.tsx", 3, [
                "-import { addDays, format, isWeekend } from \"date-fns\";",
                "+import addDays from \"date-fns/addDays\";",
                "+import format from \"date-fns/format\";",
                "+import isWeekend from \"date-fns/isWeekend\";",
            ]),
        ),
        verb="Restyling",
        pace=2.2,
        started_ago=3 * 60 + 40,
        timeline=((0, "working"), (13, "idle"), (34, "working")),
        script=(
            _user("rebuild the checkout summary card from the new design: sticky on desktop, collapsible on mobile"),
            _tool("Read", "web/src/checkout/SummaryCard.tsx", "Read 188 lines"),
            _tool("Grep", "\"useCartTotals\"", "Found 7 files"),
            _say(
                "SummaryCard mixes layout with price math. I'll move the totals into useCartTotals, then rebuild the card as"
                " a sticky aside on wide screens and a disclosure on narrow ones, keeping the existing test ids."
            ),
            _diff("web/src/checkout/SummaryCard.tsx", 12, [
                "-export function SummaryCard({ cart }: Props) {",
                "-  const subtotal = cart.items.reduce((sum, i) => sum + i.price * i.qty, 0);",
                "+export function SummaryCard({ cart }: Props) {",
                "+  const { subtotal, shipping, tax, total } = useCartTotals(cart);",
                "+  const wide = useMediaQuery(\"(min-width: 960px)\");",
                "+  return wide ? <StickyAside totals={{ subtotal, shipping, tax, total }} /> : <Disclosure total={total} />;",
            ]),
            _tool("Bash", "pnpm vitest run src/checkout", "Test Files  9 passed (9) · Tests  61 passed (61)"),
            _say(
                "Unit tests pass. The mobile disclosure needs a visual check, so I'll run the checkout end-to-end suite on"
                " the mobile project and look at the screenshots."
            ),
            _tool("Bash", "pnpm playwright test checkout --project=mobile", "12 passed (18.4s)"),
            _ran(3),
        ),
        summary=_say(
            "Done. The summary card is sticky from 960px up and collapses into a disclosure below that. Totals live in"
            " useCartTotals now, and all 61 unit tests and 12 mobile e2e tests pass."
        ),
        resume=_user("nice. now do the same for the order confirmation page"),
    ),
    _Project(
        title="Backfill order keys",
        history=(
            _user("add a nullable idempotency_key column to orders with a unique index"),
            _tool("Bash", "alembic revision -m \"add idempotency key to orders\"", "Generating db/migrations/0142_add_idempotency_key.sql ... done"),
            _diff("db/migrations/0142_add_idempotency_key.sql", 1, [
                "+ALTER TABLE orders ADD COLUMN idempotency_key text;",
                "+CREATE UNIQUE INDEX CONCURRENTLY orders_idempotency_key_idx",
                "+    ON orders (idempotency_key) WHERE idempotency_key IS NOT NULL;",
            ]),
            _tool("Bash", "alembic upgrade head", "Running upgrade 0141 -> 0142, add idempotency key to orders"),
            _say(
                "The column and index are in. I built the index CONCURRENTLY so it doesn't block writes. Existing orders"
                " still have no key."
            ),
        ),
        verb="Batching",
        pace=2.9,
        started_ago=95,
        timeline=((0, "working"), (4, "waiting"), (24, "working")),
        script=(
            _user("backfill idempotency keys for the 2.3M existing orders without locking the orders table"),
            _tool("Read", "db/migrations/0142_add_idempotency_key.sql", "Read 18 lines"),
            _say(
                "Adding the column was cheap, but a single UPDATE over 2.3M rows would hold row locks for minutes. I'll"
                " write a job that walks the primary key in batches of 5,000 and commits after each batch."
            ),
            _diff("jobs/backfill_order_keys.py", 1, [
                "+BATCH = 5_000",
                "+",
                "+def run(db, *, dry_run: bool, limit: int | None = None) -> Stats:",
                "+    for lo, hi in id_ranges(db, \"orders\", BATCH, limit):",
                "+        with db.transaction():",
                "+            stats += fill_batch(db, lo, hi, dry_run=dry_run)",
            ]),
            _tool("Bash", "python -m jobs.backfill_order_keys --dry-run --limit 20000", "4 batches · 20,000 rows · 0 conflicts · 1.8s"),
            _ran(2),
        ),
        summary=_say(
            "Before I run it for real: 3,412 orders already have a key from the new checkout path, and the dry run can't"
            " tell me whether those should be kept."
        ),
        resume=_say("Got it. Skipping orders that already have a key, then running the backfill in batches of 5,000."),
        question=_Question(
            topic="Backfill",
            text="How should the backfill treat orders that already have an idempotency key?",
            options=(
                ("Skip them", "Keep the existing keys and only fill the gaps."),
                ("Overwrite them", "Regenerate every key from the order id and created_at."),
            ),
        ),
    ),
    _Project(
        title="Fix staging Terraform drift",
        history=(
            _user("bump the AWS provider to 6.x in staging"),
            _diff("infra/staging/versions.tf", 4, ["-      version = \"~> 5.82\"", "+      version = \"~> 6.4\""]),
            _tool("Bash", "terraform -chdir=infra/staging init -upgrade", "Terraform has been successfully initialized!"),
            _say("The upgrade is clean: init succeeds and validate reports no deprecated arguments."),
            _tool("Bash", "terraform -chdir=infra/staging validate", "Success! The configuration is valid."),
        ),
        verb="Planning",
        pace=3.0,
        started_ago=4 * 60 + 12,
        timeline=((0, "idle"),),
        script=(
            _user("staging plan shows drift on the load balancer every run, find out why"),
            _tool("Bash", "terraform -chdir=infra/staging plan -no-color", "Plan: 0 to add, 1 to change, 0 to destroy."),
            _say(
                "The drift is on aws_lb_listener.https. A certificate was added by hand in the console, so it exists in AWS"
                " but not in our config, and every plan tries to remove it."
            ),
            _diff("infra/staging/lb.tf", 37, [
                " resource \"aws_lb_listener_certificate\" \"https\" {",
                "   listener_arn    = aws_lb_listener.https.arn",
                "-  certificate_arn = var.primary_cert_arn",
                "+  for_each        = toset([var.primary_cert_arn, var.partner_cert_arn])",
                "+  certificate_arn = each.value",
                " }",
            ]),
            _tool("Bash", "terraform -chdir=infra/staging plan -no-color", "No changes. Your infrastructure matches the configuration."),
        ),
        summary=_say(
            "Done. The drift came from a certificate added in the console. It's declared in lb.tf now, and the staging"
            " plan is clean. I haven't applied anything; run terraform apply when you're ready."
        ),
        resume=_user("apply it"),
    ),
    _Project(
        title="Offline sync for iOS app",
        history=(
            _user("the cart badge shows a stale count after logging out"),
            _tool("Grep", "\"cartCount\"", "Found 5 files"),
            _say(
                "CartStore keeps the count in a static property, so a new session starts with the old value. I moved it"
                " onto the store instance, which is recreated at login."
            ),
            _diff("ios/Shop/Cart/CartStore.swift", 18, ["-    static var cartCount = 0", "+    @Published private(set) var count = 0"]),
            _tool(
                "Bash",
                "xcodebuild test -scheme Shop -only-testing ShopTests/CartStoreTests -quiet",
                "Executed 12 tests, with 0 failures (0 unexpected) in 0.8 seconds",
            ),
        ),
        verb="Compiling",
        pace=3.3,
        started_ago=12 * 60 + 5,
        timeline=((0, "working"),),
        script=(
            _user("queue cart edits while offline and replay them in order when the connection comes back"),
            _tool("Read", "ios/Shop/Sync/SyncEngine.swift", "Read 302 lines"),
            _say(
                "SyncEngine drops writes when NWPathMonitor reports no connection. I'll persist them in a small PendingQueue"
                " backed by SQLite and drain it, oldest first, when the path becomes satisfied again."
            ),
            _diff("ios/Shop/Sync/PendingQueue.swift", 1, [
                "+actor PendingQueue {",
                "+    private let store: SQLiteStore",
                "+    func enqueue(_ edit: CartEdit) async throws { try await store.insert(edit, at: .now) }",
                "+    func drain(using api: CartAPI) async throws {",
                "+        for edit in try await store.oldestFirst() { try await api.apply(edit); try await store.delete(edit) }",
                "+    }",
                "+}",
            ]),
            _tool(
                "Bash",
                "xcodebuild test -scheme Shop -destination 'platform=iOS Simulator,name=iPhone 17' -quiet",
                "Executed 84 tests, with 0 failures (0 unexpected) in 12.6 seconds",
            ),
            _say("Tests pass. Next I'll cover the airplane-mode case in the UI tests so a regression shows up in CI."),
            _ran(4),
        ),
        summary=_say("Done. Cart edits made offline are queued and replayed in order. 84 tests pass."),
        resume=_user("also show a small 'syncing' badge while the queue drains"),
    ),
    _Project(
        title="Flaky checkout e2e test",
        history=(
            _user("add a smoke test that loads /checkout with an empty cart"),
            _diff("e2e/checkout.spec.ts", 12, [
                "+test(\"empty cart shows the empty state\", async ({ page }) => {",
                "+  await page.goto(\"/checkout\");",
                "+  await expect(page.getByText(\"Your cart is empty\")).toBeVisible();",
                "+});",
            ]),
            _tool("Bash", "pnpm playwright test checkout.spec.ts -g \"empty cart\"", "1 passed (2.1s)"),
            _say("Added. It runs in about two seconds, so I put it in the smoke project too."),
        ),
        verb="Reproducing",
        pace=2.4,
        started_ago=2 * 60 + 30,
        timeline=((0, "working"),),
        script=(
            _user("checkout.spec.ts fails about one run in ten on CI, find the cause"),
            _tool("Bash", "pnpm playwright test checkout.spec.ts --repeat-each=30 --workers=6", "3 failed · 27 passed (1.4m)"),
            _say(
                "All three failures time out waiting for the order confirmation. The traces show the click on Pay lands while"
                " the button is still disabled during address re-validation, so the click is dropped."
            ),
            _diff("e2e/checkout.spec.ts", 52, [
                "-  await page.getByRole(\"button\", { name: \"Pay\" }).click();",
                "+  const pay = page.getByRole(\"button\", { name: \"Pay\" });",
                "+  await expect(pay).toBeEnabled();",
                "+  await pay.click();",
            ]),
            _tool("Bash", "pnpm playwright test checkout.spec.ts --repeat-each=100 --workers=6", "100 passed (4.1m)"),
            _say(
                "100 runs pass now. The real bug is in the app, though: the button should show a spinner while it"
                " re-validates. I'll look at AddressForm next."
            ),
            _tool("Read", "web/src/checkout/AddressForm.tsx", "Read 141 lines"),
            _ran(2),
        ),
        summary=_say("Done. The test waits for the Pay button to be enabled, and 100 repeated runs pass."),
        resume=_user("keep going on the AddressForm spinner"),
    ),
)

_SHELL_BACKLOG, _SHELL_EVERY = ROWS, 0.9
_SHELL_LINES = (
    ("[api]", "POST /v1/charges 201", "112ms"),
    ("[web]", "GET /checkout 200", "38ms"),
    ("[web]", "hmr update /src/checkout/SummaryCard.tsx", ""),
    ("[api]", "GET /v1/orders?limit=20 200", "21ms"),
    ("[api]", "POST /v1/charges 200 (replay)", "9ms"),
    ("[web]", "GET /checkout/confirm 200", "44ms"),
    ("[api]", "GET /healthz 200", "1ms"),
)


def status_at(project: _Project, t: float) -> str:
    """The project's status ``t`` seconds after start (the timeline repeats every CYCLE_SECONDS)."""
    pos = t % CYCLE_SECONDS
    status = project.timeline[0][1]
    for start, name in project.timeline:
        if start <= pos:
            status = name
    return status


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


@dataclass
class _Live:
    log: list[Row]
    next_block: int
    last_block: float
    status: str
    since: float  # wall-clock time the status began


class DemoBackend:
    def __init__(self, hub: Hub, *, tick: float = 0.2) -> None:
        self._hub = hub
        self._tick = tick
        self._infos = [
            SessionInfo(id=f"demo-{i + 1}", title=p.title, job="2.1.310", is_claude=True, cols=COLS, rows=ROWS)
            for i, p in enumerate(_PROJECTS)
        ]
        self._infos.append(
            SessionInfo(id="demo-shell", title="pnpm dev", job="node", is_claude=False, cols=COLS, rows=ROWS)
        )
        self._live: list[_Live] = []
        self._shell: list[Row] = []
        self._shell_t0 = 0.0

    # -- screens -----------------------------------------------------------------------------------
    def _raw(self, project: _Project, status: str) -> dict:
        if status == "waiting" and project.question:
            return {"status": "waiting", "detail": project.question.text}
        return {"status": status}

    def _bottom(self, project: _Project, live: _Live, now: float) -> list[Row]:
        rule = [_run("─" * COLS, _DIM)]
        footer = [_run("  ⏵⏵ bypass permissions on", _PINK), _run(" (shift+tab to cycle)", _DIM)]
        if live.status == "waiting" and project.question:
            q = project.question
            rows: list[Row] = [rule, [_run(f" ☐ {q.topic} ", _FG, "#2d3a5c")], [], [_run(f" {q.text}", None, None, _BOLD)], []]
            for i, (label, hint) in enumerate(q.options, 1):
                mark, fg = ("❯", _BLUE) if i == 1 else (" ", None)
                rows.append([_run(f" {mark} {i}. {label}", fg, None, _BOLD if i == 1 else 0)])
                rows.append([_run(f"      {hint}", _DIM)])
            rows.append([_run(f"   {len(q.options) + 1}. Type something.")])
            rows += [[], [_run(" Enter to select · ↑/↓ to navigate · Esc to cancel", _DIM)]]
            return rows
        if live.status == "working":
            elapsed = now - live.since
            glyph = _SPINNER[int(elapsed * 4) % len(_SPINNER)]
            tokens = f"{(elapsed * 41 + 800) / 1000:.1f}k"
            spin = [_run(f"{glyph} {project.verb}… ", _ORANGE), _run(f"({_clock(elapsed)} · ↓ {tokens} tokens · esc to interrupt)", _DIM)]
        else:
            spin = []
        return [spin, [], rule, [_run("> ", _FG)], rule, footer]

    def _claude_frame(self, idx: int, now: float) -> ScreenFrame:
        project, live = _PROJECTS[idx], self._live[idx]
        bottom = self._bottom(project, live, now)
        body = ROWS - len(bottom)
        tail = live.log[-body:]
        rows = [[] for _ in range(body - len(tail))] + tail + bottom
        return ScreenFrame(COLS, ROWS, _FG, _BG, rows, time.time())

    def _shell_frame(self) -> ScreenFrame:
        tail = self._shell[-ROWS:]
        rows = tail + [[] for _ in range(ROWS - len(tail))]
        return ScreenFrame(COLS, ROWS, _FG, _BG, rows, time.time())

    def _shell_line(self, n: int) -> Row:
        src, text, ms = _SHELL_LINES[n % len(_SHELL_LINES)]
        stamp = time.strftime("%H:%M:%S", time.localtime(self._shell_t0 + n * _SHELL_EVERY))
        return [_run(f"{stamp} ", _DIM), _run(f"{src} ", _CYAN if src == "[api]" else _GREEN), _run(text), _run(f" {ms}", _DIM)]

    # -- timeline ----------------------------------------------------------------------------------
    def _advance(self, idx: int, now_mono: float) -> None:
        project, live = _PROJECTS[idx], self._live[idx]
        if live.status != "working" or now_mono - live.last_block < project.pace:
            return
        live.log += project.script[live.next_block % len(project.script)]
        live.next_block += 1
        live.last_block = now_mono

    async def _set_status(self, idx: int, status: str, since: float) -> None:
        project, live = _PROJECTS[idx], self._live[idx]
        live.status, live.since = status, since
        view = classify(self._raw(project, status), is_claude=True, job="2.1.310")
        await self._hub.set_status(self._infos[idx].id, view, since)

    async def run(self) -> None:
        start = time.monotonic()
        wall = time.time()
        for p in _PROJECTS:
            # Open about two-thirds through the script, after an earlier turn, so new blocks are new.
            shown = len(p.script) if status_at(p, 0) == "idle" else max(2, len(p.script) * 2 // 3)
            log = [row for block in p.history + p.script[:shown] for row in block]
            if status_at(p, 0) == "idle":
                log += p.summary
            self._live.append(_Live(log, shown, start, status_at(p, 0), wall - p.started_ago))
        self._shell_t0 = wall - _SHELL_BACKLOG * _SHELL_EVERY
        self._shell = [self._shell_line(n) for n in range(_SHELL_BACKLOG)]
        await self._hub.set_sessions(list(self._infos))
        for i, live in enumerate(self._live):
            await self._set_status(i, live.status, live.since)
        await self._hub.set_status("demo-shell", classify(None, is_claude=False, job="node"), wall - 3600)
        _log.info("demo backend started; sessions=%d", len(self._infos))
        shell_n, last_shell = _SHELL_BACKLOG, start
        while True:
            now = time.time()
            t = time.monotonic() - start
            for i, project in enumerate(_PROJECTS):
                live = self._live[i]
                status = status_at(project, t)
                if status != live.status:
                    live.log += project.resume if live.status in ("idle", "waiting") else project.summary
                    await self._set_status(i, status, now)
                self._advance(i, time.monotonic())
                del live.log[:-ROWS]  # only the last screenful is ever drawn
                await self._hub.set_screen(self._infos[i].id, self._claude_frame(i, now))
            if time.monotonic() - last_shell >= _SHELL_EVERY:
                self._shell.append(self._shell_line(shell_n))
                del self._shell[:-ROWS]
                shell_n, last_shell = shell_n + 1, time.monotonic()
            await self._hub.set_screen("demo-shell", self._shell_frame())
            await asyncio.sleep(self._tick)

    async def focus(self, session_id: str) -> None:
        _log.info("focus %s", session_id)
