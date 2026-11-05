'use strict';
const DATA=JSON.parse(document.getElementById('review-data').textContent);
const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
const rows=DATA.candidates, byKey=new Map(rows.map(r=>[r.review_key,r]));
const decisions=new Map(); let staleRecords=[], selected=null, visible=[], view=null, projection=null, drag=null;
let mapScene=null, mapFrame=0;
// Geographic bounding-volume trees are built once. View changes query these trees rather
// than walking national geometry or rebuilding SVG paths for equipment already on screen.
function overlaps(a,b){return a.minX<=b.maxX&&a.maxX>=b.minX&&a.minY<=b.maxY&&a.maxY>=b.minY;}
function spatialIndex(items){
  const bounds={minX:Infinity,minY:Infinity,maxX:-Infinity,maxY:-Infinity};
  for(const item of items){bounds.minX=Math.min(bounds.minX,item.minX);bounds.minY=Math.min(bounds.minY,item.minY);bounds.maxX=Math.max(bounds.maxX,item.maxX);bounds.maxY=Math.max(bounds.maxY,item.maxY);}
  if(items.length<=16)return {...bounds,items};
  const axis=bounds.maxX-bounds.minX>=bounds.maxY-bounds.minY?'X':'Y';
  items.sort((a,b)=>(a['min'+axis]+a['max'+axis])-(b['min'+axis]+b['max'+axis]));
  const middle=items.length>>1;
  return {...bounds,left:spatialIndex(items.slice(0,middle)),right:spatialIndex(items.slice(middle))};
}
function queryIndex(node,bounds,out=[]){
  if(!overlaps(node,bounds))return out;
  if(node.items){for(const item of node.items)if(overlaps(item,bounds))out.push(item);}
  else {queryIndex(node.left,bounds,out);queryIndex(node.right,bounds,out);}
  return out;
}
const busIndex=spatialIndex(Object.values(DATA.buses).map(bus=>({id:bus.bus_id,value:bus,minX:bus.longitude,maxX:bus.longitude,minY:bus.latitude,maxY:bus.latitude})));
const lineIndex=spatialIndex(DATA.lines.map(line=>{
  const item={id:line.line_id,value:line,minX:Infinity,minY:Infinity,maxX:-Infinity,maxY:-Infinity};
  for(const [lon,lat] of line.coordinates){item.minX=Math.min(item.minX,lon);item.maxX=Math.max(item.maxX,lon);item.minY=Math.min(item.minY,lat);item.maxY=Math.max(item.maxY,lat);}
  return item;
}));
function segmentIntersects(a,b,bounds){
  let enter=0,leave=1;
  for(const [axis,min,max] of [[0,bounds.minX,bounds.maxX],[1,bounds.minY,bounds.maxY]]){
    const delta=b[axis]-a[axis];
    if(delta===0){if(a[axis]<min||a[axis]>max)return false;continue;}
    let near=(min-a[axis])/delta,far=(max-a[axis])/delta;
    if(near>far)[near,far]=[far,near];
    enter=Math.max(enter,near);leave=Math.min(leave,far);
    if(enter>leave)return false;
  }
  return true;
}
const schema='kr-candidate-review-1', hashPattern=/^[a-f0-9]{64}$/;
const identityFields=['review_key','identity_key','input_hash','substation_osm_id','component_ids','bus_ids'];
const stable=v=>JSON.stringify(v && typeof v==='object' ? (Array.isArray(v)?v.map(x=>JSON.parse(stable(x))):Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(stable(v[k]))]))) : v);
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error);}
function text(tag,content,parent){const el=document.createElement(tag);el.textContent=content;if(parent)parent.append(el);return el;}
function option(select,value,label){const el=text('option',label,select);el.value=value;}
function identity(r){return Object.fromEntries(identityFields.map(k=>[k,r[k]]));}
function keyFor(h,i){return `kr-review-v1:${h}:${i}`;}
function currentState(r){return decisions.get(r.review_key)?.status || 'unreviewed';}
function staleFor(r){return staleRecords.filter(d=>d.identity_key===r.identity_key || (d.substation_osm_id===r.substation_osm_id && stable([...d.bus_ids].sort())===stable([...r.bus_ids].sort())));}
function validateDocument(doc){
  if(!doc || doc.schema_version!==schema || !hashPattern.test(doc.input_hash) || !Array.isArray(doc.decisions) || !Array.isArray(doc.stale_records))throw Error('지원하지 않는 스키마 또는 입력 버전/기록 목록 오류');
  const seen=new Set(), active=[], stale=[];
  for(const [quarantine,records] of [[false,doc.decisions],[true,doc.stale_records]])for(const d of records){
    if(!d || typeof d!=='object' || !hashPattern.test(d.input_hash) || !hashPattern.test(d.identity_key) || d.review_key!==keyFor(d.input_hash,d.identity_key))throw Error('검토 키/입력 해시 오류');
    if(seen.has(d.review_key))throw Error('중복 검토 키');seen.add(d.review_key);
    if(!quarantine && d.input_hash!==doc.input_hash)throw Error('문서와 기록의 입력 버전 불일치');
    if(!Object.hasOwn(DATA.states,d.status) || d.re_review_required!==quarantine || typeof d.substation_osm_id!=='string' || !d.substation_osm_id || !Array.isArray(d.component_ids) || d.component_ids.length!==2 || !d.component_ids.every(v=>Number.isInteger(v)&&v>=0) || !Array.isArray(d.bus_ids) || d.bus_ids.length!==2 || !d.bus_ids.every(v=>typeof v==='string'&&v))throw Error('검토 상태/식별자 오류');
    for(const f of ['notes','evidence_sources','reviewed_at'])if(typeof d[f]!=='string'||d[f].length>20000)throw Error('메모/출처/시각 오류');
    if(!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(d.reviewed_at)||!Number.isFinite(Date.parse(d.reviewed_at)))throw Error('시간대가 있는 검토 시각이 필요합니다.');
    const known=byKey.get(d.review_key);
    if(known && stable(identity(known))!==stable(identity(d)))throw Error('검토 키의 후보 식별자가 다릅니다.');
    if(!quarantine && d.input_hash===DATA.summary.input_hash){if(!known)throw Error('이 입력에 존재하지 않는 검토 키');active.push({...d});}
    else stale.push({...d,re_review_required:true});
  }
  return {decisions:active,stale_records:stale};
}
function importDocument(doc){
  const parsed=validateDocument(doc); // All validation/merge checks finish before any state changes.
  for(const d of parsed.decisions){const old=decisions.get(d.review_key);if(old && stable(old)!==stable(d))throw Error('현재 기록과 충돌합니다. 현재 기록을 내보내고 새로 연 화면에서 불러오세요.');}
  const quarantine=new Map(staleRecords.map(d=>[d.review_key,d]));
  for(const d of parsed.stale_records){const old=quarantine.get(d.review_key);if(old&&stable(old)!==stable(d))throw Error('이전 버전 보관 기록과 충돌합니다.');quarantine.set(d.review_key,d);}
  for(const d of parsed.decisions)decisions.set(d.review_key,d);
  staleRecords=[...quarantine.values()].sort((a,b)=>a.review_key.localeCompare(b.review_key));
  renderList(); if(selected)showDecision();
  message(`현재 버전 기록 ${parsed.decisions.length}건 복원 / 이전 버전 기록 ${staleRecords.length}건 보관 · 이전 기록은 적용되지 않으며 재검토가 필요합니다.`);
  return parsed;
}
function exportDocument(){
  const now=new Date().toISOString();
  const exported=[...decisions.values()].map(d=>({...d,reviewed_at:d.reviewed_at||now})).sort((a,b)=>a.review_key.localeCompare(b.review_key));
  const doc={schema_version:schema,input_hash:DATA.summary.input_hash,decisions:exported,stale_records:staleRecords};
  validateDocument(doc);return doc;
}
$('export').onclick=()=>{
  try{const doc=exportDocument(),blob=new Blob([JSON.stringify(doc,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download='review_decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    for(const d of doc.decisions)decisions.set(d.review_key,d);
    message(`검토 기록 ${doc.decisions.length}건 / 재검토 보관 ${doc.stale_records.length}건을 내보냈습니다. 다운로드 파일을 보관하세요.`);
  }catch(e){message(e.message,true);}
};
$('import').onchange=async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>20*1024*1024)throw Error('20 MB 이하 JSON만 지원합니다.');importDocument(JSON.parse(await file.text()));}catch(e){message(`불러오기 실패: ${e.message}`,true);}finally{event.target.value='';}};
function draft(){
  if(!selected)return;
  decisions.set(selected.review_key,{...identity(selected),status:$('decision-status').value,notes:$('notes').value,evidence_sources:$('sources').value,reviewed_at:null,re_review_required:false});
  $('draft-note').textContent='임시 기록됨 · 내보내기 전까지 이 화면에만 보관됩니다.';renderList();
}
for(const id of ['decision-status','notes','sources'])$(id).addEventListener(id==='decision-status'?'change':'input',draft);
function showDecision(){
  const d=decisions.get(selected.review_key);
  $('decision-status').value=d?.status||'unreviewed';$('notes').value=d?.notes||'';$('sources').value=d?.evidence_sources||'';
  $('draft-note').textContent=d ? (d.reviewed_at?`기록 시각: ${d.reviewed_at}`:'임시 기록됨 · JSON으로 내보내세요.'):'검토 기록 없음 · 기본 상태 미검토';
  const old=staleFor(selected);$('stale-title').textContent=`이전 입력 기록 ${old.length}건 · 전체 보관 ${staleRecords.length}건 · 재검토 필요`;
  $('stale-detail').textContent=JSON.stringify({this_candidate:old,all_quarantined:staleRecords},null,2);
}
function renderList(){
  const station=$('station-filter').value,voltage=$('voltage-filter').value,status=$('status-filter').value,query=$('search').value.toLocaleLowerCase().trim();
  visible=rows.filter(r=>(!station||r.substation_osm_id===station)&&(!voltage||String(r.voltage_kv)===voltage)&&(!status||(status==='stale'?staleFor(r).length:currentState(r)===status))&&(!query||[r.substation_name,r.substation_osm_id,...r.component_ids,...r.bus_ids,r.candidate_id].join(' ').toLocaleLowerCase().includes(query)));
  const sort=$('sort').value;
  visible.sort(sort==='distance'?((a,b)=>a.distance_m-b.distance_m||a.pair_rank-b.pair_rank):sort==='priority'?((a,b)=>b.priority_score-a.priority_score||a.distance_m-b.distance_m):((a,b)=>a.pair_rank-b.pair_rank||b.priority_score-a.priority_score||a.distance_m-b.distance_m));
  $('list-count').textContent=`${visible.length}건 / ${new Set(visible.map(r=>r.component_ids.join(':'))).size}쌍`;
  $('candidates').replaceChildren();
  for(const r of visible){
    const el=text('button','',$('candidates'));el.className='candidate'+(selected?.review_key===r.review_key?' active':'');el.dataset.key=r.review_key;
    el.setAttribute('aria-pressed',String(selected?.review_key===r.review_key));
    text('span',`쌍 순위 #${r.pair_rank} · ${r.priority_band} ${r.priority_score}점 · ${r.distance_m.toFixed(2)} m`,el);
    text('strong',r.substation_name,el);text('span',`${r.substation_osm_id} · ${r.voltage_kv} kV · 성분 ${r.component_ids.join(' ↔ ')}`,el);
    const note=text('span',`${DATA.states[currentState(r)]} · 동일 쌍 근거 ${r.pair_evidence_count}곳${staleFor(r).length?' · 재검토 필요':''}`,el);note.className='muted';
    text('span',r.bus_ids.join(' ↔ '),el).className='muted';el.onclick=()=>select(r.review_key);
  }
  if(!visible.length)text('p','일치하는 후보가 없습니다. 필터를 조정하세요.',$('candidates'));
}
function fact(label,value){text('dt',label,$('facts'));text('dd',typeof value==='string'?value:JSON.stringify(value),$('facts'));}
function select(key){
  selected=byKey.get(key);const r=selected; if(!r)return;
  $('selection-title').textContent=`${r.substation_name} · ${r.voltage_kv} kV · 성분 ${r.component_ids.join(' / ')}`;
  $('facts').replaceChildren();
  for(const [k,v] of [['변전소 OSM',r.substation_osm_id],['성분 / 쌍 순위',`${r.component_ids.join(' ↔ ')} / #${r.pair_rank}`],['모선',r.bus_ids.join('\n')],['직선 대권거리',`${r.distance_m.toFixed(2)} m (모선 ↔ 모선)`],['원본 위경도',r.bus_ids.map(id=>`${DATA.buses[id].latitude}, ${DATA.buses[id].longitude}`).join('\n')],['차수 / 포함 성분',`${r.endpoint_degrees.join(' / ')} / ${r.station_component_count}개`],['전압 태그',r.voltage_tags],['점수 내역',JSON.stringify(r.score_parts)],['검토 경고',r.warnings.join(', ')||'관측된 경고 없음 (접속 확인 아님)'],['동일 쌍 변전소',r.pair_substations.join(', ')],['인접 모선 중복',String(r.nearby_bus_overlap)],['OSM 근거',r.evidence]])fact(k,v);
  const station=DATA.stations[r.substation_osm_id];
  $('geometry-note').textContent=station.polygon_available?`실제 OSM 경계 · 후보 포함 여부 ${r.a_inside_polygon} / ${r.b_inside_polygon} · 관련 OSM 설비 ${station.related_objects.length}개. 객체가 없다는 것이 실제 설비 부재를 뜻하지 않습니다.`:'OSM 변전소 폴리곤 지리 정보가 없습니다. 임의 경계는 그리지 않습니다.';
  $('required').textContent=r.required_information+' · 회선·인출구 대응, 전압/형상 태그의 원본 OSM 버전, 운전 시점 및 개폐 상태 확인이 필요합니다.';
  $('object-detail').textContent=JSON.stringify(station,null,2);showDecision();renderList();drawMap();
}
function svgEl(tag,attrs,parent=$('map')){const el=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))el.setAttribute(k,String(v));if(parent)parent.append(el);return el;}
function inspect(el,obj){el.classList.add('equipment');el.onclick=()=>{if(drag?.moved)return;$('object-detail').textContent=JSON.stringify(obj,null,2);};}
function project(xy){return [(xy[0]-projection.lon)*projection.x,(projection.lat-xy[1])*projection.y];}
function polygonRings(geometry){return geometry?.type==='Polygon'?geometry.coordinates:geometry?.type==='MultiPolygon'?geometry.coordinates.flat():[];}
function routePath(coords){return coords.map((xy,i)=>`${i?'L':'M'}${project(xy).join(',')}`).join(' ');}
function reconcileEquipment(items,active,cache,create,parent){
  const wanted=new Set(items.map(item=>item.id));
  for(const [id,node] of active)if(!wanted.has(id)){node.remove();active.delete(id);}
  for(const item of items)if(!active.has(item.id)){
    let node=cache.get(item.id);
    if(!node){node=create(item.value);cache.set(item.id,node);}
    (typeof parent==='function'?parent(item.value):parent).append(node);active.set(item.id,node);
  }
}
function updateEquipment(){
  if(!mapScene||!view)return;
  const bounds={minX:projection.lon+view.x/projection.x,maxX:projection.lon+(view.x+view.w)/projection.x,
    minY:projection.lat-(view.y+view.h)/projection.y,maxY:projection.lat-view.y/projection.y};
  const buses=queryIndex(busIndex,bounds),ids=new Set(buses.map(item=>item.id));
  // Candidate endpoints remain identifiable even when a user pans away from the station.
  for(const id of selected.bus_ids)if(!ids.has(id))buses.push({id,value:DATA.buses[id]});
  const lines=queryIndex(lineIndex,bounds).filter(item=>{
    const coords=item.value.coordinates;
    for(let i=1;i<coords.length;i++)if(segmentIntersects(coords[i-1],coords[i],bounds))return true;
    return false;
  });
  const sideOf=busId=>selected.component_ids.indexOf(DATA.bus_components[busId]);
  const color=side=>side===0?'#58dded':side===1?'#ffb45d':'#698196';
  reconcileEquipment(lines,mapScene.lines,mapScene.lineCache,line=>{
    const side=sideOf(line.bus1_id);
    const node=svgEl('path',{d:routePath(line.coordinates),fill:'none',stroke:side<0?'#41596e':color(side),'stroke-width':side<0?1:2.5,'data-line-id':line.line_id},null);
    inspect(node,{...line,component_id:DATA.bus_components[line.bus1_id]});return node;
  },line=>sideOf(line.bus1_id)<0?mapScene.background:mapScene.colored);
  reconcileEquipment(buses,mapScene.buses,mapScene.busCache,bus=>{
    const point=project([bus.longitude,bus.latitude]),target=selected.bus_ids.includes(bus.bus_id);
    const node=svgEl('circle',{cx:point[0],cy:point[1],fill:color(sideOf(bus.bus_id)),stroke:target?'white':'#101e2b','stroke-width':target?2:1,'data-bus-id':bus.bus_id,'data-target':target},null);
    inspect(node,{...bus,component_id:DATA.bus_components[bus.bus_id]});return node;
  },mapScene.markers);
  for(const marker of mapScene.buses.values())marker.setAttribute('r',view.h*(marker.dataset.target==='true'?.013:.006));
}
function setView(immediate=false){
  if(!view)return;
  $('map').setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);
  const label=$('map').querySelector('[data-layer="distance-label"]');
  if(label){label.setAttribute('font-size',view.h*.033);label.setAttribute('y',Number(label.dataset.anchorY)-view.h*.025);}
  if(immediate){cancelAnimationFrame(mapFrame);mapFrame=0;updateEquipment();}
  else if(!mapFrame)mapFrame=requestAnimationFrame(()=>{mapFrame=0;updateEquipment();});
}
function drawMap(){
  cancelAnimationFrame(mapFrame);mapFrame=0;
  const r=selected,station=DATA.stations[r.substation_osm_id],buses=r.bus_ids.map(id=>DATA.buses[id]);
  projection={lon:(buses[0].longitude+buses[1].longitude)/2,lat:(buses[0].latitude+buses[1].latitude)/2,y:Math.PI/180*6371008.8};projection.x=projection.y*Math.cos(projection.lat*Math.PI/180);
  const rings=station.polygon_available?polygonRings(station.geometry):[];
  const focus=[...buses.map(b=>[b.longitude,b.latitude]),...rings.flat()].map(project),xs=focus.map(p=>p[0]),ys=focus.map(p=>p[1]);
  const center=[(Math.min(...xs)+Math.max(...xs))/2,(Math.min(...ys)+Math.max(...ys))/2],aspect=$('map').clientWidth/$('map').clientHeight;
  const h=Math.max(160,(Math.max(...ys)-Math.min(...ys))*1.5,(Math.max(...xs)-Math.min(...xs))*1.5/aspect),w=h*aspect;
  view={x:center[0]-w/2,y:center[1]-h/2,w,h};$('map').replaceChildren();
  const background=svgEl('g',{'data-layer':'context'}),colored=svgEl('g',{'data-layer':'components'});
  if(rings.length){const el=svgEl('path',{d:rings.map(ring=>routePath(ring)+' Z').join(' '),fill:'#aa88ef18',stroke:'#b99af7','stroke-width':2,'fill-rule':'evenodd','data-layer':'station'});inspect(el,station);}
  // A measured-distance annotation only. No new electrical edge exists in the payload.
  const pa=project([buses[0].longitude,buses[0].latitude]),pb=project([buses[1].longitude,buses[1].latitude]);
  svgEl('line',{x1:pa[0],y1:pa[1],x2:pb[0],y2:pb[1],stroke:'#f28c9b','stroke-width':2,'stroke-dasharray':'5 5','data-layer':'distance'});
  const label=svgEl('text',{x:(pa[0]+pb[0])/2,y:(pa[1]+pb[1])/2-h*.025,fill:'#f6c7cf','font-size':h*.033,'text-anchor':'middle','data-layer':'distance-label','data-anchor-y':(pa[1]+pb[1])/2});label.textContent=`${r.distance_m.toFixed(2)} m · 검토 거리`;
  mapScene={background,colored,markers:svgEl('g',{'data-layer':'buses'}),lines:new Map(),buses:new Map(),lineCache:new Map(),busCache:new Map()};
  setView(true);
}
function zoom(factor,cx=.5,cy=.5){if(!view)return;const nw=Math.min(3e6,Math.max(20,view.w*factor)),nh=nw*view.h/view.w;view={x:view.x+(view.w-nw)*cx,y:view.y+(view.h-nh)*cy,w:nw,h:nh};setView();}
$('zoom-in').onclick=()=>zoom(.75);$('zoom-out').onclick=()=>zoom(1/.75);$('fit').onclick=()=>selected&&drawMap();
$('map').addEventListener('wheel',e=>{e.preventDefault();const rect=$('map').getBoundingClientRect();zoom(Math.exp(Math.max(-1,Math.min(1,e.deltaY*.002))),(e.clientX-rect.left)/rect.width,(e.clientY-rect.top)/rect.height);},{passive:false});
$('map').addEventListener('pointerdown',e=>{if(!view)return;drag={id:e.pointerId,x:e.clientX,y:e.clientY,view:{...view},moved:false};});
$('map').addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;const rect=$('map').getBoundingClientRect(),dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>3){drag.moved=true;$('map').setPointerCapture(e.pointerId);$('map').classList.add('dragging');}view={...drag.view,x:drag.view.x-dx/rect.width*drag.view.w,y:drag.view.y-dy/rect.height*drag.view.h};setView();});
function endDrag(e){if(drag&&drag.id===e.pointerId){$('map').classList.remove('dragging');setTimeout(()=>{drag=null;},0);}}
$('map').addEventListener('pointerup',endDrag);$('map').addEventListener('pointercancel',endDrag);
$('map').addEventListener('keydown',e=>{if(e.key==='+')zoom(.75);else if(e.key==='-')zoom(1/.75);});
for(const [id,label]of Object.entries(DATA.states)){option($('status-filter'),id,label);option($('decision-status'),id,label);}
for(const s of Object.values(DATA.stations).sort((a,b)=>(a.name||a.osm_id).localeCompare(b.name||b.osm_id,'ko')))option($('station-filter'),s.osm_id,`${s.name||s.osm_id} · ${s.osm_id}`);
for(const kv of [...new Set(rows.map(r=>r.voltage_kv))].sort((a,b)=>a-b))option($('voltage-filter'),String(kv),`${kv} kV`);
for(const id of ['station-filter','voltage-filter','status-filter','sort','search'])$(id).addEventListener(id==='search'?'input':'change',renderList);
$('summary').textContent=`후보 ${DATA.summary.candidates}건 · 고유 성분 쌍 ${DATA.summary.distinct_component_pairs}개 · 변전소 ${DATA.summary.substations}곳 · 입력 ${DATA.summary.input_hash.slice(0,12)}`;
$('score-policy').textContent=JSON.stringify(DATA.summary.score_policy,null,2);
renderList();if(rows.length)select(rows[0].review_key);
window.addEventListener('beforeunload',e=>{if(decisions.size||staleRecords.length){e.preventDefault();e.returnValue='';}});
// Read-only inspection helpers also let browser regression exercise the exact interchange used by the UI.
window.KRReview={data:DATA,select,validateDocument,importDocument,exportDocument,get selected(){return selected;},get visible(){return visible;},get view(){return {...view};}};
