// Independent full-data oracle in tests only; production uses its persistent spatial indexes.
(async()=>{
  const api=window.KRReview,svg=document.getElementById('map'),results=[],snapshots=[];
  const check=(name,ok)=>{results.push({name,ok:!!ok});if(!ok)throw Error(name);};
  const tick=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const candidate=api.data.candidates.find(r=>r.substation_osm_id===window.__viewportStation);
  function projected(xy){
    const ends=candidate.bus_ids.map(id=>api.data.buses[id]),lon=(ends[0].longitude+ends[1].longitude)/2,lat=(ends[0].latitude+ends[1].latitude)/2;
    const metres=Math.PI/180*6371008.8;
    return [(xy[0]-lon)*metres*Math.cos(lat*Math.PI/180),(lat-xy[1])*metres];
  }
  function inView(point){const v=api.view;return point[0]>=v.x&&point[0]<=v.x+v.w&&point[1]>=v.y&&point[1]<=v.y+v.h;}
  // Cohen–Sutherland oracle, independent of the production slab intersection algorithm.
  function segmentVisible(a,b){
    const v=api.view,code=p=>(p[0]<v.x?1:p[0]>v.x+v.w?2:0)|(p[1]<v.y?4:p[1]>v.y+v.h?8:0);
    a=[...a];b=[...b];
    for(let i=0;i<10;i++){
      const ca=code(a),cb=code(b);if(!(ca|cb))return true;if(ca&cb)return false;
      const c=ca||cb;let x,y;
      if(c&8){y=v.y+v.h;x=a[0]+(b[0]-a[0])*(y-a[1])/(b[1]-a[1]);}
      else if(c&4){y=v.y;x=a[0]+(b[0]-a[0])*(y-a[1])/(b[1]-a[1]);}
      else if(c&2){x=v.x+v.w;y=a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]);}
      else {x=v.x;y=a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]);}
      if(c===ca)a=[x,y];else b=[x,y];
    }
    throw Error('oracle clipping did not converge');
  }
  const lineVisible=line=>line.coordinates.slice(1).some((xy,i)=>segmentVisible(projected(line.coordinates[i]),projected(xy)));
  function snapshot(stage){
    const expectedBuses=Object.values(api.data.buses).filter(b=>inView(projected([b.longitude,b.latitude]))).map(b=>b.bus_id);
    const expectedLines=api.data.lines.filter(lineVisible).map(l=>l.line_id);
    const actualBuses=new Set([...svg.querySelectorAll('[data-bus-id]')].map(el=>el.dataset.busId));
    const actualLines=new Set([...svg.querySelectorAll('[data-line-id]')].map(el=>el.dataset.lineId));
    const wanted=new Set([...expectedBuses,...candidate.bus_ids]);
    snapshots.push({stage,visible_buses:expectedBuses.length,rendered_buses:actualBuses.size,visible_lines:expectedLines.length,rendered_lines:actualLines.size});
    check(`${stage}: all viewport buses`,actualBuses.size===wanted.size&&[...wanted].every(id=>actualBuses.has(id)));
    check(`${stage}: all intersecting routes`,expectedLines.every(id=>actualLines.has(id)));
    check(`${stage}: candidate endpoint emphasis`,candidate.bus_ids.every(id=>{const node=[...svg.querySelectorAll('[data-bus-id]')].find(el=>el.dataset.busId===id);return node?.dataset.target==='true'&&node.getAttribute('stroke')==='white';}));
    check(`${stage}: component colors`,[...svg.querySelectorAll('[data-line-id]')].every(node=>{
      const line=api.data.lines.find(l=>l.line_id===node.dataset.lineId),side=candidate.component_ids.indexOf(api.data.bus_components[line.bus1_id]);
      return node.getAttribute('stroke')===(side===0?'#58dded':side===1?'#ffb45d':'#41596e')&&node.parentNode.dataset.layer===(side<0?'context':'components');
    }));
    return {buses:expectedBuses,lines:expectedLines};
  }
  try{
    check('station candidate found',!!candidate);api.select(candidate.review_key);await tick();
    document.getElementById('notes').value='viewport navigation preserves this review';document.getElementById('notes').dispatchEvent(new Event('input',{bubbles:true}));
    const initial=snapshot('initial'),targetNode=svg.querySelector('[data-target=true]'),stationNode=svg.querySelector('[data-layer=station]');
    const retainedLines=new Map([...svg.querySelectorAll('[data-line-id]')].map(el=>[el.dataset.lineId,el]));
    if(window.__viewportStation==='way/887782992')check('Gogang initial two buses',initial.buses.length===2);
    else check('large station has eight components',candidate.station_component_count===8);
    for(let i=0;i<9;i++)document.getElementById('zoom-out').click();await tick();
    const expanded=snapshot('zoom out nine times');
    check('zoom out reveals new buses',expanded.buses.some(id=>!initial.buses.includes(id)));
    check('zoom out reveals new routes',expanded.lines.some(id=>!initial.lines.includes(id)));
    check('existing SVG nodes retained',targetNode===svg.querySelector('[data-target=true]')&&stationNode===svg.querySelector('[data-layer=station]')&&[...retainedLines].every(([id,node])=>node===([...svg.querySelectorAll('[data-line-id]')].find(el=>el.dataset.lineId===id))));
    let globalBusScans=0,globalLineScans=0;
    const originalValues=Object.values,originalIterator=api.data.lines[Symbol.iterator];
    Object.values=function(value){if(value===api.data.buses)globalBusScans++;return originalValues(value);};
    api.data.lines[Symbol.iterator]=function(){globalLineScans++;return originalIterator.call(this);};
    // Synchronous input bursts are coalesced to one equipment update in the next animation frame.
    let radiusWrites=0;
    const observer=new MutationObserver(records=>{radiusWrites+=records.filter(r=>r.target===targetNode&&r.attributeName==='r').length;});
    observer.observe(svg,{attributes:true,subtree:true});
    for(let i=0;i<12;i++){document.getElementById('zoom-in').click();document.getElementById('zoom-out').click();}
    await tick();observer.disconnect();Object.values=originalValues;api.data.lines[Symbol.iterator]=originalIterator;
    check('input burst uses one equipment frame',radiusWrites===1);
    check('no national geometry scans during zoom',globalBusScans===0&&globalLineScans===0);
    snapshot('batched zoom');
    const beforeDrag={buses:new Set(expanded.buses),lines:new Set(expanded.lines)},rect=svg.getBoundingClientRect(),v=api.view;
    const nearest=Object.values(api.data.buses).map(b=>projected([b.longitude,b.latitude])).filter(p=>!inView(p))
      .sort((a,b)=>((a[0]-v.x-v.w/2)/v.w)**2+((a[1]-v.y-v.h/2)/v.h)**2-((b[0]-v.x-v.w/2)/v.w)**2-((b[1]-v.y-v.h/2)/v.h)**2)[0];
    const dx=-Math.max(-.65,Math.min(.65,(nearest[0]-v.x-v.w/2)/v.w))*rect.width;
    const dy=-Math.max(-.55,Math.min(.55,(nearest[1]-v.y-v.h/2)/v.h))*rect.height;
    window.__reviewInputSent=false;window.__reviewInput={name:'drag',x:rect.x+rect.width*(dx>0?.15:.85),y:rect.y+rect.height*(dy>0?.2:.8),dx,dy};
    while(!window.__reviewInputSent)await tick();window.__reviewInput=null;await tick();
    const moved=snapshot('trusted drag');
    check('drag reveals new equipment',moved.buses.some(id=>!beforeDrag.buses.has(id))||moved.lines.some(id=>!beforeDrag.lines.has(id)));
    check('drag culls equipment outside viewport',expanded.buses.filter(id=>!moved.buses.includes(id)&&!candidate.bus_ids.includes(id)).every(id=>![...svg.querySelectorAll('[data-bus-id]')].some(el=>el.dataset.busId===id)));
    for(let i=0;i<5;i++)document.getElementById('zoom-in').click();await tick();snapshot('zoom back in');
    check('zoom in culls offscreen routes',svg.querySelectorAll('[data-line-id]').length===api.data.lines.filter(lineVisible).length);
    document.getElementById('fit').click();await tick();snapshot('fit reset');
    check('candidate persists across navigation',api.selected.review_key===candidate.review_key);
    // Switch candidates before a scheduled frame runs: old projected nodes must not leak.
    document.getElementById('zoom-out').click();const other=api.data.candidates.find(r=>r.substation_osm_id!==candidate.substation_osm_id);api.select(other.review_key);api.select(candidate.review_key);await tick();snapshot('selection cancels pending frame');
    check('review survives viewport and candidate changes',api.exportDocument().decisions.find(d=>d.review_key===candidate.review_key)?.notes==='viewport navigation preserves this review');
    window.__viewportReport={ok:true,results,snapshots,station:candidate.substation_name};
  }catch(e){window.__viewportReport={ok:false,results,snapshots,error:String(e),stack:e.stack};}
})();
