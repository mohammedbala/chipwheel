; I2C master: SDA P0, SCL P1, open-drain. Host tag = 9th bit: 1 releases SDA
; (slave ACK, or master NACK on the last read), 0 = master ACK.
; Config: CLKD=1, PH3=1, MSB=1, INSEL=1, IDLE 1111, open-drain 1111, WRAP_TOP 1.
; Shifts end with SCL low; releasing SCL waits until it reads high (stretching).
; Entries: 4 = START (idle bus), 2 = repeated START, 6 = STOP.
byte:   SHIFT 9, D, A  ; 0: 8 data bits + ACK bit, push byte, F = ACK bit
        BRF1 stop      ; 1: NACK -> STOP; otherwise wraps to byte
rstart: SET P0, 1      ; 2: release SDA (SCL low)
        SET P1, 1      ; 3: release SCL
start:  SET P0, 0      ; 4: START (SDA falls while SCL high)
        BR byte        ; 5
stop:   SET P0, 0      ; 6: SDA low (SCL low)
        SET P1, 1      ; 7: release SCL
        SET P0, 1      ; 8: SDA rises while SCL high = STOP
        HALT           ; 9
