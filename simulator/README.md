# Chipwheel experiment bench

Run from the project root:

```sh
python3 scripts/simulator.py
```

Open http://127.0.0.1:8766/simulator/. The server listens only on this Mac.
It serves project files and regenerates the verification evidence from the
result JSON files when the page loads. Refresh evidence after running new
verification. No packages or internet connection are needed.

Use Load experiment after editing instructions. Play or Step clock advances
the view; the slider and waveform select an edge. Instruction buttons jump to
their first execution. The schematic shows state after the selected edge.
UART checks compare the pin and completion edge to the independent 8N1 timing
requirement. Pulse/custom programs show completion or faults without a UART
claim. A program still busy at 50,000 clocks is marked as truncated.

The browser model assumes complete program loading and enabled clocks. It
does not model loader writes, reset interruptions, disabled clocks or competing
host requests. Unloaded memory stops the model; physical memory is undefined.
The fault selectors change only this teaching model. They do not alter RTL.
Actual RTL results appear separately with links to their evidence.

Notebook entries are saved in local browser storage for this origin. Export
JSON for a durable backup; importing appends validated entries. Notebook saves
are model explorations, never verification results. No data is sent remotely.
Opening index.html directly also works with the generated evidence snapshot,
but browser storage behavior may differ; use the local server for persistence.

The live design view polls `/simulator/live.json` every three seconds. It reads
the current RTL, clock configuration, tile allocation, program encoder and
verification result files. Register dimensions and opcode-case counts are
extracted from the v1 source declarations; unrecognized structures show unknown
values. This is a functional schematic, not an inferred netlist or floorplan.

Stages compare source hashes, and mapping/physical stages also compare config
hashes. Mapped verification compares its netlist hash to the current mapped
artifact, plus synthesis provenance. Historical results remain visible without
claiming a current pass. The browser teaching model is pinned to the verified
v1 RTL hash; edits show a model mismatch warning rather than silently inventing
new hardware behavior. Update the model and its replay tests when the ISA changes.
Saved notebook entries include the live design revision when available. Recent
observed changes persist locally; they are not a complete Git history. Live
updates require the local server. The separate campaign panel provides explicit
Start/Pause/Resume controls for real RTL verification; ordinary model playback
does not launch verification. The panel reads committed ledger totals separately
from in-flight progress, shows provisional mapped-area rankings until physical
checks pass, and provides failure replay controls while the campaign is paused.
See `docs/campaign.md` for the four designs, budgets, isolation and recovery rules.

Check the teaching model against the saved RTL replay and boundary scenarios:

```sh
node simulator/engine.test.js
```

Refresh the direct-open evidence snapshot with `python3 scripts/simulator.py --snapshot`.
