const assert=require('node:assert/strict'),fs=require('node:fs'),C=require('./engine.js');
const replay=JSON.parse(fs.readFileSync('results/demo-A.json'));
const a=C.simulate(C.assemble(C.program('uart',8)),65);
assert.deepEqual(a.trace.slice(1).map(s=>s.pin),replay.observed,'Model must match saved RTL replay samples E1..E82');
for(const b of [3,8,19,4096]) for(const byte of [0,1,65,128,255]) {
  const r=C.simulate(C.assemble(C.program('uart',b)),byte);
  assert.equal(C.checkUART(r,byte,b).ok,true);
  assert.equal(r.trace.length-1,2+10*b);
}
for(const fault of ['short-wait','msb-first']) assert.equal(C.checkUART(C.simulate(C.assemble(C.program('uart',8)),65,fault),65,8).ok,false);
const pulse=C.simulate(C.assemble(C.program('pulse')),0);
assert.deepEqual(pulse.trace.filter((s,i,t)=>i&&s.pin!==t[i-1].pin).map(s=>s.edge),[1,5,11,14]);
assert.equal(C.simulate(C.assemble('JMP 0'),0,'none',100).truncated,true);
assert.equal(C.simulate(Array(32).fill(0x1000),0).trace.at(-1).error,1);
assert.equal(C.simulate([0x7000],0).trace.at(-1).error,1);
assert.equal(C.simulate([0x1002],0).trace.at(-1).error,1);
assert.equal(C.simulate(C.assemble('COUNT 1\nDJNZ 0\nHALT'),0).trace.length-1,3);
assert.throws(()=>C.assemble('WAIT 4096'));
assert.throws(()=>C.simulate([0],256));
console.log('PASS: recorded RTL waveform, UART boundaries, pulse timing, both faults, loops, overflow and validation');
