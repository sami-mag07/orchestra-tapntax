"""
Measure what the three decisions save, across queues of different shape and size.

    python3 scripts/benchmark.py                 # writes .orchestra/bench.json
    python3 scripts/benchmark.py --live          # the same sweep against a real provider (slow, costs money)

Offline it uses the deterministic mock provider, so the token counts are estimates of a fixed shape rather
than a vendor's bill; the point of the sweep is the relationship, not the absolute euro. Every configuration
sees exactly the same records.

Three configurations, each a real run:

    all large     every stage for every record, on the largest tier: what a team gets by default
    cascade       every stage for every record, smallest tier first: model routing only
    full          triage rules, routes, per-stage conditions and the cascade together
"""

from __future__ import annotations

import dataclasses
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestra.config import mock_models, tier_models                  # noqa: E402
from orchestra.data import Params                                      # noqa: E402
from orchestra.orchestrator import Orchestrator, build_context         # noqa: E402
from orchestra.permissions import Approvals                            # noqa: E402
from orchestra.pipeline import build_steps                             # noqa: E402
from orchestra.providers import MockProvider, make_provider            # noqa: E402
from orchestra.router import RoutePolicy                               # noqa: E402
from orchestra.scenarios import load as load_scenario                  # noqa: E402
from orchestra.triage import Triage                                    # noqa: E402

ROUTINE = [
    "How do I reset my password?",
    "Where do I change my email address in the app?",
    "How do I find last year's return?",
    "Is the app available in Spanish?",
    "How do I update my bank details?",
    "Where do I log in on a new phone?",
]
HARD = [
    "My tax notice says a 300 euro refund. The app estimated 900 euros. Why the difference?",
    "How much wage tax is due on 60000 euros gross, tax class 1?",
    "I want to cancel my Taxfix+ subscription. Please confirm by email.",
    "Refund me 49.99 euros immediately, I was charged twice.",
    "My employer changed my tax class mid-year and the app ignored it.",
    "I am threatening legal action if this is not resolved this week.",
]


DETAIL = ["My reference is {n}.", "Ticket {n}, second attempt.", "Account {n}.", "Case {n}, please advise.",
          "This is about number {n}."]


def dataset(n: int, routine_share: float, duplicate_share: float = 0.0, seed: int = 7) -> list:
    """A queue of n tickets.

    `routine_share` is how much of it a rule can recognise. `duplicate_share` is how much of it is worded
    identically to an earlier ticket, which is what the cache can collapse. Keeping the two separate matters:
    otherwise a handful of repeated sentences makes the cache look like the router.
    """
    rng = random.Random(seed)
    rows, seen = [], []
    for i in range(n):
        if seen and rng.random() < duplicate_share:
            text = rng.choice(seen)
        else:
            pool = ROUTINE if rng.random() < routine_share else HARD
            text = rng.choice(pool) + " " + rng.choice(DETAIL).format(n=4700 + i)
            seen.append(text)
        rows.append({"id": f"b{i:03d}", "customer": f"c{i:03d}@example.com", "text": text})
    return rows


def run(scn, records: list, mode: str, provider, models, params) -> dict:
    ctx = build_context(provider=provider, models=models, persist=False, approvals=Approvals(),
                        baseline="large" if mode == "all large" else None)
    registry = scn.make_tools(params)
    agents = scn.agents(registry)
    stages = scn.stages()
    triage = Triage()
    rules = getattr(scn, "TRIAGE", None)
    route_of = getattr(scn, "route_from_category", None)

    if mode in ("all large", "cascade"):
        stages = [dataclasses.replace(st, when=None, routes=None) for st in stages]
        rules, route_of = None, None
    # Budgets are per agent per run, so they scale with the queue. Without this the expensive configuration
    # simply stops early and looks cheap, which would measure the budget rather than the design.
    scale = max(len(records), 1)
    if mode == "all large":
        large, small = models["large"], models["small"]
        scale *= max(1.0, (large.input_usd + large.output_usd) / max(small.input_usd + small.output_usd, 1e-9))
        for a in agents:
            a.policy = RoutePolicy(start_tier="large", max_tier="large", min_confidence=0.0,
                                   max_tokens=a.policy.max_tokens)
    for a in agents:
        a.identity = dataclasses.replace(a.identity, budget_usd=a.identity.budget_usd * scale)

    orch = Orchestrator(agents, ctx, max_workers=8)
    orch.triage = triage
    orch.run(build_steps([dict(r) for r in records], stages, params, rules, route_of, triage))
    led = ctx.ledger
    return {
        "mode": mode,
        "records": len(records),
        "cost": round(led.total_cost, 6),
        "cost_per_record": round(led.total_cost / max(len(records), 1), 6),
        "calls": led.model_calls,
        "calls_per_record": round(led.model_calls / max(len(records), 1), 3),
        "cache_hits": led.cache_hits,
        "by_tier": led.calls_by_tier(),
        "routed_past": orch.routed_past,
        "stages_never_created": triage.stages_skipped,
        "by_rule": triage.by_rule,
        "by_model": triage.by_model,
        "routes": dict(triage.routes),
        "to_human": len(orch.human_queue),
    }


def main(argv: list) -> int:
    live = "--live" in argv
    scn = load_scenario("support")
    params = Params.parse([], getattr(scn, "DEFAULTS", {}))
    models = tier_models() if live else mock_models()
    provider = make_provider() if live else MockProvider(scn.mock_responder(models))
    modes = ("all large", "cascade", "full")

    shares = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    sizes = [10, 20, 40, 80] if not live else [6]
    dupes = [0.0, 0.2, 0.4, 0.6, 0.8]

    by_share, by_size, by_dupe = [], [], []
    for share in shares:
        recs = dataset(40, share)
        row = {"routine_share": share}
        for m in modes:
            row[m] = run(scn, recs, m, provider, models, params)
        by_share.append(row)
        print(f"  routine {share:.0%}: " + "  ".join(
            f"{m} ${row[m]['cost']:.4f}/{row[m]['calls']} calls" for m in modes))

    for n in sizes:
        recs = dataset(n, 0.5)
        row = {"records": n}
        for m in modes:
            row[m] = run(scn, recs, m, provider, models, params)
        by_size.append(row)
        print(f"  n={n:<4}" + "  ".join(f"{m} ${row[m]['cost']:.4f}" for m in modes))

    for d in dupes:
        recs = dataset(40, 0.5, duplicate_share=d)
        row = {"duplicate_share": d, "distinct": len({r["text"] for r in recs})}
        for m in modes:
            row[m] = run(scn, recs, m, provider, models, params)
        by_dupe.append(row)
        print(f"  duplicates {d:.0%} ({row['distinct']} distinct): "
              + "  ".join(f"{m} ${row[m]['cost']:.4f}" for m in modes))

    doc = {"provider": provider.name, "modes": list(modes),
           "models": {t: m.name for t, m in models.items()},
           "by_share": by_share, "by_size": by_size, "by_duplicate": by_dupe}
    out = Path(".orchestra/bench.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
