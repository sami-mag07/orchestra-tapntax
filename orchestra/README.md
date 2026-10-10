# Orchestra

Agent orchestration with a permission gate, a cost cascade, and a measured comparison instead of a claim.
Built for the Cursor Hackathon at Taxfix, Berlin, 17 September 2026. Python 3 standard library only, no pip.

```bash
python3 orchestra/demo.py                        # offline, deterministic, under a second
python3 orchestra/demo.py --compare              # the same work routed and on the large model only, measured
python3 orchestra/demo.py --live --compare       # with a real provider from .env
python3 orchestra/web/build.py                   # both runs as one page: .orchestra/runs/compare.html
python3 -m unittest tests.test_orchestra         # 56 tests
```

## What it does

A dataset goes in the front. Four agents work each record: one classifies, one establishes the facts with
real oracles, one independently rechecks, one writes the reply. Results leave through the sinks. Around
them run six rails that are the same for every agent.

| Rail | What it does |
|---|---|
| Router | Smallest model first. Escalates only when the answer does not pass the accept check. |
| Gate | Every tool call is checked in code before it runs: allowlist, data class, budget, approval. |
| Cache | Identical prompts never reach a model, and identical concurrent ones collapse into one call. |
| Ledger | Cost per agent, and the counterfactual of running everything on one model. |
| Audit | Append-only record of every decision. No prompt text, no secrets. |
| Harness | Oracle, cases, metric, pass bar declared before the run. Prints the number you can say out loud. |

The claim this exists to support: the same result for less money, and no agent holding a master key.
On the built-in scenario against Claude Haiku, Sonnet and Opus, the routed run scored 6 of 6 with zero
silent errors at 74 percent less cost than the same work on the large model.

## The five places you plug something in

1. **Source** (`orchestra/scenarios/<brief>.py: source()`, or `--data file.csv`) takes csv, tsv, json, jsonl,
   txt or a folder. Foreign column names map onto `id`, `text` and `customer`; every other column survives
   and prompts read it as `{column}`.
2. **Tools** (`make_tools()`) take any Python callable, plus every MCP server tool via `--mcp mcp.json` as
   `<server>.<tool>`. Each carries a data class, and no agent may run one until its identity says so.
3. **Agents** (`orchestra/agents/*.py`) carry a prompt, an allowlist, data classes, a budget and a tier range.
4. **Stages** (`STAGES` in the scenario) are one line each: who, what task, what it waits for, what it reads.
5. **Sinks** (`sinks()`) write csv, jsonl, json or md. Anything that touches the world is a tool, so it is gated.

Parameters reach every prompt: `--set market=ES --set language=es` becomes `{p.market}` and `{p.language}`.

## Layout

```
orchestra/          the infrastructure: router, gate, cache, ledger, audit, data, pipeline, mcp, web
orchestra/agents/   the four roles, one file each
orchestra/scenarios/ one file per brief: records, tools, stages, expectations, sinks
spine/              the oracles: the BMF federal wage-tax algorithm, statutes with a citation resolver,
                    e-invoices, the validation harness, and the `brownie` skill that rotates onto a brief
tests/              56 tests
```

Keys live in `.env` only, never in code. Copy `.env.example` and pick a provider: `claude_code` runs headless
`claude -p` with your own login and needs no key, `anthropic` and `openai` take one.
