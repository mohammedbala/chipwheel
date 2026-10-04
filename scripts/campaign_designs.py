"""Controlled Chipwheel candidates and their encoders."""
import hashlib

CANDIDATES = {
    'A': {'label': 'Baseline', 'words': 32, 'fused': False},
    'B': {'label': 'Smaller memory', 'words': 16, 'fused': False},
    'C': {'label': 'Fused instruction', 'words': 32, 'fused': True},
    'D': {'label': 'Combined', 'words': 16, 'fused': True},
}

def generate(source, config):
    if config['words'] == 16:
        edits = {
            'program_mem [0:31]': 'program_mem [0:15]',
            'reg [4:0] pc;': 'reg [3:0] pc;',
            'pc == 31': 'pc == 15',
            'argument > 31': 'argument > 15',
            'argument[4:0]': 'argument[3:0]',
            'if (uio_in[2]) program_mem[uio_in[7:3]][15:8] <= ui_in;\n                        else program_mem[uio_in[7:3]][7:0] <= ui_in;':
            'if (uio_in[7]) error_flag <= 1;\n                        else if (uio_in[2]) program_mem[uio_in[6:3]][15:8] <= ui_in;\n                        else program_mem[uio_in[6:3]][7:0] <= ui_in;',
        }
        for old, new in edits.items():
            if old not in source:
                raise ValueError('Baseline no longer matches candidate generator: '+old)
            source = source.replace(old, new)
    if config['fused']:
        source = source.replace('                            default: fault;', '''                            7: begin
                                   pin_value <= data_shift[0];
                                   data_shift <= {1'b0, data_shift[7:1]};
                                   wait_left <= argument;
                                   advance;
                               end
                            default: fault;''')
    return source

def uart(config, duration):
    if not isinstance(duration, int) or not 3 <= duration <= 4096:
        raise ValueError('UART duration must be 3..4096')
    if config['fused']:
        return [0x4008, 0x1000, 0x2000+duration-2, 0x7000+duration-2,
                0x5003, 0x1001, 0x2000+duration-2, 0]
    return [0x4008, 0x1000, 0x2000+duration-2, 0x3000,
            0x2000+duration-3, 0x5003, 0x1001, 0x2000+duration-2, 0]

def mutation(source, name):
    if name == 'short-wait':
        old = 'wait_left <= argument;'
        new = "wait_left <= (argument == 0 ? 0 : argument - 1'b1);"
    elif name == 'msb-first':
        old = "pin_value <= data_shift[0];\n                                   data_shift <= {1'b0, data_shift[7:1]};"
        new = "pin_value <= data_shift[7];\n                                   data_shift <= {data_shift[6:0], 1'b0};"
    else:
        raise ValueError(name)
    if old not in source:
        raise ValueError('Mutation target missing')
    return source.replace(old, new)
