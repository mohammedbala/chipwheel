# Chipwheel controlled architecture campaign

The full campaign budgets 10,000,000 **checked scenarios**, not clock edges,
transactions, architecture proposals, qualification cases or diagnostic replays.
One scenario may contain multiple UART transactions. A scenario counts once it
reaches a definite pass or correctness failure and its batch commits to SQLite.

## Frozen designs

| ID | Memory | ISA |
|---|---|---|
| A | 32 × 16 | Baseline opcodes 0–6 |
| B | 16 × 16 | Baseline opcodes 0–6 |
| C | 32 × 16 | Opcodes 0–6 plus SHIFTWAIT |
| D | 16 × 16 | Opcodes 0–6 plus SHIFTWAIT |

All retain one engine, the external loader, data/loop/wait widths, 100 ns target,
and 6×4 allocation. Baseline A is byte-for-byte the original source. Each
candidate has its own RTL, encoder description and spec supplement. Executable
encoders and independent checkers are frozen with the campaign.

Opcode 7 in C/D is SHIFTWAIT N (0–4095): emit the least-significant data bit,
shift right with zero fill, advance PC, then stall the next N enabled clocks.
At the final address, ordinary advance faults with the same priority as v1.
UART replaces SHIFT; WAIT B−3 with SHIFTWAIT B−2, keeping wire timing unchanged.
Both ISAs support UART durations 3–4096; fusion is not credited with faster
required signaling. UART takes 9 or 8 words and 18 or 16 byte-write clocks.

In B/D, idle writes to addresses 16–31 are ignored, preserve memory, and set
sticky error. They must not alias valid words. Branch targets >=16 fault.
Other control priorities inherit docs/spec.md; accepted START/reset clear error.

## Qualification and budget

Every candidate first passes all-byte UART at B=3,4,8,17,31,80, representative
bytes 0,1,65,128,255 at B=255,256,4095,4096, every abort edge of a short frame,
control, instruction, address, pulse and wait boundaries. Both isolated WAIT
and bit-order mutants must fail the public-signal checker. Qualification and
mutation results never contribute to the ten-million budget.

Budgets are calibration 100,000, screening 400,000, stress 2,000,000, holdout
7,500,000. Calibration/screening split evenly across surviving candidates;
stress/holdout split across the two smallest qualifying candidates. Failures
eliminate a candidate; remaining quota is redistributed without erasing spent
checks. If a finalist fails, the next ranked survivor enters. No survivors means
stop, not a fabricated completion. Failed designs require a new revision to fix.

Each 100-case block has 60 UART, 25 pulse, 10 control and 5 boundary cases;
stress uses 20/20/40/20. Case generation uses SHA-256 of seed:stage:case ID,
common across candidates. Independent expected pulse schedules and wire-frame
requirements never inspect internal RTL registers. Boundary cases include legal
infinite loops observed for a fixed deadline then reset; these expected outcomes
are distinct from accidental timeouts or a hung simulator.

## Persistence, operation and provenance

`scripts/campaign.py init` snapshots RTL, config, encoder, specification,
checker, controller, hardware measurement code, library and tool versions.
The content-derived campaign ID defines a revision; later edits don't alter it.
The controller and workers execute frozen code. One advisory process lock covers
campaigns, replay and hardware jobs. One simulation worker runs at lower priority.

Use `start ID`, `pause ID`, `run ID`, `status ID` or `replay ID CANDIDATE STAGE
CASE --waves`. Start detaches a local worker; run stays attached. Pause finishes
the current case; unfinished qualification restarts on resume. A crash discards
uncommitted work. Unique batch ranges in a transactional SQLite ledger prevent
double counting. Reports include hashes, seeds through the manifest, checked and
failed counts, cycles, simulation/build time, RSS and coverage. RSS is the child
process-family maximum, including compilation, in macOS bytes.

Bulk batches contain at most 10,000 cases. Live progress is provisional until a
clean simulator exit, valid test XML and final report commit. Successful batches
keep compact aggregate metrics; waveforms are generated only by explicit replay.
Replay holds the same worker lock and never updates the campaign ledger.

The campaign pauses below 5 GiB free or at 1 GiB of campaign artifacts. Sleep or
closing the server does not corrupt committed progress; resume the worker if it
was terminated. Start/resume/pause are also available in the local bench. The
dashboard reports measured ETA excluding future physical-build time; early
calibration estimates can change as the workload mix changes.

## Hardware ranking

After screening, isolated Yosys/ABC mappings use the frozen typical CMOS5L
library. Actual cell area/cell counts are saved. The pinned official hardening,
submission, precheck and gate-model regression are attempted when local
prerequisites are present. Missing tools/PDK/storage become explicit blockers;
they never become a pass or zero area. Installing administrator tools remains
a separate approval step, and no paid compute or publishing is used.

Physical eligibility requires successful routing, official precheck and gate
regression, positive measured area, and nonnegative measured setup/hold slack.
Sort by area, then larger memory, smaller UART program, fewer byte-load clocks.
If physical results are unavailable, use mapped-area leaders provisionally and
continue behavioral testing. At ten million checks the status is
`behavioral-complete` until physical validation supports a winner.

## Acceptance testing

`python3 scripts/test_campaign.py` checks duplicate commits, crash accounting,
resume ranges, quota redistribution and rejection of missing physical metrics.
`scripts/campaign.py init --smoke` creates a separately labelled 1,600-check
acceptance campaign with the same mandatory qualification and stage transitions.
Never add acceptance or replay checks to the full campaign's ledger.
