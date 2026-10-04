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
