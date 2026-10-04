; UART TX 8N1, LSB first. Host sends each byte with tag 0 (the start bit).
; Config: P0 push-pull, IDLE P0=1, MSB=0, CLKD=0, WRAP 1->0, DIV = bit-1.
tx:   SHIFT 9, D      ; pull {byte, 0}; start bit + 8 data bits, one tick each
      SET P0, 1       ; stop bit; wraps to tx
