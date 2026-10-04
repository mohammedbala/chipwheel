(() => {
  const $=id=>document.getElementById(id), esc=x=>String(x??'unknown').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let design=null,edge=null,stage='physical',changes=[],busy=false,lastSeen=null;
  try{const saved=JSON.parse(localStorage.getItem('chipwheel-live-v1')||'null');if(saved){lastSeen=saved.files;changes=Array.isArray(saved.changes)?saved.changes.slice(0,12):[];}}catch{}
  function draw(){
    if(!design)return;const a=design.architecture,host=$('design-map'),w=Math.max(240,host.clientWidth),small=w<720;
    const nodes=[['loader','Host → loader','WRITE · address · byte select'],['memory','Instruction memory',`${a.words??'?'} words × ${a.word_bits??'?'} bits`],['engine','Clocked sequencer',`${a.pc_bits??'?'}-bit PC · ${a.wait_bits??'?'}-bit WAIT`],['pin','Programmable pin','OUT / SHIFT → pin_value'],['checker','Independent checker','Expected timing vs RTL pin']];
    const gap=small?18:14,nw=small?w-12:(w-gap*4)/5,nh=small?64:100,h=small?nodes.length*(nh+gap)-gap:148;
    const s=edge?.state,active=design.model_matches&&s?.busy?(s.executed===null?'engine':s.action.startsWith('SHIFT')||s.action.startsWith('OUT')?'pin':'memory'):null;
    let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Functional path from host loader to instruction memory, sequencer, programmable pin and independent checker">`;
    nodes.forEach(([id,title,sub],i)=>{const x=small?6:i*(nw+gap),y=small?i*(nh+gap):15;
      if(i){const px=small?w/2:x-gap,py=small?y-gap:y+nh/2;svg+=`<path class="connection" d="${small?`M${px} ${py}V${y}`:`M${px} ${py}H${x}`}"/>`;}
      const values={loader:'External pin writes',memory:s?`Next address ${s.pc}`:'Program stored in registers',engine:s?`Wait ${s.wait} · loop ${s.count}`:'One instruction / enabled edge',pin:s?`PIN ${s.pin} · BUSY ${s.busy}`:'Idle high',checker:'Public signals only'};
      svg+=`<rect x="${x}" y="${y}" width="${nw}" height="${nh}" class="${active===id?'energized':''}"/><text x="${x+10}" y="${y+20}">${esc(title)}</text><text class="sub" x="${x+10}" y="${y+39}">${esc(sub)}</text><text class="sub" x="${x+10}" y="${y+(small?56:76)}">${esc(design.model_matches?values[id]:(id==='memory'||id==='engine'?sub:'Schematic requires review'))}</text>`;
    });
    svg+='</svg>';host.innerHTML=svg;
    $('design-motion').textContent=edge&&design.model_matches?`Browser experiment · ${edge.config.kind} · E${s.edge}: ${s.action} · pin ${s.pin}. The checker block represents our separate RTL verification.`:'Functional design schematic. Play or step an experiment below to follow its state.';
  }
  function detail(){const s=design?.stages.find(s=>s.id===stage);if(!s)return;$('stage-detail').innerHTML=`<strong>${esc(s.title)}</strong> · ${esc(s.detail)}${s.file?` · <a href="../results/${encodeURIComponent(s.file)}" target="_blank" rel="noopener">Evidence</a>`:''}${!s.current&&s.status!=='pending'?' · Historical: source or configuration differs.':''}`;}
  function render(){
    const a=design.architecture;
    $('design-facts').innerHTML=`<span><strong>${esc(design.module)}</strong></span><span>${a.words??'?'} × ${a.word_bits??'?'} instruction memory</span><span>${design.opcode_count} opcode cases</span><span>${esc(design.clock_period_ns)} ns clock target</span><span>${esc(design.tiles)} tile allocation</span>`;
    $('design-stages').innerHTML=design.stages.map((s,i)=>`<button data-stage="${s.id}" class="stage ${s.current?s.status:'historical'}" aria-pressed="${s.id===stage}"><span class="stage-number">${i+1}</span><span>${esc(s.title)}<small>${s.status==='pending'?'Not run':!s.current?'Historical · '+esc(s.status):esc(s.status)}</small></span></button>`).join('');
    $('design-stages').querySelectorAll('button').forEach(b=>b.onclick=()=>{stage=b.dataset.stage;render();});
    $('model-warning').hidden=design.model_matches;
    $('model-warning').textContent='RTL has changed. The simulator below still models the verified v1 design. Its waveforms are illustrative until the model is updated and checked against the new RTL.';
    $('design-hash').textContent=`RTL ${design.rtl_sha256?.slice(0,16)??'missing'} · config ${design.config_sha256?.slice(0,16)??'missing'} · routing ${design.routing_status} · slack ${design.timing_slack_ns===null?'unverified':design.timing_slack_ns+' ns'}`;
    $('design-changes').innerHTML=changes.length?changes.map(c=>`<li>${esc(c.time)} · ${esc(c.description)}</li>`).join(''):'<li>Watching the current design. Future source and result changes appear here.</li>';
    detail();draw();
  }
  async function poll(){
    if(busy)return;busy=true;
    try{
      const response=await fetch('live.json',{cache:'no-store'});if(!response.ok)throw Error('Unavailable');const data=await response.json(),next=data.design;
      if(!next?.revision||!Array.isArray(next.stages))throw Error('Invalid project snapshot');
      if(design?.revision!==next.revision){
        const previous=design?.files||lastSeen;
        if(previous){const altered=Object.keys(next.files).filter(k=>previous[k]!==next.files[k]);const resultChange=design&&JSON.stringify(design.stages)!==JSON.stringify(next.stages);
          if(altered.length||resultChange)changes.unshift({time:new Date().toLocaleString(),description:altered.length?'Changed: '+altered.join(', '):'Verification results updated'});
        }
        changes=changes.slice(0,12);design=next;window.CHIPWHEEL_DESIGN=next;lastSeen=next.files;
        try{localStorage.setItem('chipwheel-live-v1',JSON.stringify({files:next.files,changes}));}catch{}
        render();window.dispatchEvent(new CustomEvent('chipwheel:evidence',{detail:data.evidence}));
      }
      $('live-status').textContent='Live · checked '+new Date().toLocaleTimeString();$('live-status').classList.remove('offline');
    }catch{$('live-status').textContent=location.protocol==='file:'?'Live view needs the local server':'Disconnected · showing last received design';$('live-status').classList.add('offline');}
    finally{busy=false;}
  }
  window.addEventListener('chipwheel:edge',e=>{edge=e.detail;draw();});new ResizeObserver(draw).observe($('design-map'));poll();setInterval(poll,3000);
})();
