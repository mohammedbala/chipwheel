(() => {
  const $=id=>document.getElementById(id), C=Chipwheel, esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let result, words, config, selected=0, timer=null, verdict, notebook=[];
  try{notebook=JSON.parse(localStorage.getItem('chipwheel-notebook-v1')||'[]');if(!Array.isArray(notebook))notebook=[];}catch{}
  function stop(){clearInterval(timer);timer=null;$('play').textContent='Play';}
  function preset(){if($('preset').value!=='custom')try{$('code').value=C.program($('preset').value,Number($('bits').value));}catch(e){$('error').textContent=e.message;}}
  function load(){stop();try{
    const nextWords=C.assemble($('code').value),nextConfig={kind:$('preset').value,payload:Number($('payload').value),bits:Number($('bits').value),fault:$('fault').value,code:$('code').value};
    if(nextConfig.kind==='uart' && nextConfig.code.trim()!==C.program('uart',nextConfig.bits))nextConfig.kind='custom';
    if(!['none','short-wait','msb-first'].includes(nextConfig.fault))throw Error('Unknown fault mode.');
    const nextResult=C.simulate(nextWords,nextConfig.payload,nextConfig.fault);
    words=nextWords;config=nextConfig;result=nextResult;verdict=config.kind==='uart'?C.checkUART(result,config.payload,config.bits):null;
    selected=0;$('cursor').max=result.trace.length-1;$('cursor').value=0;$('word-count').textContent=`· ${words.length}/32 words`;$('error').textContent='';render();
  }catch(e){$('error').textContent=e.message;}}
  function go(e){selected=Math.max(0,Math.min(result.trace.length-1,e));$('cursor').value=selected;render();}
  function render(){
    if(!result)return;const s=result.trace[selected], width=Math.max(250,$('diagram').clientWidth-26), compact=width<540, h=compact?360:205;
    window.dispatchEvent(new CustomEvent('chipwheel:edge',{detail:{state:s,config,words:words.length}}));
    const nodes=compact?[[10,10,'Instruction memory',`PC ${s.pc} / ${words.length} words`],[10,100,'Control / counters',`WAIT ${s.wait} · LOOP ${s.count}`],[10,190,'Data shift register',s.data.toString(2).padStart(8,'0')],[10,280,'Output pin',String(s.pin)]]:[[10,45,'Instruction memory',`PC ${s.pc} / ${words.length} words`],[width*.34,45,'Control / counters',`WAIT ${s.wait} · LOOP ${s.count}`],[width*.34,132,'Data shift register',s.data.toString(2).padStart(8,'0')],[width*.73,45,'Output pin',String(s.pin)]];
    const nw=compact?width-20:width*.25;
    const wire=compact?`M ${width/2} 78 V 280`:`M ${10+nw} 80 H ${width*.34} M ${width*.34+nw} 80 H ${width*.73} M ${width*.34+nw/2} 113 V 132`;
    $('diagram').innerHTML=`<svg viewBox="0 0 ${width} ${h}" role="img" aria-label="At edge ${selected}, PC ${s.pc}, wait ${s.wait}, loop ${s.count}, data ${s.data}, pin ${s.pin}"><path class="wire" d="${wire}"/>${nodes.map(([x,y,title,value],i)=>`<rect x="${x}" y="${y}" width="${nw}" height="68" class="${(i===1&&s.busy)||(i===3&&s.executed!==null)?'active':''}"/><text x="${x+10}" y="${y+22}" class="small">${title}</text><text x="${x+10}" y="${y+48}">${esc(value)}</text>`).join('')}</svg>`;
    $('current-time').textContent=`E${selected} · ${(selected*.1).toFixed(1)} µs`;$('edge-label').textContent=`E${selected}`;
    $('readout').textContent=`${s.action}  |  BUSY ${s.busy}  ERROR ${s.error}`;
    $('step').disabled=selected===result.trace.length-1;
    $('verdict').classList.toggle('fail',!!(verdict&&!verdict.ok)||!!s.error);
    $('verdict').textContent=verdict?`${verdict.ok?'MATCH':'MISMATCH'} · ${verdict.reason}`:result.truncated?'Stopped at 50,000 clocks: program is still busy.':result.trace.at(-1).error?'Program stopped at undefined memory or an instruction fault.':'Program completes at E'+(result.trace.length-1)+'. No protocol checker is selected.';
    drawWave();
    const row=Math.min(words.length-1,s.executed??s.pc),start=Math.max(0,row-2),end=Math.min(words.length,start+6);
    $('trace-list').innerHTML=words.slice(start,end).map((w,i)=>`<button class="${start+i===row?'selected':''}" data-address="${start+i}">${String(start+i).padStart(2,'0')} ${C.names[w>>>12]} ${w&4095}</button>`).join('');
    $('trace-list').querySelectorAll('button').forEach(b=>b.onclick=()=>{stop();const e=result.trace.findIndex(s=>s.executed===Number(b.dataset.address));if(e>=0)go(e);});
  }
  function drawWave(){
    const w=Math.max(260,$('waveform').clientWidth),left=55,right=w-12,last=result.trace.length-1,span=Math.min(120,last),lo=Math.max(0,Math.min(last-span,selected-Math.floor(span/2))),hi=lo+span;
    const x=e=>left+(e-lo)/Math.max(1,span)*(right-left), pin=e=>result.trace[Math.min(e,last)].pin;
    function path(get,high,low){let d=`M${x(lo)} ${get(lo)?high:low}`;for(let e=lo+1;e<=hi;e++)d+=`H${x(e)}V${get(e)?high:low}`;return d;}
    const n=w<450?3:6,ticks=Array.from({length:n+1},(_,i)=>Math.round(lo+span*i/n));
    $('waveform').innerHTML=`<svg viewBox="0 0 ${w} 166" role="img" aria-label="Pin and busy waveform, clocks ${lo} through ${hi}"><rect x="${left}" y="15" width="${right-left}" height="117" fill="#f7fafb" stroke="#d5dfe4"/>${ticks.map(e=>`<path d="M${x(e)} 15V132" stroke="#e1e8ec"/><text x="${x(e)}" y="153" text-anchor="${e===lo?'start':e===hi?'end':'middle'}">${e}</text>`).join('')}<text x="0" y="44">PIN</text><text x="0" y="108">BUSY</text><path d="${path(pin,30,67)}" fill="none" stroke="#087b9b" stroke-width="2.5"/>${config.kind==='uart'?`<path d="${path(e=>C.expectedUART(e,config.payload,config.bits),36,73)}" fill="none" stroke="#a54630" stroke-width="1.5" stroke-dasharray="4 3"/>`:''}<path d="${path(e=>result.trace[e].busy,95,121)}" fill="none" stroke="#536977" stroke-width="1.5"/><path d="M${x(selected)} 15V132" stroke="#172c3a" stroke-width="1.5"/><text x="${left}" y="165">Clock edge</text></svg>`;
    $('waveform').querySelector('svg').onclick=ev=>{stop();const r=ev.currentTarget.getBoundingClientRect();go(Math.round(lo+((ev.clientX-r.left)*w/r.width-left)/(right-left)*span));};
  }
  function persist(){try{localStorage.setItem('chipwheel-notebook-v1',JSON.stringify(notebook));return true;}catch{$('notice').textContent='Browser storage unavailable. Export your notebook to preserve it.';return false;}}
  function provenance(entry){
    const c=entry.campaign,d=entry.design;
    const campaign=c&&/^[a-zA-Z0-9-]+$/.test(c.id)?`<small><a href="../results/campaigns/${encodeURIComponent(c.id)}/manifest.json" target="_blank" rel="noopener" title="${esc(c.revision)}">Campaign ${esc(c.id)}</a> · ${Number(c.checked||0).toLocaleString()} checks when saved</small>`:'';
    const design=typeof d?.rtl_sha256==='string'?`<small title="${esc(d.rtl_sha256)}">Design ${esc(d.rtl_sha256.slice(0,12))}${d.model_matches===false?' · model differs':''}</small>`:'';
    const link=c&&/^[a-zA-Z0-9-]+$/.test(c.id)&&d?.rtl_sha256===c.baseline_rtl_sha256?`<small><a href="../results/campaigns/${encodeURIComponent(c.id)}/frozen/src/project.v" target="_blank" rel="noopener">Frozen design source</a></small>`:'';
    return campaign+design+link;
  }
  function notes(){
    $('notebook').innerHTML=notebook.length?`<div class="table-wrap"><table><thead><tr><th>Exploration</th><th>Configuration</th><th>Outcome</th><th>Notes</th><th></th></tr></thead><tbody>${notebook.map((n,i)=>`<tr><td>${esc(n.name)}<small>${esc(n.date)}</small>${provenance(n)}</td><td>${esc(n.config.kind)} · byte ${n.config.payload}<small>${n.config.bits} clocks/bit · ${esc(n.config.fault)}</small></td><td>${esc(n.outcome)}<small>Browser model</small></td><td>${esc(n.note)}</td><td><button data-replay="${i}">Replay</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="caption">No saved explorations yet. Start with byte 65, then compare the two deliberate faults.</p>';
    $('notebook').querySelectorAll('[data-replay]').forEach(b=>b.onclick=()=>{const c=notebook[Number(b.dataset.replay)].config;for(const [id,v] of Object.entries({preset:c.kind,payload:c.payload,bits:c.bits,fault:c.fault,code:c.code}))$(id).value=v;load();window.scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});});
  }
  function evidence(){
    $('evidence').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Experiment / source</th><th>Result</th><th>Measured evidence</th></tr></thead><tbody>${(window.CHIPWHEEL_EVIDENCE||[]).map(e=>`<tr><td>${esc(e.name)}<small><a href="../results/${encodeURIComponent(e.file)}" target="_blank" rel="noopener">${esc(e.file)}</a></small></td><td class="status ${e.status==='blocked'?'blocked':''}">${esc(e.status)}</td><td>${esc(e.detail)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  $('load').onclick=load;$('preset').onchange=()=>{preset();load();};$('bits').onchange=()=>{preset();load();};$('payload').onchange=load;$('fault').onchange=load;
  $('code').oninput=()=>{$('preset').value='custom';};
  $('step').onclick=()=>{stop();go(selected+1);};$('restart').onclick=()=>{stop();go(0);};$('cursor').oninput=()=>{stop();go(Number($('cursor').value));};
  $('play').onclick=()=>{if(timer){stop();return;}if(selected===result.trace.length-1)go(0);$('play').textContent='Pause';timer=setInterval(()=>{go(selected+1);if(selected===result.trace.length-1)stop();},Number($('speed').value));};$('speed').onchange=stop;
    $('save').onclick=()=>{notebook.push({name:$('run-name').value.trim()||`${config.kind} · byte ${config.payload}`,note:$('run-note').value.trim(),date:new Date().toISOString(),campaign:window.CHIPWHEEL_CAMPAIGN||null,design:window.CHIPWHEEL_DESIGN?{rtl_sha256:window.CHIPWHEEL_DESIGN.rtl_sha256,revision:window.CHIPWHEEL_DESIGN.revision,model_matches:window.CHIPWHEEL_DESIGN.model_matches}:null,config:{...config},outcome:verdict?verdict.reason:result.truncated?'Cycle limit reached':`Completed E${result.trace.length-1}${result.trace.at(-1).error?' with fault':''}`});const stored=persist();notes();if(stored)$('notice').textContent='Experiment saved in this browser.';};
  $('export').onclick=()=>{const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify({version:1,experiments:notebook},null,2)],{type:'application/json'}));a.href=url;a.download='chipwheel-notebook.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  $('import').onchange=async ev=>{try{const f=ev.target.files[0];if(!f)return;if(f.size>2000000)throw Error('Notebook exceeds 2 MB.');const n=JSON.parse(await f.text());if(n.version!==1||!Array.isArray(n.experiments)||n.experiments.length>500)throw Error('Invalid notebook format.');for(const e of n.experiments){if(typeof e.name!=='string'||typeof e.note!=='string'||typeof e.date!=='string'||typeof e.outcome!=='string'||!e.config||!['uart','pulse','custom'].includes(e.config.kind)||!['none','short-wait','msb-first'].includes(e.config.fault))throw Error('Invalid experiment record.');C.assemble(e.config.code);if(!Number.isInteger(e.config.payload)||e.config.payload<0||e.config.payload>255)throw Error('Invalid payload.');if(!Number.isInteger(e.config.bits)||e.config.bits<3||e.config.bits>4096)throw Error('Invalid bit duration.');}notebook.push(...n.experiments);persist();notes();$('notice').textContent=`Imported ${n.experiments.length} experiments.`;}catch(e){$('notice').textContent=e.message;}finally{ev.target.value='';}};
  window.addEventListener('chipwheel:evidence',e=>{window.CHIPWHEEL_EVIDENCE=e.detail;evidence();});
  $('refresh').onclick=()=>location.reload();new ResizeObserver(()=>render()).observe($('diagram'));preset();load();notes();evidence();
})();
