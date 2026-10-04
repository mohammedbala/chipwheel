# Official hardening — status log

## 2026-10-03: local container attempt (blocked on disk space)

Goal: run the pinned Tiny Tapeout CMOS5L flow (LibreLane 3.1.0.dev3,
dockerized) locally for the v2 entry (`src/project.v`, 1x1 tile, 20 ns).

Done and kept:
- `brew install colima docker` (user-owned Homebrew, no administrator rights).
- PDK: sparse checkout of IHP-Open-PDK at the pinned commit `2bbec755` in
  `.tools/pdk` (441 MB: everything except `ihp-sg13g2/libs.doc` and
  `ihp-sg13g2/libs.tech/openems`; the CMOS5L tree symlinks into SG13G2 tech
  scripts, so those stay). `SOURCES` records the revision, as the official
  install script does.
- `tt_tool.py --create-user-config --ihp` passes (port check, 1x1 die
  `0 0 202.08 154.98`, `RT_MAX_LAYER Metal4`). Needs `scripts/yowasp-yosys` on
  PATH (`.tools/shim`) and Homebrew's Cairo (`DYLD_FALLBACK_LIBRARY_PATH`).
- Design change for the flow: the PDK's LibreLane config excludes
  `sg13cmos5l_lgcp_1` (integrated clock gate) from synthesis and PnR, so each
  memory word's clock gate is a `sg13cmos5l_dllrq_1` transparent-low latch plus
  `sg13cmos5l_and2_1`. Area 13,829 µm² (was 13,687); all v2 suites re-run and pass.

What failed:
- With 7.4 GiB free, the Colima VM plus `docker pull` of
  `ghcr.io/librelane/librelane:3.1.0.dev3` (arm64: 1.19 GB compressed; the main
  layer unpacks to about 4.4 GB, from its gzip trailer) filled the disk. The VM's
  container store reported I/O errors. The v1 campaign paused itself through its
  5 GiB disk guard (503,303 checks, 0 failures) and was resumed afterwards.
- Recovery: deleted only what this session created (the corrupted VM and its
  data disk, Colima's base-image cache, the Homebrew bottles just downloaded,
  `v2/build`). Free space returned to about 8 GiB. No other files were touched.

Space needed: VM about 1 GiB, image about 5.6 GiB (the container store keeps
both the compressed and unpacked copies), runs up to 1 GiB, plus a 5 GiB
reserve so the machine and the campaign keep working: **at least 15 GiB free**.

To finish once space is available:

```sh
sh scripts/harden_local.sh
```

It refuses to start below 15 GiB, then hardens, saves `results/hardening/`
(log, metrics, stats, warnings) and replays the v2 directed suite plus a
random campaign on the routed netlist. Alternative without local disk: push
to GitHub, where `.github/workflows/gds.yaml` runs the same flow, precheck and
gate-level test.

## 2026-10-04: GitHub Actions (official Tiny Tapeout flow) — hardened

Repository: https://github.com/mohammedbala/chipwheel (public). The unchanged
template workflows ran on every push: `test`, `docs` and `gds`
(LibreLane 3.1.0.dev3 hardening → precheck → gate-level test → viewer).

1. First run (`35e7540`): placement and routing completed, setup met (8.88 ns
   slack at 20 ns, slow corner), but 13 fast-corner hold violations (worst
   −31 ps), all the clock-gating check inside each memory word's discrete clock
   gate (latch → AND), which the flow's hold repair does not fix. Fix
   (`4a75749`): a `sg13cmos5l_dlygate4sd3_1` between latch and AND gate.
2. Second run: **hardening passed, Tiny Tapeout precheck passed (all 9
   checks), viewer published**. The gate-level test failed to compile because
   the template's `test/Makefile` omits the PDK's `sg13cmos5l_udp.v`, which the
   flop/latch/mux cell models need (the template's adder example never needs it).
   Fix: add it. The cocotb test then passes locally on the routed submission
   netlist, as do the 32 directed v2 tests and 1.23M random cycles
   (`v2/results/fuzz_postlayout.json`).

Signoff numbers (1x1 tile, die 202.08 × 154.98 µm, 20 ns clock):

| Metric | Value |
|---|---|
| Worst setup slack (slow 1.08 V 125 °C) | 8.29 ns → about 85 MHz achievable |
| Worst hold slack (fast 1.32 V −40 °C) | +0.105 ns |
| Routing DRC / Magic DRC / LVS / antenna | 0 / 0 / 0 / 0 |
| Std-cell area | 18,186 µm²: logic 13,549 (sequential 7,014) + timing-repair buffers 2,932 + clock tree 1,704 |
| Fill | 10,756 µm²; utilization 62.8 % of the core |
| Total power (typical) | 0.73 mW |

Viewer: https://mohammedbala.github.io/chipwheel/
