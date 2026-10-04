/* Behavioral teaching model of src/project.v; not an RTL verification tool. */
(function (root) {
  const names = ['HALT', 'OUT', 'WAIT', 'SHIFT', 'COUNT', 'DJNZ', 'JMP'];
  const limits = [0, 1, 4095, 0, 255, 31, 31];
  function assemble(text) {
    const words = text.split('\n').map(x => x.split('#')[0].trim()).filter(Boolean).map((line, i) => {
      const parts = line.toUpperCase().split(/\s+/), op = names.indexOf(parts[0]);
      const arg = parts.length === 1 ? 0 : Number(parts[1]);
      if (parts.length > 2 || op < 0 || !Number.isInteger(arg) || arg < 0 || arg > limits[op]) throw Error(`Instruction ${i + 1}: invalid ${line}`);
      return (op << 12) | arg;
    });
    if (!words.length || words.length > 32) throw Error('Use 1–32 instructions.');
    return words;
  }
  function program(kind, b) {
    if (kind === 'pulse') return 'OUT 0\nWAIT 2\nOUT 1\nWAIT 4\nOUT 0\nWAIT 1\nHALT';
    if (!Number.isInteger(b) || b < 3 || b > 4096) throw Error('Bit duration must be 3–4096 clocks.');
    return `COUNT 8\nOUT 0\nWAIT ${b - 2}\nSHIFT\nWAIT ${b - 3}\nDJNZ 3\nOUT 1\nWAIT ${b - 2}\nHALT`;
  }
  function simulate(words, payload, fault = 'none', max = 50000) {
    if (!Number.isInteger(payload) || payload < 0 || payload > 255) throw Error('Payload must be 0–255.');
    let s = {edge:0, pc:0, wait:0, count:0, data:payload, pin:1, busy:1, error:0, action:'START accepted; first instruction at E1', executed:null};
    const trace = [{...s}];
    function fail(reason) {s.busy=0; s.pin=1; s.error=1; s.action=reason;}
    function advance() {if(s.pc === 31) fail('Fault: advance beyond address 31'); else s.pc++;}
    while(s.busy && s.edge < max) {
      s = {...s, edge:s.edge+1, executed:null};
      if(s.wait) {s.wait--; s.action='WAIT stall';}
      else if(s.pc >= words.length) fail('Unloaded address: memory is undefined; model stopped');
      else {
        const w=words[s.pc], op=w>>>12, a=w&4095;
        s.executed=s.pc; s.action=`${names[op] || 'INVALID'} ${a}`;
        if(op>6 || a>limits[op]) fail('Fault: invalid instruction');
        else switch(op) {
          case 0: s.busy=0; s.pin=1; break;
          case 1: s.pin=a; advance(); break;
          case 2: s.wait=fault==='short-wait'?Math.max(0,a-1):a; advance(); break;
          case 3: s.pin=fault==='msb-first'?(s.data>>>7)&1:s.data&1; s.data=fault==='msb-first'?(s.data<<1)&255:s.data>>>1; advance(); break;
          case 4: s.count=a; advance(); break;
          case 5: if(s.count>1){s.count--;s.pc=a;}else{s.count=0;advance();} break;
          case 6: s.pc=a; break;
        }
      }
      trace.push({...s});
    }
    return {trace, truncated:!!s.busy};
  }
  function expectedUART(edge, byte, b) {
    if(edge<2 || edge>=2+9*b) return 1;
    if(edge<2+b) return 0;
    return (byte>>>Math.floor((edge-2-b)/b))&1;
  }
  function checkUART(result, byte, b) {
    const end=2+10*b;
    for(let e=0;e<=end;e++) {
      const s=result.trace[Math.min(e,result.trace.length-1)];
      if(s.pin!==expectedUART(e,byte,b)) return {ok:false,edge:e,reason:`Pin mismatch at E${e}`};
      if(s.busy!==(e<end?1:0)) return {ok:false,edge:e,reason:`Completion mismatch at E${e}; expected E${end}`};
    }
    return {ok:true,edge:end,reason:`8N1 waveform matches; completion E${end}`};
  }
  const api={names,assemble,program,simulate,expectedUART,checkUART};
  if(typeof module!=='undefined') module.exports=api; else root.Chipwheel=api;
})(globalThis);
