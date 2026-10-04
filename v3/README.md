# Chipwheel v3 (in development)

A general programmable protocol engine: any logic-level digital protocol that
fits 8 pins and edges up to clk/2 becomes a program. It has:
- Two state machines that execute the RP2040 PIO instruction encoding, so PIO
  programs and pioasm syntax carry over.
- A hardware line unit per state machine for per-bit work: serializer and
  deserializer, bit stuffing, NRZ/NRZI/Manchester, CRC16 and timing recovery.
- Three "translation" features for protocols faster than the pins (below).

Specification: [docs/spec.md](docs/spec.md). The competition entry is still
v2 (`src/project.v`). Branch `v3` holds the v3 Tiny Tapeout candidate (4x2
tiles) for CI hardening.

## Status (2026-10-04)

| | |
|---|---|
| Area, same-flow measure (`scripts/arena.py`) | 119,507 µm²: 8,502 cells, 523 flops, 793 latches (`results/arena/CW3_r5`) |
| Per state machine vs the one-SM PIO-class clone | 59.8k vs 142.3k µm²: 2.4x smaller |
| Timing, pre-layout, slow corner | 13.1 ns reg2reg at a 20 ns period |
| Protocol tests | 22 (UART, SPI master and slave, QSPI, I2C, WS2812, USB-style TX/RX, CAN-style, Manchester, 1-Wire, links, DDR, timestamps) pass on the model, the RTL and the Yosys gate-level netlist |
| Mutation check | 25/25 injected RTL faults caught |
| Random differential fuzz | 1,500 cases, 5.75M cycles, 0 mismatches (27k DDR captures, 19k timestamps, 3.6k link transfers) |
| Tiny Tapeout cocotb test | 7/7 scenarios on RTL |

Earlier I estimated about 50k µm² for v3. The real design is 2.4x that,
mainly because two full PIO-compatible data paths with 16-bit registers cost
more than estimated. It still fits 4x2 tiles at about 45% cell density.
Post-route timing and gate-level results come from the CI flow on branch `v3`.

## Translating fast protocols

Logic cannot sample faster than its pins. Above about 50 Mb/s per pin, and for
analog lines, an external PHY or bridge has to come first. The research is in
[../docs/fast-protocols.md](../docs/fast-protocols.md), including which chips
make USB HS, DVI, 10BASE-T1S and CAN FD reachable. Behind that step, v3
translates at three levels:

- **Rate: IN_DDR.** Every pin is also sampled on the falling clock edge.
  `in pins, 2` records two samples per clock, which is 100 MS/s at 50 MHz.
- **Representation: IN TIME.** A free-running clock counter is an IN source.
  A program pushes the time of every edge, and the host decodes the timing in
  software instead of receiving every sample.
- **Protocol: SM-to-SM links.** One state machine terminates protocol A and
  its received entries go straight into the other state machine's transmit
  buffer, which speaks protocol B. No host is involved.

The test `test_link_translates_usb_line_to_uart_without_host` shows the link
end to end. A USB-style line at 12.5 Mb/s (NRZI, bit stuffing, CRC16) comes in
on one pin and goes out as 25 Mbaud UART on another while the host bus is
idle. Each link direction holds one entry, so both sides must run at similar
average rates. A packet buffer (section 6 of the research note) would remove
that limit.

## Run

```sh
cd v3/test
../../.venv/bin/python -m pytest -q test_protocols3.py      # directed tests, RTL replay
CW3_GL=../../results/arena/CW3_r5/netlist.v ../../.venv/bin/python -m pytest -q test_protocols3.py
python3 fuzz3.py --cases 200 --cycles 3000 --seed 1        # random model-vs-RTL
python3 mutants3.py                                        # injected faults
```

Programs are in `programs/` (pioasm syntax plus v3 extensions; see
`model/asm.py`). The reference model is `model/cw3.py`. The bench and the
independent devices are in `model/bench3.py` and `model/devices3.py`.
