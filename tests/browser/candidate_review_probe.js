(async()=>{
  const api=window.KRReview,results=[];
  const check=(name,ok)=>{results.push({name,ok:!!ok});if(!ok)throw Error(name);};
  const tick=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
  const change=(id,value)=>{const el=document.getElementById(id);el.value=value;el.dispatchEvent(new Event(id==='search'?'input':'change',{bubbles:true}));};
  try{
    check('384 actual candidates / 367 pairs',api.data.candidates.length===384&&api.data.summary.distinct_component_pairs===367);
    check('165 station filters',document.getElementById('station-filter').options.length===166);
    const first=api.data.candidates[0],second=api.data.candidates.find(r=>r.review_key!==first.review_key);
    document.querySelectorAll('.candidate')[1].click();await tick();check('candidate selection',api.selected.review_key===second.review_key);
    check('original route vertices',!!document.querySelector('path[data-line-id]')&&document.querySelectorAll('[data-bus-id]').length>=2);
    const route=document.querySelector('path[data-line-id]'),rawLine=api.data.lines.find(l=>l.line_id===route.dataset.lineId);
    check('all original route vertices retained',(route.getAttribute('d').match(/[ML]/g)||[]).length===rawLine.coordinates.length);
    document.querySelector('path[data-line-id]').dispatchEvent(new MouseEvent('click',{bubbles:true}));
    check('line OSM details',document.getElementById('object-detail').textContent.includes('line_id'));
    change('station-filter',first.substation_osm_id);check('station filter',api.visible.every(r=>r.substation_osm_id===first.substation_osm_id));
    change('voltage-filter',String(first.voltage_kv));check('voltage filter',api.visible.every(r=>r.voltage_kv===first.voltage_kv));
    change('station-filter','');change('voltage-filter','');change('search',String(first.component_ids[0]));
    check('component search',api.visible.some(r=>r.review_key===first.review_key));change('search','impossible-nonexistent-id');check('empty search',api.visible.length===0);change('search','');
    change('sort','distance');check('distance sort',api.visible.every((r,i)=>i===0||r.distance_m>=api.visible[i-1].distance_m));
    change('sort','priority');check('priority sort',api.visible.every((r,i)=>i===0||r.priority_score<=api.visible[i-1].priority_score));
    api.select(first.review_key);await tick();
    const initial=document.getElementById('map').getAttribute('viewBox'),marker=document.querySelector('circle[data-target=true]'),initialRadius=Number(marker.getAttribute('r'));
    document.getElementById('zoom-in').click();await tick();check('zoom button',initial!==document.getElementById('map').getAttribute('viewBox'));
    check('equipment markers keep screen size on zoom',Math.abs(Number(marker.getAttribute('r'))/initialRadius-.75)<1e-8);document.getElementById('fit').click();
    const rect=document.getElementById('map').getBoundingClientRect();
    window.__reviewInput={name:'wheel',x:rect.x+rect.width/2,y:rect.y+rect.height/2};
    while(!window.__reviewInputSent)await tick();window.__reviewInputSent=false;await tick();
    const zoomed=document.getElementById('map').getAttribute('viewBox');check('trusted wheel zoom',initial!==zoomed);
    window.__reviewInput={name:'drag',x:rect.x+rect.width*.8,y:rect.y+rect.height*.8};
    while(!window.__reviewInputSent)await tick();window.__reviewInput=null;await tick();
    check('trusted pointer drag',document.getElementById('map').getAttribute('viewBox')!==zoomed);
    document.getElementById('fit').click();
    change('decision-status','electrical_evidence_obtained');
    const attack='</script><img src=x onerror="window.PWNED=1">';
    change('notes',attack);document.getElementById('notes').dispatchEvent(new Event('input',{bubbles:true}));
    change('sources','docs/SLD-001.pdf');document.getElementById('sources').dispatchEvent(new Event('input',{bubbles:true}));
    const doc=api.exportDocument();check('JSON export status, notes, timestamp',doc.decisions.length===1&&doc.decisions[0].notes===attack&&doc.decisions[0].status==='electrical_evidence_obtained'&&!!doc.decisions[0].reviewed_at);
    window.__reviewExport=doc;
    // Real download through the UI, followed by a file-input import driven by CDP.
    document.getElementById('export').click();window.__reviewFileStage='download';
    while(window.__reviewFileStage!=='uploaded')await tick();
    while(!document.getElementById('message').textContent.includes('복원'))await tick();
    check('file upload restores record',document.getElementById('notes').value===attack&&!window.PWNED);
    change('status-filter','electrical_evidence_obtained');check('status filter',api.visible.length===1);change('status-filter','');
    const conflict=JSON.parse(JSON.stringify(api.exportDocument()));conflict.decisions[0].notes='conflicting';let rejected=false;try{api.importDocument(conflict);}catch(e){rejected=true;}
    check('same version conflicting import is atomic',rejected&&api.exportDocument().decisions[0].notes===attack);
    const old=JSON.parse(JSON.stringify(doc));old.input_hash='b'.repeat(64);old.decisions[0].input_hash=old.input_hash;old.decisions[0].review_key=`kr-review-v1:${old.input_hash}:${old.decisions[0].identity_key}`;
    api.importDocument(old);const roundtrip=api.exportDocument();
    check('foreign version quarantined',roundtrip.stale_records.length===1&&roundtrip.stale_records[0].re_review_required&&roundtrip.decisions.length===1);
    check('stale records visible',document.getElementById('stale-title').textContent.includes('1건'));
    change('status-filter','stale');check('stale filter',api.visible.some(r=>r.review_key===first.review_key));change('status-filter','');
    const duplicate=JSON.parse(JSON.stringify(doc));duplicate.decisions.push({...duplicate.decisions[0]});rejected=false;try{api.importDocument(duplicate);}catch(e){rejected=true;}check('duplicate keys rejected',rejected);
    const tampered=JSON.parse(JSON.stringify(doc));tampered.decisions[0].bus_ids=['unknown','tampered'];rejected=false;try{api.importDocument(tampered);}catch(e){rejected=true;}check('candidate identity tampering rejected',rejected);
    const invalid=JSON.parse(JSON.stringify(doc));invalid.schema_version='unsupported';rejected=false;try{api.importDocument(invalid);}catch(e){rejected=true;}check('unknown schema rejected',rejected);
    check('readiness and raw graph unchanged',api.data.summary.ac_pf_readiness==='NOT_READY'&&api.data.lines.length===3984&&!window.PWNED&&!document.querySelector('img'));
    window.__reviewReport={results,ok:true};
  }catch(e){window.__reviewReport={results,ok:false,error:String(e),stack:e.stack};}
})();
