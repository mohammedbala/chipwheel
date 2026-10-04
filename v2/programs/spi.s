; SPI master, CPHA=0 (modes 0 and 2): MOSI P0, SCK P1, MISO P2, CS P3.
; Config: CLKD=1, PH3=0, MSB=1, INSEL=0, IDLE = CS 1 | MISO 1 | SCK CPOL,
; open-drain mask 0100 (MISO is an input), WRAP_TOP 0. Run entry 1, end entry 3.
loop:  SHIFT 8, D, A   ; 0: full-duplex byte; wraps to itself (streaming)
       SET P3, 0       ; 1: entry: select
       BR loop         ; 2
end:   SET P1, 0       ; 3: entry: SCK back to CPOL (test patches the value)
       SET P3, 1       ; 4: deselect
       HALT            ; 5
