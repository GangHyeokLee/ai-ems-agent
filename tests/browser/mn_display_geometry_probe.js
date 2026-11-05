(async () => {
 const results=[];const check=(name,v,detail)=>results.push({name,pass:!!v,detail});
 const wait=async p=>{const t=performance.now();while(!p()){if(performance.now()-t>90000)throw Error('timeout');await new Promise(r=>setTimeout(r,30));}};
 const shot=async name=>{window.__krScreenshotDone=false;window.__krScreenshot=name;await wait(()=>window.__krScreenshotDone);window.__krScreenshot=null;};
 try {
 await wait(()=>currentMapData && pfPreview && gridEntries.length===3);
 const select=document.getElementById('grid-select');select.value='mn-osm-35k';select.dispatchEvent(new Event('change'));
 await wait(()=>selectedGridId==='mn-osm-35k' && currentMapData?.lines.length===159 && pfPreview?.metadata.grid_id==='mn-osm-35k');
 document.getElementById('map').scrollIntoView({block:'center'});await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 check('map counts',currentMapData.buses.length===173 && currentMapData.lines.length===159 && currentMapData.transformers.length===27);
 check('159 line shapes',document.querySelectorAll('.network-line').length===159);
 check('reference layers absent',!document.querySelector('[class*="reference-line"], [class*="reference-substation"], [data-line-id^="REF-"]'));
 await shot('national');
 for(const [name,lon,lat,zoom] of [['ulaanbaatar',106.9,47.9,20],['west',92.5,48.3,5],['east',113.8,48.0,5]]){
  resetMapZoom();const p=mapProjection({longitude:lon,latitude:lat});const b=baseViewBox();setMapView(p.x-b[2]/zoom/2,p.y-b[3]/zoom/2,b[2]/zoom);
  await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));await shot(name);
 }
 resetMapZoom();
 const hit=document.querySelector('.network-line-hit');hit.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:500,clientY:500}));
 check('line selection',document.getElementById('outage-line').value===hit.dataset.lineId && document.querySelector('.selected-line')?.dataset.lineId===hit.dataset.lineId);
 check('tooltip',document.getElementById('map-tooltip-content').textContent.includes(hit.dataset.lineId));
 highlightLine(hit.dataset.lineId,'monitored-line');check('analysis highlight',document.querySelector('.network-line.monitored-line')?.dataset.lineId===hit.dataset.lineId);
 const routeCount=document.querySelectorAll('.network-line[data-geometry-source="osm"]').length;check('geometry count',routeCount===141,routeCount);
 const geometry = await fetch('/api/grids/mn-osm-35k/display-geometry').then(r=>r.json());
 check('18 original chords',document.querySelectorAll('.network-line[data-geometry-source="straight"]').length===18);
 check('multipart gaps preserved',geometry.lines.every(line=>{
  const path=[...document.querySelectorAll('.network-line')].find(n=>n.dataset.lineId===line.line_id);
  const hit=[...document.querySelectorAll('.network-line-hit')].find(n=>n.dataset.lineId===line.line_id);
  return path.tagName==='path' && (path.getAttribute('d').match(/M/g)||[]).length===line.osm_parts.length && hit.getAttribute('d')===path.getAttribute('d');
 }));
 check('model data untouched',currentMapData.lines.every(line=>!line.osm_parts));
 const route=document.querySelector('.network-line[data-geometry-source="osm"]');
 const line=currentMapData.lines.find(x=>x.line_id===route.dataset.lineId);
 renderMonitoredLineMarker({bus0:line.bus1_id,bus1:line.bus2_id},line.line_id);
 const expected=route.getPointAtLength(route.getTotalLength()/2), marker=document.querySelector('.monitored-marker circle');
 check('monitored marker on route',Math.abs(Number(marker.getAttribute('cx'))-expected.x)<.001 && Math.abs(Number(marker.getAttribute('cy'))-expected.y)<.001);
 const input=document.querySelector('#voltage-filters input[data-voltage="35"]');input.checked=false;input.dispatchEvent(new Event('change'));
 check('voltage filter', [...document.querySelectorAll('.network-line[data-voltage="35"]')].every(n=>n.classList.contains('layer-hidden')));
 input.checked=true;input.dispatchEvent(new Event('change'));
 const mode=document.getElementById('pf-display-mode');mode.value='line_loading';mode.dispatchEvent(new Event('change'));
 const result=pfLineResultMap.get(route.dataset.lineId), expectedColor=lineLoadingColor(result?.analysis_status==='ANALYZED'?result.loading_percent:null);
 const colorProbe=document.createElementNS(SVG_NS,'path');colorProbe.style.stroke=expectedColor;
 check('PF coloring',route.style.stroke===colorProbe.style.stroke,{actual:route.style.stroke,expected:colorProbe.style.stroke});
 mode.value='base';mode.dispatchEvent(new Event('change'));
 const point=route.getPointAtLength(route.getTotalLength()*.37),screen=new DOMPoint(point.x,point.y).matrixTransform(route.getScreenCTM());
 window.__krInputSent=false;window.__krTrustedInput={name:'click',x:screen.x,y:screen.y};await wait(()=>window.__krInputSent);window.__krTrustedInput=null;
 check('trusted route click',document.getElementById('outage-line').value===route.dataset.lineId);
 const mv=baseViewBox();setMapView(mv[0]+mv[2]/4,mv[1]+mv[3]/4,mv[2]/2);check('zoom',!!mapView && mapView.zoom===2);setMapView(mapView.x+1,mapView.y+1,mapView.w);check('pan',!!mapView);resetMapZoom();
 const rect=document.getElementById('network-map').getBoundingClientRect();
 window.__krInputSent=false;window.__krTrustedInput={name:'zoom',x:rect.left+rect.width/2,y:rect.top+rect.height/2};await wait(()=>window.__krInputSent && mapView?.zoom>1);window.__krTrustedInput=null;
 check('trusted wheel zoom',mapView.zoom>1);
 const viewBefore={...mapView};window.__krInputSent=false;window.__krTrustedInput={name:'pan',x:rect.left+rect.width/2,y:rect.top+rect.height/2};await wait(()=>window.__krInputSent);window.__krTrustedInput=null;
 await new Promise(r=>setTimeout(r,150));check('trusted drag pan',mapView.x!==viewBefore.x||mapView.y!==viewBefore.y);resetMapZoom();
 const nativeFetch=window.fetch;
 window.fetch=(url,...args)=>String(url).endsWith('/display-geometry')?Promise.resolve(new Response('{}',{status:404})):nativeFetch(url,...args);
 await loadNetworkMap();check('missing optional asset keeps full map',currentMapData.lines.length===159 && pfPreview && document.querySelectorAll('.network-line[data-geometry-source="straight"]').length===159);
 window.fetch=nativeFetch;await loadNetworkMap();check('geometry restored after reload',mongoliaDisplayGeometry.size===141);
 let release;
 window.fetch=(url,...args)=>String(url).endsWith('/display-geometry')?new Promise(resolve=>{release=()=>resolve(new Response(JSON.stringify(geometry),{status:200}));}):nativeFetch(url,...args);
 const pending=loadNetworkMap();await wait(()=>release);
 select.value='kr-kpg193';select.dispatchEvent(new Event('change'));await wait(()=>currentMapData?.buses.length===193);
 release();await pending;window.fetch=nativeFetch;
 check('stale geometry cannot enter KPG',selectedGridId==='kr-kpg193' && mongoliaDisplayGeometry.size===0 && currentMapData.buses.length===193);
 for(const id of ['kr-kpg193','kr-osm-transmission']){
  select.value=id;select.dispatchEvent(new Event('change'));await wait(()=>selectedGridId===id && currentMapData && (id==='kr-kpg193' ? currentMapData.buses.length===193 : currentMapData.grid_id===id));
  check(id+' unchanged geometry state',!document.querySelector('.network-line[data-geometry-source="osm"]'));
 }
 }catch(e){check('exception',false,e.stack||String(e));}
 window.__krReport={results};
})();
