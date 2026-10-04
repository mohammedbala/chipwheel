; UART RX 8N1, LSB first, RX on P2. Tick = half a bit (DIV = bit/2 - 1).
; Config: CLKD=1, PH3=0, MSB=0, INSEL=0, WRAP 2->0. Captures land on bit centres.
rx:   WAIT P2, 0      ; start-bit edge
      SHIFT 8, A      ; 8 clocked captures (P1 unused), push the byte
      WAIT P2, 1      ; stop bit; wraps to rx
