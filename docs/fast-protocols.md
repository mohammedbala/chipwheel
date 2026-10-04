# Fast protocols through translation — research note (2026-10-04)

Scope: how a Tiny Tapeout (TT) IHP SG13CMOS5L protocol engine with 8 protocol
pins and a ≤50 MHz clock can take part in protocols far faster than its pins.
Nothing here has been tried on silicon or a board.

## Summary

The chip cannot drive or receive any multi-hundred-Mb/s line directly. On
paper its pads are fast enough. The limits are the TT multiplexer and a single
clock input with no PLL. The way in is to let an external chip handle the
electrical layer and talk to it over a slow, narrow logic-level interface. This works
best when Chipwheel generates the interface clock (SPI-like links, FT1248,
TFP410, TJA1410, CAN transceivers). It works worst when the external chip
generates a clock of 50 MHz or more and expects setup within one cycle (ULPI,
FT232H synchronous FIFO, and RMII at the edge). USB Full-Speed, CAN FD,
10BASE-T1S (via a 3-pin PMD), DVI 640x480 (via TFP410) and USB High-Speed
data (via FT1248) look reachable. 100 Mb Ethernet over RMII is plausible but
needs per-board phase tuning. PCIe, SATA, DDR, GbE, USB 3 and MIPI high-speed
links are out of reach except through bridges that terminate the protocol.

## 1. Tiny Tapeout IHP I/O limits (what I could verify)

- **Pads:** in the CMOS5L padring, `uo_out` uses `sg13cmos5l_IOPadOut30mA`,
  `uio` uses `sg13cmos5l_IOPadInOut30mA`, and `ui_in` and the clock use
  `sg13cmos5l_IOPadIn` ([tt_ihp_gpio.v][mux-gpio], drive bits `10` = 30 mA in
  [tt_ihp_wrapper.v][mux-wrap]). The SG13G2 26a branch is the same. I/O is
  3.3 V: the liberty corners are 3.0–3.6 V ([IHP IO lib][ihp-io], read at the
  pinned PDK commit `2bbec755`).
- **Pad speed (liberty):** for `IOPadInOut30mA` into 1–10 pF, core-to-pad
  delay is 1.7–2.6 ns typical and 2.9–4.2 ns slow, with edges of 0.5–1.6 ns
  typical and 0.8–2.5 ns slow. Input delay is 0.1–1.1 ns. Rising and falling
  inputs differ by about 0.5 ns. The pads are not the bottleneck.
- **Multiplexer budget:** the CMOS5L top-level signoff constraints cap
  pad→project at **5 ns** and project→pad at **12.5 ns** ([tt_top SDC][mux-sdc];
  inside the mux 2.5 / 7.5 ns, [tt_mux SDC][mux-sdc2]). These are max-delay
  constraints only, with no minimum delay and no skew guarantee. The pad load
  in that analysis is 0.03 pF, so real 10–25 pF loads add about 1–2 ns.
- **Clock:** the TT FAQ promises at least 50 MHz ([FAQ][tt-faq]). The only
  measured figures are for sky130: 66 MHz input rating, 33 MHz output toggle,
  about 20 ns mux round trip, <2 ns pin-to-pin spread, up to 10 ns clock
  insertion delay ([clock][tt-clock], [GPIO][tt-gpio]). The demo board makes
  1 Hz–66.5 MHz. The IHP 0p1 test chip was constrained at 50 MHz
  ([0p1 SDC][tt-ihp0p1]). **I found no published IHP-silicon measurement of
  maximum clock or toggle rate.**
- **Best-supported range:** 50 MHz is the design point. Up to about 66 MHz may
  work but is unverified. At 50 MHz SDR, pin data rates are ≤50 Mb/s (a 25 MHz
  square wave), comfortably within both pad ratings.
- **Worst-case clock-pad→output-pad delay (estimate):** about
  5 + 1 + 0.5 + 12.5 + 2 ≈ **21 ns** in the slow corner, probably half that
  typically. This rules out tight system-synchronous interfaces.
- **Reaction latency:** the v2 synchronizers (`pa`, `pb`) add 2–3 clocks, so
  pin-in to pin-out reaction takes about 60–80 ns (estimate).

## 2. Fast protocols on their own wires

| Protocol | Line rate / encoding | Electrical | Why beyond a 50 MHz logic pin |
|---|---|---|---|
| USB 2.0 FS | 12 Mb/s NRZI, bit stuffing ([USB 2.0][usb2]) | 3.3 V single-ended pair, 28–44 Ω drivers, 4–20 ns edges | **Not beyond:** 4 samples per bit at 48 MHz |
| USB 2.0 HS | 480 Mb/s NRZI | 400 mV current-mode differential, chirp handshake | 10x the pin rate and analog |
| USB 3.x | 5–20 Gb/s, 8b/10b or 128b/132b | CML SerDes | SerDes and CDR |
| 10BASE-T | 10 Mb/s Manchester (20 MHz edges), link pulses every 16 ms ([802.3][ieee8023]) | about 2.5 V differential through magnetics | **Borderline:** 50 ns half-bits need a 40 MHz clock or DDR; receive needs an external comparator |
| 100BASE-TX | 125 MBd MLT-3, 4B5B, scrambled | 3-level ±1 V | 3-level drive and CDR at 125 MBd |
| 1000BASE-T | 4 x 125 MBd PAM-5, full-duplex echo cancellation | analog hybrid | DSP PHY |
| 100BASE-T1 / 10BASE-T1S | 66.7 MBd PAM3 / 12.5 MBd DME, multidrop | single pair | PAM3 needs a PHY; T1S has 40 ns half-symbols |
| HDMI/DVI | TMDS 8b/10b, 10x pixel clock per lane, ≥250 Mb/s (DVI 25–165 MHz pixel clock, [TFP410][tfp410]) | CML | ≥250 Mb/s per lane |
| MIPI DSI/CSI-2 | D-PHY HS from 80 Mb/s to multi-Gb/s; LP mode ≤10 Mb/s at 1.2 V ([D-PHY][dphy]) | SLVS 200 mV | HS rate and 1.2 V levels |
| PCIe | 2.5–64 GT/s | CML, 100 MHz refclk | SerDes, LTSSM |
| SATA | 1.5–6 Gb/s 8b/10b, OOB bursts | CML | SerDes |
| DDR SDRAM | 200 MT/s and up, DQS strobes | SSTL 2.5/1.8/1.5 V | about 25–40 pins; typical minimum clocks of roughly 83–300 MHz (DLL on) |
| QSPI / Octal | 104–133 MHz SDR; xSPI 200 MHz DDR | 3.3 / 1.8 V | clock above 50 MHz; Octal needs 11 pins |
| SD UHS | SDR50/104 at 100/208 MHz, 1.8 V; UHS-II LVDS 1.56 Gb/s ([SD][sd]) | 1.8 V switchover | voltage and clock |
| CAN FD / XL | FD ≤5–8 Mb/s; XL ≤20 Mb/s, PWM on TXD above 8 Mb/s ([TI][ti-canxl]) | through a transceiver | **Logic pins are fine;** loop delay up to 255 ns needs delay compensation |
| LVDS display | 7 bits per pixel clock per pair, 10–135 MHz pixel clock ([SN75LVDS83B][lvds83b]) | LVDS | 70–945 Mb/s per pair |
| I3C HDR | SDR 12.5 MHz; HDR-DDR 25 Mb/s raw | push-pull 1.2–3.3 V | **Controller feasible;** target fails the 12 ns `tSCO` ([tSCO][i3c-tsco]) |

## 3. Translators: standard narrow interfaces

"PHY" means the engine still implements the protocol. "Bridge" means the
external chip terminates the protocol and the engine only moves bytes.

| Interface (chip) | Level | Pins | Clock / timing | Fits 8 pins at ≤50 MHz? |
|---|---|---|---|---|
| ULPI ([USB3300][usb3300]) | PHY, USB HS | 12 | PHY drives 60 MHz; link setup 5 ns, PHY output 2–5 ns → clock-to-pad ≤11.7 ns. [USB3320][usb3320] accepts a 60 MHz clock from the link | **No:** above the clock limit, 12 pins, ~21 ns worst-case path |
| UTMI | PHY, USB HS | ~30 | 60 MHz x 8 or 30 MHz x 16 | No |
| MII | PHY, Ethernet | 16 | 25 MHz (100 Mb) or 2.5 MHz (10 Mb), separate TX and RX clocks from the PHY | 10 Mb receive-only (6 pins); full duplex needs ≥12 |
| RMII ([LAN8720A][lan8720]) | PHY, 10/100 | 8 + REF_CLK on `clk` | 50 MHz. REF_CLK-in: setup 4 ns, hold 1.5 ns, PHY output ≤14 ns. REF_CLK-out: setup 7 ns, hold 2 ns, output ≤5 ns | **Marginal:** see note |
| SMII / RGMII / GMII | PHY | 4 / 12 / 24 | 125 MHz (RGMII DDR) | No |
| 100BASE-T1 PHY ([TJA1101][tja1101]) | PHY | as RMII | as RMII | As RMII |
| OA 3-pin PMD ([TJA1410][tja1410], [spec][oa-t1s]) | PHY, 10BASE-T1S | 3 | Clock-less. TX carries DME; RX is a ≥12 ns low pulse per line edge | **Yes,** with a pulse catcher |
| SPI MAC-PHY ([ENC28J60][enc28j60] ≤20 MHz, [KSZ8851SNL][ksz8851] ≤40 MHz) | Bridge (MAC) | 4–5 | Engine-mastered SPI | **Yes** (engine builds frames) |
| [TFP410][tfp410] DVI transmitter | PHY, TMDS | 6–7 with tied or fanned data bits | IDCK 25–165 MHz; setup 1.2 ns, hold 1.3 ns at 24-bit SDR | **Yes** at 25.175 MHz (shared or forwarded clock) |
| [SN75LVDS83B][lvds83b] | PHY, FPD-Link | 6+ (fanned) | 10–135 MHz | Yes, for panels with a low pixel clock |
| [SSD2828][ssd2828] / [TC358746][tc358746] | Bridge to DSI / CSI-2 | SPI 3–4 / parallel 11 | SPI (command-mode DSI) / PCLK ≤166 MHz | SSD2828 over SPI: yes. TC358746: needs 11 pins |
| DS92LV / TLK SerDes | raw SerDes | 11–18 | 10- to 16-bit words at ≥30 MHz | No (width) |
| FT232H FT1248 ([AN_167][an167]) | Bridge, USB HS | 4 (1-bit) / 7 (4-bit) / 11 | **Engine supplies SCLK** ≤30 MHz | **Yes,** about 12.5 MB/s raw in 4-bit at 25 MHz |
| FT232H async / sync 245 FIFO ([DS][ft232h]) | Bridge | 12 / 14 | Async ≤8 MB/s; sync 60 MHz with 7.5 ns setup | Async: with `ui`/`uo` pins. Sync: no |
| FT600/FT601 ([DS][ft60x]), FX3 GPIF II ([AN65974][fx3]) | Bridge, USB 3 | 16–32 bits + control | 66/100 MHz; GPIF also 8-bit, async or slow | FX3 8-bit async: with extra pins |
| PIPE ([spec][pipe]) | PHY, PCIe | 8-bit at 250 MHz or 16-bit at 125 MHz + ~30 pins | — | No |
| [AX99100][ax99100] | Bridge, PCIe | SPI 4 | PCIe→SPI master ≤42 MHz (engine is the slave) | Yes, at the slave rates the synchronizers allow (~10 MHz) |

**RMII budget.** In REF_CLK-in mode the TT clock-pad→output-pad delay must fall
between 1.5 and 16 ns. The worst case above is about 21 ns, and typical
probably 8–12 ns. On receive, data is valid 14–23 ns after the edge, a 9 ns
window that the uncertain clock-versus-data insertion delay may miss. It
needs a selectable launch and capture edge (ideally plus one delay tap),
calibrated on the preamble (`0x55` gives alternating dibits), or a board that
can phase-shift the TT clock against REF_CLK. 10 Mb RMII holds each dibit
for 10 clocks, which helps but is not a formal fix. Data at 12.5 MB/s also
outruns the host bus (roughly 8 MB/s for a v2-style handshake), so frames
must be buffered.

## 4. On-chip tricks

- **DDR input capture** (rising- and falling-edge flops): STA-checkable and
  cheap; gives a 10 ns grid that depends on the clock's duty cycle. **DDR
  output** needs a glitch-prone clock-gated mux in front of the 12.5 ns output
  path; avoid it. Instead drive the external clock at clk/2, so that "DDR at
  the device" is SDR inside the chip (HyperBus, TFP410 dual-edge mode).
- **Multi-phase / delay-line sampling:** `dlygate4sd3_1` takes 0.24 / 0.35 /
  0.54 ns (fast / typ / slow), a 2.2x spread. A 5 ns tap needs 9–21 cells.
  It must be calibrated at run time (count taps per clock, TDC style). STA
  cannot sign it off, and the flow's resizer may disturb the chain. Every tap
  is asynchronous, so each needs synchronizers. Best effort only.
- **Pulse / edge catcher:** a toggle flop clocked by the pin, then
  synchronized. This catches edges narrower than a clock (TJA1410's 12 ns RX
  pulses) and allows edge timestamping. The pin acts as a local clock, so it
  needs a CDC review.
- **Equivalent-time sampling:** only for repetitive signals, and it needs a
  vernier clock offset that one clock pin cannot give. Monitoring only.
- **Protocol-aware decimation:** record edge timestamps or only the
  packet-boundary windows (SYNC, SFD, EOP). This fits sniffing 10BASE-T, T1S,
  CAN and USB FS through a slow host bus.
- **External front-ends:** shift registers or LVDS deserializers need a
  forwarded word clock. Self-clocked lines need CDR first, which makes the
  front-end a PHY.
- **Rate adaptation:** the needed buffer size is the largest packet that must
  go out without gaps or be answered quickly. That is 64 B for USB FS bulk,
  CAN FD and minimum Ethernet frames; 512 B for USB HS bulk; 1518 B for full
  Ethernet frames; 2048 B for CAN XL.
- **External fast clock:** clocking the chip from the protocol's own clock
  (REF_CLK, CLKOUT) gives one clock domain and is the only sound
  system-synchronous option. Those inputs must then bypass the 2-flop
  synchronizers. A second clock domain on a `uio` pin needs custom SDC and is
  risky in the TT flow.
- **Sniffing versus participation:** sniffing tolerates fixed latency.
  Participating has deadlines: a USB FS device must respond within 6.5 bit
  times (~540 ns, 26 clocks at 48 MHz), USB HS within 192 bit times, and the
  CAN ACK slot comes one bit after the CRC. An I3C target's 12 ns `tSCO` is
  impossible here.

## 5. Verdict

| Protocol | Natively on TT pins? | Via external translator | Via on-chip trick | Impossible on TT, and why |
|---|---|---|---|---|
| USB FS | **Yes:** 2 pins plus a 1.5 kΩ pull-up, 48 MHz, series resistors; edge rate and impedance not formally compliant (precedent: [TinyFPGA][tinyfpga] at 48 MHz) | — | DDR gives 8 samples per bit | — |
| USB HS | No | FT1248 (4–7 pins, ≤30 MHz, bridge); ULPI no | 512 B buffer | ULPI: 60 MHz, 11.7 ns budget, 12 pins |
| USB 3.x | No | FX3 GPIF 8-bit async (bridge) | — | SerDes; PIPE |
| 10BASE-T | Transmit with resistors and magnetics at 40 MHz (TX-only precedent: [Pico-10BASE-T][pico10bt]) | ENC28J60 SPI; MII-10; RMII-10 | Receive: comparator + DDR oversampling | Formal compliance |
| 100BASE-TX / 100BASE-T1 | No | RMII PHY (LAN8720A, TJA1101), 8 pins + clk, marginal; KSZ8851SNL SPI | Edge-select capture + preamble calibration; buffer | Native: MLT-3/PAM3 |
| 1000BASE-T | No | None (a 10/100/1000 PHY can only be forced down to 100 Mb) | — | GMII/RGMII at 125 MHz |
| 10BASE-T1S | No | **TJA1410, 3 pins, clock-less (engine does DME/PLCA)**; LAN8651 SPI | Pulse catcher + DDR | — |
| HDMI/DVI | No | **TFP410, 6–7 pins, 25.175 MHz, 3-bit color** | No frame buffer: algorithmic or line-based pixels | Modes with a pixel clock above ~50 MHz (e.g. 1024x768@60 = 65 MHz) |
| LVDS display | No | SN75LVDS83B, fanned bits | — | Panels needing more than 50 MHz |
| MIPI DSI/CSI-2 | No | SSD2828 over SPI; TC358746 with 11 pins | — | Native HS (SLVS, ≥80 Mb/s) |
| PCIe / SATA | No | AX99100 (PCIe→SPI) only; SATA none practical | — | SerDes; PIPE ≥125 MHz; PATA bridges need ~30 pins |
| DDR SDRAM | No | Substitute HyperRAM (12 pins, CK = clk/2) or QSPI PSRAM | — | Pins, SSTL, minimum clock |
| QSPI / Octal | QSPI at SCLK = clk/2 (25 MHz) | — | Read-latency compensation | Octal: 11 pins, 1.8 V |
| SD | Default Speed (25 MHz, 4-bit, 6 pins) | — | Four parallel CRC16s | UHS: 1.8 V, ≥100 MHz |
| CAN FD / XL | **Yes, with transceiver TXD/RXD:** FD ≤8 Mb/s at 40 MHz (5 tq) | SIC-XL in NRZ mode ≤8 Mb/s | Delay compensation ([CiA][can-tdc]) | XL FAST PWM 10–20 Mb/s (needs ≥80 MHz) |
| I3C | Controller SDR and HDR-DDR | I3C hub for 1.2/1.8 V | — | Target `tSCO` ≤12 ns |

## 6. Recommendations for v3

Area uses 31 µm² per latch bit and 49 µm² per flop bit; cells from the CMOS5L
library.

| Feature | Unlocks | Area (approx.) |
|---|---|---|
| Synchronous input mode per pin group (one register, no 2-flop synchronizer) + rising/falling capture and launch select | RMII, sampling FT1248/QSPI read data, SD | ~0.001 mm² |
| DDR capture (2 samples per pin per clock into the shifter) | USB FS 8x, 10BASE-T receive, T1S, MII-100 sniffing, I3C HDR | ~0.003 mm² |
| Pulse catcher on 2 pins + 16 x 20-bit edge-timestamp FIFO (latches) | TJA1410, decimated sniffing, equivalent-time | ~0.015 mm² |
| Clock-forward output (latch+AND, like v2's memory clock gates) | TFP410 IDCK, RMII REF_CLK-in, SCLK = clk | <0.0001 mm² |
| 8-bit lane mode + strobe and flags on `ui`/`uo` | FT232H async FIFO, FT1248-8, FX3, HyperRAM, TC358746 | ~0.002 mm² |
| CRC unit taking 2/4 bits per clock, polynomial ≤32 bits, reflection; plus 4 x CRC16 | Ethernet over RMII, SD 4-bit, CAN FD/USB | ~0.006 + 0.004 mm² |
| 32-bit TX history with a compare tap | CAN FD delay compensation, loopback checks | ~0.002 mm² |
| Packet buffer: 64 B latch / **2 KB IHP SRAM** (`RM_IHPSG13_1P_1024x16`, 237x336 µm) | USB FS, CAN FD / full Ethernet frames, USB HS bulk | 0.022 / **0.080 mm²** (2 KB of latches ≈ 0.5 mm², not feasible) |
| Optional delay-tap sampler (4 phases x 2 pins) | RMII receive margin, experiments | ~0.002 mm², high risk |

Priority: the first four features cost about 0.02 mm² and unlock most rows
above. The SRAM macro is the single largest unlock for whole-frame Ethernet
and USB HS. TT reports that the 1024x8 version was taped out and works on
SG13G2, but warns that integration is still hard ([memory][tt-mem]). TT
support on CMOS5L is unconfirmed, although the macros are in the CMOS5L PDK.
Keep the design clock-agnostic so the same silicon can run at 48 (USB FS),
40 (CAN, 10BASE-T), 50 (RMII) and 25.175 MHz (DVI).

[tt-faq]: https://tinytapeout.com/faq/
[tt-clock]: https://tinytapeout.com/specs/clock/
[tt-gpio]: https://tinytapeout.com/specs/gpio/
[tt-mem]: https://tinytapeout.com/specs/memory/
[tt-ihp0p1]: https://github.com/TinyTapeout/tinytapeout-ihp-0p1/blob/main/designs/ihp-sg13g2/tt-chip/constraint.sdc
[mux-gpio]: https://github.com/TinyTapeout/tt-multiplexer/blob/ihp-sg13cmos5l/rtl/tt_ihp_gpio.v
[mux-wrap]: https://github.com/TinyTapeout/tt-multiplexer/blob/ihp-sg13cmos5l/ol2/tt_top/tt_ihp_wrapper.v
[mux-sdc]: https://github.com/TinyTapeout/tt-multiplexer/blob/ihp-sg13cmos5l/ol2/tt_top/signoff.sdc
[mux-sdc2]: https://github.com/TinyTapeout/tt-multiplexer/blob/ihp-sg13cmos5l/ol2/tt_mux/signoff.sdc
[ihp-io]: https://github.com/IHP-GmbH/IHP-Open-PDK/tree/main/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/lib
[usb2]: https://www.usb.org/document-library/usb-20-specification
[usb3300]: https://ww1.microchip.com/downloads/en/DeviceDoc/00001783C.pdf
[usb3320]: https://www.microchip.com/content/dam/mchp/documents/UNG/ProductDocuments/DataSheets/USB3320C-Data-Sheet-DS00005796.pdf
[lan8720]: https://ww1.microchip.com/downloads/en/DeviceDoc/00002165B.pdf
[ft232h]: https://ftdichip.com/wp-content/uploads/2020/07/DS_FT232H.pdf
[an167]: https://www.ftdichip.com/Support/Documents/AppNotes/AN_167_FT1248_Parallel_Serial_Interface_Basics.pdf
[ft60x]: https://www.ftdichip.com/Support/Documents/DataSheets/ICs/DS_FT600Q-FT601Q%20IC%20Datasheet.pdf
[fx3]: https://www.mouser.com/pdfdocs/AN65974_Designing_with_the_EZ-USB_FX3_Slave_FIFO_Interface.pdf
[tfp410]: https://www.ti.com/lit/ds/symlink/tfp410.pdf
[lvds83b]: https://www.ti.com/product/SN75LVDS83B
[ssd2828]: https://www.solomon-systech.com/en/product/mobile-system/mipi-master-bridge-chip/ssd2828/
[tc358746]: https://toshiba.semicon-storage.com/us/semiconductor/product/interface-bridge-ics-for-mobile-peripheral-devices/camera-interface-bridge-ics/detail.TC358746AXBG.html
[dphy]: https://www.mipi.org/specifications/d-phy
[pipe]: https://www.intel.com/content/dam/www/public/us/en/documents/white-papers/phy-interface-pci-express-sata-usb30-architectures-3.1.pdf
[ax99100]: https://www.asix.com.tw/en/product/Interface/PCIe_Bridge/AX99100A
[oa-t1s]: https://opensig.org/wp-content/uploads/2024/01/OPEN_Alliance_10BASE-T1S_PMD_Transceiver_Interface_1p5_Final.pdf
[tja1410]: https://www.nxp.com/products/TJA1410
[tja1101]: https://www.nxp.com/products/TJA1101B
[ksz8851]: https://ww1.microchip.com/downloads/aemDocuments/documents/UNG/ProductDocuments/DataSheets/KSZ8851SNL-Single-Port-Ethernet-Controller-with-SPI-DS00002381C.pdf
[enc28j60]: https://ww1.microchip.com/downloads/en/DeviceDoc/39662c.pdf
[ieee8023]: https://standards.ieee.org/ieee/802.3/10422/
[sd]: https://www.sdcard.org/downloads/pls/
[can-tdc]: https://can-cia.org/fileadmin/cia/documents/proceedings/2013_hartwich_v2.pdf
[ti-canxl]: https://www.ti.com/lit/an/sdaa190/sdaa190.pdf
[i3c-tsco]: https://onlinedocs.microchip.com/oxy/GUID-598A6CC5-BA9B-433D-BAFE-893E2A72A7A3-en-US-12/GUID-DBBEEBB0-6159-4032-BD9E-213FAD98CFBA.html
[tinyfpga]: https://github.com/tinyfpga/TinyFPGA-Bootloader
[pico10bt]: https://github.com/kingyoPiyo/Pico-10BASE-T
