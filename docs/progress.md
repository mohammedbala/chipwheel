# Progress log — 2026-10-03

## Completed and measured

- Inspected an empty workspace on Apple M4 macOS, arm64, 10 logical CPUs,
  16 GiB RAM. Git, Python and make were present; Icarus, Yosys, Docker,
  LibreLane and KLayout were not on PATH. No existing project files changed.
- Read Jane Street's announcement and cloned the actual CMOS5L template.
  Current allocation is 6x4, deadline January 18, 2027. No 8x4 assumption.
- Unmodified template test passed before RTL edits (one example-adder test).
  `results/template-baseline.log`, `.xml` and `.json` preserve evidence.
- Created a cycle specification before implementing the engine.
- Implemented real instruction fetch, writable 32x16 memory, seven opcodes,
  reset/start behavior, actual pin loader, UART and pulse programs. No internal
  memory preloading shortcut is used. Verified RTL external loading, not silicon.
- Independent public-signal checker verifies full 8N1 waveform and decoded byte.
- Initial short run: 1,110 checked transactions, 32 instruction traces,
  144,368 cycles, 5.257835375 s simulation, no failures/timeouts.
- Random campaign: seed 20261003, 2,000 checked messages, 530,943 cycles,
  19.227497792 s simulation, no failures/timeouts. No waveforms in bulk runs.
- Both deliberate faults were detected: short WAIT fails completion at E72
  (expected E82); MSB-first fails the first data bit at E10 for byte 65.
  Faults live in isolated copies. `results/mutations.json` records unchanged
  correct RTL hash and both detections.
- Diagnostic byte-A replay generated a real FST waveform.
- All 20 host encoder unit tests passed, covering invalid requests and the
  wait-overhead bug. The final short verification passed again (1,110 checked,
  32 traces), and the template Makefile integration regression also passed.
- Official support tools generated actual 6x4 config and checked top-level
  ports; template clock constraint aligned to 10 MHz / 100 ns.
- Standalone Yosys/ABC CMOS5L mapping completed: 1,782 cells,
  46,939.662 µm², build 1.116520917 s. `check -assert` passes after loading
  library blackbox definitions. No generic cells remain in mapped statistics.
- Mapped functional regression passed all 1,110 transactions and 32 traces,
  144,368 cycles, 10.284585375 s. Timing blocks are omitted from PDK models
  for this zero-delay test; source models and transformation are preserved.
- README, program words, setup/benchmark/replay/flow commands and roadmap written.
  No architecture search, multiple engines or protocol-specific RTL added.

## Failed work and resulting fixes

- First runner failed to locate the test module. Added the test directory to
  the runner's module search path; no RTL/test expectations changed.
- First UART run caught the program's start/stop waits one clock too long.
  Instruction semantics and UART waveform requirements were unchanged.
  Corrected those waits from B-1 to B-2. Saved failing log/JSON under
  `results/regression-wait-overhead-failure.*`; all-duration waveform tests
  and a literal B=3 encoder regression permanently cover this bug.
- An overflow test initially expected sticky error before the faulting edge.
  Corrected the test to expect error at the final fault edge, matching the
  original spec. No RTL change or relaxation of fault behavior.
- YoWASP cache compilation initially exhausted disk space. Removed only the
  temporary NumPy source-build cache created by this session, then retried
  with a project-local YoWASP cache. Port checking and synthesis now run.
- A second Python download failed due to disk space; the successfully created
  Python 3.13.16 flow environment runs the pinned support dependencies.
  Fresh setup selects Python 3.11, matching official workflow requirements.
- Official PDK simulation models have edge-sensitive `ifnone` paths unsupported
  by Icarus. Direct model compile failed. The explicitly functional-only
  transformation strips specify blocks, directly connects their delayed inputs,
  and includes the official UDP model. Mapped behavior passes; this gives no
  physical timing validation.
- First replay enabled plusargs but the cocotb runner still suppressed dumps.
  Enabled waveform output in the runner; confirmed the FST exists. Bulk runs
  continue to suppress dumps.
- Actual official hardening invocation failed with “No compatible container
  engine found.” See `results/layout-flow.log` and `.json`, also the initial
  `official-layout.log`. No compatible container engine, full PDK or physical
  tools are installed. Initial disk headroom was less than 250 MiB. No system
  installation, cloud job or publication was attempted.

## Unverified / incomplete

- Official flow synthesis, placement, CTS, routing, GDS, DRC/precheck and timing
  slack at 100 ns. Standalone mapped cell area is not a full-flow result.
- Official post-layout gate-level workflow (no routed netlist exists).
- Manufactured silicon, electrical margins and loading from asynchronous hosts.
- Power-up memory validity, partial-program protection, input sampling, SPI,
  I2C/open-drain signaling. Reset preserves memory; host must fully load words.
- Fresh setup on a different machine, Linux environment and million-message
  campaigns. Present commands are exercised on this Mac unless noted.

## Provenance

| Component | Revision / version |
| --- | --- |
| CMOS5L template | b86a2a781484bcab7ba522dc5de540086695a430 |
| tt-support-tools, ihp-sg13cmos5l | d66cf179e7bc4d296362ab7e2e3b344dc3c4f665 |
| tt-gds-action, ihp-cmos5l | 3412659307918422f3f0727917cf9b499aaca588 |
| IHP PDK (official action pin) | 2bbec755dc67ca3db0261c3d6163e15735d66710 |
| Icarus / cocotb / pytest | 13.0 / 2.0.1 / 8.4.2 |
| Yosys (YoWASP) | 0.55, git 60f126cd0 |
| YoWASP package/runtime | 0.55.0.0.post944 / 1.78 |
| LibreLane | 3.1.0.dev3 |
| Simulation launcher Python | 3.9.6 (embedded simulator library reports 3.9.13) |
| Local flow Python | 3.13.16 |

Local changes remain reviewable in Git. Each measured run records RTL/content
hashes, since the upstream template revision alone does not describe edits.
Official template action references remain unchanged; local setup pins the
support/action/PDK versions above. No pull request or remote publication.

Next single milestone: complete the official physical build at 100 ns and
record routing/precheck/timing evidence. See `roadmap.md` for later experiments.

## Ten-million-check campaign implementation — 2026-10-03

Implemented the approved A/B/C/D comparison, deterministic independent scenario
checks, directed qualification and two deliberate RTL mutations per design.
The baseline source remains SHA-256
`fa5ee7e7005e764dd658916f07065339fe05d089ff615dd552a46cdd6cb9f15c`.
Candidate snapshots and executable checking code are isolated by campaign hash.

The runner uses one low-priority worker, cached builds, batches of at most
10,000 scenarios, a transactional SQLite ledger and atomic progress reports.
Only final batch reports count. Pause finishes the current scenario; crashes
discard unfinished work. A process lock prevents simultaneous campaigns,
hardware jobs and diagnostic replays. Free space below 5 GiB or campaign files
reaching 1 GiB trigger a pause. Runtime estimates weight measured costs by the
remaining stage mix. Physical builds are excluded from the simulation estimate.

The live bench includes start/pause/resume, committed and in-flight counts,
stage allocation, candidate comparison, coverage gaps, failure provenance and
waveform replay. Notebook entries preserve and link their campaign revision.

Acceptance campaign `acceptance-a5d4fee310f89b1a` completed 1,600 checks, exactly
400 per stage, with zero failures. Qualification passed 1,912 directed cases
for A/B and 1,917 for C/D; both injected faults were detected for all four.
End-to-end recovery compared 100 uninterrupted scenarios with a 9-case paused
prefix plus a 91-case resumed suffix: counts, cycles, UART transactions and
coverage matched. An invalid RTL build was rejected without budget credit.
A real diagnostic waveform replay also left the ledger unchanged. Evidence:
`results/campaigns/acceptance-a5d4fee310f89b1a/acceptance-validation.json`.

All four acceptance designs reproduced mapped CMOS5L measurements:

| Candidate | Cells | Mapped cell area (µm²) |
| --- | ---: | ---: |
| A — 32 words | 1,782 | 46,939.6620 |
| B — 16 words | 982 | 25,520.7456 |
| C — 32 words + SHIFTWAIT | 1,834 | 47,272.7556 |
| D — 16 words + SHIFTWAIT | 975 | 25,418.9880 |

D's mapped area is about 45.8% below A. This is **provisional mapped-area
evidence**, not a routed winner. All four physical results are explicitly
blocked: the full pinned PDK, a working container engine, the official precheck
Nix environment, and sufficient setup disk space are unavailable. No
administrator installation or paid service was used. A final winner requires
positive routed area, passing routing/precheck/gate regression, and nonnegative
setup/hold slack at 100 ns.

Six runner unit tests passed, including stale-design/netlist rejection, missing
or non-finite physical metrics, exact reallocation and stage-weighted forecasts.
The existing live-design and interactive-engine checks passed. A second worker
launch was rejected while the real full campaign was running.

Full campaign `cw10m-b3ee8dcbc289a910` was started with seed 20261003 and the
approved 100,000 / 400,000 / 2,000,000 / 7,500,000 stage budgets. Its mandatory
qualification runs separately before bulk checks. Use its live state and ledger
for the current count; the ten-million run was not complete at implementation
handoff. Acceptance results never contribute to that budget. The full campaign
will remeasure hardware after screening rather than reuse acceptance rankings.

### Continued campaign and independent ledger audit

The worker continued calibration without a source change or a new campaign.
An independent audit of 41,453 committed checks reconstructed every scenario
digest and coverage count from the frozen generator. All case ranges were
contiguous and every credited design had passing directed and mutation evidence.
No failures were recorded at this audit. The reusable audit also rejects stale
source hashes, changed digests/coverage, duplicate or missing ranges, incomplete
reports, and evaluations after a candidate fails. Four fault-focused audit tests
passed. The complete 1,600-check acceptance ledger passed its completion audit;
requiring completion of the still-running 10M ledger correctly failed.

The live API and bench now expose the most recent audit snapshot. These
read-only audit and presentation changes leave the running campaign's frozen
RTL, checker, controller and hardware configuration unchanged. Current evidence
is `results/campaigns/cw10m-b3ee8dcbc289a910/ledger-audit.json`; its count advances
only when the audit command is run again.

## Chipwheel v2 and incumbent benchmark — 2026-10-03

Goal set by the owner: a design at least 10x better than incumbents in speed
and size. Built `v2/` (spec, RTL, reference model, replay/fuzz/mutation tests)
without touching v1 or the running campaign, and a same-flow benchmark:
`scripts/arena.py` (Yosys/ABC on CMOS5L + a pre-layout NLDM STA written here,
typical and slow corners) and `scripts/compare.py`. Incumbents are real
open-source designs in `incumbents/`: an RP2040-PIO clone (fpga_pio, 1 and 4
state machines) and SERV, QERV and FemtoRV32 bit-banging GPIO.

v2 r6: 13,687 µm² (v1 46,940), UART TX 10 clocks/byte (v1 33), full-duplex SPI
17 clocks/byte (v1 cannot). Throughput per area at equal clock: 11.3x v1,
10.4x/11.6x PIO (one state machine), 103x+ CPU bit-banging; size 10.4–14.3x
smaller than PIO and the CPUs. Not 10x: speed versus PIO (parity at the 1 bit/clock
UART limit), size versus v1 (3.4x), and the 4-state-machine PIO per channel
(8.4x/9.4x). Details and caveats: `v2/docs/benchmark.md`.

Verified: 32 directed protocol tests on RTL and gate-level netlist, random
model-vs-RTL differential testing (millions of cycles, RTL and gates), 12/12
injected faults caught. Unverified: official LibreLane hardening, precheck and
post-route timing (no container engine/full PDK locally), silicon.

### v2 becomes the Tiny Tapeout entry — 2026-10-03

Owner accepted throughput per area as the 10x measure and asked to make v2 the
entry and harden it locally. `src/project.v` is now v2 (top `tt_um_chipwheel`,
50 MHz, 1x1 tile); v1's RTL, config, info.yaml, datasheet, spec and cocotb test
are archived in `v1/`. `docs/spec.md` and `docs/info.md` describe v2; the
cocotb test (`test/test.py`, models in `test/cells_sim.v`) replays UART TX/RX,
SPI and I2C scenarios against the reference model and passes. The PDK excludes
its integrated clock-gate cell from the flow, so memory clock gates are now a
latch plus AND gate (13,829 µm²; all v2 suites re-pass on RTL and gates).
Local hardening is blocked on disk space; see `docs/hardening.md`.
