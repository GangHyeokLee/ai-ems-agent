/* ===== 계통 모델 · DC 조류 · 감도 해석 엔진 ===== */
let M=null;
const nb=()=>M.buses.length;
const nameOf=i=>M.naming==='num'?`${M.buses[i].n}번`:M.buses[i].n;
const lname=l=>M.naming==='num'?`${M.buses[l.f].n}–${M.buses[l.t].n}`:`${M.buses[l.f].n}–${M.buses[l.t].n}`;
function freshState(){return {out:{},add:[],gen:M.gens.map(g=>({p:g.p,on:g.on})),load:M.buses.map(b=>b.load),ren:M.buses.map(()=>0),newGen:[],seq:0}}
function cloneS(s){return {out:{...s.out},add:s.add.map(l=>({...l})),gen:s.gen.map(g=>({...g})),load:[...s.load],ren:[...(s.ren||M.buses.map(()=>0))],newGen:s.newGen.map(g=>({...g})),seq:s.seq}}
const allLines=s=>M.lines.concat(s.add);
const gensOf=s=>M.gens.map((g,i)=>({...g,...s.gen[i],i})).concat(s.newGen);
const nC=l=>l.cx.length;
const remC=(l,s)=>nC(l)-(s.out[l.id]||0);
function lineX(l,s,extra){const k=remC(l,s)-(extra&&extra[l.id]||0);if(k<=0)return null;let y=0;for(let i=0;i<k;i++)y+=1/l.cx[i];return 1/y}
function lineR(l,s,extra){const k=remC(l,s)-(extra&&extra[l.id]||0);let r=0;for(let i=0;i<Math.max(k,0);i++)r+=l.cr[i];return r}
function injections(s){const n=nb(),inj=new Array(n).fill(0);
  gensOf(s).forEach(g=>{if(g.on)inj[g.b]+=g.p});s.load.forEach((v,i)=>inj[i]-=v);if(s.ren)s.ren.forEach((v,i)=>inj[i]+=v);
  M.dc.forEach(d=>{inj[d.f]-=d.p;inj[d.t]+=d.p});return inj}
/* 분산 슬랙: 원자력 제외 운전 발전기의 여유에 비례해 수급 차이를 나눔 */
function balance(s){const inj=injections(s);let mis=-inj.reduce((a,b)=>a+b,0);
  const units=[];M.gens.forEach((g,i)=>{const u=s.gen[i];if(u.on&&g.type!=='원자력')units.push({u,g})});s.newGen.forEach(u=>{if(u.on)units.push({u,g:u})});
  for(let it=0;it<3&&Math.abs(mis)>0.5;it++){
    const room=units.map(({u,g})=>mis>0?Math.max(0,g.max-u.p):Math.max(0,u.p-g.min));const tot=room.reduce((a,b)=>a+b,0);if(tot<=0)break;
    const take=Math.min(Math.abs(mis),tot);units.forEach(({u},k)=>{u.p+=Math.sign(mis)*take*room[k]/tot});mis-=Math.sign(mis)*take}
  return mis}
/* 조밀 가우스 소거 */
function solve(A,b){const n=b.length;const a=A.map((r,i)=>{const x=r.slice();x.push(b[i]);return x});
  for(let c=0;c<n;c++){let p=c,mx=Math.abs(a[c][c]);for(let r=c+1;r<n;r++){const v=Math.abs(a[r][c]);if(v>mx){mx=v;p=r}}
    if(mx<1e-10)return null;if(p!==c){const t=a[c];a[c]=a[p];a[p]=t}
    const pc=a[c][c],rc=a[c];for(let r=c+1;r<n;r++){const f=a[r][c]/pc;if(f===0)continue;const rr=a[r];for(let k=c;k<=n;k++)rr[k]-=f*rc[k]}}
  const x=new Array(n);for(let r=n-1;r>=0;r--){let v=a[r][n];for(let k=r+1;k<n;k++)v-=a[r][k]*x[k];x[r]=v/a[r][r]}return x}
function bmat(s,extra){const n=nb(),B=[...Array(n)].map(()=>new Float64Array(n));
  allLines(s).forEach(l=>{const x=lineX(l,s,extra);if(!x)return;const y=1/x;B[l.f][l.f]+=y;B[l.t][l.t]+=y;B[l.f][l.t]-=y;B[l.t][l.f]-=y});return B}
const SL=()=>M.slack;
function reduceIdx(){const n=nb(),idx=[];for(let i=0;i<n;i++)if(i!==SL())idx.push(i);return idx}
/* DC 조류: extra={lineId:추가로 빠지는 회선 수} */
function dcpf(s,extra){const n=nb(),B=bmat(s,extra),idx=reduceIdx(),inj=injections(s);
  const A=idx.map(i=>idx.map(j=>B[i][j]));const sol=solve(A,idx.map(i=>inj[i]/100));if(!sol)return null;
  const th=new Float64Array(n);idx.forEach((i,k)=>th[i]=sol[k]);return flowsFrom(s,th,extra)}
function flowsFrom(s,th,extra){const fl={},R={},lines=allLines(s);
  lines.forEach(l=>{const x=lineX(l,s,extra);fl[l.id]=x?(th[l.f]-th[l.t])/x*100:0;R[l.id]=lineR(l,s,extra)});
  return {fl,R,th,lines,extra:extra||{},s}}
const isOut=(r,l)=>!(r.R[l.id]>0);
const ld=(r,l)=>r.R[l.id]>0?Math.abs(r.fl[l.id])/r.R[l.id]*100:0;
const live=r=>r.lines.filter(l=>!isOut(r,l));
const viol=r=>live(r).filter(l=>ld(r,l)>100);
const clean=r=>!!r&&live(r).every(l=>ld(r,l)<=98.5);
/* 감도 행렬 X = Bred^-1 (가우스-조던) */
function xmat(s){const n=nb(),B=bmat(s),idx=reduceIdx(),m=idx.length;
  const a=idx.map(i=>{const r=new Float64Array(2*m);idx.forEach((j,k)=>r[k]=B[i][j]);return r});idx.forEach((_,k)=>a[k][m+k]=1);
  for(let c=0;c<m;c++){let p=c,mx=Math.abs(a[c][c]);for(let r=c+1;r<m;r++){const v=Math.abs(a[r][c]);if(v>mx){mx=v;p=r}}if(mx<1e-10)return null;
    if(p!==c){const t=a[c];a[c]=a[p];a[p]=t}const rc=a[c],pc=rc[c];for(let k=c;k<2*m;k++)rc[k]/=pc;
    for(let r=0;r<m;r++){if(r===c)continue;const rr=a[r],f=rr[c];if(f===0)continue;for(let k=c;k<2*m;k++)rr[k]-=f*rc[k]}}
  const X=[...Array(n)].map(()=>new Float64Array(n));idx.forEach((i,ki)=>idx.forEach((j,kj)=>X[i][j]=a[ki][m+kj]));return X}
/* 빠른 N-1: 셔먼-모리슨 갱신 (회선 1개 탈락) */
function n1fast(s,r0,X){const res=[];if(!r0||!X)return res;
  for(const l of allLines(s)){if(remC(l,s)<=0)continue;const x0=lineX(l,s),x1=lineX(l,s,{[l.id]:1});
    const db=(x1?1/x1:0)-1/x0;const f=l.f,t=l.t;const Xa=new Float64Array(nb());for(let i=0;i<nb();i++)Xa[i]=X[i][f]-X[i][t];
    const aXa=Xa[f]-Xa[t],den=1+db*aXa;const ath=r0.th[f]-r0.th[t];
    if(Math.abs(den)<1e-7){res.push({l,island:true,v:[]});continue}
    const k=db*ath/den;const th=new Float64Array(nb());for(let i=0;i<nb();i++)th[i]=r0.th[i]-Xa[i]*k;
    const r=flowsFrom(s,th,{[l.id]:1});const v=viol(r).filter(m=>{const b=r0.R[m.id]>0?Math.abs(r0.fl[m.id])/r0.R[m.id]*100:0;return b<=100||ld(r,m)>b+2});res.push({l,r,v,island:false})}
  return res}
function n1full(s,l){return dcpf(s,{[l.id]:1})}
/* 재급전: 감도 기반 탐욕 탐색 → 정밀 재계산으로 검증 */
function busCaps(s){const n=nb(),up=new Array(n).fill(0),dn=new Array(n).fill(0),cost=new Array(n).fill(0),cw=new Array(n).fill(0);
  gensOf(s).forEach(g=>{if(!g.on||g.type==='원자력'||(typeof maintGen==='function'&&maintGen(g)))return;up[g.b]+=Math.max(0,g.max-g.p);dn[g.b]+=Math.max(0,g.p-g.min);cost[g.b]+=g.c*g.max;cw[g.b]+=g.max});
  return {up,dn,c:cost.map((v,i)=>cw[i]?v/cw[i]:0)}}
function moveGen(s,b,mw){const us=gensOf(s).filter(g=>g.b===b&&g.on&&g.type!=='원자력'&&!(typeof maintGen==='function'&&maintGen(g)));const cap=us.map(g=>mw>0?g.max-g.p:g.p-g.min);const tot=cap.reduce((a,c)=>a+c,0);if(tot<=0)return 0;
  const take=Math.min(Math.abs(mw),tot);us.forEach((g,k)=>{const d=Math.sign(mw)*take*cap[k]/tot;if(g.i!=null)s.gen[g.i].p+=d;else{const ng=s.newGen.find(x=>x.id===g.id);ng.p+=d}});return Math.sign(mw)*take}
function redispatch(s0){let s=cloneS(s0);const moves=[];
  for(let it=0;it<10;it++){const r=dcpf(s);if(!r)return null;if(clean(r))break;
    const worst=live(r).sort((a,b)=>ld(r,b)-ld(r,a))[0];const X=xmat(s);if(!X)return null;
    const xl=lineX(worst,s),sg=Math.sign(r.fl[worst.id]);const P=new Float64Array(nb());for(let k=0;k<nb();k++)P[k]=(X[worst.f][k]-X[worst.t][k])/xl;
    const cap=busCaps(s);let best=null;
    for(let i=0;i<nb();i++){if(cap.dn[i]<1)continue;for(let j=0;j<nb();j++){if(j===i||cap.up[j]<1)continue;
      const e=-sg*(P[j]-P[i]);if(e<0.02)continue;const need=(Math.abs(r.fl[worst.id])-0.97*r.R[worst.id])/e;
      if(need>cap.dn[i]||need>cap.up[j])continue;const score=need*(1+Math.max(0,cap.c[j]-cap.c[i])/400);
      if(!best||score<best.score)best={i,j,need,score}}}
    if(!best)return null;const mw=Math.ceil(best.need/10)*10;
    const d=-moveGen(s,best.i,-mw),u=moveGen(s,best.j,d);
    const m=moves.find(x=>x.i===best.i&&x.j===best.j);if(m)m.mw+=u;else moves.push({i:best.i,j:best.j,mw:u})}
  const r=dcpf(s);if(!clean(r))return null;
  const cap=busCaps(s0);const cost=Math.round(moves.reduce((a,m)=>a+m.mw*(cap.c[m.j]-cap.c[m.i]),0));
  return {moves,total:Math.round(moves.reduce((a,m)=>a+m.mw,0)),cost,state:s,r}}
function shed(s0){let s=cloneS(s0);const cuts={};
  for(let it=0;it<10;it++){const r=dcpf(s);if(!r)return null;if(clean(r))break;
    const worst=live(r).sort((a,b)=>ld(r,b)-ld(r,a))[0];const X=xmat(s);if(!X)return null;const xl=lineX(worst,s),sg=Math.sign(r.fl[worst.id]);
    const P=new Float64Array(nb());for(let k=0;k<nb();k++)P[k]=(X[worst.f][k]-X[worst.t][k])/xl;
    const cap=busCaps(s);let best=null;
    for(let k=0;k<nb();k++){const room=s.load[k]*0.6-(cuts[k]||0);if(room<1)continue;
      for(let i=0;i<nb();i++){if(cap.dn[i]<1)continue;const e=-sg*(P[k]-P[i]);if(e<0.02)continue;
        const need=(Math.abs(r.fl[worst.id])-0.97*r.R[worst.id])/e;if(need>room||need>cap.dn[i])continue;if(!best||need<best.need)best={k,i,need}}}
    if(!best)return null;const mw=Math.ceil(best.need/10)*10;s.load[best.k]-=mw;moveGen(s,best.i,-mw);cuts[best.k]=(cuts[best.k]||0)+mw}
  const r=dcpf(s);if(!clean(r))return null;const list=Object.entries(cuts).map(([k,mw])=>({b:+k,mw}));
  return {cuts:list,total:list.reduce((a,c)=>a+c.mw,0),state:s,r}}
/* 예방 재급전: N-1 위반 사고 수를 줄이는 탐욕 탐색 */
function n1eval(s){const r=dcpf(s);if(!r)return null;const X=xmat(s);if(!X)return null;const c=n1fast(s,r,X);const vi=c.filter(x=>!x.island&&x.v.length);const base=viol(r);
  let over=0;vi.forEach(x=>x.v.forEach(l=>over+=ld(x.r,l)-100));base.forEach(l=>over+=(ld(r,l)-100)*3);return {r,X,c,vi,base,score:base.length*1000+vi.length*10+over}}
function preventive(s0,maxIt){maxIt=maxIt||16;let cur=cloneS(s0),ev=n1eval(cur);if(!ev)return null;const start=ev;let best={s:cloneS(cur),ev},stall=0;
  for(let it=0;it<maxIt;it++){if(!ev.base.length&&!ev.vi.length)break;
    let cands=[];if(ev.base.length)cands=ev.base.map(l=>({l,ck:null,fl:ev.r.fl[l.id],R:ev.r.R[l.id],p:ld(ev.r,l)}));
    else ev.vi.forEach(c=>c.v.forEach(l=>cands.push({l,ck:c.l,fl:c.r.fl[l.id],R:c.r.R[l.id],p:ld(c.r,l)})));
    cands.sort((a,b)=>a.p-b.p);cands=cands.slice(0,8);const cap=busCaps(cur);let bp=null;const xc={};
    for(const cd of cands){let sk=cur,X=ev.X;if(cd.ck){sk=cloneS(cur);sk.out[cd.ck.id]=(sk.out[cd.ck.id]||0)+1;X=xc[cd.ck.id]||(xc[cd.ck.id]=xmat(sk));if(!X)continue}
      const xl=lineX(cd.l,sk);if(!xl)continue;const sg=Math.sign(cd.fl),fr=X[cd.l.f],to=X[cd.l.t],need0=Math.abs(cd.fl)-0.95*cd.R;
      for(let i=0;i<nb();i++){if(cap.dn[i]<10)continue;const Pi=(fr[i]-to[i])/xl;for(let j=0;j<nb();j++){if(j===i||cap.up[j]<10)continue;const e=-sg*((fr[j]-to[j])/xl-Pi);if(e<0.03)continue;
        const need=Math.max(10,need0/e);if(need>cap.dn[i]||need>cap.up[j])continue;const sc=need*(1+Math.max(0,cap.c[j]-cap.c[i])/300);if(!bp||sc<bp.sc)bp={i,j,need,sc}}}}
    if(!bp)break;const mw=Math.ceil(bp.need/10)*10;const d=-moveGen(cur,bp.i,-mw);moveGen(cur,bp.j,d);
    const ev2=n1eval(cur);if(!ev2)break;if(ev2.score<best.ev.score-1e-6){best={s:cloneS(cur),ev:ev2};stall=0}else stall++;ev=ev2;if(stall>=3)break}
  return {start,best:best.ev,state:best.s}}

/* ===== 자연어 이벤트 해석 (규칙 기반) ===== */
const FAULT=/고장|탈락|트립|정지|끊|나가|나간|개방|휴전|작업|빠지|빠진|제외|소실/;
const RESTORE=/복구|재투입|투입|복귀|정상화|살리|재가압/;
const NEWW=/신설|신규|증설|새로|건설|하나 더|추가로 놓|추가 건설/;
const UP=/증가|늘|올|상향|증발|커지|많아|높/;
const DOWN=/감소|줄|내리|낮|하향|감발|작아|적어|떨어/;
const ALLC=/전\s*회선|모든\s*회선|양\s*회선|회선\s*(모두|전부|다)|\d\s*회선\s*(모두|전부|다)|루트\s*(전체|모두)|송전로\s*전체/;
let NAMEIDX=null,PLANTS=null;
function buildIndex(){const names=M.buses.map((b,i)=>({t:b.n,i})).filter(x=>M.naming!=='num');NAMEIDX=names.sort((a,b)=>b.t.length-a.t.length);
  const ps=[...new Set(M.gens.map(g=>g.plant))];PLANTS=[];
  ps.forEach(p=>{if(M.naming==='num')return;PLANTS.push({t:p,p,full:true});const short=p.replace(/(화력|원자력|복합|열병합|발전소|발전|에너지)$/,'');if(short.length>=2&&short!==p&&!M.buses.some(b=>b.n===short)&&!ps.some(q=>q!==p&&q.startsWith(short)))PLANTS.push({t:short,p})});
  PLANTS.sort((a,b)=>b.t.length-a.t.length)}
function mentions(seg){const found=[],mask=seg.split('');
  const take=(st,en,o)=>{for(let k=st;k<en;k++)if(mask[k]==='\u0000')return false;for(let k=st;k<en;k++)mask[k]='\u0000';found.push({...o,st,en});return true};
  const plantScan=(list)=>{for(const P of list){let from=0,ix;while((ix=seg.indexOf(P.t,from))>=0){from=ix+P.t.length;let en=from;const um=seg.slice(en).match(/^\s*(?:#\s*(\d+)|(\d+)\s*호기)/);let unit=null;if(um){unit=+(um[1]||um[2]);en+=um[0].length}take(ix,en,{k:'plant',p:P.p,unit})}}};
  plantScan(PLANTS.filter(P=>P.full));
  seg.replace(/\bG\s?(\d{1,3})\b/gi,(m,d,ix)=>{const g=M.gens.find(x=>x.id==='G'+d);if(g)take(ix,ix+m.length,{k:'gen',g:g.id});return m});
  if(M.naming==='num'){seg.replace(/(?:버스|모선|bus)?\s*(\d{1,3})\s*(?:번)?(?:\s*(?:버스|모선))?/gi,(m,d,ix)=>{const after=seg.slice(ix+m.length);if(/^\s*(mw|MW|%|kv|kV|회선|호기|배|퍼센트|메가)/.test(after))return m;
      const before=seg.slice(Math.max(0,ix-1),ix);if(/[#Gg\d.]/.test(before))return m;const i=M.buses.findIndex(b=>b.n===d);if(i>=0){const lead=m.match(/^\s*/)[0].length;take(ix+lead,ix+m.length,{k:'bus',i})}return m})}
  else for(const N of NAMEIDX){let from=0,ix;while((ix=seg.indexOf(N.t,from))>=0){from=ix+N.t.length;take(ix,ix+N.t.length,{k:'bus',i:N.i})}}
  if(/발전|출력|호기|#|트립|정지|기동/.test(seg))plantScan(PLANTS.filter(P=>!P.full));
  return found.sort((a,b)=>a.st-b.st)}
function corridorBetween(s,a,b){return allLines(s).find(l=>(l.f===a&&l.t===b)||(l.f===b&&l.t===a))}
function splitSeg(t){return t.split(/[,，;]|그리고|하고\s|및|또한|면서|한 뒤|한 다음|동시에|(?<=[가-힣])고\s|(?<=[가-힣])며\s/).map(x=>x.trim()).filter(Boolean)}
function numMW(seg){const m=seg.match(/(\d+(?:\.\d+)?)\s*(mw|메가|MW)/i);return m?+m[1]:null}
function relOp(seg){if(/절반|반으로|반토막/.test(seg))return {mul:.5};if(/두\s*배|2\s*배/.test(seg))return {mul:2};if(/세\s*배|3\s*배/.test(seg))return {mul:3};
  const p=seg.match(/(\d+(?:\.\d+)?)\s*(%|퍼센트|프로)/);if(p){const v=+p[1]/100;return {mul:DOWN.test(seg)?1-v:1+v}}
  const h=seg.match(/([1-9])\s*할/);if(h){const v=+h[1]/10;return {mul:DOWN.test(seg)?1-v:1+v}}return null}
const isSet=seg=>/\d+(?:\.\d+)?\s*(mw|MW|메가)?\s*(로|으로|까지|에 맞)/.test(seg);
function parseEvents(text,s){
  const evs=[],issues=[];let pending=[];
  const segs=splitSeg(text.replace(/\s+/g,' '));
  for(const seg of segs){
    const ms=mentions(seg),mw=numMW(seg),rel=relOp(seg);
    const buses=ms.filter(m=>m.k==='bus'),gens=ms.filter(m=>m.k==='gen'||m.k==='plant');
    const isLoad=/부하|수요/.test(seg)&&!/선로|과부하/.test(seg.replace(/부하율/,''));
    const isGen=gens.length>0||(/발전|출력|호기|발전기/.test(seg)&&!/선로/.test(seg));
    if(isLoad&&buses.length){for(const b of buses){const cur=s.load[b.i];let v=null,mode='set';
        if(rel)v=Math.round(cur*rel.mul);else if(mw!=null)v=isSet(seg)?mw:DOWN.test(seg)?Math.max(0,cur-mw):cur+mw;
        if(v==null){issues.push(`${nameOf(b.i)} 부하를 얼마나 바꿀지 알려 주세요 (예: 100MW, 절반, 10%).`);continue}
        evs.push({k:'load',b:b.i,v})}continue}
    if(isGen){let units=[];
      for(const g of gens){if(g.k==='gen')units.push(M.gens.findIndex(x=>x.id===g.g));else{const all=M.gens.map((x,i)=>({x,i})).filter(o=>o.x.plant===g.p);
        if(g.unit){const u=all.find(o=>new RegExp(`#${g.unit}$`).test(o.x.name));if(u)units.push(u.i);else issues.push(`${g.p}에 ${g.unit}호기가 없습니다.`)}else units.push(...all.filter(o=>s.gen[o.i].on).map(o=>o.i))}}
      if(!gens.length&&buses.length){
        if(NEWW.test(seg)||/추가/.test(seg)&&/발전기|발전소/.test(seg)){evs.push({k:'addgen',b:buses[0].i,v:mw||100,assumed:mw==null});continue}
        const at=M.gens.map((x,i)=>({x,i})).filter(o=>o.x.b===buses[0].i);if(!at.length){issues.push(`${nameOf(buses[0].i)}에는 발전기가 없습니다. 새로 넣으려면 “${nameOf(buses[0].i)}에 발전기 100MW 신설”처럼 말해 주세요.`);continue}
        units=at.filter(o=>s.gen[o.i].on||/기동|투입/.test(seg)).map(o=>o.i)}
      if(!units.length){if(!issues.length)issues.push('어느 발전기인지 확정하지 못했습니다.');continue}
      const curTot=units.reduce((a,i)=>a+(s.gen[i].on?s.gen[i].p:0),0);
      if(FAULT.test(seg)&&!UP.test(seg)){units.forEach(i=>evs.push({k:'gen',i,v:0,trip:true}));continue}
      let target=null;if(rel)target=curTot*rel.mul;else if(mw!=null)target=isSet(seg)?mw:DOWN.test(seg)?curTot-mw:UP.test(seg)||/기동/.test(seg)?curTot+mw:null;
      if(target==null&&/기동|투입/.test(seg)){target=units.reduce((a,i)=>a+M.gens[i].min,0)}
      if(target==null){issues.push(`${units.length>1?M.gens[units[0]].plant:M.gens[units[0]].name} 출력을 얼마로 바꿀지 알려 주세요 (예: 300MW로, 100MW 줄여).`);continue}
      const caps=units.map(i=>M.gens[i].max),ct=caps.reduce((a,b)=>a+b,0);
      units.forEach((i,k)=>{const g=M.gens[i];const v=Math.max(0,Math.min(g.max,target*caps[k]/ct));evs.push({k:'gen',i,v:Math.round(v),clip:target*caps[k]/ct>g.max+0.5})});continue}
    // 선로
    const pairs=[];let k2=0;const lids=[...seg.matchAll(/\bL(\d{1,3})\b/g)].map(m=>m[0]);
    while(k2<buses.length-1){const a=buses[k2],b=buses[k2+1],gap=seg.slice(a.en,b.st);
      if(/^\s*([–\-~―−]|에서|와|과|랑|이랑|부터|~|하고|및)?\s*$/.test(gap)){pairs.push([a.i,b.i]);k2+=2}else k2++}
    const single=buses.length===1&&!pairs.length?buses[0].i:null;
    const act=NEWW.test(seg)?'add':RESTORE.test(seg)?'restore':FAULT.test(seg)?'out':null;
    const lineRefs=pairs.map(p=>({p,l:corridorBetween(s,p[0],p[1])})).concat(lids.map(id=>({l:allLines(s).find(x=>x.id===id),p:null})));
    if(!act){if(lineRefs.length)pending.push(...lineRefs);else if(single!=null&&/선로|송전/.test(seg))pending.push({amb:single});continue}
    const refs=pending.concat(lineRefs);pending=[];
    if(!refs.length&&single!=null){if(act==='add'){issues.push('신설할 선로의 양 끝 변전소를 알려 주세요.');continue}
      evs.push({k:'amb',a:single});continue}
    if(!refs.length){if(act)issues.push('대상 선로를 확정하지 못했습니다. “A–B 선로”처럼 양 끝을 알려 주세요.');continue}
    const allc=ALLC.test(seg),cm=seg.match(/(\d)\s*회선/);
    for(const r of refs){
      if(r.amb!=null){evs.push({k:'amb',a:r.amb});continue}
      if(act==='add'){if(!r.p){issues.push('신설은 양 끝 변전소로 알려 주세요.');continue}const rm=seg.match(/정격\s*(\d+)/);evs.push({k:'add',a:r.p[0],b:r.p[1],r:rm?+rm[1]:null});continue}
      if(!r.l){issues.push(`${r.p.map(nameOf).join('과 ')} 사이에는 선로가 없습니다. 새로 놓는 경우라면 “신설”이라고 말해 주세요.`);continue}
      const rem=remC(r.l,s),tot=r.l.cx?r.l.cx.length:1;
      if(act==='restore'){const outN=tot-rem;if(!outN){issues.push(`${lname(r.l)} 선로는 모든 회선이 운전 중입니다.`);continue}evs.push({k:'restore',id:r.l.id,n:allc?outN:Math.min(outN,cm?+cm[1]:1)});continue}
      if(rem<=0){issues.push(`${lname(r.l)} 선로는 이미 전 회선 탈락 상태입니다.`);continue}
      const n=allc?rem:Math.min(rem,cm?+cm[1]:1);evs.push({k:'out',id:r.l.id,n,assumed:!allc&&!cm&&tot>1})}}
  if(pending.length)issues.push('선로에 어떤 일이 생기는지(탈락, 복구, 신설) 알려 주세요.');
  return {evs,issues}}
function applyEv(s,e){
  if(e.k==='out'){s.out[e.id]=(s.out[e.id]||0)+e.n}
  else if(e.k==='restore'){s.out[e.id]=Math.max(0,(s.out[e.id]||0)-e.n);if(!s.out[e.id])delete s.out[e.id]}
  else if(e.k==='add'){const kv=Math.max(M.buses[e.a].kv,M.buses[e.b].kv);const same=M.lines.filter(l=>l.kv===kv);const med=a=>a.sort((x,y)=>x-y)[Math.floor(a.length/2)];
    const r=e.r||med(same.map(l=>l.cr[0]))||200,x=med(same.map(l=>l.cx[0]))||.1;const id='N'+(++s.seq);
    s.add.push({id,f:e.a,t:e.b,cx:[x],cr:[r],kv,isNew:true,eq:`NEW-${kv}-${M.buses[e.a].id}-${M.buses[e.b].id}`});e.id=id;e.rr=r}
  else if(e.k==='addgen'){const id='GN'+(++s.seq);s.newGen.push({id,b:e.b,p:e.v,on:1,min:0,max:Math.round(e.v*1.2),c:60,type:'신규',name:`${nameOf(e.b)} 신규 발전기`,plant:'신규'});e.id=id}
  else if(e.k==='gen'){const u=s.gen[e.i];e.before=u.on?u.p:0;if(e.v<=0){u.p=0;u.on=0}else{u.on=1;u.p=e.v}e.after=e.v}
  else if(e.k==='load'){e.before=s.load[e.b];s.load[e.b]=e.v;e.after=e.v}}
function evText(e,s){const L=id=>allLines(s).find(l=>l.id===id);
  if(e.k==='out'){const l=L(e.id),t=l.cx.length;return `${lname(l)} ${l.kv?l.kv+'kV ':''}선로 ${t>1?(e.n>=t?'전 회선 ':e.n+'회선 '):''}탈락${t>1?` (${t}회선 중)`:''}${e.assumed?' · 1회선으로 가정':''} <span class="eid">${l.eq}</span>`}
  if(e.k==='restore'){const l=L(e.id);return `${lname(l)} 선로 ${e.n}회선 복구 <span class="eid">${l.eq}</span>`}
  if(e.k==='add'){const l=L(e.id);return `${nameOf(e.a)}–${nameOf(e.b)} ${l.kv?l.kv+'kV ':''}선로 신설 (정격 ${Math.round(e.rr)}MW${e.r?'':', 같은 전압 선로 중앙값으로 가정'}) <span class="eid">${l.eq}</span>`}
  if(e.k==='addgen')return `${nameOf(e.b)} 발전기 신설 ${e.v}MW${e.assumed?' (기본값 가정)':''}`;
  if(e.k==='gen'){const g=M.gens[e.i];return e.trip?`${g.name} 정지 (${Math.round(e.before)}→0MW)`:`${g.name} 출력 ${Math.round(e.before)}→${Math.round(e.after)}MW${e.clip?' (최대출력 제한)':''}`}
  if(e.k==='load')return `${nameOf(e.b)} 부하 ${Math.round(e.before)}→${Math.round(e.after)}MW`;return ''}

const KV=v=>v?` ${v}kV`:'';
/* ===== 화면 ===== */
const DOCS=[
{id:'운영절차 3.2',t:'선로 과부하 시 조치',b:'정격 100%를 넘는 과부하는 15분 안에 해소한다. 조치 순서는 발전 재급전, 계통 재구성, 부하 차단 순으로 한다.',k:['과부하','재급전','조치','고장']},
{id:'운영절차 4.1',t:'부하 차단 승인',b:'부하 차단은 당직 책임자의 승인을 받아 시행하며, 차단량과 복구 시각을 운영일지에 기록한다.',k:['차단','승인']},
{id:'운영절차 2.4',t:'N-1 안전도 기준',b:'평상시 계통은 임의의 단일 설비 고장에도 정격을 넘지 않도록 운전한다. 위반 사고가 있으면 예방 재급전을 검토한다.',k:['N-1','상정사고','안전도','기준']},
{id:'운영절차 5.3',t:'자동 대안이 없을 때',b:'재급전과 부하 차단으로 해소되지 않는 위반은 상위 관제소에 즉시 보고하고 계통 분리 운전을 검토한다.',k:['보고','에스컬레이션','분리']},
{id:'운영절차 6.1',t:'설비 작업 휴전',b:'계획 휴전 전 해당 설비를 제외한 상태로 조류와 N-1을 미리 검토하고, 위반이 있으면 휴전 시간대를 조정한다.',k:['휴전','작업','신설']},
{id:'운영절차 7.2',t:'대용량 발전기 탈락',b:'대용량 발전기 탈락 시 주파수 회복을 위해 운전 예비력을 즉시 투입하고, 예비력이 부족하면 부하 차단을 검토한다.',k:['발전기','트립','정지','원자력','예비력']}];
const TOOLS=[
['get_model_catalog','1.0.0','계통 모델의 모선·선로·발전기 목록 조회','r','동기'],
['get_measurements','1.4.0','실시간 DB에서 모선·선로 측정값 조회','r','동기'],
['identify_equipment','2.2.0','자연어 설비명·이벤트를 설비 ID로 확정','r','동기'],
['create_snapshot','1.0.3','해석에 쓸 계통 상태를 시점 고정','r','동기'],
['build_scenario','0.8.0','가정 이벤트를 스냅샷 사본에 반영 (탈락·복구·신설·출력·부하)','c','동기'],
['run_power_flow','1.3.0','스냅샷 또는 시나리오 DC 조류계산','c','동기'],
['run_contingency','1.3.0','지정 설비 탈락 상정사고 해석','c','비동기'],
['run_n1_screening','2.0.0','감도(셔먼-모리슨) 기반 전 선로 N-1 스크리닝','c','비동기'],
['run_redispatch_opf','1.0.0','감도 기반 최소 재급전 탐색 후 정밀 재계산 검증','c','비동기'],
['eval_load_shedding','1.0.0','감도 기반 최소 부하 차단 탐색 후 검증','c','동기'],
['search_procedure','1.3.0','운영절차·규정 문서 검색','r','동기'],
['save_workspace','0.6.0','승인된 이벤트·조치를 디지털 트윈에 반영','x','동기']];
const PERM={r:['조회','r'],c:['계산','c'],x:['제어 · 승인 필요','x']};
let BASE=null,twin=null,lastScen=null,logs=[],feedback=[],calls={},alarmsT={},curTrace='-',traceSeq=0,qbRuns=[],n1Cache=null,baseNote='';
const $=id=>document.getElementById(id);
const esc=t=>String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pad=n=>String(n).padStart(2,'0');
const now=()=>{const d=new Date();return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`};
const snapId=()=>{const d=new Date();return `SN-${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`};
const fmt=n=>Math.round(n).toLocaleString('ko-KR');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const big=()=>M.buses.length>40;

function loadModel(id){M=MODELS[id];buildIndex();
  let s=freshState();balance(s);let r=dcpf(s);baseNote='';
  if(!clean(r)){const rd=redispatch(s);if(rd){s=rd.state;baseNote=`DC 근사에서 ${viol(r).length}개 선로가 정격을 소폭 넘어 ${fmt(rd.total)}MW 재급전으로 보정한 운전점에서 시작합니다.`}}
  if(typeof TL!=='undefined'){tlClose();TL.plans={};TL.sum=[]}BASE=cloneS(s);twin=cloneS(s);lastScen=null;alarmsT={};n1Cache=null;vb=null;view=null;views.length=0;
  $('modelDesc').textContent=M.desc+(baseNote?' — '+baseNote:'');
  if(typeof addJN==='function')addJN('model',`계통 모델 ${M.name} 로드`);if(typeof snapPush==='function')snapPush('정기',`계통 모델 로드 · 기준 운전점`,BASE,'-');$('tlOpen').textContent=M.profile?`하루 흐름 재생 · ${M.profile.label} 실측 프로필`:'하루 흐름 재생 · 표준 부하 곡선';
  logCall('get_model_catalog',M.name,`${M.buses.length} buses, ${M.lines.length} corridors, ${M.gens.length} gens`,'0.0s');
  drawSLD(true);if(typeof buildMapIdx==='function'){buildMapIdx();SELHL=null;if($('mapCard'))$('mapCard').hidden=true}chipsDefault();resetBench();{const E=examples();$('q').placeholder=`${E.ln(E.L3)} 선로 휴전하고 ${qName(E.loads[0].i)} 부하 10% 늘면?`}
  const m=document.createElement('div');m.className='sys';m.textContent=`계통 모델: ${M.name} · 모선 ${M.buses.length}개, 선로 ${M.lines.length}개${M.lines.some(l=>l.cx.length>1)?'(병렬 회선 묶음)':''}, 발전기 ${M.gens.length}기`;$('msgs').appendChild(m);scroll()}

/* ---------- 계통도 ---------- */
let vb=null,view=null;const views=[];
function shown(){return view?view.state:twin}
function drawBanner(){const bn=$('vbanner'),lb=$('liveBtn'),sb=$('stepbar');if(!view||view.live&&!view.label){bn.hidden=true;sb.hidden=true;lb.classList.remove('pri');return}
  bn.hidden=false;bn.className='vbanner'+(view.live?' live':'');$('vlabel').textContent=view.label;lb.classList.toggle('pri',!view.live);
  if(!view.story){sb.hidden=true;return}sb.hidden=false;const st=view.story;
  $('sbar').innerHTML=st.map((x,k)=>`<button type="button" class="sbtn sk-${x.kind}${k===view.si?' on':''}" data-si="${k}" title="${esc(x.cap)}">${k?k:'전체'}</button>`).join('');
  $('sbar').querySelectorAll('.sbtn').forEach(b=>b.onclick=()=>{stopPlay();goStep(+b.dataset.si)});
  $('scap').innerHTML=`<b>${view.si?view.si+'. ':''}${esc(st[view.si].kindTxt)}</b> ${esc(st[view.si].cap)}`;$('splay').textContent=playT?'정지':'재생'}
function goStep(k){const st=view.story[k];view.si=k;view.state=st.state;view.hl=st.hl;view.ref=st.ref;animateTo(st.focus&&st.focus.length?fitBox(st.focus):storyFull());drawSLD()}
let playT=null;function stopPlay(){if(playT){clearTimeout(playT);playT=null;drawBanner()}}
function playStory(){stopPlay();if(!view||!view.story)return;const v=view;let k=0;const tick=()=>{if(view!==v){playT=null;return}goStep(k);k++;if(k<v.story.length){playT=setTimeout(tick,k===1?1500:1900)}else{playT=setTimeout(()=>{playT=null;if(view===v)goStep(0)},1900)}drawBanner()};playT=setTimeout(tick,50);drawBanner()}
let animId=null;function animateTo(t){if(animId)cancelAnimationFrame(animId);const s={...vb},t0=performance.now(),D=matchMedia('(prefers-reduced-motion: reduce)').matches?1:520;
  const f=now=>{const k=Math.min(1,(now-t0)/D),e=k<.5?2*k*k:1-Math.pow(-2*k+2,2)/2;vb={x:s.x+(t.x-s.x)*e,y:s.y+(t.y-s.y)*e,w:s.w+(t.w-s.w)*e,h:s.h+(t.h-s.h)*e};setVB();if(k<1)animId=requestAnimationFrame(f)};animId=requestAnimationFrame(f)}
function storyFull(){return {x:-M.w*.04,y:-M.h*.24,w:M.w*1.08,h:M.h*1.27}}
function fitBox(idx){if(!idx||!idx.length||M.buses.length<=6)return storyFull();const xs=idx.map(i=>M.buses[i].x),ys=idx.map(i=>M.buses[i].y);
  let x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);const minW=M.w*(big()?.24:.45);
  let w=Math.max(x1-x0,minW)*1.4,h=Math.max(y1-y0,minW*M.h/M.w)*1.4;const ar=M.w/M.h;if(w/h>ar)h=w/ar;else w=h*ar;w=Math.min(w,M.w*1.05);h=Math.min(h,M.h*1.05);
  h*=1.12;w=h*ar;return {x:(x0+x1)/2-w/2,y:(y0+y1)/2-h/2-h*.13,w,h}}
function fitTo(idx){animateTo(fitBox(idx))}
function setView(v,fit){if(view!==v)stopPlay();view=v;if(v&&v.story){goStep(v.si||0);return}if(fit&&v&&v.hl&&v.hl.focus)fitTo(v.hl.focus);drawSLD()}
function goLive(){stopPlay();view=null;animateTo({x:0,y:0,w:M.w,h:M.h});drawSLD()}
function evHL(evs,s){const lines=new Set(),marks=[],focus=[];
  evs.forEach(e=>{if(e.k==='out'||e.k==='restore'||e.k==='add'){const l=allLines(s).find(x=>x.id===e.id);if(l){lines.add(l.id);focus.push(l.f,l.t)}}
    else if(e.k==='gen'){const g=M.gens[e.i];const d=e.after-e.before;const m=marks.find(x=>x.b===g.b&&x.k===(e.trip?'trip':'gen'));if(m){m.v+=d}else marks.push({b:g.b,k:e.trip?'trip':'gen',v:d});focus.push(g.b)}
    else if(e.k==='load'){marks.push({b:e.b,k:'load',v:e.after-e.before});focus.push(e.b)}
    else if(e.k==='addgen'){marks.push({b:e.b,k:'gen',v:e.v});focus.push(e.b)}});
  marks.forEach(m=>{m.t=m.k==='trip'?`트립 ${fmt(m.v)}MW`:m.k==='load'?`부하 ${m.v>0?'+':''}${fmt(m.v)}`:`${m.v>0?'+':''}${fmt(m.v)}MW`});return {lines,marks,focus}}
function remHL(s0,s1,base){const d=new Array(M.buses.length).fill(0),dl=new Array(M.buses.length).fill(0);
  gensOf(s1).forEach((g,k)=>{const o=k<M.gens.length?s0.gen[k].p:(s0.newGen.find(x=>x.id===g.id)||{p:g.p}).p;d[g.b]+=g.p-o});s1.load.forEach((v,i)=>dl[i]=v-s0.load[i]);
  const marks=[...base.marks.map(m=>({...m}))],focus=[...base.focus];
  d.forEach((v,i)=>{if(Math.abs(v)>5){marks.push({b:i,k:v>0?'up':'dn',t:`${v>0?'증발 +':'감발 −'}${fmt(Math.abs(v))}`});focus.push(i)}});
  dl.forEach((v,i)=>{if(v<-5){marks.push({b:i,k:'cut',t:`차단 ${fmt(-v)}`});focus.push(i)}});return {lines:base.lines,marks,focus}}
function mid(idx){const xs=idx.map(i=>M.buses[i].x),ys=idx.map(i=>M.buses[i].y);return {x:xs.reduce((a,b)=>a+b,0)/xs.length,y:ys.reduce((a,b)=>a+b,0)/ys.length}}
function buildStory(base,s,evs,r,r0,alts,HL,label){const st=[];const evHLone=e=>{const t=cloneS(base);const x={...e};const h=evHL([e],s);return h};
  const causes=[];const byKey={};
  evs.forEach(e=>{let key,cap,foc,lines=[];
    if(e.k==='out'||e.k==='restore'||e.k==='add'){const l=allLines(s).find(x=>x.id===e.id);key='L'+l.id;foc=[l.f,l.t];lines=[l.id];cap=plain(evText(e,s)).replace(/\s*[A-Z]{2,}[-\w]*$/,'')}
    else if(e.k==='gen'){const g=M.gens[e.i];key='B'+g.b;foc=[g.b];cap=null}
    else{key='B'+e.b;foc=[e.b];cap=plain(evText(e,s))}
    if(!byKey[key]){byKey[key]={foc,lines,evs:[],caps:[]};causes.push(byKey[key])}byKey[key].evs.push(e);if(cap)byKey[key].caps.push(cap)});
  causes.forEach(c=>{const ge=c.evs.filter(e=>e.k==='gen');if(ge.length){const tr=ge.every(e=>e.trip),b=M.gens[ge[0].i].b,sb=ge.reduce((a,e)=>a+e.before,0),sa=ge.reduce((a,e)=>a+e.after,0);
    c.caps.unshift(`${nameOf(b)} ${ge.length>1?`발전기 ${ge.length}기 `:M.gens[ge[0].i].name+' '}${tr?'정지':'출력 변경'} (${fmt(sb)}→${fmt(sa)}MW)`)}});
  const rest=causes.slice(3);if(rest.length){causes.length=3;causes[2]={foc:causes[2].foc.concat(...rest.map(c=>c.foc)),lines:causes[2].lines.concat(...rest.map(c=>c.lines)),caps:[causes[2].caps[0]+` 외 ${rest.length}건`]}}
  const sev=r?viol(r).sort((a,b)=>ld(r,b)-ld(r,a)):[];let imp=sev.slice(0,3).map(l=>({l,k:'v'}));
  if(r&&!imp.length&&r0){const ds=live(r).map(l=>{const x=r0.lines.find(y=>y.id===l.id);return {l,d:x&&!isOut(r0,x)?ld(r,l)-ld(r0,x):0}}).filter(o=>o.d>=5).sort((a,b)=>b.d-a.d).slice(0,2);imp=ds.map(o=>({l:o.l,k:'d'}))}
  const allF=[...causes.flatMap(c=>c.foc),...imp.flatMap(o=>[o.l.f,o.l.t])];
  st.push({kind:'all',kindTxt:'전체',cap:`원인 ${causes.length}곳 · 영향 ${imp.length}곳${sev.length>3?` (위반 전체 ${sev.length}건)`:''}${alts.length?` · 대안 ${alts.length}개`:''}`,state:s,ref:base,hl:HL,focus:allF.length>1?allF:null,at:null});
  causes.forEach(c=>st.push({kind:'cause',kindTxt:'원인',cap:c.caps.join(' · '),state:s,ref:base,hl:HL,focus:c.foc,lines:c.lines,at:mid(c.foc)}));
  imp.forEach(o=>{const x=r0&&r0.lines.find(y=>y.id===o.l.id);const b0=x&&!isOut(r0,x)?Math.round(ld(r0,x)):null;
    st.push({kind:'impact',kindTxt:'영향',cap:`${lname(o.l)}${KV(o.l.kv)} ${b0!=null?b0+'% → ':''}${Math.round(ld(r,o.l))}%${o.k==='v'?` (정격 ${fmt(r.R[o.l.id])}MW 초과)`:' (위반 아님, 증가 폭 큼)'}`,state:s,ref:base,hl:HL,focus:[o.l.f,o.l.t],lines:[o.l.id],at:{x:(M.buses[o.l.f].x+M.buses[o.l.t].x)/2,y:(M.buses[o.l.f].y+M.buses[o.l.t].y)/2}})});
  alts.forEach((a,i)=>{const h=a.view.hl;const pts=h.marks.filter(m=>['up','dn','cut'].includes(m.k)).map(m=>m.b);
    st.push({kind:'remedy',kindTxt:`대안 ${String.fromCharCode(65+i)}`,cap:`${a.h} 적용 시 위반 해소 · ${a.p.split(' · 비용')[0].split(' · 당직')[0]}`,state:a.st,ref:s,hl:h,focus:pts.concat(sev.slice(0,2).flatMap(l=>[l.f,l.t])),lines:sev.slice(0,3).map(l=>l.id),at:pts.length?mid(pts):null})});
  return st}
const plain=h=>String(h).replace(/<[^>]+>/g,'').trim();
function viewBtn(v,label){views.push(v);return `<button class="btn vbtn" type="button" data-vi="${views.length-1}">${label||'계통도에서 보기'}</button>`}
document.addEventListener('click',e=>{const b=e.target.closest('.vbtn');if(!b)return;const v=views[+b.dataset.vi];if(!v)return;if(v.story){view=null;v.si=0;setView(v);playStory()}else setView(v,true)});
let ovRaf=0;function setVB(){$('sld').setAttribute('viewBox',`${vb.x} ${vb.y} ${vb.w} ${vb.h}`);if(SC&&!ovRaf)ovRaf=requestAnimationFrame(()=>{ovRaf=0;drawOverlay()})}
function n1Now(st){st=st||twin;if(n1Cache&&n1Cache.s===st)return n1Cache.v;const r=dcpf(st);const X=r?xmat(st):null;const v=n1fast(st,r,X).filter(c=>c.island||c.v.length);n1Cache={s:st,v};return v}
let SC=null;
function scaleK(){const s=$('sld'),rc=s.getBoundingClientRect();if(!rc.width||!vb)return M.w/600;return Math.max(vb.w/rc.width,vb.h/rc.height)}
const tw=(t,fz)=>{let w=0;for(const ch of String(t))w+=/[가-힣]/.test(ch)?1:/[0-9]/.test(ch)?.62:/[A-Za-z#]/.test(ch)?.62:/[%→·−+]/.test(ch)?.8:/\s/.test(ch)?.32:.6;return w*fz};
function drawSLD(resetView){SC=null;
  const svg=$('sld'),S0=shown(),r=dcpf(S0),hl=view&&view.hl||{};if(resetView||!vb){vb={x:0,y:0,w:M.w,h:M.h}}setVB();
  const lines=allLines(S0),B=M.buses,mode=M.id==='six'?'six':big()?'geo':'bar';svg.dataset.mode=mode;
  const rRef=view&&view.ref?dcpf(view.ref):null,dPct=l=>{if(!rRef||!r)return 0;const x=rRef.lines.find(y=>y.id===l.id);if(!x||isOut(rRef,x)||isOut(r,l))return 0;return ld(r,l)-ld(rRef,x)};
  const stepL=view&&view.story?new Set(view.story[view.si].lines||[]):new Set();const warnTh=big()?90:80;
  const hide=view&&view.hideFlows;const LS=lines.map(l=>{const out=r?isOut(r,l):false,part=(S0.out[l.id]||0)>0&&!out,p=r&&!out?ld(r,l):0,d=dPct(l);return {l,out,part,p,d,c:out?'out':hide?'':p>100?'a':p>=warnTh?'w':''}});
  const swpx=l=>mode==='geo'?(l.kv>=765?2.6:l.kv>=345?1.4:.8):mode==='bar'?2.2:2.6;
  const ln=(l,cls,w,extra='')=>`<line class="${cls}" vector-effect="non-scaling-stroke" style="stroke-width:${w}px" x1="${B[l.f].x}" y1="${B[l.f].y}" x2="${B[l.t].x}" y2="${B[l.t].y}"${extra}/>`;
  let h='<g id="gBase">';
  if(rRef)LS.forEach(o=>{if(Math.abs(o.d)<8)return;h+=ln(o.l,`dl ${o.d>0?'dup':'ddn'}`,(mode==='geo'?5:9)*Math.min(1.7,.6+Math.abs(o.d)/40))});
  if(hl.lines)LS.forEach(o=>{if(hl.lines.has(o.l.id))h+=ln(o.l,'halo',mode==='geo'?8:12)});
  LS.forEach(o=>{const l=o.l;h+=`<g class="line" tabindex="0" role="button" data-l="${l.id}"><title>${lname(l)}${KV(l.kv)}${l.cx.length>1?` ${remC(l,S0)}/${l.cx.length}회선`:''}${hide?'':` · 부하율 ${Math.round(o.p)}% · 정격 ${fmt(r?r.R[l.id]:0)}MW`}</title>${ln(l,'hit',14)}${ln(l,`ln ${o.c}${l.isNew?' nw':''}${o.part?' part':''}`,swpx(l)*(o.c==='a'?1.7:1))}</g>`});
  if(mode==='six')B.forEach((b,i)=>{const gs=gensOf(S0).filter(g=>g.b===i&&g.on),L=S0.load[i],below=b.y>200;
    h+=`<rect class="bus" x="${b.x-38}" y="${b.y-3}" width="76" height="6" rx="1"><title>${nameOf(i)}</title></rect>`;
    if(gs.length){const gy=below?b.y+30:b.y-30;h+=`<line class="stub" x1="${b.x-18}" y1="${b.y}" x2="${b.x-18}" y2="${below?gy-11:gy+11}"/><circle class="gen" cx="${b.x-18}" cy="${gy}" r="11"/><path class="gw" d="M${b.x-25} ${gy} q3.5 -6 7 0 t7 0"/>`}
    if(L){const lx=b.x+18,ly=below?b.y+18:b.y-18;h+=`<line class="stub" x1="${lx}" y1="${b.y}" x2="${lx}" y2="${ly}"/><path class="ld" d="M${lx-6} ${ly} L${lx+6} ${ly} L${lx} ${below?ly+9:ly-9}Z"/>`}});
  h+='</g><g id="gOver"></g>';svg.innerHTML=h;
  svg.querySelectorAll('g.line').forEach(g=>{const f=()=>{if(dragMoved)return;const l=allLines(S0).find(x=>x.id===g.dataset.l);$('q').value=`${M.buses[l.f].n}–${M.buses[l.t].n} 선로가 고장 나면 어떻게 돼?`;$('q').focus()};
    g.addEventListener('click',f);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();f()}})});
  const evB=new Set();LS.forEach(o=>{if(o.out||o.part||o.p>=90||o.l.isNew||stepL.has(o.l.id)){evB.add(o.l.f);evB.add(o.l.t)}});(hl.marks||[]).forEach(m=>evB.add(m.b));
  SC={mode,S0,r,LS,hl,stepL,evB,warnTh,hide,gb:new Set(gensOf(S0).filter(g=>g.on&&g.p>0.5).map(g=>g.b))};drawOverlay();
  const tl=S0.load.reduce((a,b)=>a+b,0),tg=gensOf(S0).filter(g=>g.on).reduce((a,g)=>a+g.p,0)+(S0.ren||[]).reduce((a,b)=>a+b,0);const mx=r?Math.max(...live(r).map(l=>ld(r,l))):0;
  $('kLoad').textContent=fmt(tl)+' MW';$('kGen').textContent=fmt(tg)+' MW';$('kMax').textContent=r?Math.round(mx)+'%':'분리';$('kMax').style.color=mx>100?'var(--alarm)':mx>=90?'var(--warn)':'';
  const nv=r?n1Now(S0).length:0;$('kN1').textContent=nv+'건';$('kN1').style.color=nv?'var(--warn)':'';
  $('inj').innerHTML=allLines(twin).filter(l=>remC(l,twin)>0).map(l=>[l,lname(l)]).sort((a,b)=>a[1].localeCompare(b[1],'ko')).map(([l,n])=>`<option value="${l.id}">${n}${KV(l.kv)}</option>`).join('');
  $('legend').hidden=mode!=='geo';drawAlarms(dcpf(twin));drawBanner();if(typeof dashLive==='function')dashLive()}
function drawOverlay(){if(!SC)return;const g=$('gOver');if(!g)return;const {mode,S0,r,LS,hl,stepL,evB,gb,warnTh,hide}=SC,B=M.buses,k=scaleK(),P=v=>v*k;
  const boxes=[],pad=P(2);let h='';
  const hit=b=>boxes.some(o=>b.x<o.x+o.w+pad&&b.x+b.w+pad>o.x&&b.y<o.y+o.h+pad&&b.y+b.h+pad>o.y);
  const place=(cs,force)=>{for(const c of cs)if(!hit(c)){boxes.push(c);return c}if(force){boxes.push(cs[0]);return cs[0]}return null};
  const box=(cx,cy,w,hh)=>({x:cx-w/2,y:cy-hh/2,w,h:hh,cx,cy});
  // 모선 도형
  const nr=i=>mode==='geo'?P(B[i].kv>=765?4.6:B[i].kv>=345?3.5:2.7):mode==='bar'?P(4):P(6);
  if(mode==='geo')B.forEach((b,i)=>{const R=nr(i);h+=`<circle class="${gb.has(i)?'gnode':'lnode'}" vector-effect="non-scaling-stroke" cx="${b.x}" cy="${b.y}" r="${R}"><title>${nameOf(i)}${KV(b.kv)}</title></circle>`;boxes.push(box(b.x,b.y,R*2,R*2))});
  if(mode==='bar')B.forEach((b,i)=>{const w=P(24),hh=P(5);h+=`<rect class="bus" x="${b.x-w/2}" y="${b.y-hh/2}" width="${w}" height="${hh}" rx="${P(1)}"><title>${nameOf(i)}</title></rect>`;boxes.push(box(b.x,b.y,w,hh));
    if(gb.has(i)){const gy=b.y-P(11);h+=`<circle class="gen" vector-effect="non-scaling-stroke" cx="${b.x}" cy="${gy}" r="${P(6)}"/>`;boxes.push(box(b.x,gy,P(12),P(12)))}});
  if(mode==='six')B.forEach((b,i)=>{const below=b.y>200,ny=below?b.y+64:b.y-62;boxes.push(box(b.x,b.y,80,8));boxes.push(box(b.x-18,below?b.y+30:b.y-30,24,24));
    const gs=gensOf(S0).filter(x=>x.b===i&&x.on),L=S0.load[i],meta=gs.map(x=>`${x.name} ${Math.round(x.p)}MW`).concat(L?[`부하 ${L}MW`]:[]);
    h+=`<text class="bn" x="${b.x}" y="${ny}" text-anchor="middle">${b.n}</text>`;boxes.push(box(b.x,ny-4,tw(b.n,13)+4,16));
    if(meta.length){const t=meta.join(' · ');h+=`<text class="bm" x="${b.x}" y="${ny+15}" text-anchor="middle">${t}</text>`;boxes.push(box(b.x,ny+11,tw(t,11)+4,14))}});
  // 번호 배지
  if(view&&view.story)view.story.forEach((st,i)=>{if(!i||!st.at)return;const R=P(10),d=P(17),cur=i===view.si;
    const cs=[[-1,-1],[1,-1],[-1,1],[1,1],[0,-1.5],[0,1.5]].map(([a,b2])=>box(st.at.x+a*d,st.at.y+b2*d,R*2,R*2));const c=place(cs,true);
    h+=`<g class="sbadge${cur?' cur':''} sk-${st.kind}" data-si="${i}" role="button" tabindex="0"><title>${esc(st.cap)}</title><circle vector-effect="non-scaling-stroke" cx="${c.cx}" cy="${c.cy}" r="${R}"/><text x="${c.cx}" y="${c.cy+R*.4}" text-anchor="middle" style="font-size:${R*1.15}px">${i}</text></g>`});
  // 증감 표시
  (hl.marks||[]).forEach(m=>{const b=B[m.b],R=P(mode==='geo'?9:14),fz=P(11.5),w=tw(m.t,fz)+fz*.9,hh=fz*1.65;h+=`<circle class="mk mk-${m.k}" vector-effect="non-scaling-stroke" cx="${b.x}" cy="${b.y}" r="${R}"/>`;
    const cs=[box(b.x,b.y-R-hh/2-P(3),w,hh),box(b.x,b.y+R+hh/2+P(3),w,hh),box(b.x+R+w/2+P(4),b.y,w,hh),box(b.x-R-w/2-P(4),b.y,w,hh)];const c=place(cs,true);
    h+=`<g class="mtag mk-${m.k}" pointer-events="none"><rect vector-effect="non-scaling-stroke" x="${c.x}" y="${c.y}" width="${w}" height="${hh}" rx="${P(3)}"/><text x="${c.cx}" y="${c.cy+fz*.36}" text-anchor="middle" style="font-size:${fz}px">${m.t}</text></g>`});
  // 선로 부하율 표시
  const tags=LS.map(o=>{const l=o.l;if(hide&&!o.out&&!o.part)return null;const pri=o.out||o.part||o.p>100||l.isNew||stepL.has(l.id)?2:(mode!=='geo'&&(mode==='six'||B.length<=14))||o.p>=warnTh?1:0;if(!pri)return null;
    const showBA=view&&view.ref&&!o.out&&(o.p>100||stepL.has(l.id))&&Math.abs(o.d)>=1;const t=o.out?'탈락':hide?`${remC(l,S0)}/${l.cx.length}회선`:(o.part?`${remC(l,S0)}/${l.cx.length} `:'')+(l.isNew?'신설 ':'')+(showBA?`${Math.round(o.p-o.d)}→`:'')+Math.round(o.p)+'%';
    return {o,pri,t,sev:o.out?999:o.p}}).filter(Boolean).sort((a,b)=>b.pri-a.pri||b.sev-a.sev);
  tags.forEach(T=>{const l=T.o.l,a=B[l.f],b=B[l.t],fz=P(mode==='six'?11:10.5),w=tw(T.t,fz)+fz*.9,hh=fz*1.65,dx=b.x-a.x,dy=b.y-a.y,L=Math.hypot(dx,dy)||1,nx=-dy/L,ny=dx/L;
    const cs=[];for(const t of [.5,.38,.62,.28,.72])for(const off of [0,1,-1])cs.push(box(a.x+dx*t+nx*off*(hh*.9),a.y+dy*t+ny*off*(hh*.9),w,hh));
    const c=place(cs,T.pri===2);if(!c)return;const cl=T.o.out?'':T.o.p>100?'a':T.o.p>=warnTh?'w':'';
    h+=`<g class="tag ${cl}" pointer-events="none"><rect vector-effect="non-scaling-stroke" x="${c.x}" y="${c.y}" width="${w}" height="${hh}" rx="${P(3)}"/><text x="${c.cx}" y="${c.cy+fz*.36}" text-anchor="middle" style="font-size:${fz}px">${T.t}</text></g>`});
  // 모선 이름
  if(mode!=='six'){const zoomed=vb.w<M.w*.55;const order=B.map((b,i)=>i).filter(i=>mode==='bar'||zoomed||B[i].kv>=765||evB.has(i)).sort((x,y)=>(evB.has(y)-evB.has(x))||(B[y].kv-B[x].kv));
    order.forEach(i=>{const b=B[i],fz=P(mode==='geo'?(evB.has(i)?12:11):12),t=b.n,w=tw(t,fz)+P(2),hh=fz*1.25,R=nr(i)+P(3);
      const cs=[box(b.x+R+w/2,b.y,w,hh),box(b.x-R-w/2,b.y,w,hh),box(b.x,b.y-R-hh/2,w,hh),box(b.x,b.y+R+hh/2,w,hh),box(b.x+R+w/2,b.y-hh*.8,w,hh),box(b.x-R-w/2,b.y+hh*.8,w,hh)];
      const c=place(cs,false);if(!c)return;h+=`<text class="bn ${mode==='geo'?'geo':''}${evB.has(i)?' ev':''}" x="${c.cx}" y="${c.cy+fz*.36}" text-anchor="middle" style="font-size:${fz}px;stroke-width:${P(2.6)}px">${t}</text>`})}
  g.innerHTML=h;
  g.querySelectorAll('g.sbadge').forEach(x=>{const f=()=>{stopPlay();goStep(+x.dataset.si)};x.addEventListener('click',e=>{e.stopPropagation();f()});x.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();f()}})})}
function drawAlarms(r){const items=[];if(typeof TL!=='undefined'&&TL.on)forecasts().slice(0,4).forEach(x=>items.push(['f',`${x.k}시간 뒤 ${pad(x.h)}:00 ${lname(x.l)} ${Math.round(x.p)}% 예상`]));
  Object.entries(twin.out).forEach(([id,n])=>{const l=allLines(twin).find(x=>x.id===id);items.push(['a',`${lname(l)}${KV(l.kv)} 선로 ${l.cx.length>1?(n>=l.cx.length?'전 회선 ':n+'회선 '):''}트립`])});
  gensOf(twin).forEach((g,i)=>{const sch=typeof TL!=='undefined'&&TL.on?(M.profile?M.profile.hours[TL.h].gen[i]>0.5:M.gens[i].on):M.gens[i]&&M.gens[i].on;if(i<M.gens.length&&sch&&!g.on)items.push(['a',`${g.name} 정지`])});
  if(!r)items.push(['a','계통 분리 발생']);else{
    viol(r).sort((a,b)=>ld(r,b)-ld(r,a)).forEach(l=>items.push(['a',`${lname(l)} 선로 과부하 ${Math.round(ld(r,l))}%`]));
    live(r).filter(l=>ld(r,l)>=90&&ld(r,l)<=100).sort((a,b)=>ld(r,b)-ld(r,a)).slice(0,6).forEach(l=>items.push(['w',`${lname(l)} 선로 부하율 ${Math.round(ld(r,l))}%`]));
    const nv=n1Now(twin);if(nv.length)items.push(['i',`N-1 위반 사고 ${nv.length}건 (예방 재급전 검토)`])}
  items.forEach(it=>{if(!alarmsT[it[1]])alarmsT[it[1]]=now()});
  $('almCnt').textContent=items.length?`${items.length}건`:'';
  $('alarms').innerHTML=items.length?items.map(([c,t])=>`<li><span class="t">${alarmsT[t]}</span><span>${esc(t)}</span><span class="lv lv-${c}">${c==='a'?'경보':c==='w'?'주의':c==='f'?'예보':'정보'}</span></li>`).join(''):'<li class="empty" style="display:block">발생 중인 경보가 없습니다.</li>'}
/* 확대·이동 */
let drag=null,dragMoved=false;
function svgPt(e){const s=$('sld'),rc=s.getBoundingClientRect();return {x:vb.x+(e.clientX-rc.left)/rc.width*vb.w,y:vb.y+(e.clientY-rc.top)/rc.height*vb.h}}
function zoom(f,cx,cy){const nw=Math.min(M.w*1.2,Math.max(M.w/8,vb.w*f)),k=nw/vb.w;cx=cx??vb.x+vb.w/2;cy=cy??vb.y+vb.h/2;vb={x:cx-(cx-vb.x)*k,y:cy-(cy-vb.y)*k,w:nw,h:vb.h*k};setVB()}

/* ---------- 게이트웨이 ---------- */
function logCall(tool,inp,res,ms){calls[tool]=(calls[tool]||0)+1;logs.unshift({t:now(),tr:curTrace,tool,inp,res,ms});
  $('logCnt').textContent=logs.length+'건';
  $('log').innerHTML=logs.slice(0,300).map(x=>`<div><span class="s">${x.t}</span><span class="s">${x.tr}</span><span class="k">${x.tool}</span><span class="e" title="${esc(x.inp+' → '+x.res)}">${esc(x.inp)} → ${esc(x.res)}</span><span class="s">${x.ms}</span></div>`).join('');drawReg()}
const SCH={"get_model_catalog": "model_id → buses[], lines[], gens[]", "get_measurements": "snapshot? → P·Q·V 측정값[]", "identify_equipment": "text → [{type, mrid, 후보}]", "create_snapshot": "time → snapshot_id", "build_scenario": "snapshot_id, events[] → scenario_id", "run_power_flow": "scenario_id → flows[], 위반[]", "run_contingency": "scenario_id, outage → flows[], 위반[] (job_id)", "run_n1_screening": "scenario_id → 위반 사고[] (job_id)", "run_redispatch_opf": "scenario_id → 감발·증발[], 비용, 검증 (job_id)", "eval_load_shedding": "scenario_id → 차단[], 검증", "search_procedure": "query, filters → chunks[] (출처·버전)", "save_workspace": "events[], action, 승인자 → 반영 결과"};
function drawReg(){$('toolCnt').textContent=`${TOOLS.length}개 등록`;
  $('reg').innerHTML='<tr><th>툴</th><th>입력 → 출력</th><th>버전</th><th>권한</th><th>실행</th><th>호출</th></tr>'+TOOLS.map(([n,v,d,p,e])=>{const [i,o]=(SCH[n]||'- → -').split(' → ');return `<tr><td><code>${n}</code><div class="d">${d}</div></td><td class="schema"><b>${esc(i)}</b><br>→ ${esc(o)}</td><td>${v}</td><td><span class="perm ${PERM[p][1]}">${PERM[p][0]}</span></td><td>${e}</td><td>${calls[n]||0}</td></tr>`}).join('')}

/* ---------- 대화 ---------- */
function msgQ(t){const d=document.createElement('div');d.className='q';d.textContent=t;$('msgs').appendChild(d);scroll()}
function msgA(){const d=document.createElement('div');d.className='a';d.innerHTML='<ul class="steps"></ul><div class="ans"></div>';$('msgs').appendChild(d);scroll();return d}
function scroll(){const m=$('msgs');m.scrollTop=m.scrollHeight}
async function step(box,label,tool,inp,fn){const li=document.createElement('li');li.className='run';li.innerHTML=`<span class="ic"></span><span>${label} <code>${tool||''}</code></span><span class="ms"></span>`;
  box.querySelector('.steps').appendChild(li);scroll();await sleep(160);const t0=performance.now();const out=fn();const ms=Math.max(0.01,(performance.now()-t0)/1000).toFixed(2)+'s';
  li.className=out&&out.fail?'fail':'done';li.querySelector('.ms').textContent=ms;if(tool)logCall(tool,inp,out&&out.log?out.log:'ok',ms);return out}
function setChips(a){$('chips').innerHTML=a.map(x=>`<button class="chip" type="button">${esc(x)}</button>`).join('');$('chips').querySelectorAll('.chip').forEach(c=>c.onclick=()=>ask(c.textContent))}
const qName=i=>M.naming==='num'?`${M.buses[i].n}번 모선`:M.buses[i].n;
const unitQ=g=>M.naming==='num'?g.id:g.name.replace(/\s*#(\d+)$/,' $1호기');
function examples(){const s=BASE,r=dcpf(s),X=xmat(s);const cs=n1fast(s,r,X).filter(c=>!c.island);
  const sev=c=>c.v.length?Math.max(...c.v.map(l=>ld(c.r,l))):Math.max(...live(c.r).map(l=>ld(c.r,l)));cs.sort((a,b)=>sev(b)-sev(a));
  const L1=cs[0].l,L2=(cs.find(c=>c.l!==L1&&c.l.cx.length>1)||cs[1]).l,L3=cs.find(c=>c.l!==L1&&c.l!==L2).l,L4=cs.find(c=>![L1,L2,L3].includes(c.l)).l;
  const on=M.gens.map((g,i)=>({g,i})).filter(o=>s.gen[o.i].on&&s.gen[o.i].p>0);
  const biggest=on.slice().sort((a,b)=>s.gen[b.i].p-s.gen[a.i].p)[0];
  const flex=on.filter(o=>o.g.type!=='원자력'&&o.g.max-s.gen[o.i].p>=60).sort((a,b)=>(b.g.max-s.gen[b.i].p)-(a.g.max-s.gen[a.i].p))[0]||on[0];
  const loads=s.load.map((v,i)=>({v,i})).filter(o=>o.v>0).sort((a,b)=>b.v-a.v);
  const deg=i=>allLines(s).filter(l=>l.f===i||l.t===i).length;
  const ambB=loads.map(o=>o.i).find(i=>deg(i)>=2)??0;
  let none=null,best=1e9;for(let i=0;i<M.buses.length;i++)for(let j=i+1;j<M.buses.length;j++){if(corridorBetween(s,i,j))continue;const d=Math.hypot(M.buses[i].x-M.buses[j].x,M.buses[i].y-M.buses[j].y);if(d<best&&M.buses[i].kv===M.buses[j].kv){best=d;none=[i,j]}}
  const ln=l=>`${M.buses[l.f].n}–${M.buses[l.t].n}`;const flexV=Math.min(flex.g.max,Math.round((s.gen[flex.i].p+60)/10)*10);
  return {L1,L2,L3,L4,biggest,flex,flexV,loads,ambB,none,ln}}
function chipsDefault(){const E=examples();setChips([`${E.ln(E.L1)} 선로가 고장 나면?`,`${E.ln(E.L3)} 선로 휴전하고 ${unitQ(E.flex.g)} 출력 ${E.flexV}MW로 올리면?`,`${unitQ(E.biggest.g)} 트립되면?`,'N-1 상정사고 전체 점검해줘'])}
function parse(q){const t=q.replace(/\s+/g,' ');
  if(/훈련/.test(t))return {k:'train'};
  if(/보고서|운영\s*일지|상황\s*보고|보고\s*써|요약\s*보고/.test(t))return {k:'report'};
  if(/예방|선제|사전\s*조치|위반.*(줄|해소)|N-?1.*(해소|줄|개선|확보)/i.test(t)){const hm=t.match(/(\d{1,2})\s*시/);return {k:'prev',hour:hm?Math.min(23,+hm[1]):null}}
  if(/n-?1|상정사고|전체.*(점검|스크리닝)|안전도/i.test(t)&&!/고장|탈락|신설|트립|정지/.test(t))return {k:'n1'};
  if(/절차|규정|매뉴얼|어떻게 해야|기준/.test(t)&&!/MW|선로|발전|부하/i.test(t))return {k:'doc',t};
  const chain=/그 상태|거기서|거기에|이어서|그 다음|그다음|추가로 더|그 계통/.test(t)&&lastScen;
  const commit=/발생했|났어|났습니다|됐어|되었|반영해|적용해|들어갔/.test(t);const base=chain?lastScen.state:twin;
  const pe=parseEvents(t,base);if(pe.evs.length||pe.issues.length)return {k:'scen',...pe,chain,commit,base};
  if(/현재|지금|상태|과부하|경보|위험/.test(t))return {k:'status'};return {k:'unk'}}
let busy=false;
async function ask(q){if(busy||!q.trim())return;busy=true;msgQ(q);$('q').value='';curTrace='T-'+String(++traceSeq).padStart(3,'0');GWQ[curTrace]=q;
  const it=parse(q);if(it.k==='train'){busy=false;trainStart();return}addJN('query',q);if(['scen','status','n1','prev'].includes(it.k))snapPush('판단',q,it.base||twin,curTrace);
  const box=msgA(),ans=box.querySelector('.ans');await step(box,'질의 분류',null,'',()=>({}));
  try{if(it.k==='prev')await doPrevent(box,ans,q,it.hour);else if(it.k==='report')await doReport(box,ans,q);else if(it.k==='scen')await doScen(box,ans,it,q);else if(it.k==='status')await doStatus(box,ans,q);else if(it.k==='n1')await doN1(box,ans,q);else if(it.k==='doc')await doDoc(box,ans,it.t,q);
    else{ans.innerHTML=`<p>어떤 설비나 상황을 말씀하시는지 확정하지 못했습니다. ${M.naming==='num'?'“16–17 선로”, “30번 발전기”, “39번 모선 부하”':'“양평–태백 선로”, “신보령화력 1호기”, “서울 부하”'}처럼 설비를 함께 적어 주세요.</p>`;chipsDefault()}}
  finally{busy=false;scroll()}}
function tbl(rows,h){return `<table class="tbl"><tr>${h.map((x,i)=>`<th${i>=h.length-2?' style="text-align:right"':''}>${x}</th>`).join('')}</tr>${rows.map(r=>`<tr>${r.map((c,i)=>`<td class="${i>=r.length-2?'n':''} ${typeof c==='object'?c.cls:''}">${typeof c==='object'?c.v:esc(c)}</td>`).join('')}</tr>`).join('')}</table>`}
const pc=(v,hot)=>({v:Math.round(v)+'%',cls:hot?(v>100?'hot':v>=90?'wm':''):''});
function evList(evs,s){const out=[],gg={};evs.forEach(e=>{if(e.k==='gen'&&!e.trip){const p=M.gens[e.i].plant;(gg[p]=gg[p]||[]).push(e)}else out.push(`<li>${evText(e,s)}</li>`)});
  Object.entries(gg).forEach(([p,es])=>{if(es.length===1)out.push(`<li>${evText(es[0],s)}</li>`);else out.push(`<li>${M.naming==='num'?es.map(e=>M.gens[e.i].name).join(', '):p} ${es.length}기 출력 합계 ${fmt(es.reduce((a,e)=>a+e.before,0))}→${fmt(es.reduce((a,e)=>a+e.after,0))}MW</li>`)});return out.join('')}
function rdText(rd,s0){const d=new Array(M.buses.length).fill(0);gensOf(rd.state).forEach((g,k)=>{const o=k<M.gens.length?s0.gen[k].p:(s0.newGen.find(x=>x.id===g.id)||{p:g.p}).p;d[g.b]+=g.p-o});
  const dn=d.map((v,i)=>({v,i})).filter(o=>o.v<-5).sort((a,b)=>a.v-b.v),up=d.map((v,i)=>({v,i})).filter(o=>o.v>5).sort((a,b)=>b.v-a.v);const tot=up.reduce((a,o)=>a+o.v,0);
  const ls=a=>a.slice(0,3).map(o=>`${nameOf(o.i)} ${fmt(Math.abs(o.v))}MW`).join(', ')+(a.length>3?` 외 ${a.length-3}곳`:'');return `감발 ${ls(dn)} · 증발 ${ls(up)} · 총 ${fmt(tot)}MW · 비용 ${rd.cost>=0?'+':''}${fmt(rd.cost)}천원/h`}
function shText(sh){return `${sh.cuts.slice(0,3).map(c=>`${nameOf(c.b)} ${fmt(c.mw)}MW`).join(', ')}${sh.cuts.length>3?` 외 ${sh.cuts.length-3}곳`:''} 차단 · 총 ${fmt(sh.total)}MW · 당직 책임자 승인 필요`}
async function doStatus(box,ans,q){const sid=snapId();
  await step(box,'측정값 조회','get_measurements','all',()=>({log:`${allLines(twin).length} corridors`}));
  const r=await step(box,'조류계산','run_power_flow',sid,()=>{const r=dcpf(twin);if(!r)return {fail:true,log:'islanding'};r.log='converged';return r});
  if(r.fail){ans.innerHTML='<p>현재 계통이 분리되어 있습니다.</p>';return}
  const v=viol(r),top=live(r).sort((a,b)=>ld(r,b)-ld(r,a)).slice(0,6);setView({state:twin,live:true,label:'현 계통 · 부하율 상위 선로 강조',hl:{lines:new Set(top.map(l=>l.id)),marks:[],focus:top.flatMap(l=>[l.f,l.t])}},true);
  ans.innerHTML=`<p>${v.length?`현재 과부하 설비가 ${v.length}건 있습니다. 15분 안에 해소가 필요합니다.`:'현재 정격을 넘는 설비는 없습니다.'}</p><div class="lab">부하율 상위 선로</div>${tbl(top.map(l=>[lname(l)+(l.kv?`${KV(l.kv)}`:''),{v:l.eq,cls:'mono'},fmt(r.R[l.id])+'MW',pc(ld(r,l),1)]),['선로','설비 ID','정격','부하율'])}<div class="badges"><span class="bd">수렴 확인</span><span class="bd">스냅샷 ${sid}</span></div>${fbHtml()}`;
  wireFb(box,q,null);const E=examples();setChips(v.length?[`${E.ln(v[0])} 선로 과부하 어떻게 해소해?`,'N-1 상정사고 전체 점검해줘']:[`${E.ln(E.L1)} 선로가 고장 나면?`,'N-1 상정사고 전체 점검해줘'])}
async function doScen(box,ans,it,q){const base=it.base;
  await step(box,'이벤트·설비 식별','identify_equipment',q.slice(0,40),()=>({fail:!it.evs.length||it.evs.some(e=>e.k==='amb'),log:`${it.evs.length} events`}));
  const amb=it.evs.find(e=>e.k==='amb');
  if(amb){const c=allLines(base).filter(l=>(l.f===amb.a||l.t===amb.a)&&remC(l,base)>0);ans.innerHTML=`<p>${nameOf(amb.a)}에 연결된 선로가 ${c.length}개라 하나로 확정하지 못했습니다. 어느 선로인지 골라 주세요.</p>`;
    setChips(c.slice(0,6).map(l=>`${M.buses[l.f].n}–${M.buses[l.t].n} 선로가 고장 나면?`));return}
  if(!it.evs.length){ans.innerHTML=`<p>${it.issues.map(esc).join(' ')}</p>`;chipsDefault();return}
  {const ev0=it.evs.map(e=>({...e}));const tmp=cloneS(base);ev0.forEach(e=>applyEv(tmp,e));setView({state:base,live:!it.chain,label:it.chain?`${curTrace} 직전 가정 위에 설비 식별 중`:`${curTrace} 대상 설비 식별 중`,hl:evHL(ev0,tmp)},true)}
  const sid=snapId();await step(box,'스냅샷 고정','create_snapshot',it.chain?'last scenario':'now',()=>({log:sid}));
  const s=cloneS(base);let left=0;
  await step(box,'시나리오 구성','build_scenario',`${it.evs.length} events`,()=>{it.evs.forEach(e=>applyEv(s,e));left=balance(s);return {log:Math.abs(left)>1?`reserve short ${Math.round(left)}MW`:'balanced'}});
  const HL=evHL(it.evs,s);const scenView={state:s,ref:base,label:`${curTrace} 가정 시나리오 (현 계통 아님)`,hl:HL};setView(scenView,true);
  const single=it.evs.length===1&&it.evs[0].k==='out';const r0=dcpf(base);
  const r=await step(box,single?'상정사고 해석':'조류계산',single?'run_contingency':'run_power_flow',sid,()=>{const r=dcpf(s);if(!r)return {fail:true,log:'islanding'};r.log=`${viol(r).length} violations`;return r});
  const evHtml=`<div class="lab">${it.chain?'직전 가정 상태에 더해 ':'현 계통 기준으로 '}이렇게 가정했습니다</div><ol class="evs">${evList(it.evs,s)}</ol>${it.issues.length?`<p class="iss">${it.issues.map(esc).join(' ')}</p>`:''}`;
  if(r.fail){ans.innerHTML=`${evHtml}<p>이 조건에서는 계통이 분리되어 해석이 수렴하지 않습니다. 분리된 지역의 수급을 따로 검토해야 하므로 관제사 판단이 필요합니다.</p>${fbHtml()}`;wireFb(box,q,null);return}
  lastScen={state:s,evs:it.evs};const v=viol(r).sort((a,b)=>ld(r,b)-ld(r,a));addJN('scenario',`${it.evs.map(e=>plain(evText(e,s)).replace(/\s+[A-Z]{2,}[-\w]*$/,'')).slice(0,3).join(', ')} 검토 → ${v.length?`위반 ${v.length}건 (${v.slice(0,2).map(l=>`${lname(l)} ${Math.round(ld(r,l))}%`).join(', ')})`:'위반 없음'}`);
  if(v.length){v.slice(0,6).forEach(l=>HL.focus.push(l.f,l.t));setView(scenView,true)}
  const startStory=alts=>{scenView.story=buildStory(base,s,it.evs,r,r0,alts,HL);scenView.si=0;view=null;setView(scenView);playStory()};
  const before=l=>{const x=r0&&r0.lines.find(y=>y.id===l.id);return x&&!isOut(r0,x)?pc(ld(r0,x),0):{v:l.isNew?'신설':'탈락',cls:''}};
  const resv=Math.abs(left)>1?`<span class="bd w">예비력 부족 ${fmt(Math.abs(left))}MW</span>`:'';
  const hasGenTrip=it.evs.some(e=>e.k==='gen'&&e.trip);
  if(!v.length){const doc=await step(box,'절차서 검색','search_procedure','관련 절차',()=>({...(hasGenTrip?DOCS[5]:it.evs.some(e=>e.k==='add')||/휴전|작업/.test(q)?DOCS[4]:DOCS[2]),log:'1 hit'}));
    const top=live(r).sort((a,b)=>ld(r,b)-ld(r,a)).slice(0,4);
    ans.innerHTML=`${evHtml}<p>이 조건에서는 정격을 넘는 설비가 없습니다.${Math.abs(left)>1?` 다만 수급을 맞출 예비력이 ${fmt(Math.abs(left))}MW 부족합니다.`:''}</p><div class="lab">부하율 상위 선로</div>${tbl(top.map(l=>[lname(l)+(l.isNew?' (신설)':''),(l.kv?l.kv+'kV':'-'),before(l),pc(ld(r,l),1)]),['선로','전압',it.chain?'직전 가정':'현재','시나리오'])}
      <div class="cite"><b>${doc.id}</b> ${doc.t}: ${doc.b}</div>${kbRefs(hasGenTrip?['c-trip']:['c-out','c-n1'])}${noteRefs(HL.focus)}<div class="badges"><span class="bd">수렴 확인</span><span class="bd">설비 ID 확정</span>${resv}<span class="bd">스냅샷 ${sid}</span></div><div class="vrow">${viewBtn(scenView,'시나리오 계통도에서 보기')}</div>${fbHtml(true,null,it.commit)}`;
    wireFb(box,q,{evs:it.evs,state:s,alt:null});startStory([]);const E=examples();setChips([`그 상태에서 ${E.ln(E.L4)} 선로도 탈락하면?`,'현재 과부하 설비 있어?']);return}
  const rd=await step(box,'재급전 계산','run_redispatch_opf',`${sid}, ${v.length} violations`,()=>{const x=redispatch(s);return x?{...x,log:`${x.moves.length} moves, ${Math.round(x.total)}MW`}:{fail:true,log:'infeasible'}});
  const sh=await step(box,'부하 차단량 계산','eval_load_shedding',`${sid}`,()=>{const x=shed(s);return x?{...x,log:`${Math.round(x.total)}MW`}:{fail:true,log:'infeasible'}});
  const docs=await step(box,'절차서 검색','search_procedure','선로 과부하 조치',()=>{const l=rd.fail&&sh.fail?[DOCS[0],DOCS[3]]:sh.fail?[DOCS[0]]:[DOCS[0],DOCS[1]];if(/휴전|작업/.test(q))l.push(DOCS[4]);if(hasGenTrip)l.push(DOCS[5]);return {list:l,log:l.length+' hits'}});
  await step(box,'결과 검증','',' ',()=>({}));
  const alts=[];if(!rd.fail)alts.push({h:'발전 재급전',p:rdText(rd,s),st:rd.state});if(!sh.fail)alts.push({h:'부하 차단',p:shText(sh),st:sh.state});
  alts.forEach((a,i)=>{a.view={state:a.st,ref:s,label:`${curTrace} 가정 시나리오 + 대안 ${String.fromCharCode(65+i)} ${a.h} (현 계통 아님)`,hl:remHL(s,a.st,HL)}});
  setView(scenView,false);
  const what=single?`${lname(allLines(s).find(l=>l.id===it.evs[0].id))} 선로가 탈락하면`:'이 조건에서는';
  const head=alts.length?`${what} ${v.length}개 선로가 정격을 넘습니다. ${alts[0].h==='발전 재급전'?'발전 재급전으로 모든 위반이 해소되므로 이를 우선 권고합니다.':'재급전만으로는 해소되지 않아 부하 차단이 필요합니다.'}`:`${what} ${v.length}개 선로가 정격을 넘고, 재급전이나 부하 차단으로도 해소되지 않습니다. 자동 대안이 없으므로 관제사 판단으로 넘깁니다.`;
  ans.innerHTML=`${single?'':evHtml}<p>${head}</p><div class="lab">예상 위반 설비${v.length>8?` (상위 8건 / 전체 ${v.length}건)`:''}</div>${tbl(v.slice(0,8).map(l=>[lname(l),(l.kv?l.kv+'kV':'-'),before(l),pc(ld(r,l),1)]),['선로','전압',it.chain?'직전 가정':'현재',single?'고장 후':'시나리오'])}
    ${alts.length?`<div class="lab">대응 대안</div><div class="alts">${alts.map((a,i)=>`<div class="alt ${i===0?'rec':''}">${i===0?'<span class="r">추천</span>':''}<h4>${String.fromCharCode(65+i)}. ${a.h}</h4><p>${a.p}</p><div class="altv">${viewBtn(a.view,'적용 결과 계통도에서 보기')}</div></div>`).join('')}</div>`:''}
    ${docs.list.map(d=>`<div class="cite"><b>${d.id}</b> ${d.t}: ${d.b}</div>`).join('')}${kbRefs(['c-ol','c-rd'])}${noteRefs(HL.focus)}
    <div class="badges"><span class="bd">수렴 확인</span><span class="bd">설비 ID 확정</span>${alts.length?'<span class="bd">대안 사후 검증 통과</span>':'<span class="bd w">자동 대안 없음</span>'}${resv}<span class="bd">스냅샷 ${sid}</span></div><div class="vrow">${viewBtn(scenView,'시나리오 계통도에서 보기')}${v[0]?` <button type="button" class="btn vbtn" data-kg="line:${v[0].id}">관련 지식그래프</button>`:''}</div>${fbHtml(true,alts[0],it.commit)}`;
  wireFb(box,q,{evs:it.evs,state:s,alt:alts[0]||null});startStory(alts);const E=examples();setChips([`그 상태에서 ${qName(E.loads[0].i)} 부하 100MW 늘어나면?`,'현재 과부하 설비 있어?','N-1 상정사고 전체 점검해줘'])}
async function doN1(box,ans,q){const sid=snapId();
  await step(box,'스냅샷 고정','create_snapshot','now',()=>({log:sid}));
  const res=await step(box,'N-1 스크리닝','run_n1_screening',`${sid}`,()=>{const r=dcpf(twin);const x=n1fast(twin,r,r?xmat(twin):null);return {x,n:x.length,log:`${x.length} cases`}});
  const doc=await step(box,'절차서 검색','search_procedure','N-1 기준',()=>({...DOCS[2],log:DOCS[2].id}));
  const bad=res.x.filter(c=>c.island||c.v.length);{const ls=new Set(bad.map(c=>c.l.id));setView({state:twin,live:true,label:`현 계통 · N-1 위반 사고 선로 ${ls.size}개 강조`,hl:{lines:ls,marks:[],focus:[]}},false)}
  const vi=bad.filter(c=>!c.island).sort((a,b)=>Math.max(...b.v.map(l=>ld(b.r,l)))-Math.max(...a.v.map(l=>ld(a.r,l)))),isl=bad.filter(c=>c.island);
  ans.innerHTML=`<p>${bad.length?`단일 회선 고장 ${res.n}건 가운데 ${vi.length?`${vi.length}건에서 정격 위반이`:''}${vi.length&&isl.length?', ':''}${isl.length?`${isl.length}건에서 계통 분리가`:''} 생깁니다. 현재 계통은 N-1 기준을 만족하지 않으므로 예방 재급전 검토가 필요합니다.`:`단일 회선 고장 ${res.n}건 모두 정격 위반이 없습니다. N-1 기준을 만족합니다.`}</p>
    ${vi.length?tbl(vi.slice(0,10).map(c=>[lname(c.l)+(c.l.cx.length>1?' 1회선':''),c.v.slice(0,3).map(lname).join(', ')+(c.v.length>3?` 외 ${c.v.length-3}`:''),String(c.v.length),pc(Math.max(...c.v.map(l=>ld(c.r,l))),1)]),['고장 선로','위반 설비','건수','최대']):''}
    ${isl.length?`<p class="iss">계통 분리 사고: ${isl.slice(0,6).map(c=>lname(c.l)).join(', ')}${isl.length>6?` 외 ${isl.length-6}건`:''} (방사형 연결 설비)</p>`:''}
    <div class="cite"><b>${doc.id}</b> ${doc.t}: ${doc.b}</div>${kbRefs(['c-n1'])}<div class="badges"><span class="bd">감도 기반 스크리닝</span><span class="bd">스냅샷 ${sid}</span></div>${fbHtml()}`;
  wireFb(box,q,null);setChips(['예방 조치 추천해줘',...vi.slice(0,2).map(c=>`${M.buses[c.l.f].n}–${M.buses[c.l.t].n} 선로가 고장 나면?`)])}
async function doDoc(box,ans,t,q){const hits=await step(box,'절차서 검색','search_procedure',t.slice(0,30),()=>{const s=DOCS.filter(d=>d.k.some(k=>t.includes(k)));return {s:s.length?s:[DOCS[0]],log:`${s.length} hits`}});
  ans.innerHTML=`<p>관련 절차는 다음과 같습니다.</p>${hits.s.slice(0,2).map(d=>`<div class="cite"><b>${d.id}</b> ${d.t}: ${d.b}</div>`).join('')}<div class="badges"><span class="bd">문서 근거 인용</span></div>${fbHtml()}`;wireFb(box,q,null);chipsDefault()}
function fbHtml(scen,alt,commit){return `<div class="engtag">${engTag()}</div><div class="fb">${commit?'<p class="cm">실제 발생 이벤트로 해석 · 실계통 조치는 EMS에서 · 여기서는 작업 공간에 고정</p>':''}<button class="btn" data-f="ok" type="button">적절</button><button class="btn" data-f="no" type="button">부적절</button>${scen?'<button class="btn" data-f="ev" type="button">가정 상태 고정</button>':''}${alt?'<button class="btn" data-f="apply" type="button">대안 적용 상태 고정</button>':''}<button class="btn" data-f="share" type="button">공유</button><span class="st"></span></div>`}
function wireFb(box,q,act){const st=box.querySelector('.fb .st'),ap=box.querySelector('[data-f="apply"]'),ev=box.querySelector('[data-f="ev"]'),arm=[ap,ev].filter(Boolean);
  box.querySelector('[data-f="ok"]').onclick=e=>{e.target.disabled=true;box.querySelector('[data-f="no"]').disabled=true;st.textContent='적절';addFb(q,'적절',act&&act.alt?act.alt.h:'-');snapLabel(s=>s.trigger===q&&s.kind==='판단','적절'+(act&&act.alt?` · ${act.alt.h}`:''));addJN('approve',`“${q}” 결과 승인${act&&act.alt?` (추천: ${act.alt.h})`:''}`);0};
  box.querySelector('[data-f="no"]').onclick=e=>{e.target.disabled=true;box.querySelector('[data-f="ok"]').disabled=true;arm.forEach(b=>b.remove());st.textContent='부적절 사유를 골라 주세요';
    const rs=document.createElement('div');rs.className='rsn';rs.innerHTML='<span class="rsq">무엇이 틀렸나요?</span>'+REASONS.filter(r=>r.ui).map(r=>`<button type="button" class="chip" data-r="${r.id}">${r.t}</button>`).join('')+'<button type="button" class="chip" data-r="">건너뛰기</button>';box.querySelector('.fb').appendChild(rs);scroll();
    rs.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{const r=REASONS.find(x=>x.id===c.dataset.r);const fin=unit=>{rs.remove();st.textContent=r?`부적절 · ${r.t}${unit?' · '+unit:''}`:'부적절 · 사유 미지정';addFb(q,'부적절',act&&act.alt?act.alt.h:'-',r?r.id:'');if(unit)feedback[0].unit=unit;snapLabel(s=>s.trigger===q&&s.kind==='판단','부적절'+(r?` · ${r.t}`:'')+(unit?` · ${unit} 제외`:''));addJN('reject',`“${q}” 결과 부적절${r?` (사유: ${r.t}${unit?' · '+unit:''})`:''}`)};
    if(r&&r.id==='site'&&act&&act.alt&&act.alt.st&&act.state){const G0=gensOf(act.state),G1=gensOf(act.alt.st);const us=G1.map((g,i)=>({g,d:g.p-(G0[i]?G0[i].p:0)})).filter(x=>Math.abs(x.d)>5).map(x=>x.g.plant).filter((v,i,a)=>a.indexOf(v)===i).slice(0,6);if(us.length){rs.innerHTML='<span class="rsq">어느 설비가 맞지 않았나요?</span>'+us.map(x=>`<button type="button" class="chip" data-u="${x}">${x}</button>`).join('')+'<button type="button" class="chip" data-u="">건너뛰기</button>';rs.querySelectorAll('[data-u]').forEach(b=>b.onclick=()=>fin(b.dataset.u));return}}fin('')})};
  const commit=(state,label)=>{arm.forEach(b=>b.disabled=true);twin=cloneS(state);lastScen=null;view=null;logCall('save_workspace',`${act.evs.length} events${label?' + '+label:''}`,'applied (operator approved)','0.00s');addJN('apply',`디지털 트윈 반영: ${act.evs.length?act.evs.map(e=>plain(evText(e,state))).join(', '):''}${label?(act.evs.length?' + ':'')+label:''}`);st.textContent='트윈에 반영됨';drawSLD();{const rr=dcpf(twin);snapLabel(s=>s.trigger===q&&s.kind==='판단',`결과 · 반영 후 정격 초과 ${rr?viol(rr).length:'분리'}건`);snapPush('사건',`트윈 반영: ${label||'이벤트'}`,twin,curTrace)}setChips(['현재 과부하 설비 있어?','N-1 상정사고 전체 점검해줘'])};
  const go=(st2,lb)=>pinWS(q,lb,st2,st);const sh=box.querySelector('[data-f="share"]');if(sh)sh.onclick=()=>{shareAdd(q,act&&act.alt?act.alt.h:'');sh.disabled=true;st.textContent='공유됨'};if(ev)ev.onclick=()=>go(act.state,'');if(ap)ap.onclick=()=>go(act.alt.st,act.alt.h);
  if(ROLE==='연구자'){box.querySelectorAll('.fb .btn').forEach(b=>b.disabled=true);st.textContent='연구자: 운영 판단 권한 없음'}}
function addFb(q,d,a,r){feedback.unshift({t:now(),q,d,a,m:M.name,r:r||''});$('fbCnt').textContent=feedback.length+'건';
  $('fbt').innerHTML='<tr><th>시각</th><th>질의</th><th>판단</th><th>부적절 사유</th><th>원인 모델</th><th>추천 대안</th><th>계통 모델</th></tr>'+feedback.map(f=>{const r=REASONS.find(x=>x.id===f.r);const mm={llm:'LLM 오케스트레이터',se:'AI 상태추정',fc:'수요·재생 예측',rd:'재급전 최적화',pf:'조류계산'};return `<tr><td>${f.t}</td><td>${esc(f.q)}</td><td><span class="res ${f.d==='적절'?'p':'f'}">${f.d}</span></td><td>${r?r.t:'-'}</td><td class="d">${r?mm[r.m]:'-'}</td><td>${esc(f.a)}</td><td class="d">${esc(f.m)}</td></tr>`}).join('')}

/* ===== 운영 일지 ===== */
const JN=[];
function simClock(){return TL.on?`${M.profile?M.profile.label:'표준일'} ${pad(TL.h)}:00`:now()}
function addJN(type,text){JN.push({t:now(),st:TL.on?`${pad(TL.h)}:00`:null,type,text,model:M.name});if($('v-rp').classList.contains('on'))renderReport()}

/* ===== 1. 예방 조치 ===== */
function moveSummary(s0,s1){const d=new Array(M.buses.length).fill(0);gensOf(s1).forEach((g,k)=>{const o=k<M.gens.length?s0.gen[k].p:(s0.newGen.find(x=>x.id===g.id)||{p:g.p}).p;d[g.b]+=g.p-o});
  const dn=d.map((v,i)=>({v,i})).filter(o=>o.v<-5).sort((a,b)=>a.v-b.v),up=d.map((v,i)=>({v,i})).filter(o=>o.v>5).sort((a,b)=>b.v-a.v);
  let cost=0;gensOf(s1).forEach((g,k)=>{if(k<M.gens.length)cost+=(g.p-s0.gen[k].p)*g.c});return {dn,up,tot:up.reduce((a,o)=>a+o.v,0),cost:Math.round(cost)}}
const lsTxt=a=>a.slice(0,3).map(o=>`${nameOf(o.i)} ${fmt(Math.abs(o.v))}MW`).join(', ')+(a.length>3?` 외 ${a.length-3}곳`:'');
async function doPrevent(box,ans,q,hour){const target=hour!=null&&TL.on?hour:null;const sid=snapId();
  const base=target!=null?hourWithPersist(target):twin;const when=target!=null?`${pad(target)}:00 예상 계통`:'현 계통';
  await step(box,'스냅샷 고정','create_snapshot',target!=null?`forecast ${pad(target)}:00`:'now',()=>({log:sid}));
  const res=await step(box,'예방 재급전 탐색','run_redispatch_opf',`${when}, security-constrained`,()=>{const p=preventive(base);return p?{...p,log:`N-1 ${p.start.vi.length}→${p.best.vi.length}, base ${p.start.base.length}→${p.best.base.length}`}:{fail:true,log:'islanding'}});
  if(res.fail){ans.innerHTML='<p>계통이 분리된 상태라 예방 조치를 계산할 수 없습니다.</p>';return}
  await step(box,'조치 후 N-1 재검증','run_n1_screening',`${sid}`,()=>({log:`${res.best.vi.length} remaining`}));
  const doc=await step(box,'절차서 검색','search_procedure','N-1 기준',()=>({...DOCS[2],log:DOCS[2].id}));
  const S=res.start,B=res.best,mv=moveSummary(base,res.state),changed=mv.tot>5;
  const better=S.vi.length-B.vi.length,bfix=S.base.length-B.base.length;
  const head=!S.vi.length&&!S.base.length?`${when}은 이미 N-1 기준을 만족해 예방 조치가 필요 없습니다.`:
    changed?`${when}에서 ${S.base.length?`정격 초과 ${S.base.length}건과 `:''}N-1 위반 사고 ${S.vi.length}건이 있습니다. 아래 재급전을 하면 ${S.base.length?`정격 초과는 ${B.base.length}건, `:''}N-1 위반 사고는 ${B.vi.length}건으로 줄어듭니다.${B.vi.length||B.base.length?' 남은 위반은 재급전만으로는 풀리지 않아 부하 차단이나 운전 계획 조정이 필요합니다.':''}`:
    `${when}의 위반은 재급전만으로는 줄어들지 않습니다. 부하 차단이나 운전 계획 조정을 검토해야 합니다.`;
  const remain=B.vi.slice().sort((a,b)=>Math.max(...b.v.map(l=>ld(b.r,l)))-Math.max(...a.v.map(l=>ld(a.r,l)))).slice(0,6);
  const kp=(a,b,lab)=>`<div class="kpi"><span>${lab}</span><b>${a} → <span style="color:${b<a?'var(--ok)':b>0?'var(--warn)':'var(--ok)'}">${b}</span></b></div>`;
  let h=`<p>${head}</p><div class="pv">${kp(S.base.length,B.base.length,'정격 초과 선로')}${kp(S.vi.length,B.vi.length,'N-1 위반 사고')}</div>`;
  if(changed)h+=`<div class="lab">권고 재급전</div><div class="alt rec"><span class="r">예방 조치</span><p style="margin-top:4px">감발 ${lsTxt(mv.dn)} · 증발 ${lsTxt(mv.up)} · 총 ${fmt(mv.tot)}MW · 비용 ${mv.cost>=0?'+':''}${fmt(mv.cost)}천원/h</p></div>`;
  if(remain.length)h+=`<div class="lab">조치 후에도 남는 위험 (상위 ${remain.length}건)</div>`+tbl(remain.map(c=>[lname(c.l)+' 1회선 고장 시',c.v.slice(0,2).map(lname).join(', '),String(c.v.length),pc(Math.max(...c.v.map(l=>ld(c.r,l))),1)]),['상정사고','위반 설비','건수','최대']);
  h+=`<div class="cite"><b>${doc.id}</b> ${doc.t}: ${doc.b}</div>${kbRefs(['c-n1','c-rd'])}`;
  // 지도 이야기
  const story=[];const before=S,allF=[...mv.dn.slice(0,4).map(o=>o.i),...mv.up.slice(0,4).map(o=>o.i)];
  const riskL=new Set();S.vi.forEach(c=>c.v.forEach(l=>riskL.add(l.id)));S.base.forEach(l=>riskL.add(l.id));
  const marks=[...mv.dn.slice(0,5).map(o=>({b:o.i,k:'dn',t:`감발 −${fmt(-o.v)}`})),...mv.up.slice(0,5).map(o=>({b:o.i,k:'up',t:`증발 +${fmt(o.v)}`}))];
  const hlA={lines:riskL,marks,focus:allF};const hl0={lines:riskL,marks:[],focus:[...riskL].flatMap(id=>{const l=allLines(base).find(x=>x.id===id);return l?[l.f,l.t]:[]})};
  story.push({kind:'all',kindTxt:'전체',cap:`위험 선로 ${riskL.size}개 · 조치 지점 ${mv.dn.length+mv.up.length}곳 · N-1 위반 ${S.vi.length}→${B.vi.length}건`,state:res.state,ref:base,hl:hlA,focus:null,at:null});
  const worst=S.vi.concat().sort((a,b)=>b.v.length-a.v.length)[0];
  if(worst){const l=worst.v[0];story.push({kind:'impact',kindTxt:'위험',cap:`${lname(worst.l)} 1회선 고장 시 ${lname(l)} ${Math.round(ld(worst.r,l))}% (가장 자주 위반되는 사고)`,state:base,ref:null,hl:{lines:new Set([worst.l.id,l.id]),marks:[],focus:[]},focus:[worst.l.f,worst.l.t,l.f,l.t],lines:[l.id],at:{x:(M.buses[l.f].x+M.buses[l.t].x)/2,y:(M.buses[l.f].y+M.buses[l.t].y)/2}})}
  if(mv.dn.length)story.push({kind:'remedy',kindTxt:'감발',cap:`감발 ${lsTxt(mv.dn)}`,state:res.state,ref:base,hl:hlA,focus:mv.dn.slice(0,3).map(o=>o.i),at:mid(mv.dn.slice(0,3).map(o=>o.i))});
  if(mv.up.length)story.push({kind:'remedy',kindTxt:'증발',cap:`증발 ${lsTxt(mv.up)}`,state:res.state,ref:base,hl:hlA,focus:mv.up.slice(0,3).map(o=>o.i),at:mid(mv.up.slice(0,3).map(o=>o.i))});
  const pv={state:res.state,ref:base,label:`${curTrace} 예방 재급전 적용 시 ${target!=null?`(${pad(target)}:00 예상) `:''}(현 계통 아님)`,hl:hlA,story,si:0};
  h+=`<div class="badges"><span class="bd">감도 기반 탐색</span><span class="bd">조치 후 N-1 재검증</span><span class="bd">스냅샷 ${sid}</span></div>${changed?`<div class="vrow">${viewBtn(pv,'조치 효과 계통도에서 보기')}</div>`:''}`;
  if(changed&&target==null)h+=fbHtml(false,{h:'예방 재급전',st:res.state});else if(changed)h+=`<div class="fb"><button class="btn" data-f="ok" type="button">적절</button><button class="btn" data-f="no" type="button">부적절</button><button class="btn pri" data-f="plan" type="button" disabled>${pad(target)}:00 급전 계획에 예약</button><span class="st"></span></div>`;else h+=fbHtml();
  ans.innerHTML=h;
  if(changed&&target==null)wireFb(box,q,{evs:[],state:base,alt:{h:'예방 재급전',st:res.state}});
  else if(changed){const st=box.querySelector('.fb .st'),pl=box.querySelector('[data-f="plan"]');
    box.querySelector('[data-f="ok"]').onclick=e=>{e.target.disabled=true;box.querySelector('[data-f="no"]').disabled=true;st.textContent='적절';pl.disabled=false;addFb(q,'적절','예방 재급전(예약)')};
    box.querySelector('[data-f="no"]').onclick=e=>{e.target.disabled=true;box.querySelector('[data-f="ok"]').disabled=true;st.textContent='부적절';pl.remove();addFb(q,'부적절','예방 재급전(예약)')};
    pl.onclick=()=>{pl.disabled=true;snapLabel(s=>s.trigger===q&&s.kind==='판단',`적절 · ${pad(target)}:00 예방 재급전 예약`);TL.plans[target]=res.state.gen.map((g,k)=>g.p-base.gen[k].p);logCall('save_workspace',`plan redispatch @${pad(target)}:00`,'scheduled (operator approved)','0.00s');addJN('plan',`${pad(target)}:00 예방 재급전 ${fmt(mv.tot)}MW 예약 (N-1 위반 ${S.vi.length}→${B.vi.length}건 예상)`);st.textContent=`${pad(target)}:00 계획에 예약됨`;tlRecalc()}}
  else wireFb(box,q,null);
  addJN('prevent',`${when} 예방 조치 검토: 정격 초과 ${S.base.length}→${B.base.length}건, N-1 위반 ${S.vi.length}→${B.vi.length}건${changed?`, 재급전 ${fmt(mv.tot)}MW`:''}`);
  if(changed){view=null;setView(pv);playStory()}
  setChips(['현재 과부하 설비 있어?','N-1 상정사고 전체 점검해줘','오늘 운영 보고서 써줘'])}

/* ===== 2. 하루 흐름 ===== */
const TL={on:false,h:0,play:false,timer:null,sum:[],plans:{},seen:new Set(),job:0};
const LF=[.70,.67,.65,.64,.65,.69,.76,.85,.92,.96,.98,.99,.95,.97,.98,.97,.96,.98,1,.99,.95,.88,.8,.74];
function hourState(h){const s=freshState();const P=M.profile;
  if(P){const H=P.hours[h];s.load=H.load.slice();s.ren=H.ren.slice();s.gen=M.gens.map((g,i)=>({p:H.gen[i],on:H.gen[i]>0.5?1:0}))}
  else{s.load=BASE.load.map(v=>Math.round(v*LF[h]));s.gen=BASE.gen.map(g=>({p:g.p*LF[h],on:g.on}))}
  if(TL.plans[h])TL.plans[h].forEach((d,k)=>{if(s.gen[k]){s.gen[k].p+=d;if(s.gen[k].p>0.5)s.gen[k].on=1}});balance(s);return s}
function hourWithPersist(h){const s=hourState(h);s.out={...twin.out};s.add=twin.add.map(l=>({...l}));s.seq=twin.seq;s.newGen=twin.newGen.map(g=>({...g}));balance(s);return s}
function tlOpen(){if(TL.on)return;TL.on=true;TL.h=M.profile?7:8;TL.plans={};TL.seen=new Set();$('tlbar').classList.add('on');$('tlOpen').hidden=true;$('tlCtl').hidden=false;
  addJN('timeline',`하루 흐름 재생 시작 (${M.profile?M.profile.label+' 실측 프로필':'표준 부하 곡선'})`);setHour(TL.h,true);tlRecalc()}
function tlClose(){tlPause();TL.on=false;$('tlbar').classList.remove('on');$('tlOpen').hidden=false;$('tlCtl').hidden=true}
function setHour(h,quiet){TL.h=h;twin=hourWithPersist(h);snapPush('정기',`하루 흐름 ${pad(h)}:00`,twin,'-');lastScen=null;n1Cache=null;if(!view||view.live)view=null;drawSLD();tlDraw();if($('v-db').classList.contains('on'))dashRender();if(!quiet)checkForecast()}
function tlRecalc(){const job=++TL.job;TL.sum=new Array(24).fill(null);tlDraw();let h=0;
  const run=()=>{if(job!==TL.job||!TL.on)return;const s=hourWithPersist(h),r=dcpf(s);if(r){const X=xmat(s);const c=X?n1fast(s,r,X):[];
      TL.sum[h]={max:Math.max(...live(r).map(l=>ld(r,l))),viol:viol(r).map(l=>({l,p:ld(r,l)})).sort((a,b)=>b.p-a.p),n1:c.filter(x=>x.island||x.v.length).length,pv:M.profile?M.profile.hours[h].pv:0,dem:s.load.reduce((a,b)=>a+b,0)}}
    else TL.sum[h]={max:0,viol:[],n1:0,island:true,pv:0,dem:0};
    h++;tlDraw();if(h<24)setTimeout(run,0);else{drawAlarms(dcpf(twin));checkForecast(true)}};setTimeout(run,0)}
function tlDraw(){const svg=$('tlchart');if(!svg||!TL.on)return;const W=600,H=74,cw=W/24;let g='';const mxN=Math.max(5,...TL.sum.filter(Boolean).map(x=>x.n1));const mxPv=Math.max(1,...(M.profile?M.profile.hours.map(x=>x.pv):[1]));
  if(M.profile){let d='';M.profile.hours.forEach((x,h)=>{const y=H-14-(x.pv/mxPv)*(H-26);d+=(h?'L':'M')+(h*cw+cw/2)+' '+y});g+=`<path class="tpv" d="M${cw/2} ${H-14} ${d.slice(1).replace(/^/, 'L')} L${23*cw+cw/2} ${H-14}Z"/>`}
  const y100=H-14-(100/140)*(H-26);g+=`<line class="t100" x1="0" x2="${W}" y1="${y100}" y2="${y100}"/>`;
  TL.sum.forEach((x,h)=>{if(!x)return;const bh=(x.n1/mxN)*(H-34);g+=`<rect class="tn1${x.viol.length?' bad':''}" x="${h*cw+cw*.18}" y="${H-14-bh}" width="${cw*.64}" height="${bh}" rx="1.5"/>`});
  let d='';TL.sum.forEach((x,h)=>{if(!x)return;const y=H-14-Math.min(140,x.max)/140*(H-26);d+=(d?'L':'M')+(h*cw+cw/2)+' '+y});if(d)g+=`<path class="tmax" d="${d}"/>`;
  TL.sum.forEach((x,h)=>{if(x&&x.viol.length){const y=H-14-Math.min(140,x.max)/140*(H-26);g+=`<circle class="tdot" cx="${h*cw+cw/2}" cy="${y}" r="2.6"/>`}});
  Object.keys(TL.plans).forEach(h=>{g+=`<text class="tplan" x="${h*cw+cw/2}" y="10" text-anchor="middle">예약</text>`});
  g+=`<rect class="tcur" x="${TL.h*cw}" y="0" width="${cw}" height="${H-12}"/>`;
  for(let h=0;h<24;h+=3)g+=`<text class="tax" x="${h*cw+cw/2}" y="${H-2}" text-anchor="middle">${pad(h)}</text>`;
  for(let h=0;h<24;h++)g+=`<rect class="thit" data-h="${h}" x="${h*cw}" y="0" width="${cw}" height="${H}"><title>${pad(h)}:00${TL.sum[h]?` · 최대 부하율 ${Math.round(TL.sum[h].max)}% · 정격 초과 ${TL.sum[h].viol.length}건 · N-1 위반 ${TL.sum[h].n1}건`:''}</title></rect>`;
  svg.innerHTML=g;svg.querySelectorAll('.thit').forEach(r=>r.onclick=()=>{tlPause();setHour(+r.dataset.h)});
  $('tlTime').textContent=`${M.profile?M.profile.label:'표준일'} ${pad(TL.h)}:00`;$('tlSlider').value=TL.h;
  const x=TL.sum[TL.h];$('tlStat').textContent=x?`최대 부하율 ${Math.round(x.max)}% · 정격 초과 ${x.viol.length}건 · N-1 위반 ${x.n1}건${M.profile?` · 태양광 ${fmt(M.profile.hours[TL.h].pv)}MW`:''}`:'계산 중…';
  $('tlPlay').textContent=TL.play?'일시정지':'재생'}
function tlPlay(){if(TL.play){tlPause();return}TL.play=true;tlDraw();const tick=()=>{if(!TL.play)return;if(TL.h>=23){tlPause();return}setHour(TL.h+1);if(TL.play)TL.timer=setTimeout(tick,2300)};TL.timer=setTimeout(tick,600)}
function tlPause(){TL.play=false;clearTimeout(TL.timer);tlDraw()}
function forecasts(){if(!TL.on)return [];const cur=TL.sum[TL.h],now_=new Set(cur?cur.viol.map(v=>v.l.id):[]);const out=[];
  for(let k=1;k<=3;k++){const h=TL.h+k;if(h>23)break;const x=TL.sum[h];if(!x)continue;x.viol.filter(v=>!now_.has(v.l.id)&&!out.some(o=>o.l.id===v.l.id)).forEach(v=>out.push({h,k,l:v.l,p:v.p}))}return out}
function checkForecast(silent){const f=forecasts();drawAlarms(dcpf(twin));if(silent||!f.length)return;const key=f.map(x=>x.h+':'+x.l.id).join(',');const fresh=f.filter(x=>!TL.seen.has(x.h+':'+x.l.id));if(!fresh.length)return;
  fresh.forEach(x=>TL.seen.add(x.h+':'+x.l.id));const wasPlay=TL.play;if(wasPlay)tlPause();const h=fresh[0].h;
  const d=document.createElement('div');d.className='a fcast';d.innerHTML=`<div class="ans"><p><b>선제 경보 · ${pad(TL.h)}:00 기준</b> ${fresh[0].k}시간 뒤(${pad(h)}:00) ${fresh.slice(0,3).map(x=>`${lname(x.l)} ${Math.round(x.p)}%`).join(', ')}${fresh.length>3?` 외 ${fresh.length-3}건`:''} 과부하가 예상됩니다.${M.profile&&M.profile.hours[h].pv>5000?` 태양광 출력이 ${fmt(M.profile.hours[h].pv)}MW까지 늘며 남쪽에서 수도권으로 올라가는 조류가 커지는 시간대입니다.`:''}</p><div class="fb" style="border:0;padding-top:0"><button class="btn pri" type="button" data-a="prev">${pad(h)}:00 예방 조치 계산</button><button class="btn" type="button" data-a="go">${pad(h)}:00 상황 미리 보기</button>${wasPlay?'<button class="btn" type="button" data-a="cont">계속 재생</button>':''}</div></div>`;
  $('msgs').appendChild(d);scroll();snapPush('사건',`선제 경보: ${pad(h)}:00 ${fresh.slice(0,2).map(x=>lname(x.l)).join(', ')} 예상`,twin,'-');addJN('forecast',`${pad(TL.h)}:00 선제 경보: ${pad(h)}:00 ${fresh.slice(0,3).map(x=>`${lname(x.l)} ${Math.round(x.p)}%`).join(', ')} 예상`);
  d.querySelector('[data-a="prev"]').onclick=()=>ask(`${pad(h)}시 예상 상황 예방 조치 추천해줘`);
  d.querySelector('[data-a="go"]').onclick=()=>{const s=hourWithPersist(h);const ls=new Set(fresh.map(x=>x.l.id));setView({state:s,ref:twin,label:`${pad(h)}:00 예상 계통 (현 계통 아님)`,hl:{lines:ls,marks:[],focus:fresh.flatMap(x=>[x.l.f,x.l.t])}},true)};
  const c=d.querySelector('[data-a="cont"]');if(c)c.onclick=()=>{c.remove();tlPlay()}}

/* ===== 3. 운영 보고서 ===== */
function renderReport(){const r=dcpf(twin),nv=r?n1Now(twin):[],v=r?viol(r).sort((a,b)=>ld(r,b)-ld(r,a)):[];const el=$('report');
  const appr=feedback.filter(f=>f.d==='적절'),rej=feedback.filter(f=>f.d==='부적절');
  const kind=t=>({query:'질의',scenario:'검토',approve:'적절',reject:'부적절',apply:'반영',inject:'고장',forecast:'경보',prevent:'예방',plan:'예약',timeline:'운전',train:'훈련',model:'모델'}[t]||t);
  const fut=TL.on?TL.sum.map((x,h)=>({x,h})).filter(o=>o.h>TL.h&&o.x&&o.x.viol.length):[];
  const tl=JN.filter(j=>j.model===M.name);
  let h=`<div class="rph"><h1>계통 운영 상황 보고</h1><p class="rmeta">자동 작성 초안 · 작성 ${new Date().toLocaleString('ko-KR',{hour12:false})} · 계통 모델 ${esc(M.name)}${TL.on?` · 운전 시점 ${esc(simClock())}`:''}</p></div>`;
  const vtxt=!r?'현재 계통이 분리된 상태입니다.':(v.length?`정격을 넘는 선로가 ${v.length}건 있습니다: ${v.slice(0,4).map(l=>`${lname(l)} ${Math.round(ld(r,l))}%`).join(', ')}${v.length>4?` 외 ${v.length-4}건`:''}. `:'정격을 넘는 선로는 없습니다. ')+`N-1 위반 사고는 ${nv.length}건입니다.`;
  const rensum=(twin.ren||[]).reduce((a,b)=>a+b,0),gsum=gensOf(twin).filter(g=>g.on).reduce((a,g)=>a+g.p,0)+rensum;
  const outs=Object.entries(twin.out).map(([id,n])=>{const l=allLines(twin).find(x=>x.id===id);return `${lname(l)} ${n}회선`});
  h+=`<h2>1. 현재 계통 상태</h2><p>총 부하 ${fmt(twin.load.reduce((a,b)=>a+b,0))}MW, 총 발전 ${fmt(gsum)}MW${rensum>0?`(재생에너지 ${fmt(rensum)}MW 포함)`:''}입니다. ${vtxt}${outs.length?` 탈락 중인 설비: ${outs.join(', ')}.`:''}</p>`;
  h+=`<h2>2. 주요 경과</h2>`+(tl.length?`<table class="rtbl"><tr><th>시각</th><th>운전 시점</th><th>구분</th><th>내용</th></tr>${tl.slice(-30).map(j=>`<tr><td>${j.t}</td><td>${j.st||'-'}</td><td><span class="rk rk-${j.type}">${kind(j.type)}</span></td><td>${esc(j.text)}</td></tr>`).join('')}</table>`:'<p>기록된 경과가 없습니다.</p>');
  h+=`<h2>3. 판단과 조치</h2><p>관제사 판단 ${feedback.length}건 중 승인 ${appr.length}건, 부적절 ${rej.length}건입니다.${appr.length?` 승인한 대안: ${[...new Set(appr.map(f=>f.a).filter(a=>a!=='-'))].join(', ')||'해석 결과 확인'}.`:''}${Object.keys(TL.plans).length?` 급전 계획에 예약한 예방 재급전: ${Object.keys(TL.plans).map(x=>pad(+x)+':00').join(', ')}.`:''}</p>`;
  h+=`<h2>4. 향후 주의 사항</h2>`+(TL.on?(fut.length?`<p>${fut.slice(0,5).map(o=>`${pad(o.h)}:00 ${o.x.viol.slice(0,2).map(v=>`${lname(v.l)} ${Math.round(v.p)}%`).join(', ')}`).join(' / ')} 과부하가 예상됩니다. 해당 시간대 전에 예방 재급전을 검토해야 합니다.</p>`:'<p>남은 시간대에 예상되는 과부하는 없습니다.</p>'):`<p>${nv.length?`N-1 위반 사고 상위: ${nv.filter(c=>!c.island).slice(0,3).map(c=>lname(c.l)).join(', ')}. 예방 재급전 검토를 권고합니다.`:'특이 위험 없음.'}</p>`);
  const tr=TRN.hist.filter(x=>x.m===M.name);if(tr.length)h+=`<h2>5. 훈련 결과</h2><p>${tr.map(x=>`${x.t} ${x.score}점(${x.n}문항)`).join(', ')}</p>`;
  h+=`<h2>${tr.length?6:5}. 비고</h2><p>본 보고서는 테스트베드의 자동 작성 초안으로, DC 조류 근사에 기반한 수치입니다. 확정 전 담당자 검토가 필요합니다.</p>`;
  el.innerHTML=h}
async function doReport(box,ans,q){await step(box,'운영 이력 수집','get_measurements','journal',()=>({log:`${JN.length} entries`}));await step(box,'보고서 작성','',' ',()=>{renderReport();return {}});
  const tl=JN.filter(j=>j.model===M.name).length;
  ans.innerHTML=`<p>운영 보고서 초안을 작성했습니다. 현재 계통 상태, 주요 경과 ${tl}건, 판단과 조치, 향후 주의 사항으로 정리했습니다.</p><div class="fb" style="border:0;padding-top:0"><button class="btn pri" type="button" data-a="open">보고서 보기</button></div>`;
  ans.querySelector('[data-a="open"]').onclick=()=>document.querySelector('nav button[data-v="rp"]').click()}

/* ===== 4. 훈련 모드 ===== */
const TRN={on:false,probs:[],i:0,ans:[],hist:[],t0:0};
function shuffle(a){for(let i=a.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]]}return a}
function genProblems(n){let base=twin;{const r0=dcpf(base);if(!r0)base=BASE;else if(!clean(r0)){const rd=redispatch(base);base=rd?rd.state:BASE}}const r=dcpf(base);if(!r)return [];TRN.base=base;const X=xmat(base),cs=shuffle(n1fast(base,r,X).filter(c=>!c.island&&c.v.length));const ok=shuffle(n1fast(base,r,X).filter(c=>!c.island&&!c.v.length));
  const loads=base.load.map((v,i)=>({v,i})).filter(o=>o.v>50).sort((a,b)=>b.v-a.v).slice(0,12);const P=[];
  for(let k=0;k<n;k++){let evs=[];const c=k===3&&ok.length?ok[0]:cs[k%Math.max(1,cs.length)];if(!c)break;evs.push({k:'out',id:c.l.id,n:1});
    if(k%2===1&&loads.length){const L=loads[Math.floor(Math.random()*loads.length)];evs.push({k:'load',b:L.i,v:Math.round(L.v*1.15)})}
    const s=cloneS(base);evs.forEach(e=>applyEv(s,e));balance(s);const rs=dcpf(s);if(!rs){k--;continue}const v=viol(rs);
    const rd=v.length?redispatch(s):null,sh=v.length&&!rd?shed(s):null;
    const near=live(rs).filter(l=>!v.includes(l)).sort((a,b)=>ld(rs,b)-ld(rs,a)).slice(0,Math.max(2,5-v.length));
    const opts=shuffle([...v.slice(0,4),...near.slice(0,Math.max(2,5-Math.min(4,v.length)))]);
    const acts=[];if(rd)acts.push({id:'rd',t:'재급전: '+rdText(rd,s).split(' · 비용')[0],ok:2,st:rd.state});
    if(rd){const w={moves:rd.moves.map(m=>({i:m.j,j:m.i,mw:m.mw}))};const ws=cloneS(s);w.moves.forEach(m=>{const d=-moveGen(ws,m.i,-m.mw);moveGen(ws,m.j,d)});const mv=moveSummary(s,ws);acts.push({id:'wrong',t:`재급전: 감발 ${lsTxt(mv.dn)} · 증발 ${lsTxt(mv.up)}`,ok:0,st:ws})}
    if(sh)acts.push({id:'sh',t:'부하 차단: '+shText(sh).split(' · 당직')[0],ok:2,st:sh.state});else if(v.length){const s2=shed(s);if(s2)acts.push({id:'sh',t:'부하 차단: '+shText(s2).split(' · 당직')[0],ok:1,st:s2.state})}
    acts.push({id:'none',t:'조치 불필요 (감시 유지)',ok:v.length?0:2});
    if(v.length)acts.push({id:'esc',t:'상위 관제소 보고 (자동 대안 없음)',ok:rd||sh?0:2});
    P.push({evs,s,rs,v,opts,acts:shuffle(acts),hl:evHL(evs,s)})}
  return P}
function trainStart(){if(busy)return;TRN.on=true;TRN.probs=genProblems(5);TRN.i=0;TRN.ans=[];stopPlay();if(!TRN.probs.length){return}
  const d=document.createElement('div');d.className='sys';d.textContent=`훈련 모드 시작 · ${M.name} · ${TRN.probs.length}문항 · 계통도에서 부하율 표시를 가립니다`;$('msgs').appendChild(d);addJN('train','훈련 모드 시작');trainShow()}
function trainShow(){const p=TRN.probs[TRN.i];TRN.t0=performance.now();
  setView({state:p.s,ref:null,hideFlows:true,label:`훈련 문제 ${TRN.i+1}/${TRN.probs.length} (현 계통 아님)`,hl:p.hl},true);
  const box=document.createElement('div');box.className='a train';const sel=new Set();let act=null;
  box.innerHTML=`<div class="ans"><p class="tq">문제 ${TRN.i+1}/${TRN.probs.length}</p><ol class="evs">${evList(p.evs,p.s)}</ol>
    <div class="lab">① 정격을 넘을 선로를 모두 고르세요</div><div class="opts" data-g="v">${p.opts.map(l=>`<button type="button" class="opt" data-id="${l.id}">${lname(l)}${KV(l.kv)}</button>`).join('')}<button type="button" class="opt" data-id="none">위반 없음</button></div>
    <div class="lab">② 가장 적절한 조치를 고르세요</div><div class="opts col" data-g="a">${p.acts.map(a=>`<button type="button" class="opt" data-id="${a.id}">${esc(a.t)}</button>`).join('')}</div>
    <div class="fb" style="border:0"><button class="btn pri" type="button" data-a="sub" disabled>제출</button><button class="btn" type="button" data-a="quit">훈련 종료</button><span class="st"></span></div></div>`;
  $('msgs').appendChild(box);scroll();
  const upd=()=>{box.querySelector('[data-a="sub"]').disabled=!(sel.size&&act)};
  box.querySelectorAll('[data-g="v"] .opt').forEach(b=>b.onclick=()=>{const id=b.dataset.id;if(id==='none'){sel.clear();box.querySelectorAll('[data-g="v"] .opt').forEach(x=>x.classList.remove('on'))}else{box.querySelector('[data-g="v"] [data-id="none"]').classList.remove('on');sel.delete('none')}
    if(sel.has(id)){sel.delete(id);b.classList.remove('on')}else{sel.add(id);b.classList.add('on')}upd()});
  box.querySelectorAll('[data-g="a"] .opt').forEach(b=>b.onclick=()=>{act=b.dataset.id;box.querySelectorAll('[data-g="a"] .opt').forEach(x=>x.classList.toggle('on',x===b));upd()});
  box.querySelector('[data-a="quit"]').onclick=()=>trainEnd(box);
  box.querySelector('[data-a="sub"]').onclick=()=>trainGrade(box,p,sel,act)}
function trainGrade(box,p,sel,act){const sec=(performance.now()-TRN.t0)/1000;const truth=new Set(p.v.filter(l=>p.opts.includes(l)).map(l=>l.id));const pick=new Set([...sel].filter(x=>x!=='none'));
  let tp=0;pick.forEach(x=>{if(truth.has(x))tp++});const prec=pick.size?tp/pick.size:(truth.size?0:1),rec=truth.size?tp/truth.size:(pick.size?0:1);const det=Math.round(((prec+rec)/2)*50);
  const A=p.acts.find(a=>a.id===act),best=p.acts.find(a=>a.ok===2),actS=A.ok===2?50:A.ok===1?25:0;const score=det+actS;TRN.ans.push({score,sec});
  box.querySelectorAll('.opt').forEach(b=>{b.disabled=true;const id=b.dataset.id;if(b.closest('[data-g="v"]')){if(truth.has(id)||(id==='none'&&!truth.size))b.classList.add('right');else if(b.classList.contains('on'))b.classList.add('wrong')}
    else{const a=p.acts.find(x=>x.id===id);if(a.ok===2)b.classList.add('right');else if(a.ok===1)b.classList.add('half');if(b.classList.contains('on')&&a.ok<2)b.classList.add('wrong')}});
  let post='';if(A.st){const r2=dcpf(A.st);post=clean(r2)?' 선택한 조치를 적용하면 위반이 모두 사라집니다.':` 선택한 조치를 적용해도 ${viol(r2).length}개 선로가 정격을 넘습니다.`}
  const fb=box.querySelector('.fb');fb.innerHTML=`<p class="cm"><b>${score}점</b> (탐지 ${det}/50 · 조치 ${actS}/50 · ${sec.toFixed(1)}초) — 정답 위반 설비: ${p.v.length?p.v.map(l=>`${lname(l)} ${Math.round(ld(p.rs,l))}%`).join(', '):'없음'}. 시스템 추천: ${esc(best.t)}.${post}</p><button class="btn pri" type="button" data-a="next">${TRN.i+1<TRN.probs.length?'다음 문제':'결과 보기'}</button><button class="btn" type="button" data-a="show">정답 계통도 보기</button>`;
  const showV={state:p.s,ref:TRN.base,label:`훈련 문제 ${TRN.i+1} 정답 (현 계통 아님)`,hl:p.hl};setView(showV,true);
  fb.querySelector('[data-a="show"]').onclick=()=>setView(showV,true);
  fb.querySelector('[data-a="next"]').onclick=e=>{e.target.disabled=true;TRN.i++;if(TRN.i<TRN.probs.length)trainShow();else trainEnd(null)};scroll()}
function trainEnd(box){if(box)box.querySelectorAll('button').forEach(b=>b.disabled=true);TRN.on=false;const n=TRN.ans.length;goLive();if(!n){addJN('train','훈련 중단');return}
  const tot=Math.round(TRN.ans.reduce((a,x)=>a+x.score,0)/n),avg=TRN.ans.reduce((a,x)=>a+x.sec,0)/n;
  const d=document.createElement('div');d.className='a';d.innerHTML=`<div class="ans"><p><b>훈련 결과 ${tot}점</b> · ${n}문항 · 평균 ${avg.toFixed(1)}초</p><p class="d">같은 문제를 시스템은 모두 정답으로 풀었고, 문항당 계산 시간은 0.1초 이내였습니다. 결과는 평가 탭의 훈련 기록과 운영 보고서에 남습니다.</p></div>`;$('msgs').appendChild(d);scroll();
  TRN.hist.unshift({t:now(),m:M.name,n,score:tot,avg:avg.toFixed(1)});addJN('train',`훈련 완료: ${n}문항 ${tot}점, 평균 ${avg.toFixed(1)}초`);
  $('trt').innerHTML='<tr><th>시각</th><th>계통 모델</th><th>문항</th><th>점수</th><th>평균 응답</th><th>시스템</th></tr>'+TRN.hist.map(x=>`<tr><td>${x.t}</td><td>${x.m}</td><td>${x.n}</td><td>${x.score}점</td><td>${x.avg}초</td><td class="d">100점 · 0.1초 이내</td></tr>`).join('')}

/* ===== 평가: 질의 이해 · N-1 성능 평가 ===== */
function buildQB(){const E=examples(),s=BASE,n=l=>`${M.buses[l.f].n}–${M.buses[l.t].n}`,ab=l=>[`${M.buses[l.f].n}`,`${M.buses[l.t].n}`];
  const out=(l,all)=>({k:'out',id:l.id,n:all?l.cx.length:1}),g=E.biggest,lo=E.loads;
  const sec=E.flex.i===E.biggest.i?null:E.flex;const B=i=>qName(i);
  const notOnL=allLines(s).find(l=>remC(l,s)===l.cx.length);
  const Q=[
   {c:'단일 고장',q:`${n(E.L1)} 선로가 고장 나면?`,a:'answer',e:[out(E.L1)]},
   {c:'전 회선 고장',q:`${n(E.L2)} 선로 ${E.L2.cx.length>1?'전 회선 ':''}탈락하면?`,a:'answer',e:[out(E.L2,true)]},
   {c:'복합 이벤트',q:`${n(E.L3)} 선로 휴전하고 ${unitQ(E.flex.g)} 출력 ${E.flexV}MW로 올리면?`,a:'answer',e:[out(E.L3),{k:'gen',i:E.flex.i,v:E.flexV}]},
   {c:'발전 정지',q:`${unitQ(g.g)} 트립되면?`,a:'answer',e:[{k:'gen',i:g.i,v:0,trip:true}]},
   {c:'부하 변화',q:`${B(lo[0].i)} 부하가 100MW 늘어나면?`,a:'answer',e:[{k:'load',b:lo[0].i,v:Math.round(lo[0].v+100)}]},
   {c:'상대 표현',q:`${B(lo[1].i)} 부하가 절반으로 줄면?`,a:'answer',e:[{k:'load',b:lo[1].i,v:Math.round(lo[1].v*.5)}]},
   {c:'설비 신설',q:`${M.buses[E.none[0]].n}–${M.buses[E.none[1]].n} 선로를 신설하면?`,a:'answer',e:[{k:'add',a:E.none[0],b:E.none[1],r:null}]},
   {c:'다중 고장',q:`${n(E.L1)}랑 ${n(E.L4)} 둘 다 나가면?`,a:'answer',e:[out(E.L1),out(E.L4)]},
   {c:'표현 변형',q:`${ab(E.L3)[0]}에서 ${ab(E.L3)[1]} 가는 선로 트립되면?`,a:'answer',e:[out(E.L3)]},
   {c:'복합 3건',q:`${n(E.L4)} 선로 탈락하고 ${B(lo[2].i)} 부하 10% 늘고 ${unitQ(g.g)} 정지하면?`,a:'answer',e:[out(E.L4),{k:'load',b:lo[2].i,v:Math.round(lo[2].v*1.1)},{k:'gen',i:g.i,v:0,trip:true}]},
   {c:'애매한 질의',q:`${M.buses[E.ambB].n} 쪽 선로 하나 나가면 어때?`,a:'clarify',e:[]},
   {c:'없는 설비',q:`${M.buses[E.none[0]].n}–${M.buses[E.none[1]].n} 선로 탈락하면?`,a:'clarify',e:[]},
   {c:'변화량 없음',q:`${unitQ(E.flex.g)} 발전 좀 늘려봐`,a:'clarify',e:[]},
   {c:'상태 모순',q:`${n(notOnL)} 선로 복구하면?`,a:'clarify',e:[]}];
  return Q}
function normEvs(evs,base){const s=cloneS(base);const keys=[];
  for(const e0 of evs){const e={...e0};applyEv(s,e);
    if(e.k==='out'||e.k==='restore')keys.push(`${e.k}:${e.id}×${e.n}`);else if(e.k==='add')keys.push(`add:${[M.buses[e.a].id,M.buses[e.b].id].sort().join('-')}`);
    else if(e.k==='addgen')keys.push(`addgen:${M.buses[e.b].id}=${e.v}`);else if(e.k==='gen')keys.push(`gen:${M.gens[e.i].id}=${Math.round(e.v)}`);else if(e.k==='load')keys.push(`load:${M.buses[e.b].id}=${Math.round(e.v)}`)}
  balance(s);return {keys:keys.sort(),state:s}}
function keyText(k){const [t,v]=k.split(':');const KO={out:'탈락',restore:'복구',add:'신설',addgen:'발전기 신설',gen:'출력',load:'부하'};
  if(t==='out'||t==='restore'){const [id,nn]=v.split('×');const l=allLines(BASE).find(x=>x.id===id);return `${l?lname(l):id} ${KO[t]} ${nn}회선`}
  if(t==='gen'){const [id,mw]=v.split('=');const g=M.gens.find(x=>x.id===id);return `${g?g.name:id} 출력 ${mw}MW`}
  if(t==='load'){const [id,mw]=v.split('=');const i=M.buses.findIndex(b=>b.id===id);return `${nameOf(i)} 부하 ${mw}MW`}
  if(t==='add'){const [a,b]=v.split('-');return `${nameOf(M.buses.findIndex(x=>x.id===a))}–${nameOf(M.buses.findIndex(x=>x.id===b))} 신설`}return `${KO[t]||t} ${v}`}
function resetBench(){$('qbModel').textContent=M.name;$('n1Model').textContent=M.name;
  ['qbPass','qbAct','qbEv','qbFix','bCases','bMatch','bViol','bFix'].forEach(id=>$(id).textContent='-');
  $('qb').innerHTML='<tr><td class="d">채점 전 · 행 선택 시 정답과 엔진 답 비교</td></tr>';
  $('bench').innerHTML='<tr><td class="d">채점 전</td></tr>';$('runQB').textContent='질의 이해 채점';$('runBench').textContent='계산 정확도 채점'}
async function runQB(){const b=$('runQB');b.disabled=true;b.textContent='실행 중';const t0=performance.now();const base=BASE,Q=buildQB();
  let rows='',P=0,A=0,E=0,En=0,F=0,Fn=0;const mk=v=>v==null?'<span class="d">–</span>':v?'<span class="res p">맞음</span>':'<span class="res f">틀림</span>';
  for(const t of Q){await sleep(30);const p=parseEvents(t.q,base);const ok=p.evs.length&&!p.evs.some(e=>e.k==='amb')&&!p.issues.length;const act=ok?'answer':'clarify';const actOk=act===t.a;if(actOk)A++;
    let evOk=null,detOk=null,fixOk=null,pk=[],ek=[],note=ok?'':(p.issues[0]||(p.evs.some(e=>e.k==='amb')?'설비 후보가 여러 개라 되물음':'이벤트 없음'));
    if(t.a==='answer'){En++;const ex=normEvs(t.e,base);ek=ex.keys;const rt=dcpf(ex.state);
      if(ok){const pr=normEvs(p.evs,base);pk=pr.keys;evOk=JSON.stringify(pk)===JSON.stringify(ek);const rp=dcpf(pr.state);
        detOk=!!((!rt&&!rp)||(rt&&rp&&JSON.stringify(viol(rt).map(l=>l.id).sort())===JSON.stringify(viol(rp).map(l=>l.id).sort())));
        if(!rt)note=rp?'정답은 계통 분리인데 놓침':'계통 분리 (정답과 일치)';
        else if(viol(rt).length){Fn++;const rem=redispatch(pr.state)||shed(pr.state);
          if(rem){const s2=cloneS(ex.state);M.gens.forEach((g,k)=>{const d=rem.state.gen[k].p-pr.state.gen[k].p;if(Math.abs(d)>.01){s2.gen[k].p+=d}});s2.load=s2.load.map((v,k)=>v+(rem.state.load[k]-pr.state.load[k]));
            fixOk=clean(dcpf(s2));note=fixOk?'엔진 조치가 정답 계통에서도 위반 해소':'정답 계통에선 위반 잔존'}
          else{fixOk=!(redispatch(ex.state)||shed(ex.state));note=fixOk?'자동 대안 없음 (정답과 일치)':'존재하는 대안을 놓침'}if(fixOk)F++}
        else note=note||'위반 없음'}
      else{evOk=false;detOk=false;if(rt&&viol(rt).length)Fn++}if(evOk)E++}
    else if(ok)note='되묻지 않고 답함';
    const pass=actOk&&evOk!==false&&detOk!==false&&fixOk!==false;if(pass)P++;
    rows+=`<tr class="qr" tabindex="0"><td><span class="cat">${t.c}</span><div>${esc(t.q)}</div></td><td>${t.a==='answer'?'답변':'되묻기'}</td><td>${act==='answer'?'답변':'되묻기'}</td><td>${mk(t.a==='answer'?evOk:null)}</td><td>${mk(t.a==='answer'?detOk:null)}</td><td>${mk(fixOk)}</td><td><span class="res ${pass?'p':'f'}">${pass?'통과':'실패'}</span></td></tr>
    <tr class="qd" hidden><td colspan="7"><div class="qdg"><div><b>정답 이벤트</b>${ek.length?ek.map(k=>`<div>${keyText(k)}</div>`).join(''):'<div>없음 (되묻기가 정답)</div>'}</div><div><b>엔진 추출 결과</b>${pk.length?pk.map(k=>`<div class="${ek.includes(k)?'':'miss'}">${keyText(k)}</div>`).join(''):`<div>${ok?'없음':'되묻기'}</div>`}</div><div><b>메모</b><div>${esc(note||'-')}</div></div></div></td></tr>`}
  $('qb').innerHTML='<tr><th>질의 (자동 생성)</th><th>정답 행동</th><th>엔진 행동</th><th>이벤트 추출</th><th>위반 탐지</th><th>조치 검증</th><th>판정</th></tr>'+rows;
  $('qb').querySelectorAll('tr.qr').forEach(r=>{const f=()=>{const d=r.nextElementSibling;d.hidden=!d.hidden;r.classList.toggle('open',!d.hidden)};r.onclick=f;r.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();f()}}});
  const sec=((performance.now()-t0)/1000).toFixed(1);
  $('qbPass').textContent=`${P}/${Q.length}`;$('qbAct').textContent=Math.round(A/Q.length*100)+'%';$('qbEv').textContent=Math.round(E/En*100)+'%';$('qbFix').textContent=Fn?Math.round(F/Fn*100)+'%':'-';
  logCall('identify_equipment',`question benchmark ${Q.length} (${M.name})`,`${P} pass`,sec+'s');
  BM.last.qb=`${P}/${Q.length} 통과`;if($('v-ev').classList.contains('on'))bmRender();qbRuns.unshift({t:now(),m:M.name,k:'질의',p:`${P}/${Q.length}`,x:`이벤트 ${Math.round(E/En*100)}% · 조치 ${Fn?Math.round(F/Fn*100)+'%':'-'}`,sec});drawRuns();b.disabled=false;b.textContent='다시 실행'}
async function runN1(){const b=$('runBench');b.disabled=true;const t0=performance.now();const s=BASE,r=dcpf(s),X=xmat(s);
  const fast=n1fast(s,r,X);const tf=performance.now()-t0;let match=0,vio=0,isl=0,fix=0,need=0,esc_=0;const rows=[];
  for(let k=0;k<fast.length;k++){if(k%8===0){b.textContent=`정밀 재계산 ${k}/${fast.length}`;await sleep(0)}
    const c=fast[k],rf=n1full(s,c.l);const a=c.island?'I':c.v.map(l=>l.id).sort().join(),bb=!rf?'I':viol(rf).map(l=>l.id).sort().join();const same=a===bb;if(same)match++;
    if(!rf){isl++;rows.push({c,same,fullV:[],act:'계통 분리',post:'-',res:same?'e':'f'});continue}
    const fv=viol(rf);if(!fv.length){if(!same)rows.push({c,same,fullV:fv,act:'-',post:'-',res:'f'});continue}
    vio++;let act='',post='',res='p';
    if(need<25){need++;const s2=cloneS(s);s2.out[c.l.id]=(s2.out[c.l.id]||0)+1;const rd=redispatch(s2),sh=rd?null:shed(s2);
      if(rd){act=`재급전 ${fmt(rd.total)}MW`;post=clean(dcpf(rd.state))?'위반 해소':'잔존'}else if(sh){act=`부하 차단 ${fmt(sh.total)}MW`;post=clean(dcpf(sh.state))?'위반 해소':'잔존'}else{act='관제사 에스컬레이션';post='자동 대안 없음';res='e';esc_++}
      if(post==='위반 해소'||res==='e')fix++;else res='f'}else{act='(상위 25건만 조치 계산)';post='-';res='s'}
    if(!same)res='f';rows.push({c,same,fullV:fv,act,post,res})}
  const tt=((performance.now()-t0)/1000).toFixed(1);
  $('bCases').textContent=fast.length;$('bMatch').textContent=Math.round(match/fast.length*100)+'%';$('bViol').textContent=`${vio}건${isl?` · 분리 ${isl}`:''}`;$('bFix').textContent=need?Math.round(fix/need*100)+'%':'-';
  rows.sort((a,b)=>(a.same-b.same)||((b.fullV[0]?Math.max(...b.fullV.map(l=>ld(b.c.r||dcpf(s),l))):0)-(a.fullV[0]?Math.max(...a.fullV.map(l=>ld(a.c.r||dcpf(s),l))):0)));
  const L=res=>({p:'통과',e:'통과 (보고)',f:'실패',s:'탐지만'}[res]);
  $('bench').innerHTML='<tr><th>고장 (1회선)</th><th>빠른 스크리닝</th><th>정밀 재계산</th><th>일치</th><th>추천 조치</th><th>사후 검증</th><th>판정</th></tr>'+rows.map(x=>`<tr><td>${lname(x.c.l)}${x.c.l.kv?' '+x.c.l.kv+'kV':''}</td><td>${x.c.island?'계통 분리':x.c.v.length?x.c.v.slice(0,2).map(lname).join(', ')+(x.c.v.length>2?` 외 ${x.c.v.length-2}`:''):'위반 없음'}</td><td>${x.act==='계통 분리'?'계통 분리':x.fullV.length?x.fullV.slice(0,2).map(lname).join(', ')+(x.fullV.length>2?` 외 ${x.fullV.length-2}`:''):'위반 없음'}</td><td>${x.same?'일치':'<span class="res f">불일치</span>'}</td><td>${x.act}</td><td>${x.post}</td><td><span class="res ${x.res==='s'?'e':x.res}">${L(x.res)}</span></td></tr>`).join('')+
    `<tr><td colspan="7" class="d">위반이 없고 두 방법이 일치한 사고 ${fast.length-rows.length}건은 생략했습니다. 빠른 스크리닝 ${(tf/1000).toFixed(2)}초 · 전체 ${tt}초</td></tr>`;
  logCall('run_n1_screening',`benchmark ${fast.length} cases (${M.name})`,`match ${match}/${fast.length}`,tt+'s');
  BM.last.n1=`일치 ${Math.round(match/fast.length*100)}%`;if($('v-ev').classList.contains('on'))bmRender();qbRuns.unshift({t:now(),m:M.name,k:'N-1',p:`${Math.round(match/fast.length*100)}% 일치`,x:`위반 ${vio}건 · 조치 ${need?Math.round(fix/need*100)+'%':'-'}`,sec:tt});drawRuns();b.disabled=false;b.textContent='다시 실행'}
function drawRuns(){$('qbRuns').innerHTML='<tr><th>시각</th><th>계통 모델</th><th>성능 평가</th><th>결과</th><th>세부</th><th>소요</th></tr>'+qbRuns.map(r=>`<tr><td>${r.t}</td><td>${r.m}</td><td>${r.k}</td><td>${r.p}</td><td>${r.x}</td><td>${r.sec}s</td></tr>`).join('')}

/* ===== 학습 파이프라인 (MLOps 시뮬레이션) ===== */
const REASONS=[{id:'und',t:'질문을 잘못 이해함',m:'llm'},{id:'idn',t:'설비를 잘못 짚음',m:'llm'},{id:'exp',t:'근거·설명이 부족함',m:'llm'},{id:'sta',t:'현재 상태 값이 실제와 다름',m:'se'},{id:'fcs',t:'예측이 빗나감',m:'fc'},{id:'site',t:'대안이 현장 사정에 안 맞음',m:'rd'},{id:'calc',t:'계산 수치가 의심됨',m:'pf'}];
const MLM={llm:{name:'LLM 오케스트레이터',short:'LLM',owner:'본 과제',metric:'질의 통과율',unit:'%',better:'up',thr:90,
    vers:[{v:1,stage:'Archived',val:78.6,ds:'ds-llm-v1',date:'2026-05-29'},{v:2,stage:'Archived',val:82.1,ds:'ds-llm-v2',date:'2026-07-03'},{v:3,stage:'Archived',val:87.9,ds:'ds-llm-v3',date:'2026-08-14'},{v:4,stage:'Production',val:91.4,ds:'ds-llm-v4',date:'2026-09-18'}],
    card:{base:'온프레미스 오픈 웨이트 32B (가정)',data:'운영 대화 2,814건, 승인·부적절 612건(사유 포함)',range:'KPG 193 · IEEE 39 질의 유형 14종',limit:'자유 서술형 질의, 여러 계통을 한 번에 묻는 질의에 약함',params:'프롬프트 v7 · 사례 검색 · LoRA (lr 2e-5, epoch 2)'},
    cand:93.2,suites:[['질의 이해 통과율','%','up',90,91.4,93.2],['이벤트 추출 정확도','%','up',95,96.8,97.5],['근거 없는 수치','건','down',1,1,0],['재발 방지 문항 (과거 부적절 사례)','%','up',100,100,100]]},
  fc:{name:'수요·재생 예측',short:'예측',owner:'예측 과제',metric:'MAPE',unit:'%',better:'down',thr:4,
    vers:[{v:1,stage:'Archived',val:5.8,ds:'ds-fc-v1',date:'2026-04-30'},{v:2,stage:'Archived',val:4.9,ds:'ds-fc-v2',date:'2026-06-26'},{v:3,stage:'Archived',val:4.3,ds:'ds-fc-v3',date:'2026-08-07'},{v:4,stage:'Production',val:3.7,ds:'ds-fc-v4',date:'2026-09-25'}],
    card:{base:'시계열 트랜스포머 + 기상 입력',data:'2022~2026년 시간대별 수요·태양광·풍력, 기상 예보',range:'봄·여름·가을 운전 조건',limit:'겨울 한파 피크 학습 사례 부족',params:'입력 168시간 · 예측 24시간 · lr 1e-4'},
    cand:3.4,suites:[['MAPE (전체)','%','down',4,4.6,3.4],['피크 시간 오차','%','down',5,5.9,4.1],['재발 방지: 2024 폭염 주간','%','down',6,5.2,5.0]]},
  rd:{name:'재급전 최적화',short:'재급전',owner:'최적화 과제',metric:'조치 성공률',unit:'%',better:'up',thr:92,
    vers:[{v:1,stage:'Archived',val:84.0,ds:'ds-rd-v1',date:'2026-05-15'},{v:2,stage:'Archived',val:90.2,ds:'ds-rd-v2',date:'2026-07-10'},{v:3,stage:'Production',val:93.5,ds:'ds-rd-v3',date:'2026-08-28'}],
    card:{base:'감도 기반 탐색 + 학습형 비용 보정',data:'N-1 위반 사고 1.2만 건, 관제사 선택 대안 340건',range:'KPG 193 정상·N-1 운전',limit:'정비 일정 정보가 실시간으로 반영되지 않음',params:'후보 쌍 상위 8 · 비용 가중 0.3'},
    cand:95.1,suites:[['조치 성공률','%','up',92,93.5,95.1],['비용 증가 (기존 대비)','%','down',5,0,3.2],['재발 방지: 2023 겨울 피크 사례','건','down',0,0,2]]},
  se:{name:'AI 상태추정',short:'상태추정',owner:'상태추정 과제',metric:'잔차 RMSE',unit:'MW',better:'down',thr:12,ext:true,
    vers:[{v:1,stage:'Archived',val:15.2,ds:'외부 과제 관리',date:'2026-06-12'},{v:2,stage:'Production',val:11.8,ds:'외부 과제 관리',date:'2026-09-04'}],
    card:{base:'그래프 신경망 상태추정 (외부 과제 제공)',data:'외부 과제 자체 데이터 (계측 시계열)',range:'정상 운전, 불량 측정 3% 이하',limit:'재생에너지 비중 30% 이상 구간 검증 필요',params:'외부 과제 관리'},
    cand:10.9,suites:[['잔차 RMSE','MW','down',12,11.8,10.9],['불량 측정 탐지율','%','up',95,96.1,97.0],['학습 범위 밖 경고','%','up',100,100,100]]}};
const MLK=['llm','fc','rd','se'];
const ARCH={llm:'문장 입력 · 툴 호출',fc:'설비 ID 기준 시계열 (구성 변경 무관)',rd:'GNN · 연결 그래프 입력 (구성 변경 대응)',se:'고정 크기 입력 (모선 193) · 구성 변경 시 재학습 필요'};const CONSENT={};
const ML={state:{},run:null,runs:[],sel:'llm',task:'triage',logs:[],cands:{},gate:null,busy:false};
const T=(id,ti,sub,lane,col,row,desc,tool,full)=>({id,ti,sub,lane,col,row,desc,tool,full:full||ti});
const TASKS=[T('collect','운영 기록 수집','collect_ops_logs','o',0,0,'대화 · 툴 호출 트레이스 · 승인/부적절 · 실시간 DB 스냅샷 수집','Langfuse · 실시간 DB'),
  T('export','반출 게이트','export_gate','o',1,0,'민감 정보 필터 후 학습망 반출 · 감사 기록','망연계 시스템 · 감사 로그'),
  T('lake','원본 적재','load_data_lake','t',1,1.5,'날짜별 원본 보관 · 정제 방식 변경 시 재생성 가능','MinIO (객체 저장소)'),
  T('triage','피드백 정제','triage_feedback','t',2,1.5,'부적절 건 재현 · 원인 모델 판정 · 담당자 확인 라벨','테스트베드 재현 · 담당자 확인'),
  ...MLK.map((k,i)=>T('ds_'+k,MLM[k].short,'build_dataset_'+k,'t',3,i,'과거 데이터 · 희소 고장 사례 유지 + 신규 라벨 · 버전 부여 · 시험 데이터 고정','lakeFS · DVC','데이터셋 생성 · '+MLM[k].name)),
  ...MLK.map((k,i)=>T('tr_'+k,MLM[k].short+(MLM[k].ext?' (외부)':''),MLM[k].ext?'receive_external_'+k:'train_'+k,'t',4,i,MLM[k].ext?'외부 과제 학습 모델 수신 · 본 과제는 평가 담당':'과제별 학습 코드 · 공통 템플릿 (입력: 데이터셋 버전, 출력: MLflow 등록)',MLM[k].ext?'외부 과제 → 레지스트리':'Airflow → GPU 노드',(MLM[k].ext?'외부 학습 수신 · ':'학습 · ')+MLM[k].name)),
  ...MLK.map((k,i)=>T('reg_'+k,MLM[k].short,'register_'+k,'t',5,i,'모델 카드와 함께 후보 등록 (데이터 버전 · 성능 · 입출력 · 운전 범위 · 한계)','MLflow Model Registry','MLflow 등록 · '+MLM[k].name)),
  T('eval','평가 게이트','evaluate_testbed','t',6,1.5,'공통 시나리오 · 모델별 기준 · 재발 방지 문항로 운영 모델과 비교 · 기준 통과 시 진행','테스트베드 성능 평가'),
  T('imp','반입 게이트','import_gate','o',6,0,'책임자 승인 모델만 서비스 영역 반입 · 승인자 · 시험 결과 기록','승인 · 전자 서명'),
  T('shadow','섀도 운영','shadow_run','o',7,0,'운영 질의에 기존 모델과 병행 실행 · 관제사에게는 기존 모델 답만 표시','서빙 · 비교 로그'),
  T('deploy','배포','deploy_model','o',8,0,'운영 모델 교체 · 즉시 롤백 가능','KServe · vLLM')];
const EDGES=[['collect','export'],['export','lake'],['lake','triage'],...MLK.flatMap(k=>[['triage','ds_'+k],['ds_'+k,'tr_'+k],['tr_'+k,'reg_'+k],['reg_'+k,'eval']]),['eval','imp'],['imp','shadow'],['shadow','deploy']];
const GRID_ROWS=[['collect','수집'],['export','반출'],['lake','적재'],['triage','정제'],['tr_llm','학습 LLM'],['tr_fc','학습 예측'],['tr_rd','학습 재급전'],['tr_se','학습 상태추정'],['eval','평가 게이트'],['imp','반입'],['shadow','섀도'],['deploy','배포']];
function mlSeedRuns(){const S=(o)=>Object.fromEntries(GRID_ROWS.map(([id])=>[id,o[id]||'success']));
  ML.runs=[{id:'R-0904',date:'09-04',kind:'정기',st:S({tr_llm:'skipped',tr_fc:'skipped',tr_rd:'skipped'}),note:'상태추정 v2 외부 반입, 평가 통과 후 배포'},
    {id:'R-0911',date:'09-11',kind:'정기',st:S({export:'failed',lake:'none',triage:'none',tr_llm:'none',tr_fc:'none',tr_rd:'none',tr_se:'none',eval:'none',imp:'none',shadow:'none',deploy:'none'}),note:'반출 게이트 네트워크 오류, 다음 날 재실행'},
    {id:'R-0912',date:'09-12',kind:'재실행',st:S({tr_fc:'skipped',tr_rd:'skipped',tr_se:'skipped'}),note:'LLM v4 배포'},
    {id:'R-0925',date:'09-25',kind:'성능 저하',st:S({tr_llm:'skipped',tr_rd:'skipped',tr_se:'skipped'}),note:'예측 v4 배포 (MAPE 4.3→3.7)'},
    {id:'R-1002',date:'10-02',kind:'부적절 누적',st:S({tr_llm:'skipped',tr_fc:'skipped',tr_se:'skipped',eval:'failed',imp:'none',shadow:'none',deploy:'none'}),note:'재급전 v4 평가 게이트 불합격 (재발 방지: 2023 겨울 피크 2건 실패)'},
    {id:'R-1016',date:'10-16',kind:'정기',st:S({tr_llm:'skipped',tr_fc:'skipped',tr_rd:'skipped',tr_se:'skipped',eval:'skipped',imp:'skipped',shadow:'skipped',deploy:'skipped'}),note:'재학습 대상 없음'}];
  MLM.rd.vers.push({v:4,stage:'Rejected',val:94.6,ds:'ds-rd-v4',date:'2026-10-02',lin:'운영 보강판 ← v3',by:'소유 과제 학습 · 본 과제 보강 데이터 제공'})}
function mlCounts(){const _s=0;const rej=feedback.filter(f=>f.d==='부적절'),by={llm:0,fc:0,rd:0,se:0,pf:0,none:0};rej.forEach(f=>{const r=REASONS.find(x=>x.id===f.r);by[r?r.m:'none']++});return {logs:logs.length,jn:JN.length,fb:feedback.length,rej:rej.length,by}}
function mlTriggers(){const c=mlCounts();return {llm:{on:c.by.llm>0||true,why:c.by.llm>0?`LLM 관련 부적절 ${c.by.llm}건`:'분기 정기 재학습'},fc:{on:true,why:'성능 저하: 최근 4주 MAPE 4.6% (기준 4.0%)'},rd:{on:c.by.rd>0,why:c.by.rd>0?`현장 제약 관련 부적절 ${c.by.rd}건`:'재학습 신호 없음'},se:{on:true,why:'상태추정 과제가 v3 후보를 등록함'}}}

/* --- 그리기 --- */
const GX=c=>16+c*150,GW=128,GH=48,laneY={o:[12,100],t:[112,392]},rowY=(l,r)=>l==='o'?56:156+r*64;
function tpos(t){return {x:GX(t.col),y:rowY(t.lane,t.row)-GH/2}}
function mlDag(){const s=ML.state;let h=`<rect class="lane lo" x="4" y="${laneY.o[0]}" width="1356" height="${laneY.o[1]-laneY.o[0]}" rx="10"/><text class="lname" x="16" y="${laneY.o[0]+14}">테스트베드 서비스</text>
  <rect class="lane lt" x="4" y="${laneY.t[0]}" width="1356" height="${laneY.t[1]-laneY.t[0]}" rx="10"/><text class="lname" x="16" y="${laneY.t[0]+14}">학습망</text>`+[[3,'데이터셋 생성'],[4,'모델 학습'],[5,'MLflow 등록']].map(([c,t])=>`<text class="lcol" x="${GX(c)+4}" y="${laneY.t[0]+14}">${t}</text>`).join('');
  EDGES.forEach(([a,b])=>{const A=TASKS.find(t=>t.id===a),B=TASKS.find(t=>t.id===b),p=tpos(A),q=tpos(B);let d;
    if(A.col===B.col){const up=q.y<p.y;const x=p.x+GW/2;d=up?`M${x} ${p.y} L${x} ${q.y+GH}`:`M${x} ${p.y+GH} L${x} ${q.y}`}
    else{const x1=p.x+GW,y1=p.y+GH/2,x2=q.x,y2=q.y+GH/2,m=(x1+x2)/2;d=`M${x1} ${y1} C${m} ${y1},${m} ${y2},${x2} ${y2}`}
    const st=s[b]==='running'?'run':s[a]==='success'&&s[b]&&s[b]!=='none'&&s[b]!=='skipped'?'ok':s[b]==='skipped'?'sk':'';
    h+=`<path class="edge ${st}" d="${d}" marker-end="url(#mlarr)"/>`;if(st==='run')h+=`<circle class="flow" r="3.2"><animateMotion dur="1.1s" repeatCount="indefinite" path="${d}"/></circle>`});
  TASKS.forEach(t=>{const p=tpos(t),st=s[t.id]||'none',sel=ML.task===t.id;
    h+=`<g class="tk st-${st}${sel?' sel':''}" data-t="${t.id}" tabindex="0" role="button" aria-label="${t.full} ${ST_TXT[st]}"><rect x="${p.x}" y="${p.y}" width="${GW}" height="${GH}" rx="8"/><rect class="bar" x="${p.x}" y="${p.y}" width="5" height="${GH}" rx="2"/>
      <text class="tt" x="${p.x+14}" y="${p.y+20}">${t.ti}</text><text class="ts2" x="${p.x+14}" y="${p.y+36}">${t.sub}</text><circle class="dot" cx="${p.x+GW-11}" cy="${p.y+11}" r="4"/></g>`});
  $('mlDag').innerHTML=`<defs><marker id="mlarr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M1 1L9 5L1 9" fill="none" stroke="context-stroke" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>`+h;
  $('mlDag').querySelectorAll('.tk').forEach(g=>{const f=()=>{ML.task=g.dataset.t;mlDag();mlTask()};g.onclick=f;g.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();f()}}})}
const ST_TXT={none:'대기 전',queued:'대기',running:'실행 중',success:'성공',failed:'실패',skipped:'건너뜀',blocked:'승인 대기',ext:'외부 수신'};
function mlTask(){const t=TASKS.find(x=>x.id===ML.task),st=ML.state[t.id]||'none';const lg=ML.logs.filter(l=>l.task===t.id).slice(-6);
  $('mlTask').innerHTML=`<div class="mt-h"><b>${t.full}</b><span class="stb st-${st}">${ST_TXT[st]}</span></div><code class="mt-id">${t.sub}</code><p>${t.desc}</p><div class="mt-k"><span>사용 도구</span><b>${t.tool}</b></div><div class="mt-k"><span>구역</span><b>${t.lane==='o'?'서비스 영역':'학습망'}</b></div>${lg.length?`<div class="mt-log">${lg.map(l=>`<div>${l.t} ${esc(l.msg)}</div>`).join('')}</div>`:'<p class="d">이번 실행 로그 없음</p>'}`}
function mlGrid(){const runs=ML.runs.slice(-8);
  let h=`<table class="mlgrid"><tr><th></th>${runs.map(r=>`<th title="${esc(r.note)}"><div>${r.date}</div><span>${r.kind}</span></th>`).join('')}</tr>`;
  GRID_ROWS.forEach(([id,lab])=>{h+=`<tr><td class="gl">${lab}</td>${runs.map(r=>`<td><i class="sq st-${r.st[id]||'none'}" title="${r.id} · ${lab} · ${ST_TXT[r.st[id]||'none']}"></i></td>`).join('')}</tr>`});
  h+=`</table><div class="mlrun-note">${runs.slice(-3).reverse().map(r=>`<div><b>${r.id}</b> ${esc(r.note)}</div>`).join('')}</div>`;$('mlGrid').innerHTML=h}
function mlLogs(){$('mlLog').innerHTML=ML.logs.slice(-60).map(l=>`<div class="lv-${l.lv}"><span>${l.t}</span><span>${l.task}</span><span>${esc(l.msg)}</span></div>`).join('')||'<div class="d" style="padding:10px 12px">실행 대기</div>';const el=$('mlLog');el.scrollTop=el.scrollHeight}
function mlReg(){const k=ML.sel,m=MLM[k];
  $('mlTabs').innerHTML=MLK.map(x=>{const pv=MLM[x].vers.find(v=>v.stage==='Production');return `<button type="button" class="mtab${x===k?' on':''}" data-k="${x}"><b>${MLM[x].name}</b><span>${x==='llm'?'LLM 트랙':'해석 모델 트랙'} · v${pv.v} 운영</span></button>`}).join('');
  $('mlTabs').querySelectorAll('.mtab').forEach(b=>b.onclick=()=>{ML.sel=b.dataset.k;mlReg()});
  const vs=m.vers.slice().sort((a,b)=>b.v-a.v);
  $('mlVers').innerHTML=`<tr><th>버전</th><th>계보</th><th>단계</th><th>${m.metric}</th><th>데이터셋</th><th>등록일</th></tr>`+vs.map(v=>`<tr><td><b>v${v.v}</b></td><td class="lin">${v.lin||'원본'}<div class="d">${v.by||m.owner}</div></td><td><span class="stage sg-${v.stage}">${{Production:'운영',Staging:'후보',Archived:'보관',Rejected:'불합격',Shadow:'섀도'}[v.stage]}</span></td><td>${v.val}${m.unit}</td><td><code>${v.ds}</code></td><td>${v.date}</td></tr>`).join('');
  const pts=m.vers.slice().sort((a,b)=>a.v-b.v),W=420,H=150,P=34,vals=pts.map(p=>p.val).concat(m.thr),mn=Math.min(...vals),mx=Math.max(...vals),pad=(mx-mn)*.25||1,y0=mn-pad,y1=mx+pad;
  const X=i=>P+i*(W-P-16)/Math.max(1,pts.length-1),Y=v=>H-24-(v-y0)/(y1-y0)*(H-40);
  let g=`<line class="cthr" x1="${P}" x2="${W-10}" y1="${Y(m.thr)}" y2="${Y(m.thr)}"/><text class="cl thr" x="${P+2}" y="${Y(m.thr)+(m.better==='up'?13:-5)}">기준 ${m.thr}${m.unit}</text>`;
  g+=`<path class="cline" d="${pts.map((p,i)=>`${i?'L':'M'}${X(i)} ${Y(p.val)}`).join(' ')}"/>`;pts.forEach((p,i)=>{g+=`<circle class="cpt sg-${p.stage}" cx="${X(i)}" cy="${Y(p.val)}" r="5"><title>v${p.v} ${p.val}${m.unit}</title></circle><text class="cl" x="${X(i)}" y="${H-6}" text-anchor="middle">v${p.v}</text><text class="cv" x="${X(i)}" y="${Y(p.val)-10}" text-anchor="middle">${p.val}</text>`});
  $('mlChart').innerHTML=g;$('mlChartT').textContent=`${m.metric} 추이 (${m.better==='up'?'높을수록':'낮을수록'} 좋음)`;
  const c=m.card;$('mlCard').innerHTML=`<div class="mc-k"><span>모델</span><b>${c.base}</b></div><div class="mc-k"><span>학습 데이터</span><b>${c.data}</b></div><div class="mc-k"><span>검증된 운전 범위</span><b>${c.range}</b></div><div class="mc-k"><span>알려진 한계</span><b>${c.limit}</b></div><div class="mc-k"><span>학습 설정</span><b>${c.params}</b></div><div class="mc-k"><span>입력 구조</span><b>${ARCH[k]}</b></div><div class="mc-k"><span>학습 트랙</span><b>${k==='llm'?'LLM 오케스트레이터':'해석 모델 (LLM이 API로 호출)'}</b></div><div class="mc-k"><span>교체 조건</span><b>${m.owner==='본 과제'?'평가 게이트 · 책임자 승인':'평가 게이트 · 책임자 승인 · 소유 과제 동의'}</b></div>`}
function mlTrig(){const c=mlCounts(),tg=mlTriggers();const mx=Math.max(1,...Object.values(c.by));const lab={llm:'LLM',se:'상태추정',fc:'예측',rd:'재급전',pf:'조류계산',none:'사유 미지정'};
  $('mlK').innerHTML=`<div class="kpi"><span>운영 기록 (툴 호출)</span><b>${fmt(c.logs)}건</b></div><div class="kpi"><span>관제사 판단</span><b>${c.fb}건</b></div><div class="kpi"><span>부적절 (사유 포함)</span><b>${c.rej}건</b></div><div class="kpi"><span>다음 실행 대상</span><b>${MLK.filter(k=>tg[k].on).length}개 모델</b></div>`;
  const spark=[3.6,3.7,3.6,3.8,3.9,3.8,4.0,4.1,4.3,4.4,4.6,4.6];const sw=180,sh=44,sy=v=>sh-4-(v-3.4)/(1.4)*(sh-8);
  $('mlTrig').innerHTML=`<div class="tg"><div class="tg-h"><i class="ti ti-calendar" aria-hidden="true"></i>정기 주기</div><p>분기 정기 재학습. 다음 예정 11월 1일 (D-9)</p><span class="tgs on">LLM 대상</span></div>
   <div class="tg"><div class="tg-h"><i class="ti ti-trending-up" aria-hidden="true"></i>성능 저하</div><svg viewBox="0 0 ${sw} ${sh}" class="spark"><line x1="0" x2="${sw}" y1="${sy(4)}" y2="${sy(4)}" class="cthr"/><path class="cline" d="${spark.map((v,i)=>`${i?'L':'M'}${i*(sw-6)/11+3} ${sy(v)}`).join(' ')}"/></svg><p>예측 MAPE 최근 12주 추이, 4주째 기준(4.0%) 초과</p><span class="tgs on">예측 대상</span></div>
   <div class="tg"><div class="tg-h"><i class="ti ti-message-x" aria-hidden="true"></i>부적절 누적 (데모 실제 값)</div>${Object.entries(c.by).filter(([k,v])=>v>0||['llm','rd','se','fc'].includes(k)).map(([k,v])=>`<div class="hb"><span>${lab[k]}</span><i style="width:${v/mx*100}%"></i><b>${v}</b></div>`).join('')}<p class="d">관제 화면에서 부적절하며 고른 사유가 모델별로 모입니다.</p><span class="tgs ${tg.rd.on?'on':''}">${tg.rd.on?'재급전 대상':'재급전 신호 없음'}</span></div>`}
function mlGate(){const g=ML.gate;if(!g){$('mlGate').innerHTML='<p class="d" style="padding:14px 16px;margin:0">파이프라인이 평가 게이트까지 오면 후보 모델과 운영 모델의 비교 결과가 여기에 나옵니다.</p>';return}
  let h='<div class="gates">';g.items.forEach(it=>{const m=MLM[it.k];h+=`<div class="gcard ${it.pass?'pass':'fail'}"><div class="gc-h"><b>${m.name}</b><span class="stage sg-${it.pass?'Staging':'Rejected'}">${it.pass?'합격':'불합격'}</span></div><div class="gc-s">v${it.prodV} 운영 → v${it.v} 후보 · ${(MLM[it.k].vers.find(v=>v.v===it.v)||{}).lin||''}</div>${CONSENT[it.k]!==undefined&&it.pass?`<label class="cons"><input type="checkbox" data-cons="${it.k}" ${CONSENT[it.k]?'checked':''}> ${m.owner} 동의 확인</label>`:''}
    ${it.suites.map(s=>{const [n,u,b,thr,pv,cv]=s,ok=b==='up'?cv>=thr:cv<=thr,mxv=Math.max(thr,pv,cv)*1.15||1;return `<div class="suite"><div class="su-h"><span>${n}</span><b class="${ok?'ok':'ng'}">${ok?'통과':'실패'}</b></div><div class="bars"><div class="br"><span>운영</span><i class="bp" style="width:${pv/mxv*100}%"></i><em>${pv}${u}</em></div><div class="br"><span>후보</span><i class="bc ${ok?'':'ng'}" style="width:${cv/mxv*100}%"></i><em>${cv}${u}</em></div><i class="thr" style="left:calc(36px + (100% - 90px) * ${thr/mxv})" title="기준 ${thr}${u}"></i></div></div>`}).join('')}</div>`});
  h+='</div>';const any=g.items.some(i=>i.pass);
  h+=`<div class="gate-act">${g.done?`<span class="d">${esc(g.done)}</span>`:any?`<button class="btn pri" id="mlApprove" type="button">합격 모델 승인 · 섀도 운영 시작</button><button class="btn" id="mlRejectAll" type="button">전체 보류</button><span class="d">불합격 모델: 자동 보관 · 담당 과제 통보</span>`:'<span class="d">합격 후보 없음 · 운영 모델 유지</span>'}</div>`;
  $('mlGate').innerHTML=h;$('mlGate').querySelectorAll('[data-cons]').forEach(c=>c.onchange=()=>{CONSENT[c.dataset.cons]=c.checked;addJN('apply',`${MLM[c.dataset.cons].owner} 동의 ${c.checked?'확인':'취소'}`)});const ap=$('mlApprove');if(ap){ap.onclick=()=>mlApprove(true);$('mlRejectAll').onclick=()=>mlApprove(false)}}
function mlAll(){mlDag();mlTask();mlGrid();mlLogs();mlReg();mlTrig();mlGate();$('mlRunBtn').disabled=ML.busy;$('mlRunBtn').textContent=ML.busy?'실행 중…':'파이프라인 실행'}

/* --- 실행 --- */
const mlNow=()=>{const d=new Date();return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`};
function mlLog(task,msg,lv){ML.logs.push({t:mlNow(),task,msg,lv:lv||'i'});mlLogs();if(ML.task===task)mlTask()}
function mlSet(id,st){ML.state[id]=st;mlDag();if(ML.task===id)mlTask()}
async function mlStep(id,ms,msgs,final){mlSet(id,'running');for(const m of msgs){await sleep(ms/msgs.length);mlLog(id,m)}mlSet(id,final||'success')}
async function mlRun(){if(ML.busy)return;ML.busy=true;ML.gate=null;ML.cands={};ML.logs=[];ML.state=Object.fromEntries(TASKS.map(t=>[t.id,'queued']));const c=mlCounts(),tg=mlTriggers();
  const d=new Date(),rid=`R-${pad(d.getMonth()+1)}${pad(d.getDate())}`;ML.run={id:rid,date:`${pad(d.getMonth()+1)}-${pad(d.getDate())}`,kind:'수동 실행'};mlAll();addJN('train',`학습 파이프라인 ${rid} 실행`);
  mlLog('collect',`DAG grid_ai_retrain 실행 시작 · 실행 ID ${rid}`);
  const SNw=SNAP.filter(s=>!s.ds),SNl=SNw.filter(s=>s.labels.length);await mlStep('collect',1200,[`툴 호출 트레이스 ${fmt(c.logs)}건, 운영 일지 ${c.jn}건 수집`,`관제사 판단 ${c.fb}건 (부적절 ${c.rej}건) 수집`,`스냅샷 ${SNw.length}개 수집 (정기 ${SNw.filter(s=>s.kind==='정기').length} · 사건 ${SNw.filter(s=>s.kind==='사건').length} · 판단 ${SNw.filter(s=>s.kind==='판단').length}), 라벨 연결 ${SNl.length}개`]);
  await mlStep('export',1000,['민감 정보 필터 적용 (관제사 ID 가명 처리)','반출 승인 확인 · 감사 로그 기록']);
  await mlStep('lake',800,[`원본 적재 s3://grid-lake/raw/${d.getFullYear()}/${pad(d.getMonth()+1)}/${pad(d.getDate())}/`,`스냅샷 ${SNw.length}개 → grid-lake/snapshots/ (state.parquet · topology.cim.xml · meta.json), 해시 검증 완료`]);
  await mlStep('triage',1400,[`부적절 ${c.rej}건 테스트베드 재현`,`원인 판정: LLM ${c.by.llm} · 예측 ${c.by.fc} · 재급전 ${c.by.rd} · 상태추정 ${c.by.se} · 조류계산 ${c.by.pf}${c.by.none?` · 미지정 ${c.by.none}`:''}`,`담당자 확인 라벨 ${c.rej-c.by.none}건 생성`]);
  await Promise.all(MLK.map(async k=>{const m=MLM[k],on=tg[k].on;
    if(!on){['ds_','tr_','reg_'].forEach(p=>mlSet(p+k,'skipped'));mlLog('tr_'+k,`${m.name}: ${tg[k].why} → 건너뜀`,'w');return}
    const pv0=m.vers.find(v=>v.stage==='Production'),own=m.owner!=='본 과제',nv=Math.max(...m.vers.map(v=>v.v))+1;
    if(m.ext){mlSet('ds_'+k,'skipped');await mlStep('tr_'+k,1600,[`${m.owner}에서 v${nv} 후보 수신`,'모델 카드·서명 확인'],'ext')}
    else{await mlStep('ds_'+k,900,[`ds-${k}-v${nv} 생성 · ${MODE==='live'?'실계통 운영 데이터 (적재 서버) 기반 보강':'공개 계통 시뮬레이션 데이터 기반'} · 과거 데이터 유지 + 신규 라벨`,k==='llm'?`판단 스냅샷 ${SNw.filter(s=>s.kind==='판단').length}개 + 라벨 ${SNl.length}개 포함`:`정기·사건 스냅샷 ${SNw.filter(s=>s.kind!=='판단').length}개 포함`,'시험용 데이터셋은 고정 (변경 없음)']);SNw.forEach(s=>{if(!s.ds&&(k==='llm'?s.kind==='판단':s.kind!=='판단'))s.ds=`ds-${k}-v${nv}`});
      await mlStep('tr_'+k,1800+MLK.indexOf(k)*500,[`${m.name} 학습 시작 · 트리거: ${tg[k].why}`,`epoch 1/2 · 검증 ${m.metric} 개선 중`,`학습 완료 · 검증 ${m.metric} ${m.cand}${m.unit}`])}
    await mlStep('reg_'+k,600,[`MLflow 등록: ${m.name} v${nv} (Staging)`]);ML.cands[k]=nv;m.vers.push({v:nv,stage:'Staging',val:m.cand,ds:m.ext?'외부 과제 관리':`ds-${k}-v${nv}`,date:`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`,lin:m.ext?'원본 (외부 과제 제공)':own?`운영 보강판 ← v${pv0.v} (ops${m.vers.filter(x=>(x.lin||'').includes('보강판 ← v'+pv0.v)).length+1})`:`재학습 ← v${pv0.v}`,by:m.ext?m.owner:own?'소유 과제 학습 · 본 과제 보강 데이터 제공':'본 과제'});if(own&&!m.ext)CONSENT[k]=false;mlReg()}));
  mlSet('eval','running');const BL=bmBaseline();mlLog('eval',`성능 평가 문제지 v${BM.ver} 로드 · 질의 ${BL.q.n}문항 · 재발 방지 문항 ${BL.r.n}문항 (부적절 사례)`);
  mlLog('eval',`현재 운영 엔진 실제 채점: 질의 ${BL.q.pct}% · 재발 방지 ${BL.r.pct}% (${BL.r.p}/${BL.r.n})`,BL.r.pct<100?'w':'i');
  {const s=MLM.llm.suites;s[0]=['질의 이해 통과율 (실채점 기준선)','%','up',90,BL.q.pct,Math.min(100,+(BL.q.pct+1.8).toFixed(1))];s[3]=['재발 방지 문항 (부적절 사례 '+BL.r.n+'문항)','%','up',100,BL.r.pct,100]}
  const items=[];for(const k of Object.keys(ML.cands)){const m=MLM[k];await sleep(700);let pass=true;
    m.suites.forEach(s=>{const [n,u,b,thr,pv,cv]=s,ok=b==='up'?cv>=thr:cv<=thr;if(!ok)pass=false;mlLog('eval',`${m.short} · ${n}: 운영 ${pv}${u} / 후보 ${cv}${u} (기준 ${b==='up'?'≥':'≤'}${thr}) → ${ok?'통과':'실패'}`,ok?'i':'e')});
    items.push({k,v:ML.cands[k],prodV:m.vers.find(v=>v.stage==='Production').v,pass,suites:m.suites.map(s=>s.slice())});if(!pass){const vv=m.vers.find(v=>v.v===ML.cands[k]);vv.stage='Rejected';mlLog('eval',`${m.name} v${ML.cands[k]} 불합격 → 보관, ${m.owner}에 결과 전달`,'e')}}
  ML.gate={items};const any=items.some(i=>i.pass);mlSet('eval',items.length?(any?'success':'failed'):'skipped');
  if(any){mlSet('imp','blocked');mlLog('imp','책임자 승인 대기 · 아래 평가 게이트에서 승인하세요','w')}else{['imp','shadow','deploy'].forEach(t=>mlSet(t,'none'));mlFinish()}
  ML.busy=false;mlAll();$('mlGate').scrollIntoView({behavior:'smooth',block:'center'})}
async function mlApprove(ok){const g=ML.gate;if(!g||g.done)return;if(ROLE!=='책임자'){toast('모델 승인: 당직 책임자 역할 필요');return}ML.busy=true;
  if(!ok){g.done='전체 보류 · 후보는 레지스트리에 보관';g.items.forEach(i=>{const v=MLM[i.k].vers.find(x=>x.v===i.v);if(v.stage==='Staging')v.stage='Archived'});['imp','shadow','deploy'].forEach(t=>mlSet(t,'skipped'));mlLog('imp','승인 보류','w');addJN('reject','학습 파이프라인 후보 모델 전체 보류');ML.busy=false;mlFinish();mlAll();return}
  const held=g.items.filter(i=>i.pass&&CONSENT[i.k]===false);held.forEach(i=>{const v=MLM[i.k].vers.find(x=>x.v===i.v);v.stage='Archived';v.lin+=' · 동의 대기 보류';mlLog('imp',`${MLM[i.k].name} v${i.v}: ${MLM[i.k].owner} 동의 없음 → 보류`,'w')});
  g.done='승인 · 섀도 운영 → 배포 진행';mlGate();const ps=g.items.filter(i=>i.pass&&CONSENT[i.k]!==false);if(!ps.length){g.done='동의 대기로 배포 없음';ML.busy=false;mlFinish();mlAll();return}
  await mlStep('imp',800,[`승인자: 운영 책임자 · 대상 ${ps.map(i=>`${MLM[i.k].short} v${i.v}`).join(', ')}`,'서비스 영역 반입 완료 · 서명 검증']);
  ps.forEach(i=>{const v=MLM[i.k].vers.find(x=>x.v===i.v);v.stage='Shadow'});mlReg();
  await mlStep('shadow',2600,['운영 질의 1,240건에 대해 기존·후보 모델 병행 실행 (시연에선 압축)','결과 일치율 97.8%, 불일치 27건 검토 → 후보 판단이 더 적절 21건','이상 없음 · 배포 가능']);
  await mlStep('deploy',900,ps.map(i=>`${MLM[i.k].name} v${i.v} 운영 전환 · 이전 v${i.prodV} 보관 (롤백 가능)`));
  ps.forEach(i=>{const m=MLM[i.k];m.vers.forEach(v=>{if(v.stage==='Production')v.stage='Archived'});m.vers.find(x=>x.v===i.v).stage='Production';m.suites.forEach(s=>{s[4]=s[5];if(s[3]!==s[5]||s[2]==='down'&&s[5]>0)s[5]=+(s[2]==='up'?Math.min(100,s[5]+0.7):Math.max(0,s[5]-(s[1]==='건'?1:0.2))).toFixed(1)});m.cand=m.suites[0][5]});
  g.done=`배포 완료: ${ps.map(i=>`${MLM[i.k].short} v${i.v}`).join(', ')}`;addJN('apply',`학습 파이프라인 배포: ${ps.map(i=>`${MLM[i.k].name} v${i.v}`).join(', ')}`);logCall('save_workspace',`deploy models ${ps.map(i=>i.k+':v'+i.v).join(',')}`,'deployed (approved)','0.00s');
  ML.busy=false;mlFinish();mlAll()}
function mlFinish(){const r=ML.run;if(!r)return;const st={};GRID_ROWS.forEach(([id])=>{st[id]=ML.state[id]==='ext'?'success':ML.state[id]==='blocked'?'none':ML.state[id]||'none'});
  const dep=Object.keys(ML.cands).filter(k=>MLM[k].vers.find(v=>v.v===ML.cands[k]).stage==='Production');
  ML.runs.push({...r,st,note:dep.length?`${dep.map(k=>`${MLM[k].short} v${ML.cands[k]}`).join(', ')} 배포`:'배포 없음'});ML.run=null;mlGrid()}
function mlLineSeed(){const L=MLM.llm.vers;L.forEach((v,i)=>{v.lin=i?`재학습 ← v${L[i-1].v}`:'초기 학습';v.by='본 과제'});
  const F=MLM.fc;F.vers.forEach(v=>{v.lin='원본';v.by='예측 과제'});F.vers.push({v:5,stage:'Archived',val:3.6,ds:'ds-fc-ops1',date:'2026-10-07',lin:'운영 보강판 ← v4 (ops1) · 섀도 후 미채택',by:'소유 과제 학습 · 본 과제 보강 데이터 제공'},{v:6,stage:'Archived',val:3.5,ds:'ds-fc-ops2',date:'2026-10-16',lin:'운영 보강판 ← v4 (ops2) · 동의 대기 보류',by:'소유 과제 학습 · 본 과제 보강 데이터 제공'});
  const R=MLM.rd;R.vers.forEach(v=>{if(!v.lin){v.lin='원본';v.by='최적화 과제'}});const r4=R.vers.find(v=>v.v===4);if(r4)r4.lin='운영 보강판 ← v3 (ops1) · 재발 방지 실패';R.vers.push({v:5,stage:'Staging',val:95.0,ds:'ds-rd-ops2',date:'2026-10-20',lin:'운영 보강판 ← v3 (ops2) · 최적화 과제 검토 중',by:'최적화 과제 (보강 데이터 수령 후 직접 학습)'});
  MLM.se.vers.forEach(v=>{v.lin='원본 (외부 과제 제공)';v.by='상태추정 과제'})}
function mlInit(){mlSeedRuns();mlLineSeed();ML.state=Object.fromEntries(TASKS.map(t=>[t.id,'none']));mlAll();$('mlRunBtn').onclick=mlRun}

/* ===== 지식 베이스: 규정 카탈로그 · 벡터 검색 · 지식그래프 ===== */
const KPXU={rule:'https://kpx.or.kr/board.es?mid=a11002010000&bid=0030',detail:'https://kpx.or.kr/board.es?mid=a11002020000&bid=0031',etc:'https://kpx.or.kr/board.es?mid=a11002040000&bid=0032',law:'https://kpx.or.kr/menu.es?mid=a11001010000',notice:'https://kpx.or.kr/menu.es?mid=a11001020000'};
const KDOC=[
 {id:'law-ea',kind:'법령',t:'전기사업법',sum:'전기사업의 허가, 전력시장과 전력거래소, 계통 운영의 법적 근거',src:KPXU.law,ver:'현행',topics:['시장','계통운영','근거']},
 {id:'law-re',kind:'법령',t:'신에너지 및 재생에너지 개발·이용·보급 촉진법',sum:'신·재생에너지 보급과 공급의무화(RPS) 등의 법적 근거',src:KPXU.law,ver:'현행',topics:['재생에너지']},
 {id:'n-rel',kind:'고시',t:'전력계통 신뢰도 및 전기품질 유지 기준',sum:'계통 신뢰도(상정사고 대비 등)와 주파수·전압 같은 전기품질 유지 기준',src:KPXU.notice,ver:'현행',parent:'law-ea',topics:['N-1','신뢰도','주파수','전압','과부하','상정사고']},
 {id:'n-op',kind:'고시',t:'발전사업세부허가기준, 전기요금 산정기준, 전력량계허용오차 및 전력계통운영업무에 관한 고시',sum:'발전사업 허가 세부 기준과 전력계통 운영 업무 등에 관한 고시',src:KPXU.notice,ver:'현행',parent:'law-ea',topics:['계통운영','급전']},
 {id:'n-cap',kind:'고시',t:'전력거래가격 상한에 관한 고시',sum:'전력거래가격의 상한 적용 기준',src:KPXU.notice,ver:'현행',parent:'law-ea',topics:['가격','정산']},
 {id:'n-cfd',kind:'고시',t:'정부승인차액계약 운영기준',sum:'정부승인 차액계약의 운영 기준',src:KPXU.notice,ver:'현행',parent:'law-ea',topics:['계약']},
 {id:'n-dpa',kind:'고시',t:'재생에너지전기공급사업자의 직접전력거래 등에 관한 고시',sum:'재생에너지 전기공급사업자의 직접 전력거래 기준',src:KPXU.notice,ver:'현행',parent:'law-ea',topics:['재생에너지','거래']},
 {id:'n-small',kind:'고시',t:'소규모 신·재생 에너지 발전 전력 등의 거래에 관한 지침',sum:'소규모 신·재생 발전 전력의 거래 방법',src:KPXU.notice,ver:'현행',parent:'law-ea',topics:['재생에너지','거래']},
 {id:'n-rps',kind:'고시',t:'신·재생 에너지 공급의무화제도 및 연료 혼합의무화제도 관리·운영지침',sum:'공급의무화(RPS) 제도의 관리·운영 절차',src:KPXU.notice,ver:'현행',parent:'law-re',topics:['재생에너지','RPS']},
 {id:'rule',kind:'규칙',t:'전력시장운영규칙',sum:'입찰, 가격 결정, 급전, 정산 등 전력시장 운영 전반에 관한 규칙',src:KPXU.rule,ver:'2026.7.25 시행 (개정 이력 94건)',parent:'law-ea',topics:['시장','급전','정산','출력제어','계통운영']},
 {id:'d-cost',kind:'세부운영규정',t:'비용평가세부운영규정',sum:'발전기 비용 평가 방법과 절차 (재급전 비용 산정의 기초)',src:KPXU.detail,ver:'2026.9.28 개정',dept:'시장운영팀',parent:'rule',topics:['비용','재급전','발전기']},
 {id:'d-grid',kind:'세부운영규정',t:'계통평가세부운영규정',sum:'계통 영향 평가 방법과 절차',src:KPXU.detail,ver:'2026.2.23 개정',dept:'계통개발팀',parent:'rule',topics:['계통평가','송전','신설']},
 {id:'d-cfd',kind:'세부운영규정',t:'정부승인차액계약 세부운영규정',sum:'정부승인 차액계약의 세부 운영 절차',src:KPXU.detail,ver:'2026.9.11 개정',dept:'계약시장팀',parent:'rule',topics:['계약']},
 {id:'etc',kind:'기타 규칙',t:'기타 규칙 (KPX 기타 게시판)',sum:'시장·계통 운영 관련 기타 규칙 모음 (목록 연동 예정)',src:KPXU.etc,ver:'연동 예정',parent:'rule',topics:['기타']}];
const KCON=[{id:'c-n1',t:'N-1 신뢰도',docs:['n-rel','rule'],int:['운영절차 2.4']},{id:'c-ol',t:'선로 과부하 조치',docs:['n-rel','rule'],int:['운영절차 3.2','운영절차 5.3']},{id:'c-rd',t:'재급전',docs:['rule','d-cost'],int:['운영절차 3.2']},{id:'c-shed',t:'부하 차단',docs:['n-rel'],int:['운영절차 4.1']},{id:'c-trip',t:'대용량 발전기 탈락',docs:['n-rel'],int:['운영절차 7.2']},{id:'c-out',t:'설비 휴전·신설',docs:['d-grid','n-op'],int:['운영절차 6.1']},{id:'c-curt',t:'재생에너지 출력제어',docs:['rule','law-re'],int:[]}];
const kbDoc=id=>KDOC.find(d=>d.id===id);
function kbChunks(){const c=[];KDOC.forEach(d=>c.push({id:d.id+'#요약',doc:d.t,kind:d.kind,ver:d.ver,text:d.sum,topics:d.topics,src:d.src,ext:true}));
  DOCS.forEach(d=>c.push({id:d.id,doc:'내부 운영절차 (가상)',kind:'운영절차',ver:'데모',text:`${d.t}: ${d.b}`,topics:d.k,ext:false}));
  feedback.forEach((f,i)=>c.push({id:'case-'+i,doc:'과거 사례',kind:'사례',ver:f.t,text:`“${f.q}” → ${f.d}${f.r?` (사유: ${(REASONS.find(x=>x.id===f.r)||{}).t||''})`:''}${f.a!=='-'?`, 대안: ${f.a}`:''}`,topics:[],ext:false}));return c}
const SYN={'과부하':['과부하','정격','초과','부하율'],'N-1':['N-1','상정사고','신뢰도','안전도'],'재급전':['재급전','감발','증발','비용','발전기'],'차단':['차단','부하'],'발전기':['발전기','트립','정지','탈락','원자력','예비력'],'휴전':['휴전','작업','신설'],'주파수':['주파수','예비력'],'재생':['재생','태양광','풍력','출력제어']};
function kbSearch(q,filter){const toks=q.split(/[\s,·]+/).filter(Boolean);const exp=new Set(toks);toks.forEach(t=>Object.entries(SYN).forEach(([k,v])=>{if(t.includes(k)||v.some(x=>t.includes(x)))v.forEach(x=>exp.add(x))}));
  return kbChunks().filter(c=>!filter||filter==='전체'||c.kind===filter).map(c=>{const hay=c.text+' '+c.topics.join(' ')+' '+c.doc;let s=0;exp.forEach(t=>{if(hay.includes(t))s+=toks.includes(t)?1:.55});const sc=s?Math.min(.97,.42+s*.11):0;return {...c,sc}}).filter(c=>c.sc>0).sort((a,b)=>b.sc-a.sc).slice(0,8)}
function kbRefs(keys){const ds=new Set();KCON.filter(c=>keys.includes(c.id)).forEach(c=>c.docs.forEach(d=>ds.add(d)));return [...ds].slice(0,2).map(id=>kbDoc(id)).map(d=>`<div class="cite ext"><b>${d.t}</b> <span class="kver">${d.ver}</span> ${d.sum}. <a href="${d.src}" target="_blank" rel="noopener">KPX 원문</a> <span class="d">· 조항은 원문 확인 필요</span></div>`).join('')}

/* --- 지식그래프 --- */
let KG={focus:'c-ol',hist:[]};
function kgNode(id){const [t,v]=id.split(':');
  if(id.startsWith('c-'))return {id,label:KCON.find(c=>c.id===id).t,type:'개념'};
  if(kbDoc(id)){const d=kbDoc(id);return {id,label:d.t.length>17?d.t.slice(0,16)+'…':d.t,full:d.t,type:d.kind==='세부운영규정'||d.kind==='기타 규칙'?'규정':d.kind==='규칙'?'규정':d.kind}}
  if(t==='doc')return {id,label:v,type:'운영절차'};
  if(t==='line'){const l=allLines(twin).find(x=>x.id===v);return l?{id,label:lname(l)+(l.kv?` ${l.kv}kV`:''),type:'선로'}:null}
  if(t==='bus')return {id,label:nameOf(+v),type:'변전소'};
  if(t==='plant')return {id,label:v,type:'발전소'};
  if(t==='gtype')return {id,label:v,type:'발전원'};
  if(t==='case'){const f=feedback[+v];return f?{id,label:(f.d==='부적절'?'부적절: ':'승인: ')+(f.q.length>12?f.q.slice(0,12)+'…':f.q),full:`${f.t} ${f.q} → ${f.d}`,type:'사례'}:null}
  return null}
function kgEdges(id){const E=[];const [t,v]=id.split(':');const add=(b,rel)=>{if(kgNode(b))E.push({b,rel})};
  if(id.startsWith('c-')){const c=KCON.find(x=>x.id===id);c.docs.forEach(d=>add(d,'근거 규정'));c.int.forEach(d=>add('doc:'+d,'내부 절차'));
    if(id==='c-ol'||id==='c-n1'){const r=dcpf(twin);if(r)live(r).sort((a,b)=>ld(r,b)-ld(r,a)).slice(0,3).forEach(l=>add('line:'+l.id,`부하율 ${Math.round(ld(r,l))}%`))}
    if(id==='c-trip'){const g=M.gens.map((g,i)=>({g,i})).filter(o=>twin.gen[o.i].on).sort((a,b)=>twin.gen[b.i].p-twin.gen[a.i].p)[0];if(g)add('plant:'+g.g.plant,'최대 단위기')}
    if(id==='c-rd')add('d-cost','비용 기준')}
  else if(kbDoc(id)){const d=kbDoc(id);if(d.parent)add(d.parent,'상위 근거');KDOC.filter(x=>x.parent===id).slice(0,6).forEach(x=>add(x.id,'하위 규정'));KCON.filter(c=>c.docs.includes(id)).forEach(c=>add(c.id,'관련 업무'))}
  else if(t==='doc'){KCON.filter(c=>c.int.includes(v)).forEach(c=>add(c.id,'관련 업무'))}
  else if(t==='line'){const l=allLines(twin).find(x=>x.id===v);add('bus:'+l.f,'연결');add('bus:'+l.t,'연결');add('c-n1','적용 기준');add('c-ol','적용 기준');feedback.forEach((f,i)=>{if(f.q.includes(M.buses[l.f].n)&&f.q.includes(M.buses[l.t].n))add('case:'+i,'과거 사례')})}
  else if(t==='bus'){const b=+v;allLines(twin).filter(l=>l.f===b||l.t===b).slice(0,5).forEach(l=>add('line:'+l.id,'선로'));[...new Set(M.gens.filter(g=>g.b===b).map(g=>g.plant))].slice(0,3).forEach(p=>add('plant:'+p,'소재 발전소'))}
  else if(t==='plant'){const gs=M.gens.filter(g=>g.plant===v);if(gs[0]){add('bus:'+gs[0].b,'접속 변전소');add('gtype:'+gs[0].type,'발전원')}add('d-cost','비용 평가');add('c-trip','탈락 시')}
  else if(t==='gtype'){add('d-cost','비용 평가');[...new Set(M.gens.filter(g=>g.type===v).map(g=>g.plant))].slice(0,5).forEach(p=>add('plant:'+p,'해당 발전소'))}
  else if(t==='case'){const f=feedback[+v];if(f){allLines(twin).filter(l=>f.q.includes(M.buses[l.f].n)&&f.q.includes(M.buses[l.t].n)).slice(0,2).forEach(l=>add('line:'+l.id,'대상 설비'));add(f.r==='site'?'c-rd':f.r==='sta'?'c-n1':'c-ol','관련 업무')}}
  const seen=new Set();return E.filter(e=>!seen.has(e.b)&&seen.add(e.b)).slice(0,10)}
const KGC={'법령':'#8a5cf6','고시':'#2457c5','규정':'#0e8a7d','개념':'#c33a1d','운영절차':'#a8650a','선로':'#5e7183','변전소':'#5e7183','발전소':'#23795f','발전원':'#23795f','사례':'#b4458a'};
function kgDraw(){const c=kgNode(KG.focus);if(!c){KG.focus='c-ol';return kgDraw()}const E=kgEdges(KG.focus),W=720,H=420,cx=W/2,cy=H/2,R=150;
  let h='';E.forEach((e,i)=>{const a=-Math.PI/2+i*2*Math.PI/Math.max(E.length,1);e.x=cx+Math.cos(a)*R*1.55;e.y=cy+Math.sin(a)*R*1.08;const mx=cx+(e.x-cx)*.42,my=cy+(e.y-cy)*.42;h+=`<line class="kge" x1="${cx}" y1="${cy}" x2="${e.x}" y2="${e.y}"/><text class="kgr" x="${mx}" y="${my-4}" text-anchor="middle">${e.rel}</text>`});
  const nd=(n,x,y,big)=>{const col=KGC[n.type]||'#5e7183';const w=Math.max(70,Math.min(190,n.label.length*12+22));return `<g class="kgn${big?' big':''}" data-id="${n.id}" tabindex="0" role="button"><title>${esc(n.full||n.label)}</title><rect x="${x-w/2}" y="${y-(big?24:19)}" width="${w}" height="${big?48:38}" rx="${big?12:9}" style="stroke:${col}"/><text class="kgt" x="${x}" y="${y-(big?3:1)}" text-anchor="middle" style="fill:${col}">${n.type}</text><text class="kgl" x="${x}" y="${y+(big?15:12)}" text-anchor="middle">${esc(n.label)}</text></g>`};
  E.forEach(e=>{h+=nd(kgNode(e.b),e.x,e.y,false)});h+=nd(c,cx,cy,true);$('kgSvg').innerHTML=h;
  $('kgSvg').querySelectorAll('.kgn').forEach(g=>{const f=()=>{if(g.dataset.id===KG.focus)return;KG.hist.push(KG.focus);KG.focus=g.dataset.id;kgDraw()};g.onclick=f;g.onkeydown=e=>{if(e.key==='Enter')f()}});
  $('kgBack').disabled=!KG.hist.length;const d=kbDoc(c.id);
  $('kgInfo').innerHTML=`<b>${esc(c.full||c.label)}</b> <span class="kty" style="color:${KGC[c.type]}">${c.type}</span>${d?`<p>${d.sum}</p><p class="d">${d.ver}${d.dept?` · ${d.dept}`:''} · <a href="${d.src}" target="_blank" rel="noopener">KPX에서 보기</a></p>`:`<p class="d">연결 ${E.length}개 · 노드를 누르면 그 노드를 중심으로 다시 그립니다</p>`}`}
function kbFocus(id){KG.hist.push(KG.focus);KG.focus=id;document.querySelector('nav button[data-v="kb"]').click()}

/* --- 탭 렌더 --- */
function kbRender(){const kinds=['법령','고시','규칙','세부운영규정','기타 규칙'];
  $('kbCat').innerHTML=kinds.map(k=>`<div class="kcat"><div class="kch">${k}<span>${KDOC.filter(d=>d.kind===k).length}</span></div>${KDOC.filter(d=>d.kind===k).map(d=>`<button type="button" class="kdoc" data-id="${d.id}"><b>${d.t}</b><span>${d.ver}${d.dept?` · ${d.dept}`:''}</span></button>`).join('')}</div>`).join('')+`<div class="kcat"><div class="kch">내부 운영절차 (가상)<span>${DOCS.length}</span></div>${DOCS.map(d=>`<button type="button" class="kdoc" data-id="doc:${d.id}"><b>${d.id} ${d.t}</b><span>데모용 가상 절차</span></button>`).join('')}</div>`;
  $('kbCat').querySelectorAll('.kdoc').forEach(b=>b.onclick=()=>{KG.hist.push(KG.focus);KG.focus=b.dataset.id;kgDraw();$('kgSvg').scrollIntoView({behavior:'smooth',block:'center'})});
  const ch=kbChunks();$('kbStat').innerHTML=`<div class="kpi"><span>문서</span><b>${KDOC.length+DOCS.length}건</b></div><div class="kpi"><span>벡터 청크 (데모)</span><b>${ch.length}개</b></div><div class="kpi"><span>과거 사례 청크</span><b>${feedback.length}개</b></div><div class="kpi"><span>그래프 노드 유형</span><b>${Object.keys(KGC).length}종</b></div>`;
  kbDoSearch();kgDraw()}
function kbDoSearch(){const q=$('kbQ').value.trim()||'선로 과부하 조치',f=$('kbF').value;const r=kbSearch(q,f);
  $('kbRes').innerHTML=r.length?r.map(c=>`<div class="kres"><div class="krh"><span class="ksc">${c.sc.toFixed(2)}</span><b>${esc(c.doc)}</b><span class="kty">${c.kind}</span><span class="kver">${esc(c.ver)}</span></div><p>${esc(c.text)}</p><div class="kmeta">chunk_id <code>${c.id}</code>${c.src?` · <a href="${c.src}" target="_blank" rel="noopener">출처</a>`:''}${c.ext?' · 원문 대신 요약 메타데이터':''}</div></div>`).join(''):'<p class="d">결과가 없습니다.</p>';
  logCall('search_procedure',`kb: ${q} [${f}]`,`${r.length} hits`,'0.01s')}

/* ===== 대시보드 ===== */
function dashRender(){const s=twin,r=dcpf(s),gs=gensOf(s).filter(g=>g.on);const ren=(s.ren||[]).reduce((a,b)=>a+b,0),gen=gs.reduce((a,g)=>a+g.p,0)+ren,dem=s.load.reduce((a,b)=>a+b,0);
  const res=gs.filter(g=>g.type!=='원자력').reduce((a,g)=>a+Math.max(0,g.max-g.p),0),nv=r?n1Now(s).length:0,v=r?viol(r):[],mx=r?Math.max(...live(r).map(l=>ld(r,l))):0;
  const k=(lab,val,sub,cl)=>`<div class="dk${cl?' '+cl:''}"><span>${lab}</span><b>${val}</b>${sub?`<em>${sub}</em>`:''}</div>`;
  $('dKpi').innerHTML=k('현재 수요',fmt(dem)+' MW',TL.on?`${M.profile?M.profile.label:'표준일'} ${pad(TL.h)}:00`:'실시간 운전점')+k('총 발전',fmt(gen)+' MW',`재생 ${fmt(ren)}MW`)+k('운전 예비력',fmt(res)+' MW',`예비율 ${(res/dem*100).toFixed(1)}%`,res/dem<.05?'bad':'')+k('재생에너지 비중',(ren/gen*100).toFixed(1)+'%','')+k('최대 부하율',Math.round(mx)+'%','',mx>100?'bad':mx>=90?'warn':'')+k('정격 초과',v.length+'건','',v.length?'bad':'')+k('N-1 위반 사고',nv+'건','',nv?'warn':'')+k('활성 경보',$('alarms').querySelectorAll('li:not(.empty)').length+'건','');
  const types={};gs.forEach(g=>{types[g.type]=(types[g.type]||0)+g.p});if(ren>0)types['재생']=ren;const tot=Object.values(types).reduce((a,b)=>a+b,0)||1;const tc={'원자력':'#8a5cf6','석탄':'#5e7183','LNG':'#2457c5','재생':'#e0a000','thermal':'#2457c5','신규':'#23795f'};
  $('dMix').innerHTML=`<div class="mixbar">${Object.entries(types).sort((a,b)=>b[1]-a[1]).map(([t,p])=>`<i style="width:${p/tot*100}%;background:${tc[t]||'#7d8b98'}" title="${t} ${fmt(p)}MW"></i>`).join('')}</div><div class="mixleg">${Object.entries(types).sort((a,b)=>b[1]-a[1]).map(([t,p])=>`<span><i style="background:${tc[t]||'#7d8b98'}"></i>${t==='thermal'?'화력':t} ${fmt(p)}MW · ${(p/tot*100).toFixed(1)}%</span>`).join('')}</div>`;
  if(r){const top=live(r).sort((a,b)=>ld(r,b)-ld(r,a)).slice(0,8);$('dTop').innerHTML=top.map(l=>{const p=ld(r,l);return `<button type="button" class="dline" data-l="${l.id}"><span>${lname(l)}${KV(l.kv)}</span><i><u style="width:${Math.min(100,p/1.3)}%" class="${p>100?'a':p>=90?'w':''}"></u></i><b>${Math.round(p)}%</b></button>`}).join('');
    $('dTop').querySelectorAll('.dline').forEach(b=>b.onclick=()=>{const l=allLines(twin).find(x=>x.id===b.dataset.l);document.querySelector('nav button[data-v="ops"]').click();ask(`${M.buses[l.f].n}–${M.buses[l.t].n} 선로가 고장 나면?`)});
    const kvs=[...new Set(live(r).map(l=>l.kv))].sort((a,b)=>b-a),bins=[[0,50],[50,80],[80,90],[90,100],[100,999]];
    $('dHist').innerHTML=`<table class="dh"><tr><th></th>${bins.map(b=>`<th>${b[1]>200?'100%+':`${b[0]}–${b[1]}%`}</th>`).join('')}</tr>${kvs.map(kv=>`<tr><td>${kv?kv+'kV':'전체'}</td>${bins.map((b,i)=>{const n=live(r).filter(l=>l.kv===kv&&ld(r,l)>=b[0]&&ld(r,l)<b[1]).length;return `<td><span class="hc h${i}" style="opacity:${n?Math.min(1,.25+n/20):.08}">${n}</span></td>`}).join('')}</tr>`).join('')}</table>`}
  const W=1000,H=200;let g='';if(M.profile){const P=M.profile.hours,mxD=Math.max(...P.map(x=>x.dem)),X=h=>30+h*(W-40)/23,Y=v=>H-20-v/mxD*(H-30);
    g+=`<path class="dpv" d="M${X(0)} ${Y(0)} ${P.map((x,h)=>`L${X(h)} ${Y(x.pv)}`).join(' ')} L${X(23)} ${Y(0)}Z"/><path class="ddem" d="${P.map((x,h)=>`${h?'L':'M'}${X(h)} ${Y(x.dem)}`).join(' ')}"/><path class="dnet" d="${P.map((x,h)=>`${h?'L':'M'}${X(h)} ${Y(x.dem-x.pv-x.wind)}`).join(' ')}"/>`;
    for(let h=0;h<24;h+=3)g+=`<text class="dax" x="${X(h)}" y="${H-4}" text-anchor="middle">${pad(h)}</text>`;if(TL.on)g+=`<line class="dcur" x1="${X(TL.h)}" x2="${X(TL.h)}" y1="8" y2="${H-18}"/>`;
    $('dCurveT').textContent=`${M.profile.label} 수요 · 태양광 · 순부하 (KPG 실측 프로필)`}else{$('dCurveT').textContent='표준 하루 부하 곡선';const X=h=>30+h*(W-40)/23,Y=v=>H-20-v*(H-30);g+=`<path class="ddem" d="${LF.map((v,h)=>`${h?'L':'M'}${X(h)} ${Y(v)}`).join(' ')}"/>`}
  $('dCurve').innerHTML=g;
  const prod=MLK.map(k2=>{const m=MLM[k2],p=m.vers.find(x=>x.stage==='Production');return `<div class="dsys"><span>${m.name}</span><b>v${p.v}</b><em>${m.metric} ${p.val}${m.unit}</em></div>`}).join('');
  $('dSys').innerHTML=prod+`<div class="dsys"><span>게이트웨이 호출</span><b>${logs.length}건</b><em>툴 ${TOOLS.length}개 등록</em></div><div class="dsys"><span>관제사 판단</span><b>${feedback.length}건</b><em>부적절 ${feedback.filter(f=>f.d==='부적절').length}건</em></div><div class="dsys"><span>예약된 예방 조치</span><b>${Object.keys(TL.plans).length}건</b><em>${Object.keys(TL.plans).map(h=>pad(+h)+':00').join(', ')||'-'}</em></div>`}

/* ===== 지식 베이스 v2: 세부 정보 · 개념 벡터 검색 · 사용자 지식 ===== */
const AREAS=['계통운영','신뢰도·예비력','시장·정산','재생에너지','설비·계획','계약'];
const DET={
 'law-ea':{basis:'법률',sections:['제18조 전기품질의 유지','제27조의2 전력계통의 신뢰도 유지','전력시장·전력거래소 관련 조항'],terms:['전기품질','신뢰도','전력시장'],use:'신뢰도 기준 고시와 전력시장운영규칙의 상위 근거로, 판단 근거를 법령까지 거슬러 올라갈 때 사용',area:'계통운영',lvl:'일부 원문 확인'},
 'law-re':{basis:'법률',sections:['공급의무화(RPS) 근거','신·재생에너지 보급 지원'],terms:['RPS','REC'],use:'재생에너지 출력제어나 거래 관련 질의의 상위 근거',area:'재생에너지',lvl:'제목 기반 요약'},
 'n-rel':{basis:'전기사업법 제18조·제27조의2, 시행규칙 제18조, 시행령 제17조의2',sections:['제1장 총칙: 목적과 용어 정의 (신뢰도, 운영예비력, 상정고장, 방사상 계통, 초속응성예비력 등)','제2장 전기품질 (주파수·전압 유지)','신뢰도 유지 기준 (상정고장 대비 운영)'],terms:['운영예비력','상정고장','초속응성예비력','방사상 계통'],use:'N-1 위반 판정, 발전기 트립 시 예비력 판단, 방사상 설비의 계통 분리 해석의 기준',area:'신뢰도·예비력',lvl:'원문 구조 확인'},
 'n-op':{basis:'전기사업법',sections:['발전사업 세부 허가 기준','전기요금 산정 기준','전력량계 허용오차','전력계통 운영 업무'],terms:['계통운영업무'],use:'급전·계통운영 업무 범위를 확인할 때 사용',area:'계통운영',lvl:'제목 기반 요약'},
 'n-cap':{basis:'전기사업법',sections:['거래가격 상한 적용 기준'],terms:['가격 상한'],use:'재급전 비용과 정산 영향을 설명할 때 참고',area:'시장·정산',lvl:'제목 기반 요약'},
 'n-cfd':{basis:'전기사업법',sections:['정부승인 차액계약 운영 기준'],terms:['차액계약'],use:'관제 판단과 직접 관련은 적음 (시장 업무)',area:'계약',lvl:'제목 기반 요약'},
 'n-dpa':{basis:'전기사업법',sections:['재생에너지 직접 전력거래 기준'],terms:['직접거래'],use:'재생에너지 거래 형태 확인',area:'재생에너지',lvl:'제목 기반 요약'},
 'n-small':{basis:'전기사업법',sections:['소규모 신·재생 발전 전력의 거래 방법'],terms:['소규모 재생'],use:'분산 재생에너지 관련 질의 참고',area:'재생에너지',lvl:'제목 기반 요약'},
 'n-rps':{basis:'신에너지 및 재생에너지 개발·이용·보급 촉진법',sections:['공급의무화 관리·운영 절차','연료 혼합의무화 관리'],terms:['RPS'],use:'재생에너지 제도 질의 참고',area:'재생에너지',lvl:'제목 기반 요약'},
 'rule':{basis:'전기사업법 (전력시장운영규칙 근거 조항, 확인 필요)',sections:['입찰·가격결정·급전·정산 등 시장 운영 본문','별표 3 전력계통 운영 기준','  1.0 예비력 운영 기준: 상향·하향 예비력, 공급·운영예비력, 종류별 응동·유지 시간 요건','  2.0 전력계통 상정고장 기준','  3.0 안정유지 기준 (154kV 주요 간선계통 등)'],terms:['상향예비력','하향예비력','공급예비력','운영예비력','상정고장 기준','안정유지 기준'],use:'상정고장 해석 범위, 발전기 탈락 시 예비력 투입, 재급전 근거로 가장 자주 인용',area:'계통운영',lvl:'원문 구조 확인',ver2:'개정 이력 94건 · 최신 2026.7.25 시행'},
 'd-cost':{basis:'전력시장운영규칙',sections:['발전기 비용 평가 대상과 방법','비용 자료 제출·검증 절차'],terms:['변동비','비용평가'],use:'재급전 비용(감발·증발 단가) 산정의 기초',area:'시장·정산',lvl:'제목 기반 요약'},
 'd-grid':{basis:'전력시장운영규칙',sections:['계통 영향 평가 대상','평가 방법과 절차'],terms:['계통평가'],use:'선로 신설·발전기 신규 접속 질의의 근거',area:'설비·계획',lvl:'제목 기반 요약'},
 'd-cfd':{basis:'전력시장운영규칙',sections:['차액계약 세부 운영 절차'],terms:['차액계약'],use:'관제 판단과 직접 관련은 적음',area:'계약',lvl:'제목 기반 요약'},
 'etc':{basis:'전력시장운영규칙',sections:['KPX 기타 규칙 게시판 연동 예정'],terms:[],use:'-',area:'계통운영',lvl:'연동 예정'}};
const KSEC=[{id:'s-rel-1',doc:'n-rel',t:'제1장 총칙 · 용어 정의',sum:'신뢰도, 운영예비력, 상정고장, 방사상 계통, 초속응성예비력의 정의',con:['c-n1','c-trip']},
 {id:'s-rel-2',doc:'n-rel',t:'제2장 전기품질',sum:'주파수와 전압 등 전기품질 유지',con:['c-trip']},
 {id:'s-r-b3',doc:'rule',t:'별표 3 전력계통 운영 기준',sum:'예비력 운영, 상정고장, 안정유지 기준을 담은 별표',con:[]},
 {id:'s-r-b31',doc:'rule',parent:'s-r-b3',t:'1.0 예비력 운영 기준',sum:'상향·하향 예비력 구분과 운영예비력 종류별 응동 시간·유지 시간 요건',con:['c-trip']},
 {id:'s-r-b32',doc:'rule',parent:'s-r-b3',t:'2.0 상정고장 기준',sum:'계통 해석 시 고려하는 상정고장의 범위',con:['c-n1','c-ol']},
 {id:'s-r-b33',doc:'rule',parent:'s-r-b3',t:'3.0 안정유지 기준',sum:'상정고장 기준에 따른 계통 안정성 유지 기준',con:['c-n1']}];
KDOC.forEach(d=>Object.assign(d,DET[d.id]||{}));
/* --- 개념 벡터 (데모용 장난감 임베딩) --- */
const CDIM=[['과부하',['과부하','정격','초과','부하율','넘치','넘쳐']],['상정고장',['N-1','상정','고장','신뢰도','안전도','탈락','트립']],['재급전',['재급전','감발','증발','급전']],['부하차단',['부하 차단','차단']],['예비력',['예비력','주파수','응동','발전기','원자력']],['휴전·신설',['휴전','작업','신설','계통평가','접속']],['전기품질',['전압','주파수','품질']],['재생에너지',['재생','태양광','풍력','RPS','REC','출력제어']],['시장·정산',['시장','정산','가격','입찰','상한']],['비용',['비용','변동비','단가']],['계약',['계약','차액']],['현장 경험',['메모','인수인계','주의','노하우','현장','먼저']]];
function emb(t){return CDIM.map(([n,ks])=>ks.reduce((a,k)=>a+(t.includes(k)?1:0),0))}
const cos=(a,b)=>{let d=0,x=0,y=0;a.forEach((v,i)=>{d+=v*b[i];x+=v*v;y+=b[i]*b[i]});return x&&y?d/Math.sqrt(x*y):0};
/* --- 사용자 지식 --- */
const UKB=[];let ukSeq=0;
function ukSeed(){if(UKB.length)return;const L=(a,b)=>{const ia=MODELS.kpg193.buses.findIndex(x=>x.n===a),ib=MODELS.kpg193.buses.findIndex(x=>x.n===b);return [ia,ib].filter(i=>i>=0)};
  [['설비 특이사항','서천–군산 1회선 정비 시 남은 회선 주의','2회선 중 1회선만 빠져도 남은 회선이 쉽게 정격을 넘는다. 정비 계획 시 군산 쪽 발전 감발을 먼저 검토하고, 정비는 수요가 낮은 새벽 시간대로 잡는다.',L('서천','군산'),'관제사 김OO','팀 검토','과부하 재급전 휴전'],
   ['교대 인수인계','제천–충주 오전 상시 고부하','오전 수요 증가 구간에 90% 이상으로 올라가는 날이 잦다. 08시 이전에 동해안 쪽 재급전 여유를 확인해 둘 것.',L('제천','충주'),'관제사 이OO','개인 메모','과부하 재급전'],
   ['현장 노하우','신한울원자력 단위기 트립 시 확인 순서','대용량 단위기 트립 직후 동해안 765kV 조류가 급변한다. 태백·울진 쪽 선로 부하율과 운영예비력 투입 여부를 먼저 확인한다.',L('울진','태백'),'선임 관제사 박OO','공식 절차 승격 검토','예비력 트립 발전기'],
   ['질의 템플릿','휴전 사전 검토 질의','“{선로} 선로 휴전하고 {시각}시 예상 상황 예방 조치 추천해줘” 형식으로 물으면 휴전 시간대 위험을 한 번에 본다.',[],'관제사 김OO','개인 메모','휴전 예방']].forEach(([type,t,b,tags,au,tr,kw])=>UKB.push({id:'U'+(++ukSeq),type,t,b,tags,au,trust:tr,kw,date:'2026-10-2'+(ukSeq),model:'kpg193',refs:Math.floor(3+ukSeq*2),ex:true}))}
function ukItems(){return UKB.filter(u=>u.model===M.id)}
function noteRefs(busIdx){const S=new Set(busIdx);const ns=ukItems().filter(u=>u.tags.some(b=>S.has(b)));if(!ns.length)return '';ns.forEach(u=>u.refs++);
  return ns.slice(0,2).map(u=>`<div class="cite note"><b>관제사 메모 · ${u.type}</b> <span class="tr tr-${u.trust.replace(/\s/g,'')}">${u.trust}</span> ${esc(u.t)}: ${esc(u.b)} <span class="d">— ${u.au}${u.ex?' (예시)':''}</span></div>`).join('')}
/* --- 청크 (검색 대상) --- */
function kbChunks2(){const c=[];
  KDOC.forEach(d=>c.push({id:d.id+'#요약',doc:d.t,src:'외부 규정',kind:d.kind,area:d.area||'계통운영',trust:'공식',cur:true,ver:d.ver2||d.ver,text:d.sum+'. '+(d.use||''),url:d.src,lvl:d.lvl}));
  KSEC.forEach(s=>{const d=kbDoc(s.doc);c.push({id:s.id,doc:`${d.t} · ${s.t}`,src:'외부 규정',kind:d.kind,area:d.area,trust:'공식',cur:true,ver:d.ver2||d.ver,text:s.sum,url:d.src,lvl:'원문 구조 확인'})});
  c.push({id:'rule-old#예',doc:'전력시장운영규칙 (2026.5.21 시행, 개정 전)',src:'외부 규정',kind:'규칙',area:'계통운영',trust:'공식',cur:false,ver:'2026.5.21 시행',text:'개정 전 버전의 같은 조항. 과거 사건을 당시 기준으로 검토할 때만 사용',url:KPXU.rule,lvl:'메타데이터'});
  DOCS.forEach(d=>c.push({id:d.id,doc:'내부 운영절차 (가상) '+d.id,src:'내부 절차',kind:'운영절차',area:/발전기|예비력/.test(d.b)?'신뢰도·예비력':/휴전|신설/.test(d.b)?'설비·계획':'계통운영',trust:'공식',cur:true,ver:'데모',text:`${d.t}: ${d.b}`}));
  ukItems().forEach(u=>c.push({id:u.id,doc:`${u.type} · ${u.t}`,src:'사용자 지식',kind:u.type,area:/예비력|트립/.test(u.kw)?'신뢰도·예비력':/휴전/.test(u.kw)?'설비·계획':'계통운영',trust:u.trust.startsWith('공식')?'공식':u.trust,cur:true,ver:u.date,text:u.b,kw:u.kw,tags:u.tags,au:u.au}));
  feedback.forEach((f,i)=>{const r=REASONS.find(x=>x.id===f.r);c.push({id:'case-'+i,doc:'과거 사례 '+f.t,src:'과거 사례',kind:'사례',area:'계통운영',trust:'자동 기록',cur:true,ver:f.t,text:`“${f.q}” → ${f.d}${r?` (사유: ${r.t})`:''}${f.a!=='-'?`, 대안: ${f.a}`:''}`})});
  return c.map(x=>({...x,v:emb(x.doc+' '+x.text+' '+(x.kw||''))}))}
function kbSearch2(q,F){const qv=emb(q),qm=parseEvents?mentions(q).filter(m=>m.k==='bus').map(m=>m.i):[];
  let c=kbChunks2().filter(x=>(F.src==='전체'||x.src===F.src)&&(F.area==='전체'||x.area===F.area)&&(F.trust==='전체'||x.trust===F.trust)&&(!F.cur||x.cur));
  return c.map(x=>{let sc=cos(qv,x.v);if(qm.length&&x.tags&&x.tags.some(t=>qm.includes(t)))sc=Math.min(1,sc+.25);const why=CDIM.filter((d,i)=>qv[i]&&x.v[i]).map(d=>d[0]);return {...x,sc,why}}).filter(x=>x.sc>0.05).sort((a,b)=>b.sc-a.sc).slice(0,8)}
/* --- 렌더 --- */
let KSEL='n-rel';
function kbRender(){ukSeed();const kinds=['법령','고시','규칙','세부운영규정','기타 규칙'];
  $('kbCat').innerHTML=kinds.map(k=>`<div class="kcat"><div class="kch">${k}<span>${KDOC.filter(d=>d.kind===k).length}</span></div>${KDOC.filter(d=>d.kind===k).map(d=>`<button type="button" class="kdoc${d.id===KSEL?' on':''}" data-id="${d.id}"><b>${d.t}</b><span>${d.ver2||d.ver}${d.dept?` · ${d.dept}`:''} · ${d.area||''}</span></button>`).join('')}</div>`).join('');
  $('kbCat').querySelectorAll('.kdoc').forEach(b=>b.onclick=()=>{KSEL=b.dataset.id;kbRender()});
  const d=kbDoc(KSEL),secs=KSEC.filter(s=>s.doc===d.id),cons=KCON.filter(c=>c.docs.includes(d.id));
  $('kbDet').innerHTML=`<div class="kdh"><span class="kty">${d.kind}</span><span class="lvl lv-${(d.lvl||'').replace(/\s/g,'')}">${d.lvl||''}</span></div><h3>${d.t}</h3><p>${d.sum}</p>
   <div class="kdr"><span>근거</span><b>${d.basis||'-'}</b></div><div class="kdr"><span>버전</span><b>${d.ver2||d.ver}${d.dept?` · ${d.dept}`:''}</b></div><div class="kdr"><span>업무 영역</span><b>${d.area||'-'}</b></div>
   <div class="kdr"><span>주요 구성</span><b><ul>${(d.sections||[]).map(s=>`<li${s.startsWith('  ')?' class="sub"':''}>${s.trim()}</li>`).join('')}</ul></b></div>
   ${d.terms&&d.terms.length?`<div class="kdr"><span>핵심 용어</span><b>${d.terms.map(t=>`<i class="term">${t}</i>`).join('')}</b></div>`:''}
   <div class="kdr"><span>관제 활용</span><b>${d.use||'-'}</b></div>${cons.length?`<div class="kdr"><span>연결 업무</span><b>${cons.map(c=>c.t).join(', ')}</b></div>`:''}
   <div class="kdbtn"><a class="btn" href="${d.src}" target="_blank" rel="noopener">KPX 원문</a><button class="btn pri" type="button" data-kg="${d.id}">지식그래프에서 보기</button></div>`;
  const ch=kbChunks2();$('kbStat').innerHTML=`<div class="kpi"><span>외부 규정 · 조항 묶음</span><b>${KDOC.length}건 · ${KSEC.length}개</b></div><div class="kpi"><span>검색 청크</span><b>${ch.length}개</b></div><div class="kpi"><span>사용자 지식</span><b>${ukItems().length}건</b></div><div class="kpi"><span>과거 사례</span><b>${feedback.length}건</b></div>`;
  ukRender();kbDoSearch();kgDraw()}
function kbDoSearch(){const q=$('kbQ').value.trim()||'선로가 넘치면 어떻게 조치해',F={src:$('kfSrc').value,area:$('kfArea').value,trust:$('kfTrust').value,cur:$('kfCur').checked};const r=kbSearch2(q,F),qv=emb(q);
  $('kbConc').innerHTML='추출 개념 '+(CDIM.filter((d,i)=>qv[i]).map(d=>`<i class="term">${d[0]}</i>`).join('')||'<span class="d">없음</span>');
  $('kbRes').innerHTML=r.length?r.map(c=>`<div class="kres"><div class="krh"><span class="ksc" title="코사인 유사도">${c.sc.toFixed(2)}</span><b>${esc(c.doc)}</b><span class="kty">${c.src}</span><span class="tr tr-${c.trust.replace(/\s/g,'')}">${c.trust}</span>${c.cur?'':'<span class="tr tr-old">개정 전</span>'}</div><p>${esc(c.text)}</p><div class="kmeta">겹친 개념 ${c.why.map(w=>`<i class="term s">${w}</i>`).join('')||'-'} · ${c.area} · ${esc(c.ver)}${c.url?` · <a href="${c.url}" target="_blank" rel="noopener">출처</a>`:''}${c.au?` · ${c.au}`:''}</div></div>`).join(''):'<p class="d">결과 없음</p>';
  kbMap(qv,r);logCall('search_procedure',`kb: ${q}`,`${r.length} hits`,'0.01s')}
function kbMap(qv,res){const W=360,H=260,cx=W/2,cy=H/2,R=105;const ang=CDIM.map((_,i)=>-Math.PI/2+i*2*Math.PI/CDIM.length);
  const P=v=>{const s=v.reduce((a,b)=>a+b,0)||1;let x=0,y=0;v.forEach((w,i)=>{x+=Math.cos(ang[i])*w/s;y+=Math.sin(ang[i])*w/s});return [cx+x*R,cy+y*R]};
  let g=CDIM.map((d,i)=>`<text class="mlab" x="${cx+Math.cos(ang[i])*(R+22)}" y="${cy+Math.sin(ang[i])*(R+16)+3}" text-anchor="middle">${d[0]}</text>`).join('');
  const hit=new Set(res.map(r=>r.id));kbChunks2().forEach(c=>{if(!c.v.some(Boolean))return;const [x,y]=P(c.v);const jx=(c.id.charCodeAt(c.id.length-1)%7-3)*2.2,jy=(c.id.length%5-2)*2.2;g+=`<circle class="mdot ${hit.has(c.id)?'hit':''} ms-${c.src.replace(/\s/g,'')}" cx="${x+jx}" cy="${y+jy}" r="${hit.has(c.id)?5:3.2}"><title>${esc(c.doc)}</title></circle>`});
  if(qv.some(Boolean)){const [x,y]=P(qv);g+=`<circle class="mq" cx="${x}" cy="${y}" r="7"/><text class="mql" x="${x}" y="${y-11}" text-anchor="middle">질문</text>`}
  $('kbMap').innerHTML=g}
function ukRender(){const it=ukItems();$('ukList').innerHTML=it.length?it.map(u=>`<div class="uk"><div class="ukh"><span class="kty">${u.type}</span><b>${esc(u.t)}</b><span class="tr tr-${u.trust.replace(/\s/g,'')}">${u.trust}</span></div><p>${esc(u.b)}</p><div class="kmeta">${u.au}${u.ex?' (예시)':''} · ${u.date} · 참조 ${u.refs}회${u.tags.length?` · 설비 ${u.tags.map(nameOf).join(', ')}`:''}</div>
  <div class="ukb">${u.trust==='개인 메모'?`<button class="btn" data-up="${u.id}" type="button">팀 검토 요청</button>`:u.trust==='팀 검토'?`<button class="btn" data-up="${u.id}" type="button">공식 절차로 승격 요청</button>`:''}${u.tags.length?`<button class="btn" type="button" data-kg="note:${u.id}">그래프</button>`:''}</div></div>`).join(''):'<p class="d">사용자 지식 없음</p>';
  $('ukList').querySelectorAll('[data-up]').forEach(b=>b.onclick=()=>{const u=UKB.find(x=>x.id===b.dataset.up);u.trust=u.trust==='개인 메모'?'팀 검토':'공식 절차 승격 검토';addJN('apply',`사용자 지식 “${u.t}” → ${u.trust}`);kbRender()})}
function ukAdd(){const t=$('ukT').value.trim(),b=$('ukB').value.trim();if(!t||!b)return;const ms=mentions(t+' '+b).filter(m=>m.k==='bus').map(m=>m.i);
  UKB.unshift({id:'U'+(++ukSeq),type:$('ukType').value,t,b,tags:[...new Set(ms)],au:'나 (현재 사용자)',trust:'개인 메모',kw:b,date:new Date().toISOString().slice(0,10),model:M.id,refs:0});
  $('ukT').value='';$('ukB').value='';addJN('apply',`사용자 지식 추가: ${t}`);kbRender()}
/* 지식그래프 확장: 조항·사용자 지식 노드 */
const _kgNode=kgNode,_kgEdges=kgEdges;
kgNode=function(id){if(id.startsWith('s-')){const s=KSEC.find(x=>x.id===id);return s?{id,label:s.t.length>18?s.t.slice(0,17)+'…':s.t,full:`${kbDoc(s.doc).t} · ${s.t}: ${s.sum}`,type:'조항'}:null}
  if(id.startsWith('note:')){const u=UKB.find(x=>x.id===id.slice(5));return u?{id,label:u.t.length>14?u.t.slice(0,13)+'…':u.t,full:`${u.type} · ${u.t}: ${u.b}`,type:'사용자 지식'}:null}return _kgNode(id)};
kgEdges=function(id){if(id.startsWith('s-')){const s=KSEC.find(x=>x.id===id),E=[{b:s.doc,rel:'소속 문서'}];if(s.parent)E.push({b:s.parent,rel:'상위 항목'});KSEC.filter(x=>x.parent===id).forEach(x=>E.push({b:x.id,rel:'하위 항목'}));s.con.forEach(c=>E.push({b:c,rel:'적용 업무'}));return E}
  if(id.startsWith('note:')){const u=UKB.find(x=>x.id===id.slice(5)),E=[];u.tags.forEach(b=>E.push({b:'bus:'+b,rel:'대상 설비'}));allLines(twin).filter(l=>u.tags.includes(l.f)&&u.tags.includes(l.t)).forEach(l=>E.push({b:'line:'+l.id,rel:'대상 선로'}));KCON.filter(c=>c.t.split(/[ ·]/).some(w=>w.length>1&&u.kw.includes(w.slice(0,2)))).slice(0,2).forEach(c=>E.push({b:c.id,rel:'관련 업무'}));return E}
  let E=_kgEdges(id);if(kbDoc(id))KSEC.filter(s=>s.doc===id&&!s.parent).forEach(s=>E.unshift({b:s.id,rel:'주요 조항'}));
  if(id.startsWith('c-'))KSEC.filter(s=>s.con.includes(id)).forEach(s=>E.unshift({b:s.id,rel:'근거 조항'}));
  const [t,v]=id.split(':');if(t==='bus'||t==='line'){const bs=t==='bus'?[+v]:(()=>{const l=allLines(twin).find(x=>x.id===v);return l?[l.f,l.t]:[]})();ukItems().filter(u=>bs.every(b=>u.tags.includes(b))).forEach(u=>E.push({b:'note:'+u.id,rel:'관제사 메모'}))}
  const seen=new Set();return E.filter(e=>kgNode(e.b)&&!seen.has(e.b)&&seen.add(e.b)).slice(0,11)};
KGC['조항']='#0b7285';KGC['사용자 지식']='#d9480f';

/* ===== 지식 베이스 v3 ===== */
KCON.forEach(c=>c.desc={'c-n1':'임의의 단일 설비가 고장 나도 정격을 넘지 않도록 운전하는 기준과 그 점검 업무','c-ol':'정격을 넘은 선로를 정해진 시간 안에 해소하는 업무 (재급전 → 계통 재구성 → 부하 차단 순)','c-rd':'발전기 출력을 올리고 내려 조류를 바꾸는 조치, 비용 평가 기준이 함께 적용','c-shed':'다른 수단으로 풀리지 않을 때 부하를 끊는 최후 수단, 책임자 승인 필요','c-trip':'대용량 발전기 탈락 시 주파수 회복을 위한 예비력 투입과 후속 조치','c-out':'계획 휴전·신설 전 계통 영향을 미리 검토하는 업무','c-curt':'재생에너지 과잉 시 출력을 줄이는 판단과 절차'}[c.id]||'');
/* --- 사용자 지식: 여러 관제사의 처리 기록 --- */
const UKA=[['김OO','2조'],['이OO','3조'],['박OO','선임'],['최OO','1조'],['정OO','4조'],['한OO','2조']];
function ukSeed(){if(UKB.length)return;const B=n=>MODELS.kpg193.buses.findIndex(x=>x.n===n);const T=(...ns)=>ns.map(B).filter(i=>i>=0);
 const S=[
 ['처리 사례','아산–청양 오후 과부하 예보 대응',T('아산','청양'),0,'팀 검토','2026-09-14',{s:'맑은 날 11시경, 2시간 뒤 아산–청양 107% 예보',a:'신서천화력 400MW 감발, 영흥화력 400MW 증발을 11시 30분부터 단계적으로 시행',r:'13시 실제 부하율 96%로 정격 이내 유지',l:'맑은 봄날 11~14시는 충청에서 수도권으로 올라가는 조류가 커진다. 예보가 뜨면 바로 재급전 준비'},['rule','n-rel'],'과부하 재급전 태양광'],
 ['처리 사례','신한울원자력 #1 트립 (모의 훈련)',T('울진','태백'),2,'공식 절차 승격 검토','2026-08-30',{s:'신한울원자력 #1 1,400MW 탈락 가정',a:'운영예비력 즉시 투입, 태백–울진·양평–태백 765kV 부하율 확인 후 동해안 화력 증발',r:'주파수 최저점 이후 3분 안에 회복, 선로 위반 없음',l:'트립 직후에는 동해안 765kV 조류를 먼저 본다. 예비력 투입 지시는 1분 안에'},['n-rel','rule'],'트립 예비력 주파수 발전기'],
 ['처리 사례','서천–군산 1회선 고장, 재급전 불가',T('서천','군산'),1,'팀 검토','2026-09-02',{s:'서천–군산 1회선 탈락, 남은 회선 195% 예상',a:'재급전으로 해소 불가 확인 → 군산 측 계통 재구성 검토 → 최종 파주 450MW 부하 차단안 당직 책임자 보고',r:'계통 재구성으로 부하 차단 없이 해소',l:'규정 순서(재급전 → 재구성 → 차단)대로 가면 차단까지 가지 않는 경우가 많다'},['rule','n-rel'],'과부하 차단 재구성'],
 ['부적절 사례','시스템 추천 재급전 부적절: 정비 중 발전기',T('보령'),3,'팀 검토','2026-10-06',{s:'시스템이 보령화력 #5 증발 추천',a:'해당 호기가 계획 정비 중이라 부적절 (사유: 대안이 현장 사정에 안 맞음) → 신보령화력 증발로 대체',r:'대체안으로 위반 해소',l:'정비 일정이 시스템에 반영되지 않는다. 재급전 추천은 정비표와 꼭 대조'},['d-cost'],'재급전 정비 부적절'],
 ['교대 인수인계','제천–충주 오전 상시 고부하',T('제천','충주'),1,'개인 메모','2026-10-22',{s:'오전 수요 증가 구간에 90% 이상 지속',a:'08시 이전에 동해안 재급전 여유 확인',r:'-',l:'다음 근무조도 08시 전 확인 필요'},['rule'],'과부하 재급전'],
 ['처리 사례','강남–서초 저녁 피크 115%',T('강남','서초'),4,'팀 검토','2026-09-19',{s:'19시 저녁 피크에 강남–서초 115%',a:'서울복합 증발 200MW, 동시에 수요자원(DR) 발령 검토',r:'증발만으로 98%까지 낮춤, DR 미발령',l:'수도권 내부 혼잡은 수도권 안쪽 발전기 증발이 가장 효과적'},['rule'],'과부하 재급전 수요자원'],
 ['처리 사례','양평–태백 765kV 계획 휴전 시간 조정',T('양평','태백'),2,'공식 절차 승격 검토','2026-09-25',{s:'주간 휴전 계획 사전 검토 시 제천–충주 120% 예상',a:'휴전을 새벽 02~05시로 조정 요청',r:'조정 후 위반 없음',l:'765kV 휴전은 사전 검토 질의로 시간대별 위험을 먼저 본다'},['d-grid','n-rel'],'휴전 작업 시간 조정'],
 ['교대 인수인계','광양–남해 97% 지속',T('광양','남해'),5,'개인 메모','2026-10-20',{s:'광양–남해 97% 수준이 하루 종일 지속',a:'하동화력 출력 변동 시 즉시 확인',r:'-',l:'여유가 거의 없으니 남해안 발전기 변동에 민감'},['rule'],'과부하 감시'],
 ['현장 노하우','호남 태양광 과잉 시 판단 순서',T('익산','청양'),2,'팀 검토','2026-07-11',{s:'봄·가을 낮 시간 호남 태양광 과잉',a:'① 북상 조류 선로 확인 → ② 화력 하향 여유 확인 → ③ 출력제어는 마지막',r:'-',l:'출력제어 전에 하향예비력부터 본다'},['rule','law-re'],'재생 출력제어 태양광'],
 ['처리 사례','안양–서수원 고장 시 부하 차단 결정',T('안양','서수원'),3,'팀 검토','2026-08-18',{s:'안양–서수원 1회선 탈락, 재급전 불가',a:'서수원 측 120MW 부하 차단안을 당직 책임자 승인 후 시행 대기',r:'15분 내 복구로 실제 차단은 하지 않음',l:'차단안은 승인까지 미리 받아두면 시간을 번다'},['n-rel'],'부하 차단 승인'],
 ['현장 노하우','서천–군산 1회선 정비 시 남은 회선 주의',T('서천','군산'),0,'팀 검토','2026-10-21',{s:'정비 계획 수립',a:'군산 쪽 발전 감발 먼저 검토, 정비는 새벽 시간대',r:'-',l:'2회선 중 1회선만 빠져도 남은 회선이 쉽게 넘침'},['d-grid'],'휴전 정비 과부하'],
 ['질의 템플릿','휴전 사전 검토 질의',[],0,'개인 메모','2026-10-23',{s:'휴전 계획 검토',a:'“{선로} 선로 휴전하고 {시각}시 예상 상황 예방 조치 추천해줘”',r:'-',l:'시간대별 위험과 예방 조치를 한 번에 확인'},[],'휴전 예방']];
 S.forEach(([type,t,tags,ai,tr,date,c,rules,kw])=>UKB.push({id:'U'+(++ukSeq),type,t,tags,au:UKA[ai][0],team:UKA[ai][1],trust:tr,date,c,rules,kw,b:[c.s,c.a,c.r,c.l].filter(x=>x&&x!=='-').join('. '),model:'kpg193',refs:3+(ukSeq*7)%19,ex:true}))}
function ukAdd(){const t=$('ukT').value.trim(),b=$('ukB').value.trim();if(!t||!b)return;const ms=mentions(t+' '+b).filter(m=>m.k==='bus').map(m=>m.i);
  UKB.unshift({id:'U'+(++ukSeq),type:$('ukType').value,t,tags:[...new Set(ms)],au:'나',team:'현재 사용자',trust:'개인 메모',date:new Date().toISOString().slice(0,10),c:{s:b,a:'-',r:'-',l:'-'},rules:[],kw:b,b,model:M.id,refs:0});$('ukT').value='';$('ukB').value='';addJN('apply',`사용자 지식 추가: ${t}`);kbRender()}
function ukRender(){const all=ukItems(),ft=$('ukFType').value,fa=$('ukFAu').value;const it=all.filter(u=>(ft==='전체'||u.type===ft)&&(fa==='전체'||u.au===fa));
  const aus=[...new Set(all.map(u=>u.au))];if($('ukFAu').options.length!==aus.length+1){$('ukFAu').innerHTML='<option>전체</option>'+aus.map(a=>`<option>${a}</option>`).join('');$('ukFAu').value=fa&&aus.includes(fa)?fa:'전체'}
  $('ukStat').textContent=`작성자 ${aus.length}명 · 처리 사례 ${all.filter(u=>u.type==='처리 사례'||u.type==='부적절 사례').length}건 · 인수인계 ${all.filter(u=>u.type==='교대 인수인계').length}건 · 노하우 ${all.filter(u=>u.type==='현장 노하우').length}건`;
  $('ukList').innerHTML=it.length?it.map(u=>`<div class="uk"><div class="ukh"><span class="kty">${u.type}</span><b>${esc(u.t)}</b><span class="tr tr-${u.trust.replace(/\s/g,'')}">${u.trust}</span></div>
   <div class="ukc">${[['상황',u.c.s],['조치',u.c.a],['결과',u.c.r],['교훈',u.c.l]].filter(x=>x[1]&&x[1]!=='-').map(([k,v])=>`<div><span>${k}</span><p>${esc(v)}</p></div>`).join('')}</div>
   <div class="kmeta">${u.au} (${u.team})${u.ex?' · 예시':''} · ${u.date} · 참조 ${u.refs}회${u.tags.length?` · 설비 ${u.tags.map(nameOf).join(', ')}`:''}${u.rules.length?` · 근거 ${u.rules.map(r=>{const t=kbDoc(r).t;return t.length>16?t.slice(0,15)+'…':t}).join(', ')}`:''}</div>
   <div class="ukb">${u.trust==='개인 메모'?`<button class="btn" data-up="${u.id}" type="button">팀 검토 요청</button>`:u.trust==='팀 검토'?`<button class="btn" data-up="${u.id}" type="button">공식 절차로 승격 요청</button>`:''}<button class="btn" type="button" data-kg="note:${u.id}">그래프</button></div></div>`).join(''):'<p class="d">결과 없음</p>';
  $('ukList').querySelectorAll('[data-up]').forEach(b=>b.onclick=()=>{const u=UKB.find(x=>x.id===b.dataset.up);u.trust=u.trust==='개인 메모'?'팀 검토':'공식 절차 승격 검토';addJN('apply',`사용자 지식 “${u.t}” → ${u.trust}`);kbRender()})}
function noteRefs(busIdx){const S=new Set(busIdx);const ns=ukItems().filter(u=>u.tags.length&&u.tags.every(b=>S.has(b))).concat(ukItems().filter(u=>u.tags.some(b=>S.has(b)))).filter((u,i,a)=>a.indexOf(u)===i);if(!ns.length)return '';
  return ns.slice(0,2).map(u=>{u.refs++;return `<div class="cite note"><b>${u.type} · ${esc(u.t)}</b> <span class="tr tr-${u.trust.replace(/\s/g,'')}">${u.trust}</span> ${esc(u.c.a!=='-'?`조치: ${u.c.a}.`:'')} ${esc(u.c.l!=='-'?`교훈: ${u.c.l}`:u.c.s)} <span class="d">— ${u.au} (${u.team})${u.ex?' · 예시':''}</span></div>`}).join('')}
/* --- 의미 지도: 겹침 없는 배치 --- */
function kbMap(qv,res){const W=360,H=270,cx=W/2,cy=H/2+4,R=86;const ang=CDIM.map((_,i)=>-Math.PI/2+i*2*Math.PI/CDIM.length);
  const P=v=>{const s=v.reduce((a,b)=>a+b,0)||1;let x=0,y=0;v.forEach((w,i)=>{x+=Math.cos(ang[i])*w/s;y+=Math.sin(ang[i])*w/s});return [cx+x*R*.92,cy+y*R*.92]};
  const hit=new Set(res.map(r=>r.id));const pts=kbChunks2().filter(c=>c.v.some(Boolean)).map(c=>{const [x,y]=P(c.v);return {c,x,y,r:hit.has(c.id)?5:3.4}});
  let q=null;if(qv.some(Boolean)){const [x,y]=P(qv);q={x,y,r:7,fixed:true}}const all=q?[q,...pts]:pts;
  for(let it=0;it<60;it++)for(let i=0;i<all.length;i++)for(let j=i+1;j<all.length;j++){const a=all[i],b=all[j];let dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||.01;const m=a.r+b.r+1.5;if(d<m){const k=(m-d)/2;dx/=d;dy/=d;if(!a.fixed){a.x-=dx*k;a.y-=dy*k}if(!b.fixed){b.x+=dx*k;b.y+=dy*k}}}
  all.forEach(p=>{const d=Math.hypot(p.x-cx,p.y-cy);if(d>R){p.x=cx+(p.x-cx)*R/d;p.y=cy+(p.y-cy)*R/d}});
  let g=`<circle class="mring" cx="${cx}" cy="${cy}" r="${R+6}"/>`;CDIM.forEach((d,i)=>{const lx=cx+Math.cos(ang[i])*(R+30),ly=cy+Math.sin(ang[i])*(R+22);g+=`<text class="mlab${qv[i]?' on':''}" x="${lx}" y="${ly+4}" text-anchor="middle">${d[0]}</text>`});
  pts.forEach(p=>{g+=`<circle class="mdot ${hit.has(p.c.id)?'hit':''} ms-${p.c.src.replace(/\s/g,'')}" cx="${p.x}" cy="${p.y}" r="${p.r}"><title>${esc(p.c.doc)}</title></circle>`});
  if(q)g+=`<circle class="mq" cx="${q.x}" cy="${q.y}" r="7"><title>질문</title></circle>`;$('kbMap').innerHTML=g}
/* --- 지식그래프: 최상위 분류에서 파고드는 트리 --- */
const ROOTS=[['cat:law','규정 체계','법령·고시·규칙·세부규정'],['cat:task','관제 업무','N-1, 과부하, 재급전 등'],['cat:equip','설비','765kV·345kV 변전소와 선로'],['cat:plant','발전소','발전소·발전원'],['cat:user','사용자 지식','관제사 처리 기록·노하우'],['cat:case','과거 사례','이 데모에서 쌓인 승인·부적절']];
KG={focus:'root',path:['root']};
const _n3=kgNode,_e3=kgEdges;
kgNode=function(id){if(id==='root')return {id,label:'지식 베이스',type:'시작'};const r=ROOTS.find(x=>x[0]===id);if(r)return {id,label:r[1],full:r[2],type:'분류'};return _n3(id)};
kgEdges=function(id){const E=[];const add=(b,rel)=>{if(kgNode(b))E.push({b,rel})};
  if(id==='root'){ROOTS.forEach(r=>add(r[0],r[2]));return E}
  if(id==='cat:law'){KDOC.filter(d=>d.kind==='법령').forEach(d=>add(d.id,'법령'));add('rule','규칙');return E}
  if(id==='cat:task'){KCON.forEach(c=>add(c.id,'업무'));return E}
  if(id==='cat:equip'){const r=dcpf(twin);M.buses.map((b,i)=>({b,i})).filter(o=>o.b.kv>=765||M.buses.length<=40).slice(0,9).forEach(o=>add('bus:'+o.i,o.b.kv?o.b.kv+'kV 변전소':'모선'));if(r)live(r).sort((a,b)=>ld(r,b)-ld(r,a)).slice(0,3).forEach(l=>add('line:'+l.id,`부하율 ${Math.round(ld(r,l))}%`));return E}
  if(id==='cat:plant'){const ps={};M.gens.forEach(g=>{ps[g.plant]=(ps[g.plant]||0)+g.max});Object.entries(ps).sort((a,b)=>b[1]-a[1]).slice(0,10).forEach(([p,c])=>add('plant:'+p,`설비용량 ${fmt(c)}MW`));return E}
  if(id==='cat:user'){ukItems().slice(0,12).forEach(u=>add('note:'+u.id,`${u.type} · ${u.au}`));return E}
  if(id==='cat:case'){feedback.slice(0,12).forEach((f,i)=>add('case:'+i,f.d));if(!E.length)E.push({b:'cat:user',rel:'아직 사례가 없어 사용자 지식으로'});return E}
  if(id.startsWith('note:')){const u=UKB.find(x=>x.id===id.slice(5));if(u)u.rules.forEach(r=>add(r,'근거 규정'))}
  return E.concat(_e3(id))};
function kgInfoHtml(id){const n=kgNode(id),[t,v]=id.split(':'),row=(k,x)=>`<div class="kdr"><span>${k}</span><b>${x}</b></div>`;const ttl=kbDoc(id)?kbDoc(id).t:id.startsWith('s-')?KSEC.find(x=>x.id===id).t:id.startsWith('note:')?(UKB.find(x=>x.id===id.slice(5))||{}).t:id.startsWith('case:')?'사례 '+((feedback[+id.split(':')[1]]||{}).t||''):n.label;let h=`<div class="kih"><span class="kty" style="color:${KGC[n.type]||'var(--ink-3)'}">${n.type}</span><h3>${esc(ttl||n.label)}</h3></div>`;
  if(id==='root')return h+'<p>위의 분류 중 하나를 골라 들어가세요. 노드를 누르면 한 단계 깊이 들어가고, 위쪽 경로를 누르면 되돌아옵니다.</p>';
  if(n.type==='분류')return h+`<p>${n.full}</p><p class="d">오른쪽 목록에서 하나를 골라 들어가세요.</p>`;
  const d=kbDoc(id);if(d)return h+`<p>${d.sum}</p>`+row('근거',d.basis||'-')+row('버전',d.ver2||d.ver)+row('관제 활용',d.use||'-')+`<a class="btn" href="${d.src}" target="_blank" rel="noopener" style="margin-top:8px;display:inline-block;text-decoration:none;color:var(--ink)">KPX 원문</a>`;
  if(id.startsWith('s-')){const s=KSEC.find(x=>x.id===id);return h+`<p>${s.sum}</p>`+row('소속',kbDoc(s.doc).t)+row('확인 수준','원문 구조 확인')}
  if(id.startsWith('c-')){const c=KCON.find(x=>x.id===id);const ns=ukItems().filter(u=>c.t.split(/[ ·]/).some(w=>w.length>1&&u.kw.includes(w.slice(0,2))));return h+`<p>${c.desc}</p>`+row('근거 규정',c.docs.map(x=>kbDoc(x).t).join(', '))+row('내부 절차',c.int.join(', ')||'-')+row('관련 사용자 지식',ns.length+'건')}
  if(t==='doc'){const D=DOCS.find(x=>x.id===v);return h+`<p>${D?D.t+': '+D.b:''}</p>`+row('구분','내부 운영절차 (데모용 가상)')+row('적용 업무',KCON.filter(c=>c.int.includes(v)).map(c=>c.t).join(', ')||'-')}
  if(t==='line'){const l=allLines(twin).find(x=>x.id===v),r=dcpf(twin),p=r&&!isOut(r,l)?ld(r,l):0,bad=n1Now(twin).some(c=>c.l.id===v);return h+row('전압',l.kv?l.kv+'kV':'-')+row('회선',`${remC(l,twin)}/${l.cx.length}회선 운전`)+row('정격',fmt(r?r.R[l.id]:0)+'MW')+row('현재 부하율',Math.round(p)+'%')+row('N-1',bad?'이 선로 1회선 고장 시 위반 발생':'위반 없음')+row('설비 ID',`<code>${l.eq}</code>`)}
  if(t==='bus'){const i=+v,gs=gensOf(twin).filter(g=>g.b===i&&g.on);return h+row('전압',M.buses[i].kv?M.buses[i].kv+'kV':'-')+row('부하',fmt(twin.load[i])+'MW')+row('발전',fmt(gs.reduce((a,g)=>a+g.p,0))+'MW')+row('연결 선로',allLines(twin).filter(l=>l.f===i||l.t===i).length+'개')}
  if(t==='plant'){const gs=M.gens.map((g,i)=>({g,i})).filter(o=>o.g.plant===v);return h+row('발전원',gs[0]?gs[0].g.type:'-')+row('호기',gs.length+'기')+row('설비용량',fmt(gs.reduce((a,o)=>a+o.g.max,0))+'MW')+row('현재 출력',fmt(gs.reduce((a,o)=>a+(twin.gen[o.i].on?twin.gen[o.i].p:0),0))+'MW')+row('접속',gs[0]?nameOf(gs[0].g.b):'-')}
  if(t==='gtype')return h+row('설비',M.gens.filter(g=>g.type===v).length+'기');
  if(t==='case'){const f=feedback[+v];const r=REASONS.find(x=>x.id===f.r);return h+row('질의',esc(f.q))+row('판단',f.d)+row('사유',r?r.t:'-')+row('추천 대안',esc(f.a))+row('시각',f.t)}
  if(t==='note'){const u=UKB.find(x=>x.id===v);return h+[['상황',u.c.s],['조치',u.c.a],['결과',u.c.r],['교훈',u.c.l]].filter(x=>x[1]!=='-').map(([k,x])=>row(k,esc(x))).join('')+row('작성',`${u.au} (${u.team}) · ${u.date}`)+row('신뢰 등급',u.trust)}
  return h}
function kgDraw(){if(!kgNode(KG.focus)){KG.focus='root';KG.path=['root']}const id=KG.focus,c=kgNode(id),prev=KG.path[KG.path.length-2];
  const E=kgEdges(id).filter(e=>e.b!==prev&&e.b!==id),rowH=46,W=760,H=Math.max(200,E.length*rowH+40),lx=40,lw=230,rx=420,rw=300,cy=H/2;
  let g='';E.forEach((e,i)=>{const y=20+i*rowH+rowH/2;g+=`<path class="kge" d="M${lx+lw} ${cy} C${(lx+lw+rx)/2} ${cy},${(lx+lw+rx)/2} ${y},${rx} ${y}"/><text class="kgr" x="${rx-8}" y="${y-8}" text-anchor="end">${esc(e.rel.length>18?e.rel.slice(0,17)+'…':e.rel)}</text>`});
  const box=(n,x,y,w,hh,big)=>{const col=KGC[n.type]||'#5e7183';const lab=n.label.length>(big?16:20)?n.label.slice(0,big?15:19)+'…':n.label;return `<g class="kgn${big?' big':''}" data-id="${n.id}" tabindex="0" role="button"><title>${esc(n.full||n.label)}</title><rect x="${x}" y="${y-hh/2}" width="${w}" height="${hh}" rx="9" style="stroke:${col}"/><rect class="kgbar" x="${x}" y="${y-hh/2}" width="5" height="${hh}" rx="2" style="fill:${col}"/><text class="kgt" x="${x+14}" y="${y-(big?6:3)}" style="fill:${col}">${n.type}</text><text class="kgl" x="${x+14}" y="${y+(big?13:12)}">${esc(lab)}</text>${big?'':`<text class="kgarr" x="${x+w-14}" y="${y+5}" text-anchor="end">›</text>`}</g>`};
  E.forEach((e,i)=>{g+=box(kgNode(e.b),rx,20+i*rowH+rowH/2,rw,36,false)});g+=box(c,lx,cy,lw,56,true);
  if(!E.length)g+=`<text class="kgr" x="${rx}" y="${cy}">더 들어갈 연결이 없습니다. 위쪽 경로로 돌아가세요.</text>`;
  const svg=$('kgSvg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.innerHTML=g;
  svg.querySelectorAll('.kgn:not(.big)').forEach(x=>{const f=()=>{KG.path.push(x.dataset.id);KG.focus=x.dataset.id;kgDraw()};x.onclick=f;x.onkeydown=e=>{if(e.key==='Enter')f()}});
  $('kgCrumb').innerHTML=KG.path.map((p,i)=>{const n=kgNode(p);return n?`<button type="button" class="crumb${i===KG.path.length-1?' on':''}" data-i="${i}">${esc(n.label.length>14?n.label.slice(0,13)+'…':n.label)}</button>`:''}).join('<span class="csep">›</span>');
  $('kgCrumb').querySelectorAll('.crumb').forEach(b=>b.onclick=()=>{KG.path=KG.path.slice(0,+b.dataset.i+1);KG.focus=KG.path[KG.path.length-1];kgDraw()});
  $('kgInfo').innerHTML=kgInfoHtml(id)}
function kbFocus(id){const cat=id.startsWith('note:')?'cat:user':id.startsWith('line:')||id.startsWith('bus:')?'cat:equip':id.startsWith('case:')?'cat:case':kbDoc(id)?'cat:law':id.startsWith('c-')?'cat:task':null;KG.path=['root',...(cat?[cat]:[]),id];KG.focus=id;document.querySelector('nav button[data-v="kb"]').click();setTimeout(()=>$('kgSvg').scrollIntoView({behavior:'smooth',block:'center'}),50)}
KGC['시작']='#18222d';KGC['분류']='#2457c5';

/* 지식그래프 검색 */
function kgIndex(){const L=[];KDOC.forEach(d=>L.push([d.id,d.t,d.kind,'cat:law']));KSEC.forEach(s=>L.push([s.id,s.t+' · '+kbDoc(s.doc).t,'조항','cat:law']));KCON.forEach(c=>L.push([c.id,c.t,'업무','cat:task']));
  DOCS.forEach(d=>L.push(['doc:'+d.id,d.id+' '+d.t,'운영절차','cat:task']));M.buses.forEach((b,i)=>L.push(['bus:'+i,nameOf(i),'변전소','cat:equip']));allLines(twin).forEach(l=>L.push(['line:'+l.id,lname(l)+KV(l.kv),'선로','cat:equip']));
  [...new Set(M.gens.map(g=>g.plant))].forEach(p=>L.push(['plant:'+p,p,'발전소','cat:plant']));ukItems().forEach(u=>L.push(['note:'+u.id,u.t,'사용자 지식','cat:user']));feedback.forEach((f,i)=>L.push(['case:'+i,f.q,'사례','cat:case']));return L}
function kgSearch(){const q=$('kgQ').value.trim();const box=$('kgRes');if(!q){box.hidden=true;return}const qs=q.replace(/\s/g,'');
  const r=kgIndex().map(x=>({x,s:x[1].replace(/\s/g,'').indexOf(qs)})).filter(o=>o.s>=0).sort((a,b)=>a.s-b.s||a.x[1].length-b.x[1].length).slice(0,10);
  box.hidden=false;box.innerHTML=r.length?r.map(o=>`<button type="button" data-id="${o.x[0]}" data-c="${o.x[3]}"><span class="kty">${o.x[2]}</span>${esc(o.x[1])}</button>`).join(''):'<p class="d">결과 없음</p>';
  box.querySelectorAll('button').forEach(b=>b.onclick=()=>{KG.path=['root',b.dataset.c,b.dataset.id];KG.focus=b.dataset.id;box.hidden=true;$('kgQ').value='';kgDraw()})}

/* ===== 일일 운영 보고서 (날짜별) ===== */
const RP={sel:null,days:[],status:{}};
const WD=['일','월','화','수','목','금','토'];
const dkey=d=>`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
const dlab=d=>`${d.getMonth()+1}월 ${d.getDate()}일(${WD[d.getDay()]})`;
function rand(seed){let x=Math.sin(seed)*10000;return x-Math.floor(x)}
function dayMetrics(fd,cloud){const P=MODELS.kpg193.profile.hours,G=MODELS.kpg193.gens;const hs=P.map((H,h)=>{const dem=H.dem*fd,pv=H.pv*cloud,renT=H.ren.reduce((a,b)=>a+b,0)-H.pv+pv;const cap=G.reduce((a,g,i)=>a+(H.gen[i]>0.5?g.max:0),0)*Math.max(1,fd)+renT+(H.pv-pv);return {h,dem,pv,res:cap-dem}});
  const pk=hs.reduce((a,b)=>b.dem>a.dem?b:a),mn=hs.reduce((a,b)=>b.dem<a.dem?b:a),pvm=hs.reduce((a,b)=>b.pv>a.pv?b:a),rm=hs.reduce((a,b)=>b.res/b.dem<a.res/a.dem?b:a);
  return {hs,peak:pk,min:mn,pv:pvm,rmin:rm,rate:rm.res/rm.dem*100}}
const WDN=[ // 평일 서사
 {ev:[['06:40','감시','제천–충주 오전 부하율 90% 진입, 동해안 재급전 여유 확인'],['08:10','조치','제천–충주 대비 동해화력 감발 300MW · 영흥화력 증발 300MW'],['09:30','확인','제천–충주 부하율 88%로 안정']],dec:['재급전 300MW 시행 (근거: 전력시장운영규칙, 운영절차 3.2)'],sys:[6,5,1,'설비를 잘못 짚음'],next:'오전 수요 증가 구간 제천–충주 재감시',memo:'오전 고부하 패턴 3일째, 인수인계 메모 갱신',viol:0,n1:18,au:['이OO','3조'],rv:'박OO'},
 {ev:[['09:20','경보','11:00 아산–청양 106% 예보 (태양광 증가)'],['09:40','조치','신서천화력 감발 400MW · 영흥화력 증발 400MW 예약'],['11:00','확인','아산–청양 96%, 정격 이내'],['14:30','확인','태양광 감소로 재급전 단계적 해제']],dec:['예방 재급전 400MW 예약·시행 (선제 경보 기반)'],sys:[9,8,1,'계산 수치가 의심됨'],next:'맑음 예보, 11~14시 충청 북상 조류 주의',memo:'선제 경보 2시간 전 통보 효과 확인',viol:0,n1:22,au:['김OO','2조'],rv:'박OO'},
 {ev:[['13:05','고장','신보령화력 #2 불시 정지 (1,000MW 탈락)'],['13:06','조치','운영예비력 투입 지시, 주파수 회복 확인'],['13:20','검토','시스템 재급전 추천(보령화력 #5) 부적절: 계획 정비 중'],['13:35','조치','대체안 서울복합 증발 300MW 시행']],dec:['운영예비력 투입 (근거: 신뢰도 기준 고시, 별표 3 예비력 운영 기준)','시스템 추천 부적절 1건 (사유: 현장 사정 불일치)'],sys:[7,5,2,'대안이 현장 사정에 안 맞음'],next:'신보령화력 #2 재기동 일정 확인, 정비 정보 연계 요청',memo:'정비 일정 미연계로 추천 부적절 발생, 학습 파이프라인 부적절 누적에 반영',viol:0,n1:25,au:['정OO','4조'],rv:'박OO'},
 {ev:[['10:00','휴전','양평–태백 765kV 1회선 휴전 사전 검토'],['10:15','검토','주간 휴전 시 제천–충주 120% 예상 → 새벽 02~05시로 조정 요청'],['16:00','확인','작업 부서 일정 조정 회신']],dec:['765kV 휴전 시간대 조정 (근거: 운영절차 6.1, 계통평가 세부운영규정)'],sys:[5,5,0,''],next:'익일 02~05시 양평–태백 1회선 휴전 시행 예정',memo:'휴전 사전 검토 질의 템플릿 활용',viol:0,n1:20,au:['최OO','1조'],rv:'박OO'},
 {ev:[['18:40','경보','강남–서초 부하율 98% (저녁 피크)'],['18:50','조치','서울복합 증발 200MW'],['19:30','확인','강남–서초 92%'],['21:00','확인','수요 감소, 증발 해제']],dec:['재급전 200MW, 수요자원(DR) 발령은 보류'],sys:[8,7,1,'근거·설명이 부족함'],next:'저녁 피크 수도권 내부 혼잡 지속 감시',memo:'수도권 내부 혼잡은 내부 발전기 증발이 효과적',viol:1,n1:24,au:['정OO','4조'],rv:'박OO'}];
const WKN=[ // 주말 서사
 {ev:[['11:30','감시','호남 태양광 과잉, 하향예비력 확인'],['12:00','검토','출력제어 필요성 검토 → 화력 하향 여유로 미시행'],['15:00','확인','태양광 감소']],dec:['출력제어 미시행 (하향예비력 우선 활용)'],sys:[4,4,0,''],next:'주말 낮 저수요 · 태양광 과잉 지속 감시',memo:'출력제어 전 하향예비력 우선 원칙 재확인',viol:0,n1:14,au:['한OO','2조'],rv:'이OO'},
 {ev:[['02:00','휴전','서천–군산 1회선 계획 정비 착수 (저수요 시간대)'],['02:10','조치','군산 측 발전 감발 150MW로 남은 회선 부하율 관리'],['05:40','확인','정비 완료, 2회선 복구']],dec:['계획 정비 시행 (근거: 운영절차 6.1)'],sys:[3,3,0,''],next:'정상',memo:'남은 회선 최대 부하율 84%로 관리',viol:0,n1:12,au:['최OO','1조'],rv:'이OO'}];
function rpBuild(){if(RP.days.length)return;const today=new Date();let wi=0,ki=0;
  for(let k=7;k>=1;k--){const d=new Date(today);d.setDate(today.getDate()-k);const wk=d.getDay()===0||d.getDay()===6;const nar=wk?WKN[ki++%WKN.length]:WDN[wi++%WDN.length];
    const fd=(wk?.86:.98)+rand(k*3.1)*.06,cloud=.35+rand(k*7.7)*.65;RP.days.push({key:dkey(d),date:d,lab:dlab(d),past:true,met:dayMetrics(fd,cloud),cloud,nar})}
  RP.days.push({key:dkey(today),date:today,lab:dlab(today),past:false});RP.sel=RP.days[RP.days.length-1].key;RP.days.forEach(x=>RP.status[x.key]=x.past?'공식 반영':'작성 중')}
function liveDay(){const cf=feedback.length,rej=feedback.filter(f=>f.d==='부적절');const r=dcpf(twin),v=r?viol(r):[],nv=r?n1Now(twin).length:0;
  let met=null;if(M.profile&&TL.on){met=dayMetrics(1,1)}const ev=JN.filter(j=>j.model===M.name&&!['query'].includes(j.type)).slice(-14).map(j=>[j.st||j.t.slice(0,5),{forecast:'경보',inject:'고장',apply:'조치',approve:'적절',reject:'부적절',prevent:'검토',plan:'조치',train:'훈련',model:'시스템',timeline:'운전',scenario:'검토'}[j.type]||j.type,j.text]);
  const reasons=rej.map(f=>(REASONS.find(x=>x.id===f.r)||{}).t).filter(Boolean);
  return {met,ev,dec:feedback.filter(f=>f.d==='적절').map(f=>`${f.q} → 적절${f.a!=='-'?` (${f.a})`:''}`),sys:[JN.filter(j=>j.type==='query').length,cf-rej.length,rej.length,reasons.join(', ')],next:TL.on?(TL.sum.map((x,h)=>({x,h})).filter(o=>o.h>TL.h&&o.x&&o.x.viol.length).slice(0,3).map(o=>`${pad(o.h)}:00 ${o.x.viol.slice(0,2).map(q=>`${lname(q.l)} ${Math.round(q.p)}%`).join(', ')}`).join(' / ')||'남은 시간대 예상 과부하 없음'):(nv?`N-1 위반 사고 ${nv}건 예방 재급전 검토`:'특이 위험 없음'),memo:ukItems().filter(u=>u.au==='나').map(u=>u.t).join(', ')||'-',viol:v.length,n1:nv,au:['나 (현재 사용자)','근무조'],rv:'-'}}
function rpChart(met){if(!met)return `<p class="d">${MODE==='live'?'수급 곡선: 실시간 동기 데이터 누적 중':'수급 곡선: 하루 흐름 재생 시 표시'}</p>`;const W=640,H=140,mx=Math.max(...met.hs.map(x=>x.dem))*1.06,X=h=>36+h*(W-50)/23,Y=v=>H-18-v/mx*(H-24);
  let g=`<path class="dpv" d="M${X(0)} ${Y(0)} ${met.hs.map(x=>`L${X(x.h)} ${Y(x.pv)}`).join(' ')} L${X(23)} ${Y(0)}Z"/><path class="ddem" d="${met.hs.map((x,i)=>`${i?'L':'M'}${X(x.h)} ${Y(x.dem)}`).join(' ')}"/>`;
  g+=`<circle class="pk" cx="${X(met.peak.h)}" cy="${Y(met.peak.dem)}" r="4"/><text class="pkl" x="${X(met.peak.h)}" y="${Math.max(11,Y(met.peak.dem)-8)}" text-anchor="middle">최대 ${fmt(met.peak.dem)}</text><circle class="pk mn" cx="${X(met.min.h)}" cy="${Y(met.min.dem)}" r="4"/><text class="pkl" x="${X(met.min.h)}" y="${Y(met.min.dem)+16}" text-anchor="middle">최저 ${fmt(met.min.dem)}</text>`;
  for(let h=0;h<24;h+=3)g+=`<text class="dax" x="${X(h)}" y="${H-3}" text-anchor="middle">${pad(h)}</text>`;return `<svg class="rpch" viewBox="0 0 ${W} ${H}" role="img" aria-label="수요와 태양광 곡선">${g}</svg>`}
function renderReport(){rpBuild();const el=$('report');if(!el)return;const D=RP.days.find(x=>x.key===RP.sel)||RP.days[RP.days.length-1];const N=D.past?D.nar:liveDay(),met=D.past?D.met:N.met;
  // 날짜 목록
  $('rpDays').innerHTML=RP.days.slice().reverse().map(x=>{const n=x.past?x.nar:null,st=RP.status[x.key];return `<button type="button" class="rpd${x.key===RP.sel?' on':''}" data-k="${x.key}"><b>${x.lab}${x.past?'':' · 오늘'}</b><span>${x.past?`최대 ${fmt(x.met.peak.dem)}MW · 사건 ${n.ev.length}건`:'실시간 자동 초안'}</span><i class="rps rps-${st.replace(/\s/g,'')}">${st}</i></button>`}).join('');
  $('rpDays').querySelectorAll('.rpd').forEach(b=>b.onclick=()=>{RP.sel=b.dataset.k;renderReport()});
  // 주간 추이
  if(!RP.days.some(x=>x.past))$('rpWeek').innerHTML='<text x="150" y="46" text-anchor="middle" class="dax" style="font-size:11px">실계통 연동 후 누적</text>';else{const past=RP.days.filter(x=>x.past),W=300,H=86,mxp=Math.max(...past.map(x=>x.met.peak.dem)),mnp=Math.min(...past.map(x=>x.met.peak.dem))*.95,X=i=>20+i*(W-40)/(past.length-1),Y=v=>H-18-(v-mnp)/(mxp-mnp)*(H-34);
  $('rpWeek').innerHTML=`<path class="ddem" d="${past.map((x,i)=>`${i?'L':'M'}${X(i)} ${Y(x.met.peak.dem)}`).join(' ')}"/>${past.map((x,i)=>`<circle class="pk" cx="${X(i)}" cy="${Y(x.met.peak.dem)}" r="3.5"><title>${x.lab} 최대 ${fmt(x.met.peak.dem)}MW · 최저 예비율 ${x.met.rate.toFixed(1)}%</title></circle><text class="dax" x="${X(i)}" y="${H-3}" text-anchor="middle">${x.date.getDate()}(${WD[x.date.getDay()]})</text>${x.nar.ev.some(e=>e[1]==='고장')?`<text class="evm" x="${X(i)}" y="${Y(x.met.peak.dem)-8}" text-anchor="middle">고장</text>`:''}`).join('')}`;}
  const st=RP.status[D.key],kp=(l,v,s)=>`<div class="rk2"><span>${l}</span><b>${v}</b>${s?`<em>${s}</em>`:''}</div>`;
  let h=`<div class="rph"><div><h1>일일 계통 운영 보고 <span class="draft">초안</span></h1><p class="rmeta">${D.lab} · ${MODE==='live'?'실계통 연동 (모의 피드)':'계통 모델 '+(D.past?'KPG 193 (한국 합성 계통)':esc(M.name))} · ${D.past?'예시 보고서 (가상 사건)':'자동 작성 초안'}</p></div>
    <table class="appr"><tr><th>초안 작성</th><th>검토</th><th>공식 반영</th></tr><tr><td>${N.au[0]}<br><span>${N.au[1]}</span></td><td>${st==='작성 중'?'-':N.rv}</td><td>${st==='공식 반영'?'운영부장':'-'}</td></tr></table></div>`;
  h+=`<h2>1. 수급 실적</h2>${met?`<div class="rkp">${kp('최대 수요',fmt(met.peak.dem)+'MW',pad(met.peak.h)+':00')}${kp('최저 수요',fmt(met.min.dem)+'MW',pad(met.min.h)+':00')}${kp('태양광 최대',fmt(met.pv.pv)+'MW',pad(met.pv.h)+':00')}${kp('최저 예비율',met.rate.toFixed(1)+'%',`${pad(met.rmin.h)}:00 · ${fmt(met.rmin.res)}MW`)}</div>${rpChart(met)}`:rpChart(null)}`;
  h+=`<h2>2. 계통 운영 실적</h2><div class="rkp">${kp('정격 초과 발생',N.viol+'건','')}${kp('N-1 위반 사고 (최대)',N.n1+'건','')}${kp('고장·정지',N.ev.filter(e=>e[1]==='고장').length+'건','')}${kp('휴전·작업',N.ev.filter(e=>e[1]==='휴전').length+'건','')}</div>`;
  h+=`<h2>3. 주요 경과</h2>${N.ev.length?`<table class="rtbl"><tr><th>시각</th><th>구분</th><th>내용</th></tr>${N.ev.map(e=>`<tr><td>${e[0]}</td><td><span class="rk rk-${{경보:'forecast',고장:'inject',조치:'apply',승인:'approve',부적절:'reject',검토:'prevent',휴전:'plan',확인:'train'}[e[1]]||'model'}">${e[1]}</span></td><td>${esc(e[2])}</td></tr>`).join('')}</table>`:'<p>기록된 경과가 없습니다. 관제 화면에서 질의·고장 주입·예방 조치를 하면 자동으로 채워집니다.</p>'}`;
  h+=`<h2>4. 판단과 조치 (근거)</h2>${N.dec.length?`<ul>${N.dec.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<p>승인된 조치가 없습니다.</p>'}`;
  h+=`<h2>5. 의사결정지원 활용</h2><p>질의 ${N.sys[0]}건 · 관제사 승인 ${N.sys[1]}건 · 부적절 ${N.sys[2]}건${N.sys[3]?` (부적절 사유: ${esc(N.sys[3])})`:''}. 부적절 건은 학습 파이프라인의 피드백 정제 단계로 전달됩니다.</p>`;
  h+=`<h2>6. 익일 주의 사항</h2><p>${esc(N.next)}</p><h2>7. 특이사항 · 인수인계</h2><p>${esc(N.memo)}</p><p class="d" style="margin-top:14px">DC 조류 근사와 KPG 193 합성 계통 기반의 ${D.past?'예시':'자동 작성'} 보고서입니다. 확정 전 담당자 검토가 필요합니다.</p>`;
  if(!D.past)h+=`<div class="rpact">${st==='작성 중'?'<button class="btn pri" type="button" id="rpReq">검토 요청</button>':st==='검토 중'?'<button class="btn pri" type="button" id="rpOk">공식 보고 반영 (시연)</button>':'<span class="d">공식 보고 반영 완료</span>'}</div>`;
  el.innerHTML=h;const rq=$('rpReq'),ok=$('rpOk');if(rq)rq.onclick=()=>{RP.status[D.key]='검토 중';addJN('apply','일일 운영 보고 검토 요청');renderReport()};if(ok)ok.onclick=()=>{RP.status[D.key]='공식 반영';addJN('approve','일일 운영 보고 공식 반영');renderReport()}}

/* ===== 스냅샷 저장소 ===== */
const SNAP=[];let snapSeq=0,SNSEL=null;
function fnv(s){let h=0x811c9dc5;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return (h>>>0).toString(16).padStart(8,'0')}
function topoVer(s){return fnv(M.id+JSON.stringify(s.out)+s.add.map(l=>l.id).join()).slice(0,6)}
function snapPayload(s){return {out:s.out,add:s.add.map(l=>({id:l.id,f:l.f,t:l.t,r:l.cr[0]})),gen:s.gen.map(g=>[+g.p.toFixed(1),g.on]),load:s.load.map(v=>+v.toFixed(1)),ren:(s.ren||[]).map(v=>+v.toFixed(1)),newGen:s.newGen.map(g=>({id:g.id,b:g.b,p:g.p}))}}
function snapPush(kind,trigger,state,trace){if(!M||!state)return null;const d=new Date(),body=JSON.stringify(snapPayload(state)),id=`SN-${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}-${String(++snapSeq).padStart(3,'0')}`;
  const r=dcpf(state);const sn={id,ts:now(),date:d,sim:typeof TL!=='undefined'&&TL.on?`${M.profile?M.profile.label:'표준일'} ${pad(TL.h)}:00`:'실시간 운전점',kind,trigger,trace:trace||'-',model:M.id,modelName:M.name,topo:topoVer(state),src:'SCADA · 상태추정 (모의)',quality:'불량 측정 0건',hash:fnv(body),size:+(body.length/1024).toFixed(1),state:cloneS(state),labels:[],ds:null,
    sum:{load:Math.round(state.load.reduce((a,b)=>a+b,0)),viol:r?viol(r).length:-1,max:r?Math.round(Math.max(...live(r).map(l=>ld(r,l)))):0,out:Object.keys(state.out).length}};
  SNAP.unshift(sn);if(SNAP.length>400)SNAP.pop();if($('v-sn')&&$('v-sn').classList.contains('on'))snRender();return sn}
function snapLabel(pred,label){const s=SNAP.find(pred);if(s){s.labels.push(label);if($('v-sn').classList.contains('on'))snRender()}return s}
const SN_KIND={'정기':'sk-p','사건':'sk-e','판단':'sk-d'};
function snFiltered(){const k=$('snK').value,l=$('snL').value;return SNAP.filter(s=>s.model===M.id&&(k==='전체'||s.kind===k)&&(l==='전체'||(l==='라벨 있음'?s.labels.length:l==='부적절'?s.labels.some(x=>x.startsWith('부적절')):!s.labels.length)))}
function snRender(){const all=SNAP.filter(s=>s.model===M.id),list=snFiltered();const c=k=>all.filter(s=>s.kind===k).length,lab=all.filter(s=>s.labels.length).length,tot=all.reduce((a,s)=>a+s.size,0);
  $('snK2').innerHTML=`<div class="kpi"><span>저장된 스냅샷</span><b>${all.length}개</b></div><div class="kpi"><span>정기 · 사건 · 판단</span><b>${c('정기')} · ${c('사건')} · ${c('판단')}</b></div><div class="kpi"><span>라벨 연결</span><b>${lab}개</b></div><div class="kpi"><span>용량 (원본 JSON 기준)</span><b>${tot.toFixed(1)} KB</b></div>`;
  $('snList').innerHTML=list.length?`<table class="reg"><tr><th>스냅샷 ID</th><th>저장 시각 · 운전 시점</th><th>종류</th><th>트리거</th><th>라벨</th><th>데이터셋</th></tr>${list.slice(0,80).map(s=>`<tr class="snr${s.id===SNSEL?' on':''}" data-id="${s.id}" tabindex="0"><td><code>${s.id}</code></td><td>${s.ts}<div class="d">${s.sim}</div></td><td><span class="snk ${SN_KIND[s.kind]}">${s.kind}</span></td><td>${esc(s.trigger.length>34?s.trigger.slice(0,33)+'…':s.trigger)}${s.trace!=='-'?`<div class="d">${s.trace}</div>`:''}</td><td>${s.labels.length?s.labels.slice(-2).map(x=>`<span class="snl ${x.startsWith('부적절')?'r':x.startsWith('적절')?'a':''}">${esc(x)}</span>`).join(''):'<span class="d">-</span>'}</td><td>${s.ds?`<code>${s.ds}</code>`:'<span class="d">대기</span>'}</td></tr>`).join('')}</table>`:'<p class="empty" style="display:block">조건에 맞는 스냅샷이 없습니다. 관제 화면에서 질의하거나 하루 흐름을 재생하면 쌓입니다.</p>';
  $('snList').querySelectorAll('.snr').forEach(r=>{const f=()=>{SNSEL=r.dataset.id;snRender()};r.onclick=f;r.onkeydown=e=>{if(e.key==='Enter')f()}});
  const s=SNAP.find(x=>x.id===SNSEL)||list[0];if(s&&!SNSEL)SNSEL=s.id;
  if(!s){$('snDet').innerHTML='<p class="d" style="padding:16px 20px">스냅샷을 고르면 메타데이터와 내용이 보입니다.</p>';return}
  const d=s.date,path=`grid-lake/snapshots/${d.getFullYear()}/${pad(d.getMonth()+1)}/${pad(d.getDate())}/${s.id}`;
  const row=(k,v)=>`<div class="kdr"><span>${k}</span><b>${v}</b></div>`;
  const prev=JSON.stringify({snapshot_id:s.id,kind:s.kind,captured_at:d.toISOString(),sim_time:s.sim,model:{id:s.model,topology_version:s.topo},source:s.src,quality:s.quality,sha:s.hash,labels:s.labels,state:snapPayload(s.state)},null,1);
  $('snDet').innerHTML=`<div class="snh"><span class="snk ${SN_KIND[s.kind]}">${s.kind}</span><code>${s.id}</code></div>
   ${row('트리거',esc(s.trigger))}${row('운전 시점',s.sim)}${row('추적 번호',s.trace)}${row('계통 모델',`${s.modelName} · 설비 구성 <code>${s.topo}</code>`)}${row('데이터 출처',s.src)}${row('품질',s.quality)}${row('무결성 해시',`<code>${s.hash}</code>`)}
   ${row('상태 요약',`총 부하 ${fmt(s.sum.load)}MW · 최대 부하율 ${s.sum.max}% · 정격 초과 ${s.sum.viol<0?'계통 분리':s.sum.viol+'건'} · 탈락 설비 ${s.sum.out}개`)}
   ${row('라벨',s.labels.length?s.labels.map(x=>esc(x)).join('<br>'):'아직 없음 (승인·부적절나 조치 결과가 나오면 붙습니다)')}
   ${row('저장 경로',`<code class="path">${path}/state.parquet</code><br><code class="path">${path}/topology.cim.xml</code><br><code class="path">${path}/meta.json</code>`)}
   ${row('데이터셋',s.ds?`<code>${s.ds}</code>에 포함`:'다음 학습 파이프라인 실행 때 포함 대상')}
   <div class="kdbtn"><button class="btn pri" type="button" id="snReplay">계통도에서 재현</button><button class="btn" type="button" id="snCopy">메타데이터 복사</button></div>
   <div class="ct" style="margin-top:12px">meta.json 미리보기</div><pre class="snjson" id="snJson">${esc(prev.length>2400?prev.slice(0,2400)+'\n …':prev)}</pre>`;
  $('snReplay').onclick=()=>{document.querySelector('nav button[data-v="ops"]').click();lastScen=null;setView({state:cloneS(s.state),ref:null,label:`스냅샷 재현 ${s.id} (현 계통 아님)`,hl:{lines:new Set(Object.keys(s.state.out)),marks:[],focus:[]}},true);logCall('get_measurements',`replay ${s.id}`,'restored from snapshot','0.01s')};
  $('snCopy').onclick=async()=>{try{await navigator.clipboard.writeText(prev);$('snCopy').textContent='복사됨'}catch(e){const r=document.createRange();r.selectNodeContents($('snJson'));const g=getSelection();g.removeAllRanges();g.addRange(r);$('snCopy').textContent='선택됨, Ctrl+C로 복사'}setTimeout(()=>{if($('snCopy'))$('snCopy').textContent='메타데이터 복사'},1800)}}

/* ===== 수집 파이프라인 + 데이터 레이크 ===== */
const LK={on:false,timer:null,clock:null,pk:[],raf:0,cnt:{rx:0,ok:0,bad:0,tf:0,ld:0},tp:[],log:[],part:null,seeded:{}};
function snapPush(kind,trigger,state,trace,opt){opt=opt||{};if(!M||!state)return null;const d=opt.date||new Date(),body=JSON.stringify(snapPayload(state));
  const id=`SN-${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}-${String(++snapSeq).padStart(4,'0')}`;
  const sn={id,ts:`${pad(d.getHours())}:${pad(d.getMinutes())}`,date:d,dkey:dkey(d),sim:opt.sim||(typeof TL!=='undefined'&&TL.on?`${M.profile?M.profile.label:'표준일'} ${pad(TL.h)}:00`:'실시간 운전점'),kind,trigger,trace:trace||'-',model:M.id,modelName:M.name,topo:topoVer(state),src:opt.src||'EMS 스냅샷 · 상태추정 결과 (모의)',quality:opt.bad?'불량 측정 1건':'정상',status:opt.bad?'격리':'적재',hash:fnv(body),size:+(body.length/1024).toFixed(1),state:opt.keep?state:cloneS(state),labels:opt.labels||[],ds:opt.ds||null,ex:!!opt.ex,_sum:null};
  SNAP.unshift(sn);if(SNAP.length>900)SNAP.pop();if(!opt.silent){lkPacket(kind,opt.bad);if($('v-sn').classList.contains('on'))lakeRender();if(typeof dashLive==='function')dashLive()}return sn}
function topoSum(st){const L=allLines(st),par=M.buses.map((_,i)=>i),f=i=>par[i]===i?i:(par[i]=f(par[i]));let br=0;L.forEach(l=>{const k=remC(l,st);if(k>0){br+=k;par[f(l.f)]=f(l.t)}});const act=new Set();L.forEach(l=>{if(remC(l,st)>0){act.add(l.f);act.add(l.t)}});const isl=new Set([...act].map(f)).size;
  const ch=Object.entries(st.out).map(([id,n])=>{const l=L.find(x=>x.id===id);return `${lname(l)} ${n}/${l.cx.length}회선 개방`}).concat(st.add.map(l=>`${lname(l)} 신설`));return {nodes:act.size,br,isl,ch}}
function snSum(s){if(!s._sum){const r=dcpf(s.state);s._sum={load:Math.round(s.state.load.reduce((a,b)=>a+b,0)),viol:r?viol(r).length:-1,max:r?Math.round(Math.max(...live(r).map(l=>ld(r,l)))):0,out:Object.keys(s.state.out).length}}return s._sum}
/* --- 예시 데이터: 지난 7일 --- */
function lakeSeed(){if(MODE==='live'||M.id!=='kpg193'||LK.seeded.kpg193)return;LK.seeded.kpg193=true;rpBuild();const P=M.profile.hours;
  RP.days.filter(x=>x.past).forEach((D,di)=>{const fd=D.met.peak.dem/Math.max(...P.map(h=>h.dem)),cl=D.cloud;
    for(let h=0;h<24;h++){const H=P[h],tot=H.ren.reduce((a,b)=>a+b,0)||1,sh=H.pv/tot;const s=freshState();s.load=H.load.map(v=>v*fd);s.ren=H.ren.map(v=>v*(1-(1-cl)*sh));s.gen=M.gens.map((g,i)=>({p:H.gen[i],on:H.gen[i]>0.5?1:0}));balance(s);
      const dt=new Date(D.date);dt.setHours(h,0,0,0);const old=di<3;snapPush('정기',`정기 수집 ${pad(h)}:00`,s,'-',{date:dt,sim:`${D.lab} ${pad(h)}:00`,silent:true,keep:true,ex:true,bad:(h*7+di)%41===0,ds:old?'ds-fc-v4':null})}
    D.nar.ev.forEach((e,k)=>{const [hh,mm]=e[0].split(':').map(Number),dt=new Date(D.date);dt.setHours(hh,mm,0,0);const base=SNAP.find(x=>x.dkey===dkey(D.date)&&x.kind==='정기'&&x.date.getHours()===hh)||SNAP[0];
      const kind=['고장','경보','휴전'].includes(e[1])?'사건':['검토','조치'].includes(e[1])?'판단':null;if(!kind)return;
      const lab=kind==='판단'?[e[2].includes('부적절')?'부적절 · 대안이 현장 사정에 안 맞음':'적절 · '+e[2].slice(0,18),...(e[1]==='조치'?['결과 · 반영 후 정격 초과 0건']:[])]:[];
      snapPush(kind,e[2],base.state,`T-${pad(di+1)}${k}`,{date:dt,sim:`${D.lab} ${e[0]}`,silent:true,keep:true,ex:true,labels:lab,ds:di<3&&kind==='판단'?'ds-llm-v4':null})})});
  SNAP.sort((a,b)=>b.date-a.date)}
/* --- 실시간 수집 시각화 --- */
const LST=[['src','인메모리 DB','실시간 계통 상태'],['col','가져오기','정기 · 변경 이벤트'],['val','검증','형식·범위·정합성'],['tf','변환','CIM · 그래프'],['lake','데이터 레이크','파티션 · 목록 DB']];
const LX=i=>40+i*215,LY=78,LW=170,LH=64;
function lkDraw(){const g=$('lkSvg');if(!g)return;let h=`<defs><marker id="lkarr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M1 1L9 5L1 9" fill="none" stroke="context-stroke" stroke-width="1.6" stroke-linecap="round"/></marker></defs>`;
  for(let i=0;i<4;i++)h+=`<line class="lke${LK.on?' on':''}" x1="${LX(i)+LW}" y1="${LY}" x2="${LX(i+1)}" y2="${LY}" marker-end="url(#lkarr)"/>`;
  h+=`<path class="lke q" d="M${LX(2)+LW/2} ${LY+LH/2} L${LX(2)+LW/2} ${LY+LH/2+34}" marker-end="url(#lkarr)"/><rect class="lkq" x="${LX(2)+LW/2-62}" y="${LY+LH/2+38}" width="124" height="34" rx="9"/><text class="lkqt" x="${LX(2)+LW/2}" y="${LY+LH/2+59}" text-anchor="middle">격리 ${LK.cnt.bad}건</text>`;
  const c=LK.cnt,nums=[c.rx,c.rx,c.ok,c.tf,c.ld],labs=['송신','수신','통과','변환','적재'];
  h+=`<rect class="lkgrp" x="${LX(1)-14}" y="${LY-LH/2-26}" width="${LX(3)+LW-LX(1)+28}" height="${LH+40}" rx="16"/><text class="lkgt" x="${LX(1)-2}" y="${LY-LH/2-10}">적재 서버</text><text class="lkgs" x="${LX(0)+LW/2}" y="${LY-LH/2-10}" text-anchor="middle">${MODE==='live'?'운영 측 (신규 제작)':'모의 (KPG 기반)'}</text>`;
  LST.forEach(([id,t,s],i)=>{h+=`<g class="lks ${id}${LK.on?' on':''}"><rect x="${LX(i)}" y="${LY-LH/2}" width="${LW}" height="${LH}" rx="12"/><text class="lkt" x="${LX(i)+16}" y="${LY-6}">${t}</text><text class="lkd" x="${LX(i)+16}" y="${LY+13}">${s}</text><text class="lkn" x="${LX(i)+LW-14}" y="${LY-6}" text-anchor="end">${fmt(nums[i])}</text><text class="lkd" x="${LX(i)+LW-14}" y="${LY+13}" text-anchor="end">${labs[i]}</text></g>`});
  h+=`<g id="lkPk"></g>`;g.innerHTML=h}
function lkPacket(kind,bad){LK.cnt.rx++;LK.pk.push({kind,bad,t0:performance.now(),stage:0});LK.tp.push(Date.now());lkLog(kind,bad);if(!LK.raf)LK.raf=requestAnimationFrame(lkAnim);lkStats()}
function lkAnim(now){const g=$('lkPk');const D=2600;let h='';LK.pk=LK.pk.filter(p=>{const k=(now-p.t0)/D;const x0=LX(0)+LW/2,x1=LX(4)+LW/2;let x,y=LY;
    if(p.bad&&k>.5){const q=Math.min(1,(k-.5)/.25);x=LX(2)+LW/2;y=LY+q*(LH/2+52);if(p.stage<2){p.stage=2;LK.cnt.bad++;lkDrawCounts()}if(k>.85)return false}
    else{x=x0+(x1-x0)*Math.min(1,k);const st=Math.floor(Math.min(.999,k)*4)+1;while(p.stage<st){p.stage++;if(p.stage===3)LK.cnt.ok++;if(p.stage===4)LK.cnt.tf++;lkDrawCounts()}if(k>=1){LK.cnt.ld++;lkDrawCounts();return false}}
    h+=`<circle class="lkp ${p.bad&&k>.5?'bad':p.kind==='사건'?'ev':p.kind==='판단'?'dc':'pr'}" cx="${x}" cy="${y}" r="6.5"/>`;return true});
  if(g)g.innerHTML=h;LK.raf=LK.pk.length?requestAnimationFrame(lkAnim):0}
function lkDrawCounts(){if($('v-sn').classList.contains('on'))lkDraw()}
const BADR=['EMS 상태추정 미수렴 주기','시점 불일치: 측정·추정 주기 혼재','범위: 물리 한계 초과값','완결성: 측정점 누락 3.1%'];
function lkLog(kind,bad){const s=SNAP[0];if(bad&&s)s.quality=BADR[LK.cnt.rx%BADR.length];LK.log.unshift(`${now()}  ${bad?'격리':'수신'}  ${kind==='사건'?'변경 이벤트':kind==='정기'?'정기 스냅샷':'판단 스냅샷'}  ${s?s.id.slice(-9):''}  ${bad&&s?s.quality:s?s.trigger.slice(0,26):''}`);LK.log.length=Math.min(LK.log.length,40);const el=$('lkLog');if(el)el.innerHTML=LK.log.slice(0,8).map(l=>`<div class="${l.includes('격리')?'b':''}">${esc(l)}</div>`).join('')}
function lkStats(){const el=$('lkStat');if(!el)return;const n=Date.now(),rate=LK.tp.filter(t=>n-t<60000).length;const all=SNAP.filter(s=>s.model===M.id);
  el.innerHTML=`<div class="kpi"><span>수집 상태</span><b class="${LK.on?'on':''}">${LK.on?'가동 중':'정지'}</b></div><div class="kpi"><span>처리량</span><b>${rate}건/분</b></div><div class="kpi"><span>레이크 객체</span><b>${fmt(all.length)}개</b></div><div class="kpi"><span>레이크 용량</span><b>${(all.reduce((a,s)=>a+s.size,0)/1024).toFixed(2)} MB</b></div><div class="kpi"><span>격리 객체 (누적)</span><b>${all.filter(s=>s.status==='격리').length}개</b></div>`}
function lkToggle(){LK.on=!LK.on;$('lkRun').textContent=LK.on?'수집 정지':'수집 시작';$('lkRun').classList.toggle('pri',!LK.on);if(LK.on){LK.clock=LK.clock||new Date();lkTick()}else clearTimeout(LK.timer);lkDraw();lkStats()}
function lkTick(){if(!LK.on)return;LK.clock=new Date(LK.clock.getTime()+5*60000);const bad=(LK.cnt.rx+3)%9===0;snapPush('정기',`정기 수집 ${pad(LK.clock.getHours())}:${pad(LK.clock.getMinutes())}`,twin,'-',{date:new Date(),sim:`수집 시계 ${pad(LK.clock.getHours())}:${pad(LK.clock.getMinutes())}`,bad});LK.timer=setTimeout(lkTick,1500)}
/* --- 데이터 레이크 화면 --- */
function snRender(){lakeRender()}
function lakeRender(){lakeSeed();lkDraw();lkStats();lkLog;const all=SNAP.filter(s=>s.model===M.id);
  const parts={};all.forEach(s=>{(parts[s.dkey]=parts[s.dkey]||[]).push(s)});const keys=Object.keys(parts).sort().reverse();if(!LK.part||!parts[LK.part])LK.part=keys[0]||null;
  $('lkParts').innerHTML=keys.map(k=>{const a=parts[k],d=new Date(k+'T00:00');return `<button type="button" class="lkpart${k===LK.part?' on':''}" data-k="${k}"><span class="pf">grid-lake/snapshots/${k.replace(/-/g,'/')}/</span><b>${dlab(d)}${k===dkey(new Date())?' · 오늘':''}</b><span class="pc"><i class="sk-p">${a.filter(s=>s.kind==='정기').length}</i><i class="sk-e">${a.filter(s=>s.kind==='사건').length}</i><i class="sk-d">${a.filter(s=>s.kind==='판단').length}</i><em>${(a.reduce((x,s)=>x+s.size,0)/1024).toFixed(2)} MB</em></span></button>`}).join('')||'<p class="d">파티션 없음</p>';
  $('lkParts').querySelectorAll('.lkpart').forEach(b=>b.onclick=()=>{LK.part=b.dataset.k;SNSEL=null;lakeRender()});
  const kf=$('snK').value,lf=$('snL').value,list=(parts[LK.part]||[]).filter(s=>(kf==='전체'||s.kind===kf)&&(lf==='전체'||(lf==='라벨 있음'?s.labels.length:lf==='부적절'?s.labels.some(x=>x.startsWith('부적절')):lf==='격리'?s.status==='격리':!s.labels.length)));
  $('snList').innerHTML=list.length?`<table class="reg"><tr><th>시각</th><th>종류</th><th>트리거</th><th>상태</th><th>라벨</th><th>데이터셋</th></tr>${list.slice(0,120).map(s=>`<tr class="snr${s.id===SNSEL?' on':''}" data-id="${s.id}" tabindex="0" title="${s.id}"><td><b class="snt">${s.ts}</b><div class="d snid">#${s.id.slice(-4)}</div></td><td><span class="snk ${SN_KIND[s.kind]}">${s.kind}</span></td><td class="trg">${esc(s.trigger.length>30?s.trigger.slice(0,29)+'…':s.trigger)}</td><td><span class="snst ${s.status==='격리'?'b':''}">${s.status}</span></td><td>${s.labels.length?`<span class="snl ${s.labels[0].startsWith('부적절')?'r':'a'}">${esc(s.labels[0].split(' · ')[0])}</span>${s.labels.length>1?`<span class="d">+${s.labels.length-1}</span>`:''}`:'<span class="d">-</span>'}</td><td>${s.ds?`<code>${s.ds}</code>`:'<span class="d">대기</span>'}</td></tr>`).join('')}</table>`:'<p class="empty" style="display:block">결과 없음</p>';
  $('snList').querySelectorAll('.snr').forEach(r=>{const f=()=>{SNSEL=r.dataset.id;lakeRender()};r.onclick=f;r.onkeydown=e=>{if(e.key==='Enter')f()}});
  if(!SNSEL&&list[0])SNSEL=list[0].id;const s=SNAP.find(x=>x.id===SNSEL);
  if(!s){$('snDet').innerHTML='<p class="d" style="padding:16px 20px">객체 선택 시 상세 표시</p>';return}
  const sm=snSum(s),d=s.date,path=`grid-lake/${s.status==='격리'?'quarantine':'snapshots'}/${d.getFullYear()}/${pad(d.getMonth()+1)}/${pad(d.getDate())}/${s.id}`,row=(k,v)=>`<div class="kdr"><span>${k}</span><b>${v}</b></div>`;
  const prev=JSON.stringify({snapshot_id:s.id,kind:s.kind,captured_at:d.toISOString(),sim_time:s.sim,model:{id:s.model,topology_version:s.topo,graph:(()=>{const tg=topoSum(s.state);return {nodes:tg.nodes,branches:tg.br,islands:tg.isl,changes:tg.ch}})(),key:'CIM mRID'},source:s.src,quality:s.quality,status:s.status,sha:s.hash,labels:s.labels,dataset:s.ds,state:snapPayload(s.state)},null,1);
  $('snDet').innerHTML=`<div class="snh"><span class="snk ${SN_KIND[s.kind]}">${s.kind}</span><span class="snst ${s.status==='격리'?'b':''}">${s.status}</span><code>${s.id}</code></div>
   ${row('트리거',esc(s.trigger))}${row('운전 시점',s.sim)}${row('추적 번호',s.trace)}${row('계통 모델',`${s.modelName} · 설비 구성 <code>${s.topo}</code>`)}${row('출처 · 품질',`${s.src} · ${s.quality}`)}${row('무결성 해시',`<code>${s.hash}</code> · ${s.size} KB`)}
   ${(()=>{const tg=topoSum(s.state);return row('연결 그래프',`노드 ${tg.nodes} · 지로 ${tg.br}회선 · 연결 섬 ${tg.isl}개 · 구성 <code>${s.topo}</code>`)+row('구성 변경',tg.ch.length?tg.ch.map(esc).join('<br>'):'기준 구성과 동일')})()}
   ${row('상태 요약',`총 부하 ${fmt(sm.load)}MW · 최대 부하율 ${sm.max}% · 정격 초과 ${sm.viol<0?'계통 분리':sm.viol+'건'} · 탈락 설비 ${sm.out}개`)}
   ${row('라벨',s.labels.length?s.labels.map(x=>esc(x)).join('<br>'):'없음')}${row('데이터셋',s.ds?`<code>${s.ds}</code>`:'다음 학습 파이프라인 실행 때 포함 대상')}
   ${row('저장 경로',`<code class="path">${path}/state.parquet</code><br><code class="path">${path}/topology.cim.xml</code><br><code class="path">${path}/meta.json</code>`)}
   <div class="kdbtn"><button class="btn pri" type="button" id="snReplay">계통도에서 재현</button><button class="btn" type="button" id="snCopy">메타데이터 복사</button></div>
   <details class="snd"><summary>meta.json 미리보기</summary><pre class="snjson" id="snJson">${esc(prev.length>2400?prev.slice(0,2400)+'\n …':prev)}</pre></details>`;
  $('snReplay').onclick=()=>{if(MODE==='live')GWY.audit.unshift({t:now(),trace:'-',who:ROLE,appr:'열람 권한 확인',act:`데이터 레이크 재현 ${s.id} (대외비)`,ok:true});document.querySelector('nav button[data-v="ops"]').click();lastScen=null;setView({state:cloneS(s.state),ref:null,label:`데이터 레이크 재현 ${s.id} (현 계통 아님)`,hl:{lines:new Set(Object.keys(s.state.out)),marks:[],focus:[]}},true);logCall('get_measurements',`replay ${s.id}`,'restored from data lake','0.01s')};
  $('snCopy').onclick=async()=>{try{await navigator.clipboard.writeText(prev);$('snCopy').textContent='복사됨'}catch(e){$('snCopy').textContent='복사 불가 (미리보기에서 선택)'}setTimeout(()=>{if($('snCopy'))$('snCopy').textContent='메타데이터 복사'},1800)};
  lkLogRender()}
function lkLogRender(){const el=$('lkLog');if(el)el.innerHTML=LK.log.length?LK.log.slice(0,8).map(l=>`<div class="${l.includes('격리')?'b':''}">${esc(l)}</div>`).join(''):'<div class="d">수집 대기 중</div>'}

/* ===== 성능 평가: 문제지 · 재발 방지 문항 · 게이트 연동 ===== */
const BM={ver:3,last:{},regSeeded:{}};
const REG_SEED=[{q:'신보령화력 2호기 트립되면?',reason:'site',plant:'보령화력',why:'보령화력 #5 계획 정비 중인데 증발 대상으로 추천',days:4},
 {q:'서천–군산 선로가 고장 나면?',reason:'site',plant:'신서천화력',why:'신서천화력 출력 제약 기간인데 재급전 대상으로 추천',days:6},
 {q:'아산–청양 선로가 고장 나면?',reason:'exp',why:'조치 근거 규정이 제시되지 않음',days:5}];
function regSeed(){if(MODE==='live'||M.id!=='kpg193'||BM.regSeeded.kpg193)return;BM.regSeeded.kpg193=true;lakeSeed();
  REG_SEED.forEach((r,k)=>{const d=new Date();d.setDate(d.getDate()-r.days);d.setHours(10+k,20,0,0);const base=SNAP.find(s=>s.model==='kpg193'&&s.kind==='정기'&&s.dkey===dkey(d)&&s.date.getHours()===10+k)||SNAP.find(s=>s.model==='kpg193');
    const rs=REASONS.find(x=>x.id===r.reason);const sn=snapPush('판단',r.q,r.plant==='신서천화력'?BASE:base.state,`T-R${k+1}`,{date:d,sim:`${dlab(d)} ${pad(10+k)}:20`,silent:true,keep:true,ex:true,labels:[`부적절 · ${rs.t}`]});sn.reg={reason:r.reason,plant:r.plant,why:r.why}});SNAP.sort((a,b)=>b.date-a.date)}
function regItems(){regSeed();const fbR={};feedback.forEach(f=>{if(f.d==='부적절')fbR[f.q]=f});
  return SNAP.filter(s=>s.model===M.id&&s.kind==='판단'&&s.labels.some(l=>l.startsWith('부적절'))&&/[?]|면$|면\?|하면|되면/.test(s.trigger)).map(s=>{const f=fbR[s.trigger];return {s,q:s.trigger,reason:(s.reg&&s.reg.reason)||(f&&f.r)||'',plant:s.reg&&s.reg.plant,why:(s.reg&&s.reg.why)||s.labels.find(l=>l.startsWith('부적절')).replace('부적절 · ',''),alt:f?f.a:'-',ex:s.ex}})}
function regRunOne(it){const base=it.s.state,p=parseEvents(it.q,base);const ok=p.evs.length&&!p.evs.some(e=>e.k==='amb')&&!p.issues.length;
  if(!it.ex&&!(it.reason==='site'&&it.plant)&&it.reason!=='exp'){const pk=parse(it.q).k;if(pk==='unk')return {pass:false,now:'여전히 질문을 해석하지 못함',exp:'질문 해석'};return {pass:null,now:'정답 라벨 대기',exp:'피드백 정제 단계에서 검토자가 기대 행동을 지정하면 채점'}}
  if(['und','idn'].includes(it.reason))return {pass:!!ok,now:ok?'이벤트를 정상 해석':'여전히 해석 실패',exp:'질문을 바르게 해석'};
  if(!ok)return {pass:false,now:'해석 실패',exp:'해석 후 조치 제시'};
  const s=cloneS(base);p.evs.forEach(e=>applyEv(s,{...e}));balance(s);const r=dcpf(s);if(!r)return {pass:true,now:'계통 분리 판정',exp:'-'};
  const v=viol(r);
  if(it.reason==='exp')return {pass:kbRefs(['c-ol','c-rd']).length>0&&noteRefs([]).length>=0,now:'근거 규정·조항 링크 제시',exp:'조치 근거 규정 제시'};
  if(it.reason==='site'&&it.plant){const pb=new Set(M.gens.filter(g=>g.plant===it.plant).map(g=>g.b));if(!v.length)return {pass:true,now:'위반 없음, 조치 불필요',exp:`${it.plant} 제외`};
    const rd=redispatch(s);if(!rd){const sh=shed(s);return {pass:true,now:sh?'부하 차단 제시':'관제사 에스컬레이션',exp:`${it.plant} 제외`}}
    const used=[...pb].some(b=>{let d=0;gensOf(rd.state).forEach((g,k)=>{if(g.b===b&&k<M.gens.length)d+=g.p-s.gen[k].p});return Math.abs(d)>5});
    return {pass:!used,now:used?`여전히 ${it.plant} 출력 조정을 추천`:`${it.plant} 없이 재급전 제시`,exp:`${it.plant}(정비·제약 중) 제외`}}
  const alt=v.length?(redispatch(s)?'발전 재급전':shed(s)?'부하 차단':'에스컬레이션'):'조치 불필요';return {pass:alt!==it.alt,now:alt,exp:`부적절된 답(${it.alt})과 다른 판단`}}
function bmQBScore(){const base=BASE,Q=buildQB();let P=0;Q.forEach(t=>{const p=parseEvents(t.q,base);const ok=p.evs.length&&!p.evs.some(e=>e.k==='amb')&&!p.issues.length;const act=ok?'answer':'clarify';if(act!==t.a)return;
  if(t.a==='clarify'){P++;return}const ex=normEvs(t.e.map(e=>toInternalSafe(e,base)),base),pr=normEvs(p.evs,base);if(JSON.stringify(ex.keys)===JSON.stringify(pr.keys))P++});return {p:P,n:Q.length,pct:Math.round(P/Q.length*1000)/10}}
const toInternalSafe=(e)=>e;
function bmRegScore(){const it=regItems();const r=it.map(regRunOne).filter(x=>x.pass!==null);if(!r.length)return {p:0,n:0,pct:100};const p=r.filter(x=>x.pass).length;return {p,n:r.length,pct:Math.round(p/r.length*1000)/10}}
function bmRender(){const it=regItems(),fbRej=feedback.filter(f=>f.d==='부적절').length;const L=BM.last;
  const card=(k,t,src,n,desc,score,btn)=>`<div class="bmc"><div class="bmh"><b>${t}</b><span class="kty">${src}</span></div><div class="bmn">${n}</div><p>${desc}</p><div class="bmf"><span>${score||'아직 채점 전'}</span><button class="btn" type="button" data-bm="${k}">${btn}</button></div></div>`;
  $('bmSuites').innerHTML=card('qb','질의 이해','자동 생성',`${buildQB().length}문항`,'계통 모델의 실제 설비 이름으로 만든 관제사 질의. 답할지 되물을지, 이벤트 추출, 위반 탐지, 조치 효과를 채점',L.qb&&`최근 ${L.qb}`,'채점')
   +card('n1','계산 정확도','자동 생성',`${allLines(BASE).length}개 사고`,'모든 단일 회선 고장을 빠른 계산과 정밀 재계산으로 풀어 일치 여부와 조치 효과를 채점',L.n1&&`최근 ${L.n1}`,'채점')
   +card('reg','재발 방지 문항','부적절 사례',`${it.length}문항`,`관제사가 부적절한 판단을 그때 계통 상태로 다시 풀게 해, 같은 실수를 반복하는지 채점 · 이번 세션 부적절 ${fbRej}건 편입`,L.reg&&`최근 ${L.reg}`,'채점')
   +card('tr','관제사 훈련','무작위 출제','5문항','같은 유형의 문제를 사람이 풀어 시스템과 비교 (관제 화면 훈련 모드)',TRN.hist[0]&&`최근 ${TRN.hist[0].score}점`,'훈련 시작');
  $('bmVer').textContent=`문제지 v${BM.ver} · ${M.name}`;
  $('bmSuites').querySelectorAll('[data-bm]').forEach(b=>b.onclick=()=>{const k=b.dataset.bm;if(k==='qb'){runQB();$('qbPanel').scrollIntoView({behavior:'smooth'})}else if(k==='n1'){runN1();$('n1Panel').scrollIntoView({behavior:'smooth'})}else if(k==='reg'){runReg();$('regPanel').scrollIntoView({behavior:'smooth'})}else{document.querySelector('nav button[data-v="ops"]').click();trainStart()}})}
async function runReg(){const b=$('runReg');b.disabled=true;b.textContent='채점 중';const it=regItems();await sleep(200);
  const all=it.map(x=>({x,r:regRunOne(x)})),rows=all;const dec=all.filter(o=>o.r.pass!==null),p=dec.filter(o=>o.r.pass).length,pend=all.length-dec.length;
  $('regT').innerHTML=rows.length?'<tr><th>부적절 사례</th><th>부적절 사유</th><th>기대 행동</th><th>현재 엔진 결과</th><th>판정</th></tr>'+rows.map(({x,r})=>`<tr><td><b>${esc(x.q)}</b><div class="d">${x.s.ts} · ${x.s.sim}${x.ex?' · 예시':''}</div></td><td>${esc(x.why)}</td><td>${esc(r.exp)}</td><td>${esc(r.now)}</td><td><span class="res ${r.pass===null?'e':r.pass?'p':'f'}">${r.pass===null?'라벨 대기':r.pass?'통과':'실패'}</span></td></tr>`).join(''):'<tr><td class="d">부적절 사례 없음 · 관제 화면 부적절 시 편입</td></tr>';
  $('rgP').textContent=dec.length?`${p}/${dec.length}`:'-';$('rgPct').textContent=dec.length?Math.round(p/dec.length*100)+'%':'-';$('rgN').textContent=`${dec.length-p}건${pend?` · 라벨 대기 ${pend}`:''}`;
  BM.last.reg=`${p}/${dec.length} 통과${pend?` · 대기 ${pend}`:''}`;qbRuns.unshift({t:now(),m:M.name,k:'재발 방지',p:`${p}/${dec.length}`,x:`실패 ${dec.length-p}건 → 학습 대상${pend?`, 라벨 대기 ${pend}건`:''}`,sec:'0.2'});drawRuns();logCall('run_contingency',`regression ${rows.length} cases`,`${p} pass`,'0.20s');bmRender();b.disabled=false;b.textContent='다시 채점'}
function bmBaseline(){const q=bmQBScore(),r=bmRegScore();return {q,r}}

/* ===== 게이트웨이 v2: 어댑터 상태 · 작업 대기열 · 추적 · 권한 감사 ===== */
const GWQ={},GWY={jobs:[],audit:[],jseq:0,sel:null,raf:0};
const ADP=[{id:'rtdb',n:'실시간 DB 어댑터',d:'타사 계층형 DB (모의)',tools:['get_measurements','create_snapshot','get_model_catalog']},
 {id:'pf',n:'정태해석 엔진',d:'DC 조류 · 상정사고 · N-1',tools:['build_scenario','run_power_flow','run_contingency','run_n1_screening']},
 {id:'opt',n:'최적화 엔진',d:'재급전 · 부하 차단',tools:['run_redispatch_opf','eval_load_shedding'],mk:'rd'},
 {id:'llm',n:'LLM 오케스트레이터',d:'질의 분류 · 설비 식별',tools:['identify_equipment'],mk:'llm'},
 {id:'kb',n:'지식 베이스 검색',d:'규정 · 절차 · 사례',tools:['search_procedure']},
 {id:'se',n:'AI 상태추정',d:'외부 과제 제공',tools:[],mk:'se',ext:true},
 {id:'ctl',n:'작업 공간',d:'가정 고정 · 공유',tools:['save_workspace']}];
const PROLE={r:'관제사 · 책임자 · 연구자',c:'관제사 · 책임자 · 연구자',x:'관제사 요청 → 당직 책임자 승인'};
const ASYNC=new Set(['run_contingency','run_n1_screening','run_redispatch_opf']);
const _lc=logCall;
logCall=function(tool,inp,res,ms){_lc(tool,inp,res,ms);const tr=curTrace;
  if(ASYNC.has(tool)){const j={id:'JOB-'+String(++GWY.jseq).padStart(4,'0'),tool,trace:tr,inp,st:'대기',t:now(),dur:parseFloat(ms)||0,res,retry:0};GWY.jobs.unshift(j);if(GWY.jobs.length>60)GWY.jobs.pop();
    setTimeout(()=>{j.st='실행 중';gwLive()},150);setTimeout(()=>{j.st=/infeasible|error/.test(res)?'완료 · 해 없음':'완료';gwLive()},700+Math.random()*900)}
  if(tool==='save_workspace')GWY.audit.unshift({t:now(),trace:tr,who:tr==='SIM'?'연구자 (테스트베드)':'관제사',appr:tr==='SIM'?'시나리오 주입 권한':'작업 공간 고정 (실계통 영향 없음)',act:inp,ok:true});
  gwLive()};
let gwT=0;function gwLive(){if(!$('v-gw').classList.contains('on'))return;clearTimeout(gwT);gwT=setTimeout(gwRender,120)}
function gwSeed(){if(GWY.seeded)return;GWY.seeded=true;
  GWY.audit.push({t:'09:41:12',trace:'T-0R2',who:'LLM 오케스트레이터',appr:'승인 토큰 없음',act:'save_workspace 직접 호출 시도 (재급전 반영)',ok:false,ex:true},{t:'08:15:40',trace:'T-0R1',who:'연구자 계정',appr:'운영망 제어 권한 없음',act:'save_workspace (운영 트윈) 호출 시도',ok:false,ex:true});
  GWY.jobs.push({id:'JOB-0000',tool:'run_n1_screening',trace:'T-0R3',inp:'전체 사고 365건',st:'실패 → 재시도 성공',t:'07:02:10',dur:9.4,res:'1차 시간 초과, 2차 완료',retry:1,ex:true})}
function gwRender(){gwSeed();
  // 어댑터 상태판
  const stat=t=>{const L=logs.filter(l=>l.tool===t);return {n:L.length,err:L.filter(l=>/error|infeasible/.test(l.res)).length,ms:L.length?L.reduce((a,l)=>a+(parseFloat(l.ms)||0),0)/L.length:0}};
  $('gwAdp').innerHTML=ADP.map(a=>{const s=a.tools.map(stat).reduce((x,y)=>({n:x.n+y.n,err:x.err+y.err,ms:Math.max(x.ms,y.ms)}),{n:0,err:0,ms:0});const mv=a.mk?MLM[a.mk].vers.find(v=>v.stage==='Production'):null;
    return `<div class="adp"><div class="adh"><span class="adot${a.ext?' wait':''}" title="${a.ext?'연결 대기':'정상'}"></span><b>${a.n}</b>${a.ext?'<span class="kty">외부</span>':''}</div><p>${a.d}</p><div class="adm"><span>호출<b>${s.n}</b></span><span>평균<b>${s.n?(s.ms*1000<1?'<1':Math.round(s.ms*1000))+'ms':'-'}</b></span><span>해 없음·오류<b>${s.err}</b></span></div><div class="adv">${mv?`모델 <code>${MLM[a.mk].short} v${mv.v}</code>`:`툴 ${a.tools.length}개`}${a.ext?' · 게이트웨이 연결 대기':''}</div></div>`}).join('');
  // 레지스트리
  $('reg').innerHTML='<tr><th>툴</th><th>입력 → 출력</th><th>버전</th><th>권한</th><th>사용 가능 역할</th><th>실행</th><th>호출</th></tr>'+TOOLS.map(([n,v,d,p,e])=>{const [i,o]=(SCH[n]||'- → -').split(' → ');return `<tr><td><code>${n}</code><div class="d">${d}</div></td><td class="schema"><b>${esc(i)}</b><br>→ ${esc(o)}</td><td>${v}</td><td><span class="perm ${PERM[p][1]}">${PERM[p][0].split(' ·')[0]}</span></td><td class="d">${PROLE[p]}</td><td>${e}</td><td>${calls[n]||0}</td></tr>`}).join('');
  // 작업 대기열
  const J=GWY.jobs.slice(0,12);$('gwJobs').innerHTML=J.length?`<table class="reg"><tr><th>작업</th><th>툴</th><th>추적</th><th>상태</th><th>소요</th></tr>${J.map(j=>`<tr><td><code>${j.id}</code><div class="d">${j.t}</div></td><td><code>${j.tool}</code></td><td>${j.trace}</td><td><span class="jst ${j.st.startsWith('완료')?'ok':j.st==='실행 중'?'run':j.st.includes('재시도')?'re':'q'}">${j.st}</span>${j.retry?`<div class="d">재시도 ${j.retry}회${j.ex?' · 예시':''}</div>`:''}</td><td>${j.dur?j.dur.toFixed(2)+'s':'-'}</td></tr>`).join('')}</table>`:'<p class="empty" style="display:block">비동기 작업 없음</p>';
  $('gwJobK').textContent=`실행 중 ${GWY.jobs.filter(j=>j.st==='실행 중'||j.st==='대기').length} · 완료 ${GWY.jobs.filter(j=>j.st.startsWith('완료')).length} · 재시도 ${GWY.jobs.filter(j=>j.retry).length}`;
  // 권한 감사
  $('gwAudit').innerHTML=GWY.audit.length?GWY.audit.slice(0,10).map(a=>`<div class="aud ${a.ok?'':'blk'}"><span class="aut">${a.t}<br>${a.trace}</span><div><b>${a.ok?'허용':'차단'}</b> · ${esc(a.who)}<p>${esc(a.act)}</p><span class="d">${esc(a.appr)}${a.ex?' · 예시':''}</span></div></div>`).join(''):'<p class="d">기록 없음</p>';
  // 추적
  const tr={};logs.forEach(l=>{if(!tr[l.tr])tr[l.tr]=[];tr[l.tr].unshift(l)});const ids=Object.keys(tr).filter(t=>t!=='-').sort().reverse();if(!GWY.sel||!tr[GWY.sel])GWY.sel=ids.find(t=>t.startsWith('T-'))||ids[0];
  $('gwTr').innerHTML=ids.length?ids.slice(0,20).map(t=>{const L=tr[t],tot=L.reduce((a,l)=>a+(parseFloat(l.ms)||0),0);return `<button type="button" class="trb${t===GWY.sel?' on':''}" data-t="${t}"><b>${t}</b><span>${esc(GWQ[t]||(t==='SIM'?'테스트베드 시나리오 주입':'시스템 호출'))}</span><em>호출 ${L.length} · ${tot.toFixed(2)}s</em></button>`}).join(''):'<p class="d" style="padding:12px">추적 없음</p>';
  $('gwTr').querySelectorAll('.trb').forEach(b=>b.onclick=()=>{GWY.sel=b.dataset.t;gwRender()});
  const L=tr[GWY.sel]||[];let acc=0;const tot=Math.max(.05,L.reduce((a,l)=>a+Math.max(parseFloat(l.ms)||0,.01),0));
  $('gwWf').innerHTML=L.length?`<div class="wfh"><b>${GWY.sel}</b> ${esc(GWQ[GWY.sel]||'')}</div>`+L.map((l,i)=>{const d=Math.max(parseFloat(l.ms)||0,.01),x=acc/tot*100,w=Math.max(1.5,d/tot*100);acc+=d;const t=TOOLS.find(z=>z[0]===l.tool);const p=t?t[3]:'r';
    return `<details class="wf"><summary><span class="wfn"><code>${l.tool}</code></span><span class="wfbar"><i class="p-${p}" style="left:${x}%;width:${w}%"></i></span><span class="wfms">${l.ms}</span></summary><div class="wfd"><div><span>요청</span><code>${esc(l.inp)}</code></div><div><span>응답</span><code>${esc(l.res)}</code></div><div><span>시각</span>${l.t}</div></div></details>`}).join(''):'<p class="d">추적 선택</p>'}

/* ===== 대시보드 v2 ===== */
const RGN=['수도권','강원','충청','호남','영남'],RPOS=[[0,0],[1,0],[0.5,1],[0,2],[1,2]];
function stMetrics(s){const r=dcpf(s),gs=gensOf(s).filter(g=>g.on),ren=(s.ren||[]).reduce((a,b)=>a+b,0),dem=s.load.reduce((a,b)=>a+b,0),gen=gs.reduce((a,g)=>a+g.p,0)+ren;
  const res=gs.filter(g=>g.type!=='원자력').reduce((a,g)=>a+Math.max(0,g.max-g.p),0);return {r,dem,gen,ren,res,rr:res/dem*100,rs:ren/gen*100,max:r?Math.max(...live(r).map(l=>ld(r,l))):0,v:r?viol(r).length:0}}
function dashHist(){const S=SNAP.filter(s=>s.model===M.id&&s.kind==='정기'&&s.status!=='격리').slice(0,24).reverse();return S.map(s=>{if(!s._m){const m=stMetrics(s.state);s._m={dem:m.dem,gen:m.gen,rr:m.rr,rs:m.rs,max:m.max,v:m.v}}return s._m})}
function spark(vals,cls){if(vals.length<2)return '';const W=96,H=26,mn=Math.min(...vals),mx=Math.max(...vals),sp=mx-mn||1;return `<svg class="spk ${cls||''}" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path d="${vals.map((v,i)=>`${i?'L':'M'}${(i/(vals.length-1)*W).toFixed(1)} ${(H-3-(v-mn)/sp*(H-6)).toFixed(1)}`).join(' ')}"/></svg>`}
function regionFlows(s,r){const n=RGN.length,dem=new Array(n).fill(0),gen=new Array(n).fill(0),fl={},cap={};
  M.buses.forEach((b,i)=>{dem[b.rg]+=s.load[i];gen[b.rg]+=(s.ren?s.ren[i]:0)});gensOf(s).forEach(g=>{if(g.on)gen[M.buses[g.b].rg]+=g.p});
  if(r)live(r).forEach(l=>{const a=M.buses[l.f].rg,b=M.buses[l.t].rg;if(a===b)return;const f=r.fl[l.id];const [x,y,v]=f>=0?[a,b,f]:[b,a,-f];const k=x+'>'+y;fl[k]=(fl[k]||0)+v;const kk=Math.min(a,b)+'-'+Math.max(a,b);cap[kk]=(cap[kk]||0)+r.R[l.id]});
  return {dem,gen,fl,cap}}
function dashRender(){const s=twin,m=stMetrics(s),r=m.r,nv=r?n1Now(s).length:0,H=dashHist(),al=$('alarms').querySelectorAll('li:not(.empty)').length;
  const k=(lab,val,sub,key,cl,inv)=>{const vs=H.map(x=>x[key]).filter(x=>x!=null);let d='';if(vs.length>1){const a=vs[vs.length-1],b=vs[vs.length-2],up=a>b+1e-6,dn=a<b-1e-6;const neu=['dem','gen','rs'].includes(key);d=up||dn?`<span class="dl ${neu?'nu':(up!==!!inv)?'up':'dn'}">${up?'▲':'▼'}</span>`:''}
    return `<div class="dk${cl?' '+cl:''}"><div class="dkh"><span>${lab}</span>${d}</div><b>${val}</b><div class="dkf"><em>${sub||''}</em>${key?spark(vs,cl):''}</div></div>`};
  $('dKpi').innerHTML=k('현재 수요',fmt(m.dem)+' MW',TL.on?`${M.profile?M.profile.label:'표준일'} ${pad(TL.h)}:00`:'실시간 운전점','dem')+k('총 발전',fmt(m.gen)+' MW',`재생 ${fmt(m.ren)}MW`,'gen')+k('운전 예비율',m.rr.toFixed(1)+'%',`${fmt(m.res)}MW`,'rr',m.rr<5?'bad':'',true)+k('재생에너지 비중',m.rs.toFixed(1)+'%','','rs')
   +k('최대 부하율',Math.round(m.max)+'%','','max',m.max>100?'bad':m.max>=90?'warn':'')+k('정격 초과',m.v+'건','','v',m.v?'bad':'')+k('N-1 위반 사고',nv+'건','',null,nv?'warn':'')+k('활성 경보',al+'건','',null);
  $('dTrendNote').innerHTML=`<span class="livedot"></span>디지털 트윈 기준 · 갱신 ${now()} · 추이: 정기 수집 최근 ${H.length}개 · ▲▼: 직전 수집 대비`;
  // 권역 (지리 배치)
  if(M.buses[0].rg==null){$('dReg').innerHTML='<p class="d" style="padding:16px 20px">권역 구분은 KPG 193 계통에서만 표시</p>';$('dRegK').innerHTML='';$('dFlow').innerHTML=''}else{const F=regionFlows(s,r),C=RGN.map((_,i)=>{const bs=M.buses.filter(b=>b.rg===i);return {x:bs.reduce((a,b)=>a+b.x,0)/bs.length,y:bs.reduce((a,b)=>a+b.y,0)/bs.length}});
    [[.27,.17],[.74,.19],[.36,.43],[.25,.73],[.73,.63]].forEach(([fx,fy],i)=>{C[i]={x:fx*M.w,y:fy*M.h}});const TW=214,TH=100;let g=M.buses.map(b=>`<circle class="rgd rg${b.rg}" cx="${b.x}" cy="${b.y}" r="3.2"/>`).join('');const rows=[];
    [[0,1],[0,2],[1,2],[1,4],[2,3],[2,4],[3,4]].forEach(([a,b])=>{const ab=F.fl[a+'>'+b]||0,ba=F.fl[b+'>'+a]||0,net=ab-ba,cp=F.cap[Math.min(a,b)+'-'+Math.max(a,b)]||0;if(!cp)return;const [f,t]=net>=0?[a,b]:[b,a],A=C[f],B=C[t];
      const dx=B.x-A.x,dy=B.y-A.y,L=Math.hypot(dx,dy),ux=dx/L,uy=dy/L,cut=v=>Math.min(Math.abs(TW/2/(v.x||1e-9)),Math.abs(TH/2/(v.y||1e-9)))+6,k1=cut({x:ux,y:uy}),x1=A.x+ux*k1,y1=A.y+uy*k1,x2=B.x-ux*k1,y2=B.y-uy*k1,u=Math.abs(net)/cp*100;
      g+=`<line class="rgl ${u>60?'hi':''}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" style="stroke-width:${Math.min(16,4+Math.abs(net)/900)}px" marker-end="url(#rgarr)"/>`;rows.push({f,t,v:Math.abs(net),u,cp})});
    RGN.forEach((n,i)=>{const p=C[i],net=F.gen[i]-F.dem[i];g+=`<g class="rgb"><rect x="${p.x-TW/2}" y="${p.y-TH/2}" width="${TW}" height="${TH}" rx="16"/><text class="rn" x="${p.x-TW/2+14}" y="${p.y-TH/2+28}">${n}</text><text class="rv ${net>=0?'sp':'df'}" x="${p.x+TW/2-14}" y="${p.y-TH/2+28}" text-anchor="end">${net>=0?'+':'−'}${fmt(Math.abs(net))}</text><text class="rs" x="${p.x-TW/2+14}" y="${p.y-TH/2+54}">수요 ${fmt(F.dem[i])}</text><text class="rs" x="${p.x-TW/2+14}" y="${p.y-TH/2+76}">발전 ${fmt(F.gen[i])}</text></g>`});
    $('dReg').innerHTML=`<svg class="rgsvg" viewBox="-20 40 ${M.w+40} ${M.h-60}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="권역별 수급과 권역 간 조류"><defs><marker id="rgarr" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3.2" markerHeight="3.2" orient="auto"><path d="M0 0L10 5L0 10Z" fill="context-stroke"/></marker></defs>${g}</svg>`;
    $('dFlow').innerHTML='<div class="fth"><span>권역 간 조류</span><span>MW</span><span>연계선 이용률</span></div>'+rows.sort((a,b)=>b.v-a.v).map(o=>`<div class="ftr"><span>${RGN[o.f]} <i>→</i> ${RGN[o.t]}</span><b>${fmt(o.v)}</b><span class="fu"><i style="width:${Math.min(100,o.u)}%" class="${o.u>60?'hi':''}"></i><em>${o.u.toFixed(0)}%</em></span></div>`).join('');
    const imp=Object.entries(F.fl).filter(([k2])=>k2.endsWith('>0')).reduce((a,[,v])=>a+v,0)-Object.entries(F.fl).filter(([k2])=>k2.startsWith('0>')).reduce((a,[,v])=>a+v,0);const cap0=Object.entries(F.cap).filter(([k2])=>k2.startsWith('0-')).reduce((a,[,v])=>a+v,0);
    $('dRegK').innerHTML=`<div class="kpi"><span>수도권 유입</span><b>${fmt(Math.max(0,imp))}MW</b></div><div class="kpi"><span>수도권 연계선 이용률</span><b class="${imp/cap0>.6?'w':''}">${cap0?(Math.max(0,imp)/cap0*100).toFixed(1):'-'}%</b></div><div class="kpi"><span>수도권 자급률</span><b>${(F.gen[0]/F.dem[0]*100).toFixed(0)}%</b></div>`}
  // 다가오는 위험
  const risks=[];if(TL.on){forecasts().forEach(x=>risks.push({h:x.h,t:`${lname(x.l)} ${Math.round(x.p)}% 예상`,k:'f'}));Object.keys(TL.plans).forEach(h=>{if(+h>=TL.h)risks.push({h:+h,t:'예방 재급전 예약 실행',k:'p'})});
    TL.sum.forEach((x,h)=>{if(x&&h>TL.h+3&&x.viol.length&&!risks.some(r2=>r2.h===h))risks.push({h,t:`${x.viol.slice(0,2).map(v=>`${lname(v.l)} ${Math.round(v.p)}%`).join(', ')} 예상`,k:'l'})})}
  const top=r?n1Now(s).filter(c=>!c.island).sort((a,b)=>Math.max(...b.v.map(l=>ld(b.r,l)))-Math.max(...a.v.map(l=>ld(a.r,l)))).slice(0,3):[];
  $('dRisk').innerHTML=(risks.length?`<ol class="rtl">${risks.sort((a,b)=>a.h-b.h).slice(0,7).map(x=>`<li class="rk-${x.k}"><span>${pad(x.h)}:00</span><b>${esc(x.t)}</b></li>`).join('')}</ol>`:`<p class="d">${TL.on?'남은 시간대 예상 과부하 없음':'시간대별 예보: 하루 흐름 재생 시 표시'}</p>`)
    +(top.length?`<div class="lab">상시 위험 (N-1 상위)</div>${top.map(c=>`<div class="rsk"><span>${lname(c.l)} 1회선 고장 시</span><b>${lname(c.v[0])} ${Math.round(Math.max(...c.v.map(l=>ld(c.r,l))))}%</b></div>`).join('')}<button class="btn" type="button" id="dPrev">예방 조치 계산</button>`:'');
  const dp=$('dPrev');if(dp)dp.onclick=()=>{document.querySelector('nav button[data-v="ops"]').click();ask('예방 조치 추천해줘')};
  // 설비 현황
  const eq=[];Object.entries(s.out).forEach(([id,n])=>{const l=allLines(s).find(x=>x.id===id);eq.push(['탈락·휴전',`${lname(l)}${KV(l.kv)} ${n}/${l.cx.length}회선`,'a'])});
  gensOf(s).forEach((g,i)=>{const sch=TL.on&&M.profile?M.profile.hours[TL.h].gen[i]>0.5:i<M.gens.length&&M.gens[i].on;if(i<M.gens.length&&sch&&!g.on)eq.push(['정지',g.name,'a'])});
  s.add.forEach(l=>eq.push(['신설 (가정 반영)',`${lname(l)}${KV(l.kv)}`,'n']));s.newGen.forEach(g=>eq.push(['신규 발전',`${nameOf(g.b)} ${fmt(g.p)}MW`,'n']));
  if(M.id==='kpg193')eq.push(['계획 작업 (예시)','양평–태백 765kV 1회선 휴전 · 익일 02~05시','p'],['계획 정비 (예시)','보령화력 #5 · 10월 30일까지','p']);
  $('dEq').innerHTML=eq.length?eq.map(([t,d,c])=>`<div class="eqr eq-${c}"><span>${t}</span><b>${esc(d)}</b></div>`).join(''):'<p class="d">탈락·정지 설비 없음</p>';
  // 발전 믹스·상위 선로·분포·곡선 (기존)
  const types={};gensOf(s).filter(g=>g.on).forEach(g=>{types[g.type]=(types[g.type]||0)+g.p});if(m.ren>0)types['재생']=m.ren;const tot=Object.values(types).reduce((a,b)=>a+b,0)||1;const tc={'원자력':'#8a5cf6','석탄':'#5e7183','LNG':'#2457c5','재생':'#e0a000','thermal':'#2457c5','신규':'#23795f'};
  $('dMix').innerHTML=`<div class="mixbar">${Object.entries(types).sort((a,b)=>b[1]-a[1]).map(([t,p])=>`<i style="width:${p/tot*100}%;background:${tc[t]||'#7d8b98'}" title="${t} ${fmt(p)}MW"></i>`).join('')}</div><div class="mixleg">${Object.entries(types).sort((a,b)=>b[1]-a[1]).map(([t,p])=>`<span><i style="background:${tc[t]||'#7d8b98'}"></i>${t==='thermal'?'화력':t} ${fmt(p)}MW · ${(p/tot*100).toFixed(1)}%</span>`).join('')}</div>`;
  if(r){const tp=live(r).sort((a,b)=>ld(r,b)-ld(r,a)).slice(0,7);$('dTop').innerHTML=tp.map(l=>{const p=ld(r,l);return `<button type="button" class="dline" data-l="${l.id}"><span>${lname(l)}${KV(l.kv)}</span><i><u style="width:${Math.min(100,p/1.3)}%" class="${p>100?'a':p>=90?'w':''}"></u></i><b>${Math.round(p)}%</b></button>`}).join('');
    $('dTop').querySelectorAll('.dline').forEach(b=>b.onclick=()=>{const l=allLines(twin).find(x=>x.id===b.dataset.l);document.querySelector('nav button[data-v="ops"]').click();ask(`${M.buses[l.f].n}–${M.buses[l.t].n} 선로가 고장 나면?`)});
    const kvs=[...new Set(live(r).map(l=>l.kv))].sort((a,b)=>b-a),bins=[[0,50],[50,80],[80,90],[90,100],[100,999]];
    $('dHist').innerHTML=`<table class="dh"><tr><th></th>${bins.map(b=>`<th>${b[1]>200?'100%+':`${b[0]}–${b[1]}%`}</th>`).join('')}</tr>${kvs.map(kv=>`<tr><td>${kv?kv+'kV':'전체'}</td>${bins.map((b,i)=>{const n=live(r).filter(l=>l.kv===kv&&ld(r,l)>=b[0]&&ld(r,l)<b[1]).length;return `<td><span class="hc h${i}" style="opacity:${n?Math.min(1,.25+n/20):.08}">${n}</span></td>`}).join('')}</tr>`).join('')}</table>`}
  const W=1000,Hc=200;let g='';if(M.profile){const P=M.profile.hours,mxD=Math.max(...P.map(x=>x.dem)),X=h=>30+h*(W-40)/23,Y=v=>Hc-20-v/mxD*(Hc-30);
    g+=`<path class="dpv" d="M${X(0)} ${Y(0)} ${P.map((x,h)=>`L${X(h)} ${Y(x.pv)}`).join(' ')} L${X(23)} ${Y(0)}Z"/><path class="ddem" d="${P.map((x,h)=>`${h?'L':'M'}${X(h)} ${Y(x.dem)}`).join(' ')}"/><path class="dnet" d="${P.map((x,h)=>`${h?'L':'M'}${X(h)} ${Y(x.dem-x.pv-x.wind)}`).join(' ')}"/>`;for(let h=0;h<24;h+=3)g+=`<text class="dax" x="${X(h)}" y="${Hc-4}" text-anchor="middle">${pad(h)}</text>`;if(TL.on)g+=`<line class="dcur" x1="${X(TL.h)}" x2="${X(TL.h)}" y1="8" y2="${Hc-18}"/>`;$('dCurveT').textContent=`${M.profile.label} 수요 · 태양광 · 순부하`}
  else{$('dCurveT').textContent='표준 하루 부하 곡선';const X=h=>30+h*(W-40)/23,Y=v=>Hc-20-v*(Hc-30);g+=`<path class="ddem" d="${LF.map((v,h)=>`${h?'L':'M'}${X(h)} ${Y(v)}`).join(' ')}"/>`}$('dCurve').innerHTML=g;
  // 플랫폼 상태
  const errs=logs.filter(l=>/error|infeasible/.test(l.res)).length,lastSn=SNAP.find(x=>x.model===M.id),lastRun=ML.runs[ML.runs.length-1];
  const ps=(t,v,s2,c)=>`<div class="ps ${c||''}"><span>${t}</span><b>${v}</b><em>${s2}</em></div>`;
  $('dPlat').innerHTML=ps('수집 파이프라인',LK.on?'가동 중':'대기',lastSn?`마지막 적재 ${lastSn.ts}`:'-',LK.on?'ok':'')+ps('데이터 레이크',fmt(SNAP.filter(x=>x.model===M.id).length)+'개',`격리 ${SNAP.filter(x=>x.model===M.id&&x.status==='격리').length}개`)
   +ps('성능 평가',BM.last.reg?BM.last.reg.split(' ')[0]:'채점 전',BM.last.reg?'재발 방지 문항 최근 결과':'성능 평가 탭에서 채점')+ps('학습 파이프라인',lastRun?lastRun.id:'-',lastRun?esc(lastRun.note):'-')
   +ps('게이트웨이',logs.length+'건',`해 없음·오류 ${errs}건`)+MLK.map(k2=>{const mm=MLM[k2],p=mm.vers.find(x=>x.stage==='Production');return ps(mm.name,'v'+p.v,`${mm.metric} ${p.val}${mm.unit}`)}).join('')
   +ps('주파수 · 전압','연동 예정','AC 해석 · 상태추정 연결 시 표시','pend')}

let dbT=0;function dashLive(){if(!$('v-db').classList.contains('on'))return;clearTimeout(dbT);dbT=setTimeout(dashRender,250)}

/* ===== 공개 계통 / 실계통 연동 모드 ===== */
let MODE='pub';const STORE={},LV={timer:null,tick:0,lat:0,cov:0,bad:0,badTot:0,res:0,rx:0,cim:[],sig:'',pubModel:'kpg193'};
function storeSave(m){STORE[m]={snap:SNAP.slice(),jn:JN.slice(),fb:feedback,logs,calls,jobs:GWY.jobs,audit:GWY.audit,rp:{days:RP.days,sel:RP.sel,status:RP.status},qb:qbRuns,trh:TRN.hist,bm:BM.last,lk:{cnt:LK.cnt,log:LK.log,tp:LK.tp,part:LK.part},fbt:$('fbt').innerHTML,trt:$('trt').innerHTML,runs:$('qbRuns').innerHTML}}
function storeLoad(m){const s=STORE[m];SNAP.length=0;JN.length=0;
  if(!s){feedback=[];logs=[];calls={};GWY.jobs=[];GWY.audit=[];GWY.seeded=m==='live';RP.days=[];RP.sel=null;RP.status={};qbRuns=[];TRN.hist=[];BM.last={};LK.cnt={rx:0,ok:0,bad:0,tf:0,ld:0};LK.log=[];LK.tp=[];LK.part=null;
    $('fbt').innerHTML='<tr><td class="d">승인·부적절 기록 없음</td></tr>';$('trt').innerHTML='<tr><td class="d">훈련 기록 없음</td></tr>';$('qbRuns').innerHTML='<tr><td class="d">실행 이력 없음</td></tr>';$('logCnt').textContent='0건';return}
  SNAP.push(...s.snap);JN.push(...s.jn);feedback=s.fb;logs=s.logs;calls=s.calls;GWY.jobs=s.jobs;GWY.audit=s.audit;RP.days=s.rp.days;RP.sel=s.rp.sel;RP.status=s.rp.status;qbRuns=s.qb;TRN.hist=s.trh;BM.last=s.bm;Object.assign(LK,s.lk);$('fbt').innerHTML=s.fbt;$('trt').innerHTML=s.trt;$('qbRuns').innerHTML=s.runs;$('logCnt').textContent=logs.length+'건'}
function liveState(t){const P=MODELS.kpg193.profile.hours,hf=t.getHours()+t.getMinutes()/60+t.getSeconds()/3600,h=Math.floor(hf)%24,h2=(h+1)%24,f=hf-Math.floor(hf),L=(a,b)=>a+(b-a)*f;
  const s=freshState();s.load=P[h].load.map((v,i)=>L(v,P[h2].load[i])*(1+(Math.random()-.5)*.008));s.ren=P[h].ren.map((v,i)=>L(v,P[h2].ren[i]));s.gen=M.gens.map((g,i)=>{const p=L(P[h].gen[i],P[h2].gen[i]);return {p,on:p>0.5?1:0}});
  if(twin){s.out={...twin.out};s.add=twin.add.map(l=>({...l}));s.newGen=twin.newGen.map(g=>({...g}));s.seq=twin.seq}balance(s);return s}
function liveTick(){if(MODE!=='live')return;LV.tick++;const t=new Date();twin=liveState(t);n1Cache=null;
  LV.lat=1.9+Math.random()*1.6;LV.cov=97.6+Math.random()*1.9;LV.bad=Math.random()<.14?1:0;LV.badTot+=LV.bad;LV.res=+(0.5+Math.random()*.6).toFixed(2);LV.rx+=Math.round(M.lines.length*2*LV.cov/100);
  const sig=JSON.stringify(twin.out)+twin.add.map(l=>l.id).join()+twin.newGen.map(g=>g.id).join();if(sig!==LV.sig){if(LV.sig){cimBump(LV.nextWhy||'EMS: 설비 구성 변경');LV.nextWhy=null};LV.sig=sig}
  if(!view||view.live)drawSLD();else{drawAlarms(dcpf(twin));hdrUpdate()}
  if(LV.tick%5===0&&!LV.pause)snapPush('정기',`실시간 동기 ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`,twin,'-',{src:'EMS 스냅샷 (모의 피드)',bad:!!LV.bad,sim:`${pad(t.getHours())}:${pad(t.getMinutes())} 실시간`});
  if($('v-sn').classList.contains('on'))liveSrcRender();hdrUpdate()}
function cimBump(why){const last=LV.cim[LV.cim.length-1];const p=last?last.v.split('.').map(Number):[1,0,-1];p[2]++;LV.cim.push({v:p.join('.'),t:now(),why,topo:topoVer(twin)})}
function hdrUpdate(){const tw=$('twMode');if(!tw)return;let t,c;
  if(view&&!view.live){t=/재현/.test(view.label)?'재현':'가정';c=t==='재현'?'rp':'as'}else if(MODE==='live'){t='실시간 동기';c='lv'}else{t=TL.on?`하루 흐름 ${pad(TL.h)}:00`:'기준 운전점';c='pb'}
  tw.textContent=t;tw.className='twm tw-'+c;
  $('ctxInfo').innerHTML=MODE==='live'?`<span class="livedot"></span><b>실계통 연동</b><span class="ctxs" title="모의 실시간 피드 (KPG 기반 대역) · 실계통 인터페이스 연결 전">모의 피드</span><span class="ctxm">지연 <b>${LV.lat.toFixed(1)}s</b></span><span class="ctxm">측정 수신 <b>${LV.cov.toFixed(1)}%</b></span><span class="ctxm">EMS 상태추정 <b>${LV.seConv===false?'미수렴':'수렴'}</b> · 잔차 ${LV.res}</span>`
   :`<b>공개 계통</b><span class="ctxs">${esc(M.name.replace(/\s*\(.*\)/,''))}</span><span class="ctxm">오픈 데이터 기반 시연</span><span class="ctxm">예시 데이터 포함</span>`}
const _dbn=drawBanner;drawBanner=function(){_dbn();hdrUpdate()};
async function switchMode(m){if(m===MODE||busy)return;storeSave(MODE);if(MODE==='pub')LV.pubModel=M.id;
  stopPlay();tlClose();if(LK.on)lkToggle();clearInterval(LV.timer);MODE=m;storeLoad(m);document.body.dataset.mode=m;
  document.querySelectorAll('.mtog button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.m===m));
  views.length=0;lastScen=null;view=null;
  if(m==='live'){$('model').value='kpg193';loadModel('kpg193');$('modelDesc').textContent='모의 실시간 피드 · 현재 시각 기준 계통 상태 · KPG 193 기반 대역 (실계통 연결 시 교체)';LK.on=true;LV.pause=false;$('lkRun').textContent='수집 일시정지';$('lkRun').classList.remove('pri');twin=null;BASE=liveState(new Date());twin=cloneS(BASE);LV.tick=0;LV.badTot=0;LV.cim=[];LV.sig=JSON.stringify(twin.out);cimBump('초기 모델 로드 (KPG 기반 대역)');drawSLD(true);LV.timer=setInterval(liveTick,3000);liveTick()}
  else{$('model').value=LV.pubModel;loadModel(LV.pubModel);$('lkRun').textContent='수집 시작';$('lkRun').classList.add('pri')}
  $('msgs').innerHTML='';
  const d=document.createElement('div');d.className='sys';d.textContent=m==='live'?'실계통 연동 모드 · 모의 실시간 피드 3초 주기':'공개 계통 모드 · '+M.name;$('msgs').appendChild(d);$('msgs').appendChild(hello);chipsDefault();hdrUpdate();
  const on=document.querySelector('.view.on');if(on){const k=on.id.slice(2);if(k==='sn')lakeRender();if(k==='db')dashRender();if(k==='rp')renderReport();if(k==='ev')bmRender();if(k==='gw')gwRender();if(k==='kb')kbRender()}}
const _rpb=rpBuild;rpBuild=function(){_rpb()};
/* --- 데이터 소스 · 매핑 · 망 구성 · 설비 구성 버전 --- */
function liveSrcRender(){const box=$('liveSrc');if(!box)return;box.hidden=MODE!=='live';if(MODE!=='live')return;
  const t=new Date(),ts=`${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`,cim=(LV.cim[LV.cim.length-1]||{v:'1.0.0'}).v,cut=LV.fault&&LV.fault.type==='끊김';
  LV.hist=(LV.hist||[]).concat([cut?0:Math.round(M.lines.length*2*LV.cov/100)]).slice(-24);
  const sp=v=>{const mx=Math.max(1,...v);return `<svg class="srsp" viewBox="0 0 120 26" aria-hidden="true"><path d="${v.map((x,i)=>`${i?'L':'M'}${(i/Math.max(1,v.length-1)*120).toFixed(1)} ${(24-x/mx*20).toFixed(1)}`).join(' ')}"/></svg>`};
  const S=[['ems','EMS · SCADA','운영망 · 단방향 송신',cut?'끊김':'송신 중',[['주기','3초'],['측정점',fmt(M.lines.length*2+M.buses.length)],['상태추정',LV.seConv===false?'미수렴':'수렴'],['망연계','단방향']],false],['im','인메모리 DB','전용 · 테스트베드망 수신 영역',cut?'끊김':LV.seConv===false?'추정 미수렴':'수신 중',[['주기','3초'],['지연',LV.lat.toFixed(1)+'s'],['마지막 수신',cut?'-':ts],['누적 레코드',fmt(LV.rx)]],true],
    ['ld','적재 서버','가져오기 · 검증 · 변환',cut?'대기':'처리 중',[['처리량',(LK.tp.filter(x=>Date.now()-x<60000).length)+'건/분'],['격리',(LK.cnt.bad||0)+'건'],['대기열',cut?'0':'0~2'],['구역','4개']],false],
    ['cim','CIM 계통 모델','IEC 61970 CIM/XML','동기화됨',[['설비 구성','v'+cim],['모선',M.buses.length],['선로 경로',allLines(twin).length],['마지막 동기화',(LV.cim[LV.cim.length-1]||{}).t||'-']],false],
    ['plan','계획 시스템','정비 · 휴전 · 출력 제약',PLAN_ON?'동기화됨':'반영 꺼짐',[['등록 계획',MAINT.length+'건'],['계산 제외',maintCount()+'기'],['주기','10분'],['다음 갱신',`${pad((t.getMinutes()+10-t.getMinutes()%10)%60)}분`]],false],
    ['fc','기상 · 재생 예측','수요 · 태양광 · 풍력','동기화됨',[['예보 범위','24시간'],['주기','15분'],['태양광 현재',fmt((twin.ren||[]).reduce((a,b)=>a+b,0))+'MW'],['다음 갱신',`${pad((Math.floor(t.getMinutes()/15)+1)*15%60)}분`]],false]];
  $('srcCards').innerHTML=S.map(([id,n,d,st,kv,spk])=>`<div class="src2 ${/끊김|미수렴/.test(st)?'bad':''}"><div class="s2h"><span class="s2d"></span><b>${n}</b><span class="s2s">${st}</span></div><p>${d} · <em>모의 연결</em></p><div class="s2k">${kv.map(([k,v])=>`<div><span>${k}</span><b>${v}</b></div>`).join('')}</div>${spk?sp(LV.hist):''}<div class="s2b"><button type="button" class="btn" data-ct="${id}">연결 시험</button><button type="button" class="btn" data-sm="${id}">샘플 레코드</button></div></div>`).join('');
  $('srcCards').querySelectorAll('[data-ct]').forEach(b=>b.onclick=()=>{const ok=!(cut&&b.dataset.ct==='im');toast(ok?`${b.closest('.src2').querySelector('b').textContent} 응답 ${Math.round(20+Math.random()*60)}ms · 인증 확인 · 스키마 v1.2 일치`:'응답 없음 · 피드 끊김 상태')});
  $('srcCards').querySelectorAll('[data-sm]').forEach(b=>b.onclick=()=>{const id=b.dataset.sm,l=allLines(twin)[0],r=dcpf(twin);const smp={ems:{source:'EMS/SCADA',cycle:LV.tick,one_way:true,points:M.lines.length*2+M.buses.length},im:{snapshot_time:new Date().toISOString(),se_cycle:LV.tick,se_converged:LV.seConv!==false,branch:{ems_key:`/${M.buses[l.f].n}/${l.kv}kV/BAY-01`,p_from_mw:r?+r.fl[l.id].toFixed(1):null,status:'closed'},bus:{ems_bus_no:3,v_pu:1.012,angle_deg:-4.31}},ld:{object:SNAP[0]&&SNAP[0].id,zone:'standard',checks:{time_consistency:'pass',se_converged:'pass',range:'pass',completeness:'99.1%'}},cim:{model_version:cim,mrid:l.eq,from:M.buses[l.f].n,to:M.buses[l.t].n,circuits:l.cx.length},plan:MAINT[0],fc:{issued:new Date().toISOString(),horizon_h:24,pv_mw_next_h:Math.round((twin.ren||[]).reduce((a,b)=>a+b,0)*1.04)}}[id];
    drawer('샘플 레코드 · '+b.closest('.src2').querySelector('b').textContent,`<pre class="snjson">${esc(JSON.stringify(smp,null,1))}</pre><p class="d">모의 연결 레코드 · 실제 인터페이스 확정 시 같은 필드 구조로 교체</p>`)});
  const r=dcpf(twin),ln=allLines(twin).slice(0,4),gs=M.gens.filter((g,i)=>twin.gen[i].on).slice(0,2),bs=M.buses.map((b,i)=>({b,i})).filter(o=>twin.load[o.i]>100).slice(0,2);
  const rows=[...ln.map(l=>[`/${M.buses[l.f].n}/${l.kv}kV/BAY-${l.id.slice(1).padStart(2,'0')}/P`,`${l.eq} · P_from`,r?Math.round(r.fl[l.id])+' MW':'-']),...gs.map(g=>[`/${g.plant}/${g.name.split(' ').pop().replace('#','G')}/P`,`${g.id} · P_gen`,Math.round(twin.gen[M.gens.indexOf(g)].p)+' MW']),...bs.map(o=>[`/${o.b.n}/${o.b.kv}kV/LOAD/P`,`BUS-${o.b.id} · P_load`,Math.round(twin.load[o.i])+' MW'])];
  $('srcMap').innerHTML='<tr><th>계층형 DB 경로 (예시)</th><th>표준 필드 (CIM)</th><th>현재 값</th></tr>'+rows.map(x=>`<tr><td><code>${esc(x[0])}</code></td><td><code>${x[1]}</code></td><td>${x[2]}</td></tr>`).join('');
  $('cimList').innerHTML=LV.cim.slice().reverse().slice(0,8).map(c=>`<div class="cimr"><b>v${c.v}</b><span>${c.t}</span><em>${esc(c.why)}</em><code>${c.topo}</code></div>`).join('')}

const _lkt=lkToggle;lkToggle=function(){if(MODE==='live'){LV.pause=!LV.pause;LK.on=!LV.pause;$('lkRun').textContent=LV.pause?'수집 재개':'수집 일시정지';$('lkRun').classList.toggle('pri',LV.pause);lkDraw();lkStats();return}_lkt()};

/* ===== 내부 검토용 UX: 역할 · 승인 · 경보 처리 · 시나리오 · 엔진 슬롯 · 설계 메모 ===== */
let ROLE='관제사';const PEND=[],ALM={},SCN=[];let scnSeq=0,almShowDone=false;
function toast(t){document.querySelectorAll('.toast').forEach(x=>x.remove());const d=document.createElement('div');d.className='toast';d.textContent=t;document.body.appendChild(d);setTimeout(()=>d.classList.add('on'),10);setTimeout(()=>{d.classList.remove('on');setTimeout(()=>d.remove(),300)},2400)}
/* --- 서랍 --- */
function drawer(title,html,wide){$('drTitle').textContent=title;$('drBody').innerHTML=html;$('drawer').classList.toggle('wide',!!wide);$('drawer').hidden=false;$('drScrim').hidden=false;requestAnimationFrame(()=>$('drawer').classList.add('on'))}
function drawerClose(){$('drawer').classList.remove('on');$('drScrim').hidden=true;setTimeout(()=>$('drawer').hidden=true,200)}
/* --- 역할 · 승인 --- */
function setRole(r){ROLE=r;addJN('apply',`역할 전환: ${r}`);pendRender();toast(`역할: ${r}`);document.body.dataset.role=r}
function gateCommit(q,label,fn,st,btns){if(ROLE==='책임자'){fn();return}
  if(ROLE==='연구자'){st.textContent='연구자: 트윈 반영 권한 없음';toast('연구자 역할은 운영 트윈에 반영할 수 없습니다');GWY.audit.unshift({t:now(),trace:curTrace,who:'연구자',appr:'운영 트윈 제어 권한 없음',act:`save_workspace (${label||'이벤트'})`,ok:false});return}
  btns.forEach(b=>b.disabled=true);st.textContent='당직 책임자 승인 대기';PEND.push({id:'AP-'+String(PEND.length+1).padStart(3,'0'),t:now(),q,label:label||'이벤트 반영',fn,st,trace:curTrace});pendRender();toast('승인 요청 등록 · 당직 책임자 역할에서 처리')}
function pendRender(){const n=PEND.filter(p=>!p.done).length;$('apBtn').textContent=n?`승인 대기 ${n}`:'승인 대기 없음';$('apBtn').classList.toggle('on',!!n)}
function pendOpen(){const L=PEND.slice().reverse();drawer('승인 요청',L.length?L.map(p=>`<div class="apr ${p.done?'d':''}"><div><b>${p.id}</b> <span class="d">${p.t} · ${p.trace}</span><p>${esc(p.q)}</p><span class="kty">${esc(p.label)}</span></div><div class="apb">${p.done?`<span class="snst ${p.done==='부적절'?'b':''}">${p.done}</span>`:`<button class="btn pri" data-a="${p.id}" type="button">승인 · 반영</button><button class="btn" data-r="${p.id}" type="button">부적절</button>`}</div></div>`).join('')+(ROLE!=='책임자'?'<p class="d" style="margin-top:12px">현재 역할에서는 처리 불가 · 상단 역할을 “당직 책임자”로 전환</p>':''):'<p class="d">요청 없음</p>');
  $('drBody').querySelectorAll('[data-a],[data-r]').forEach(b=>b.onclick=()=>{if(ROLE!=='책임자'){toast('당직 책임자 역할에서만 처리');return}const p=PEND.find(x=>x.id===(b.dataset.a||b.dataset.r));if(b.dataset.a){const lc=curTrace;curTrace=p.trace;p.fn();curTrace=lc;p.done='승인';GWY.audit[0]&&(GWY.audit[0].appr=`당직 책임자 승인 (${p.id})`)}else{p.done='부적절';p.st.textContent='책임자 부적절';addJN('reject',`승인 요청 ${p.id} 부적절`)}pendRender();pendOpen()})}
/* --- 경보 처리 --- */
const _da=drawAlarms;drawAlarms=function(r){_da(r);const ul=$('alarms');const lis=[...ul.querySelectorAll('li')].filter(li=>!li.classList.contains('empty'));
  lis.forEach(li=>{const t=li.children[1].textContent;li.dataset.k=t;const st=ALM[t]==='star';li.classList.toggle('al-star',st);const b=document.createElement('span');b.className='alb';b.innerHTML=`<button type="button" data-x="ask">해석</button><button type="button" data-x="star" class="${st?'on':''}" aria-pressed="${st}" title="관심 표시">${st?'★':'☆'}</button>`;li.appendChild(b)});
  lis.sort((a,b)=>(ALM[b.dataset.k]==='star')-(ALM[a.dataset.k]==='star')).forEach(li=>ul.appendChild(li));
  $('almCnt').innerHTML=`관심 <b>${lis.filter(li=>ALM[li.dataset.k]==='star').length}</b> · 전체 ${lis.length} · <span class="d">경보 처리는 EMS</span>`;
  ul.querySelectorAll('.alb button').forEach(b=>b.onclick=()=>{const k=b.closest('li').dataset.k;if(b.dataset.x==='star'){ALM[k]=ALM[k]==='star'?undefined:'star'}else{const m=k.match(/(\S+)–(\S+) 선로/);ask(/N-1/.test(k)?'예방 조치 추천해줘':m&&/트립|탈락/.test(k)?`${m[1]}–${m[2]} 선로가 고장 나면?`:'현재 과부하 설비 있어?')}drawAlarms(dcpf(twin))})};
/* --- 시나리오 라이브러리 --- */
function scnSeed(){if(SCN.length)return;[['765kV 동해안 루트 2회선 상실','계통 신뢰도',['양평–태백 선로 2회선 모두 나가면?']],['오전 피크 + 신한울 트립','예비력',['신한울원자력 1호기 트립되면?','강남 부하 300MW 늘어나면?']],['서천–군산 정비 중 고장','휴전 검토',['서천–군산 선로 휴전하면?','서산–태안 선로도 탈락하면?']],['수도권 저녁 피크 혼잡','수도권',['강남 부하 20% 늘고 서울 부하 10% 늘면?']],['충청 북상 조류 + 보령 감발','재생에너지',['보령 발전 500MW 줄이면?','아산–청양 선로가 고장 나면?']],['남해안 발전 정지','발전 탈락',['하동화력 정지하면?','광양–남해 선로가 고장 나면?']]].forEach(([n,tag,st])=>SCN.push({id:'SC-'+String(++scnSeq).padStart(3,'0'),n,tag,steps:st,model:'kpg193',by:'예시',res:null}))}
function scnRunBatch(list){const out=[];list.forEach(sc=>{let s=cloneS(BASE),ok=true,evN=0;for(const q of sc.steps){const p=parseEvents(q,s);if(!p.evs.length||p.evs.some(e=>e.k==='amb')){ok=false;break}p.evs.forEach(e=>applyEv(s,{...e}));evN+=p.evs.length}balance(s);const r=ok?dcpf(s):null;
  const v=r?viol(r):[];const rd=v.length?redispatch(s):null,sh=v.length&&!rd?shed(s):null;sc.res={ok,isl:ok&&!r,v:v.length,max:r?Math.round(Math.max(...live(r).map(l=>ld(r,l)))):0,act:!ok?'해석 실패':!r?'계통 분리':!v.length?'조치 불필요':rd?`재급전 ${fmt(rd.total)}MW`:sh?`부하 차단 ${fmt(sh.total)}MW`:'에스컬레이션',evN,t:now()};out.push(sc)});return out}
async function scnPlay(sc){drawerClose();document.querySelector('nav button[data-v="ops"]').click();for(let i=0;i<sc.steps.length;i++){while(busy)await sleep(150);await ask(i&&!/그 상태/.test(sc.steps[i])?'그 상태에서 '+sc.steps[i]:sc.steps[i]);await sleep(400)}}
function scnOpen(){scnSeed();const L=SCN.filter(s=>s.model===M.id);
  drawer('시나리오 라이브러리',`<div class="scbar"><button class="btn pri" id="scAll" type="button">전체 일괄 실행</button><button class="btn" id="scSave" type="button">현재 대화를 시나리오로 저장</button><button class="btn" id="scNew" type="button">새 시나리오</button></div>
   ${L.length?`<table class="reg sct"><tr><th>시나리오</th><th>단계</th><th>최근 결과</th><th></th></tr>${L.map(s=>`<tr><td><b>${esc(s.n)}</b><div class="d"><span class="kty">${esc(s.tag)}</span> ${s.id} · ${s.by}</div></td><td><ol class="scs">${s.steps.map(q=>`<li contenteditable="true" data-s="${s.id}">${esc(q)}</li>`).join('')}</ol><button type="button" class="lnk" data-add="${s.id}">+ 단계</button></td><td>${s.res?`<b class="${s.res.v||s.res.isl?'scbad':'scok'}">${s.res.isl?'계통 분리':s.res.ok?`위반 ${s.res.v}건 · 최대 ${s.res.max}%`:'해석 실패'}</b><div class="d">${s.res.act} · ${s.res.t}</div>`:'<span class="d">미실행</span>'}</td><td class="scbtn"><button class="btn" data-run="${s.id}" type="button">결과만</button><button class="btn pri" data-play="${s.id}" type="button">관제 화면에서 재생</button></td></tr>`).join('')}</table>`:'<p class="d">이 계통 모델의 시나리오 없음 · 새로 만들거나 대화에서 저장</p>'}
   <p class="d" style="margin-top:10px">단계 문장은 바로 편집 가능 · 2단계부터는 직전 상태 위에 누적</p>`,true);
  const B=$('drBody');B.querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>{scnRunBatch([SCN.find(x=>x.id===b.dataset.run)]);scnOpen()});
  B.querySelectorAll('[data-play]').forEach(b=>b.onclick=()=>scnPlay(SCN.find(x=>x.id===b.dataset.play)));
  B.querySelectorAll('[data-add]').forEach(b=>b.onclick=()=>{SCN.find(x=>x.id===b.dataset.add).steps.push('선로 탈락하면?');scnOpen()});
  B.querySelectorAll('li[contenteditable]').forEach((li,i)=>li.onblur=()=>{const s=SCN.find(x=>x.id===li.dataset.s);const idx=[...li.parentElement.children].indexOf(li);const v=li.textContent.trim();if(v)s.steps[idx]=v;else s.steps.splice(idx,1);s.res=null});
  $('scAll').onclick=()=>{const t0=performance.now();scnRunBatch(L);toast(`${L.length}개 일괄 실행 · ${((performance.now()-t0)/1000).toFixed(1)}초`);scnOpen()};
  $('scNew').onclick=()=>{const n=prompt('시나리오 이름');if(!n)return;SCN.push({id:'SC-'+String(++scnSeq).padStart(3,'0'),n,tag:'사용자',steps:['선로가 고장 나면?'],model:M.id,by:ROLE,res:null});scnOpen()};
  $('scSave').onclick=()=>{const qs=[...$('msgs').querySelectorAll('.q')].map(x=>x.textContent).filter(q=>parse(q).k==='scen');if(!qs.length){toast('저장할 시나리오 질의가 없습니다');return}SCN.push({id:'SC-'+String(++scnSeq).padStart(3,'0'),n:'대화 저장 '+now(),tag:'대화',steps:qs.map(q=>q.replace(/^그 상태에서\s*/,'')),model:M.id,by:ROLE,res:null});scnOpen()}}
/* --- 엔진 슬롯 · 성과 지표 --- */
const TGT={qb:90,n1:99,reg:100,rt:5,rej:15};
function kpiRender(){const q=bmQBScore(),rg=bmRegScore();const tr={};logs.forEach(l=>{if(/^T-/.test(l.tr))tr[l.tr]=(tr[l.tr]||0)+(parseFloat(l.ms)||0)});const tv=Object.values(tr),rt=tv.length?tv.reduce((a,b)=>a+b,0)/tv.length:null;
  const rj=feedback.length?feedback.filter(f=>f.d==='부적절').length/feedback.length*100:null,n1=BM.last.n1?parseFloat(BM.last.n1.replace(/[^\d.]/g,'')):null;
  const R=[['qb','질의 이해 정확도','%','up',q.pct],['n1','N-1 계산 일치율','%','up',n1],['reg','재발 방지 문항 통과율','%','up',rg.n?rg.pct:null],['rt','평균 응답 시간','s','down',rt],['rej','관제사 부적절율','%','down',rj]];
  $('kpiT').innerHTML='<tr><th>지표</th><th>목표</th><th>규칙 엔진 (현재)</th><th>LLM 오케스트레이터</th><th>상태</th></tr>'+R.map(([k,n,u,b,v])=>{const ok=v==null?null:b==='up'?v>=TGT[k]:v<=TGT[k];return `<tr><td>${n}</td><td><input class="tgt" data-k="${k}" type="number" value="${TGT[k]}" step="0.1" aria-label="${n} 목표">${u}</td><td><b>${v==null?'-':(+v).toFixed(k==='rt'?2:1)+u}</b>${v==null?`<div class="d">${k==='n1'?'계산 정확도 채점 필요':k==='reg'?'라벨 확정 문항 없음':'데이터 없음'}</div>`:''}</td><td><span class="d">연결 전 · 같은 문제지로 비교 예정</span></td><td>${ok==null?'<span class="snst" style="background:var(--bg);color:var(--ink-3)">측정 전</span>':ok?'<span class="snst">충족</span>':'<span class="snst b">미달</span>'}</td></tr>`}).join('');
  $('kpiT').querySelectorAll('.tgt').forEach(i=>i.onchange=()=>{TGT[i.dataset.k]=+i.value;kpiRender()})}
/* --- 실계통: 조치 유지 --- */
const _ls=liveState;liveState=function(t){const s=_ls(t);if(LV.hold&&Date.now()<LV.hold.until){LV.hold.d.forEach((d,k)=>{if(s.gen[k]&&Math.abs(d)>.5){s.gen[k].p=Math.max(0,s.gen[k].p+d);s.gen[k].on=s.gen[k].p>.5?1:0}});balance(s)}return s};
/* --- 설계 메모 --- */
const NOTES={ops:['계통 상황 확인과 시나리오 해석, 조치 승인의 중심 화면','디지털 트윈 · 해석 엔진 · 지식 베이스',['답변 카드 정보량: 표·대안·근거·메모를 모두 펼칠지, 접어둘지','관제사 승인과 책임자 승인을 같은 화면에서 처리할지 분리할지','계통도 단계 재생 자동 실행 여부']],
 db:['수급·권역·설비·플랫폼 상태 요약','디지털 트윈 · 데이터 레이크 정기 수집',['상황판(대형 화면) 전용 레이아웃 필요 여부','권역 구분 기준: 행정구역 vs 계통 운영 구역','예비율 산정 방식 (운전 예비력 정의) 확정']],
 gw:['오케스트레이터와 응용프로그램 사이 창구 · 권한 · 기록','게이트웨이 호출 로그',['다른 과제 어댑터 등록 절차와 모델 버전 연동 방식','비동기 작업 시간 초과 기준','권한 감사 보존 기간']],
 kb:['규정 · 절차 · 관제사 지식 · 사례 검색과 관계 탐색','KPX 규정 목록 · 내부 절차 · 사용자 지식',['규정 원문 적재 범위와 갱신 주기','사용자 지식 승격 절차의 검토자 지정','지식그래프 엔터티 표준 (CIM ID 연계)']],
 rp:['일일 계통 운영 보고와 결재','운영 일지 · 판단 기록 · 수급 실적',['기존 운영 보고 양식과의 정합','자동 작성 범위와 수기 작성 범위 구분','결재선 구성']],
 sn:['계통 상태 수집 · 검증 · 데이터 레이크 관리','디지털 트윈 · 모의 피드 · 관제 활동',['정기 수집 주기 (5분 vs 1분)와 저장 용량','격리 데이터 처리 절차','타사 실시간 DB 인터페이스 정의서 항목']],
 ev:['문제지 관리 · 채점 · 엔진 비교 · 관제사 훈련','성능 평가 문제지 · 데이터 레이크 부적절 사례',['성과 지표 목표치 확정','재발 방지 문항 정답 라벨 검토자','LLM 연결 후 비교 방식 (동일 문제지 반복 횟수)']],
 ml:['피드백 기반 재학습 · 평가 게이트 · 배포','데이터 레이크 · 성능 평가 · 모델 레지스트리',['학습망 · 운영망 반출입 절차','과제별 학습 책임 분담','섀도 운영 기간과 교체 기준']]};
function noteKey(){return 'tb-notes-v1'}
function notesLoad(){try{return JSON.parse(localStorage.getItem(noteKey())||'{}')}catch(e){return {}}}
function notesSave(o){try{localStorage.setItem(noteKey(),JSON.stringify(o))}catch(e){}}
function notesOpen(tab){const N=NOTES[tab],all=notesLoad(),mine=all[tab]||[];
  drawer('설계 메모 · '+document.querySelector(`nav button[data-v="${tab}"]`).textContent,`<div class="nt"><div class="kdr"><span>목적</span><b>${N[0]}</b></div><div class="kdr"><span>데이터</span><b>${N[1]}</b></div><div class="lab">논의할 점</div><ul class="ntq">${N[2].map(x=>`<li>${x}</li>`).join('')}</ul>
   <div class="lab">팀 코멘트 <span class="d">· 이 브라우저에 저장</span></div><div class="ntl">${mine.length?mine.map((c,i)=>`<div class="ntc"><p>${esc(c.t)}</p><span class="d">${c.who} · ${c.at}</span><button type="button" class="lnk" data-del="${i}">삭제</button></div>`).join(''):'<p class="d">코멘트 없음</p>'}</div>
   <textarea id="ntIn" rows="3" placeholder="이 화면에 대한 의견"></textarea><button class="btn pri" id="ntAdd" type="button">코멘트 남기기</button></div>`);
  $('ntAdd').onclick=()=>{const v=$('ntIn').value.trim();if(!v)return;const o=notesLoad();(o[tab]=o[tab]||[]).push({t:v,who:ROLE,at:new Date().toLocaleString('ko-KR',{hour12:false})});notesSave(o);notesOpen(tab)};
  $('drBody').querySelectorAll('[data-del]').forEach(b=>b.onclick=()=>{const o=notesLoad();o[tab].splice(+b.dataset.del,1);notesSave(o);notesOpen(tab)})}
function notesBtns(){document.querySelectorAll('.view').forEach(v=>{const k=v.id.slice(2),hd=v.querySelector('.vhead');if(!hd||hd.querySelector('.ntb'))return;const c=(notesLoad()[k]||[]).length;const b=document.createElement('button');b.className='btn ntb';b.type='button';b.innerHTML=`설계 메모${c?` <span class="ntn">${c}</span>`:''}`;b.onclick=()=>notesOpen(k);hd.appendChild(b)})}

/* ===== 최종: 계획 연계 · 7단계 적재 · 단계형 지도 · 신뢰도 감시 · 보안 ===== */
const MAINT=[{k:'unit',plant:'보령화력',unit:'#5',t:'계획 정비',until:'10월 30일'},{k:'plant',plant:'신서천화력',t:'출력 제약 (설비 점검)',until:'11월 2일'},{k:'line',a:'양평',b:'태백',t:'765kV 1회선 휴전 예정',until:'익일 02:00~05:00'}];let PLAN_ON=true,MAPLV='bus';
function maintGen(g){if(!PLAN_ON||!M||M.id!=='kpg193')return false;return MAINT.some(m=>(m.k==='plant'&&g.plant===m.plant)||(m.k==='unit'&&g.plant===m.plant&&String(g.name).endsWith(m.unit)))}
function maintCount(){return M.id==='kpg193'&&PLAN_ON?M.gens.filter(maintGen).length:0}
function engTag(){return `엔진: 규칙 엔진 v1 (기준선) · 온프레미스 · 수치 출처: 해석 툴${maintCount()?` · 정비 계획 반영 (제외 ${maintCount()}기)`:''}${MODE==='live'&&LV.trust<70?' · <b class="warnt">트윈 신뢰도 저하 중 해석</b>':''}`}
function planRender(){const b=$('planBox');if(!b)return;b.hidden=M.id!=='kpg193';if(b.hidden)return;
  $('planList').innerHTML=MAINT.map(m=>`<div class="plr"><span class="kty">${m.k==='line'?'휴전':m.k==='plant'?'출력 제약':'정비'}</span><b>${m.k==='line'?`${m.a}–${m.b} ${m.t}`:`${m.plant}${m.unit?' '+m.unit:''} · ${m.t}`}</b><em>${m.until}</em></div>`).join('');
  $('planTog').checked=PLAN_ON;$('planNote').textContent=PLAN_ON?`재급전·예방 조치 계산에서 ${maintCount()}기 제외 중`:'계획 미반영 · 정비 중 설비도 추천될 수 있음'}
/* --- 7단계 적재 파이프라인 --- */
LST.length=0;LST.push(['src','인메모리 DB','전용 · EMS/SCADA'],['col','적재','스냅샷 단위'],['val','판별·검증','시점·수렴·범위'],['tf','변환','CIM ID·그래프'],['lake','데이터 레이크','4개 구역·목록 DB']);
const NST=LST.length-1,LX2=i=>40+i*222,LW2=172,LAB=['송신','수신','통과','변환','적재'];
function lkS(){if(!LK.cnt.s)LK.cnt.s=new Array(LST.length).fill(0);return LK.cnt.s}
lkDraw=function(){const g=$('lkSvg');if(!g)return;const S=lkS(),Y=78,H=64;let h=`<defs><marker id="lkarr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M1 1L9 5L1 9" fill="none" stroke="context-stroke" stroke-width="1.6" stroke-linecap="round"/></marker></defs>`;
  h+=`<rect class="lkgrp" x="${LX2(1)-12}" y="${Y-H/2-26}" width="${LX2(3)+LW2-LX2(1)+24}" height="${H+40}" rx="16"/><text class="lkgt" x="${LX2(1)}" y="${Y-H/2-10}">적재 서버</text><text class="lkgs" x="${LX2(0)+LW2/2}" y="${Y-H/2-10}" text-anchor="middle">${MODE==='live'?'테스트베드망 입구':'모의 (KPG)'}</text>`;
  for(let i=0;i<NST;i++)h+=`<line class="lke${LK.on?' on':''}" x1="${LX2(i)+LW2}" y1="${Y}" x2="${LX2(i+1)}" y2="${Y}" marker-end="url(#lkarr)"/>`;
  h+=`<path class="lke q" d="M${LX2(2)+LW2/2} ${Y+H/2} L${LX2(2)+LW2/2} ${Y+H/2+34}" marker-end="url(#lkarr)"/><rect class="lkq" x="${LX2(2)+LW2/2-60}" y="${Y+H/2+38}" width="120" height="34" rx="9"/><text class="lkqt" x="${LX2(2)+LW2/2}" y="${Y+H/2+59}" text-anchor="middle">격리 ${LK.cnt.bad||0}건</text>`;
  LST.forEach(([id,t,s],i)=>{h+=`<g class="lks ${id}${LK.on?' on':''}"><rect x="${LX2(i)}" y="${Y-H/2}" width="${LW2}" height="${H}" rx="12"/><text class="lkt" x="${LX2(i)+12}" y="${Y-6}">${t}</text><text class="lkd" x="${LX2(i)+12}" y="${Y+14}">${s}</text><text class="lkn" x="${LX2(i)+LW2-10}" y="${Y-6}" text-anchor="end">${fmt(S[i]||0)}</text><text class="lkd" x="${LX2(i)+LW2-10}" y="${Y+14}" text-anchor="end">${LAB[i]}</text></g>`});
  h+=`<g id="lkPk"></g>`;g.setAttribute('viewBox','0 -10 1160 210');g.innerHTML=h};
lkPacket=function(kind,bad){LK.cnt.rx=(LK.cnt.rx||0)+1;lkS()[0]++;LK.pk.push({kind,bad,t0:performance.now(),stage:0});LK.tp.push(Date.now());lkLog(kind,bad);if(!LK.raf)LK.raf=requestAnimationFrame(lkAnim);lkStats()};
lkAnim=function(nw){const g=$('lkPk'),D=3200,Y=78,S=lkS();let h='';LK.pk=LK.pk.filter(p=>{const k=(nw-p.t0)/D,x0=LX2(0)+LW2/2,x1=LX2(NST)+LW2/2,kv=2/NST;let x,y=Y;
    if(p.bad&&k>kv){const q=Math.min(1,(k-kv)/.2);x=LX2(2)+LW2/2;y=Y+q*84;if(p.stage<2){p.stage=2;LK.cnt.bad=(LK.cnt.bad||0)+1;lkDrawCounts()}if(k>kv+.3)return false}
    else{x=x0+(x1-x0)*Math.min(1,k);const st=Math.min(p.bad?1:NST,Math.floor(Math.min(.999,k)*NST)+1);while(p.stage<st){p.stage++;S[p.stage]++;lkDrawCounts()}if(k>=1){LK.cnt.ld=(LK.cnt.ld||0)+1;return false}}
    h+=`<circle class="lkp ${p.bad&&k>kv?'bad':p.kind==='사건'?'ev':p.kind==='판단'?'dc':'pr'}" cx="${x}" cy="${y}" r="6.5"/>`;return true});if(g)g.innerHTML=h;LK.raf=LK.pk.length?requestAnimationFrame(lkAnim):0};
/* --- 데이터 레이크 구역 --- */
const _lr=lakeRender;lakeRender=function(){_lr();planRender();const z=$('snZ')?$('snZ').value:'표준화';const rows=[...$('snList').querySelectorAll('.snr')];
  rows.forEach(r=>{const s=SNAP.find(x=>x.id===r.dataset.id);if(!s)return;const show=z==='원본'?true:z==='격리'?s.status==='격리':z==='데이터셋'?!!s.ds:s.status!=='격리';r.hidden=!show});
  const d=$('snDet');if(d&&SNSEL){const s=SNAP.find(x=>x.id===SNSEL);if(s){const dt=s.date,base=`${dt.getFullYear()}/${pad(dt.getMonth()+1)}/${pad(dt.getDate())}/${s.id}`;
    const rows2=[...d.querySelectorAll('.kdr')];const pr=rows2.find(x=>x.firstChild.textContent==='저장 경로');if(pr)pr.querySelector('b').innerHTML=`<span class="zn">원본</span> <code class="path">grid-lake/raw/${base}.json</code> <span class="d">(EMS 스냅샷 원형 · 측정값 포함)</span><br><span class="zn ${s.status==='격리'?'b':''}">${s.status==='격리'?'격리':'표준화'}</span> <code class="path">grid-lake/${s.status==='격리'?'quarantine':'standard'}/${base}/state.parquet · topology.cim.xml</code>${s.ds?`<br><span class="zn ok">데이터셋</span> <code class="path">grid-lake/datasets/${s.ds}/</code>`:''}`;
    const sec=document.createElement('div');sec.className='kdr';sec.innerHTML=`<span>보안 등급</span><b>${MODE==='live'?'대외비 · 계통 운영 정보 · 열람 시 감사 기록':'공개 데이터 (KPG 기반)'}</b>`;if(pr)pr.after(sec)}}};
/* --- 단계형 지도 --- */
const _dov=drawOverlay;drawOverlay=function(){const svg=$('sld');if(!SC)return;const geo=SC.mode==='geo'&&M.buses[0].rg!=null;$('lvTog').hidden=!geo;svg.dataset.lv=geo?MAPLV:'bus';
  document.querySelectorAll('#lvTog button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.lv===MAPLV));if(!(geo&&MAPLV==='rg'))return _dov();
  const g=$('gOver'),k=scaleK(),P=v=>v*k,F=regionFlows(SC.S0,SC.r),C=RGN.map((_,i)=>{const bs=M.buses.filter(b=>b.rg===i);return {x:bs.reduce((a,b)=>a+b.x,0)/bs.length,y:bs.reduce((a,b)=>a+b.y,0)/bs.length}});let h='';
  [[0,1],[0,2],[1,2],[1,4],[2,3],[2,4],[3,4]].forEach(([a,b])=>{const net=(F.fl[a+'>'+b]||0)-(F.fl[b+'>'+a]||0);if(Math.abs(net)<1)return;const [f,t]=net>=0?[a,b]:[b,a],A=C[f],B=C[t],dx=B.x-A.x,dy=B.y-A.y,L=Math.hypot(dx,dy),cut=P(46);
    h+=`<line class="rlv" vector-effect="non-scaling-stroke" style="stroke-width:${Math.min(14,3+Math.abs(net)/900)}px" x1="${A.x+dx/L*cut}" y1="${A.y+dy/L*cut}" x2="${B.x-dx/L*cut}" y2="${B.y-dy/L*cut}" marker-end="url(#rlarr)"/><text class="rlt" x="${(A.x+B.x)/2}" y="${(A.y+B.y)/2-P(6)}" text-anchor="middle" style="font-size:${P(11)}px;stroke-width:${P(3)}px">${fmt(Math.abs(net))}</text>`});
  RGN.forEach((n,i)=>{const p=C[i],net=F.gen[i]-F.dem[i],R=P(40);h+=`<g class="rlb" data-rg="${i}" role="button" tabindex="0"><circle cx="${p.x}" cy="${p.y}" r="${R}"/><text x="${p.x}" y="${p.y-P(6)}" text-anchor="middle" style="font-size:${P(14)}px" class="rln">${n}</text><text x="${p.x}" y="${p.y+P(12)}" text-anchor="middle" style="font-size:${P(12)}px" class="rlv2 ${net>=0?'sp':'df'}">${net>=0?'+':'−'}${fmt(Math.abs(net))}</text><title>${n} 수요 ${fmt(F.dem[i])} · 발전 ${fmt(F.gen[i])} · 누르면 모선 보기</title></g>`});
  g.innerHTML=`<defs><marker id="rlarr" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto"><path d="M0 0L10 5L0 10Z" fill="context-stroke"/></marker></defs>`+h;
  g.querySelectorAll('.rlb').forEach(x=>{const f=()=>{MAPLV='bus';const idx=M.buses.map((b,i)=>b.rg===+x.dataset.rg?i:-1).filter(i=>i>=0);fitTo(idx);drawOverlay()};x.onclick=f;x.onkeydown=e=>{if(e.key==='Enter')f()}})};
const _sv=setView;setView=function(v,f){if(v&&v.hl&&MAPLV==='rg')MAPLV='bus';_sv(v,f)};
/* --- 게이트웨이: 연결 예정 해석 · 분산 실행 --- */
ADP.push({id:'ac',n:'AC 해석 엔진',d:'AC 조류 · 전압 안정도',tools:['run_ac_power_flow'],ext:true},{id:'dyn',n:'동태해석',d:'주파수 응답 · 과도안정도',tools:['run_dynamic_freq'],ext:true});
TOOLS.push(['run_ac_power_flow','연결 예정','AC 조류계산 · 전압 · 무효전력','c','비동기'],['run_dynamic_freq','연결 예정','발전기 탈락 시 주파수 응답','c','비동기']);
SCH.run_ac_power_flow='scenario_id → V·θ·P·Q[], 전압 위반[]';SCH.run_dynamic_freq='scenario_id, 탈락 MW → 주파수 최저점·회복 시간';
const _lc3=logCall;logCall=function(t,i,r,ms){if(t==='run_n1_screening'&&!/워커/.test(r))r=r+' · 분산 워커 8';_lc3(t,i,r,ms)};
/* --- 경보 출처 --- */
const _da3=drawAlarms;drawAlarms=function(r){_da3(r);$('alarms').querySelectorAll('li').forEach(li=>{if(li.classList.contains('empty')||li.querySelector('.asrc'))return;const t=li.children[1];const pf=/N-1|예상|시간 뒤/.test(t.textContent);const c=document.createElement('span');c.className='asrc '+(pf?'pf':'ems');c.textContent=pf?'플랫폼 해석':MODE==='live'?'EMS 수신':'트윈';t.prepend(c)})};
/* --- 실계통: 신뢰도 감시 · 장애 시험 --- */
LV.trust=100;LV.fault=null;
const _lt=liveTick;liveTick=function(){if(MODE!=='live')return;const f=LV.fault;
  if(f&&f.type==='미수렴'){LV.tick++;LV.seConv=false;LV.trust=55;snapPush('정기','EMS 상태추정 미수렴 주기 · 직전 상태 유지',twin,'-',{src:'EMS 스냅샷 (모의 피드)',bad:true});if(--f.n<=0)LV.fault=null;hdrUpdate();if($('v-sn').classList.contains('on'))liveSrcRender();return}
  LV.seConv=true;
  if(f&&f.type==='끊김'){LV.tick++;LV.lat+=3;LV.cov=0;LV.trust=Math.max(5,100-LV.lat*6);hdrUpdate();if($('v-sn').classList.contains('on'))liveSrcRender();return}
  if(!f&&Math.random()<.025)LV.fault=Math.random()<.5?{type:'지연',n:3,auto:true}:{type:'미수렴',n:1,auto:true};_lt();
  if(LV.fault&&LV.fault.type==='지연'){LV.lat=7+Math.random()*2.5;LV.cov=88+Math.random()*4;if(--LV.fault.n<=0)LV.fault=null}
  LV.trust=Math.round(Math.max(0,100-Math.max(0,LV.lat-3)*8-Math.max(0,98-LV.cov)*4-(LV.res>1?10:0)));hdrUpdate()};
function faultTest(t){if(MODE!=='live')return;if(t==='정상'){LV.fault=null;LV.lat=2.4;toast('피드 정상화')}else{LV.fault={type:t,n:t==='지연'?5:t==='미수렴'?2:999};if(t==='끊김')LV.lat=3;toast(`장애 시험: 피드 ${t}`)}addJN('apply',`장애 시험: ${t}`);liveTick()}
const _hu=hdrUpdate;hdrUpdate=function(){_hu();if(MODE!=='live')return;const tw=$('twMode'),f=LV.fault;if(!view||view.live){if(f&&f.type==='끊김'){tw.textContent='동기 끊김';tw.className='twm tw-as'}else if(LV.trust<70){tw.textContent='동기 저하';tw.className='twm tw-as'}}
  const bar=document.querySelector('.ctxbar');bar.classList.toggle('degr',LV.trust<70);const ci=$('ctxInfo');ci.insertAdjacentHTML('beforeend',`<span class="ctxm">트윈 신뢰도 <b class="${LV.trust<70?'w':''}">${LV.trust}%</b></span>${f&&f.type==='미수렴'?'<span class="ctxw">EMS 상태추정 미수렴 · 직전 상태 유지</span>':f&&f.type==='끊김'?'<span class="ctxw">피드 끊김 · 마지막 상태 유지 · 해석 결과 주의</span>':LV.trust<70?'<span class="ctxw">동기 지연 · 해석 결과 주의</span>':''}`)};
const _lsr=liveSrcRender;liveSrcRender=function(){_lsr();if(MODE!=='live')return;const tg=topoSum(twin);$('trustBox').innerHTML=`<div class="kpi"><span>트윈 신뢰도</span><b class="${LV.trust<70?'w':''}">${LV.trust}%</b></div><div class="kpi"><span>동기 지연</span><b>${LV.lat.toFixed(1)}s</b></div><div class="kpi"><span>EMS 상태추정</span><b class="${LV.seConv===false?'w':''}">${LV.seConv===false?'미수렴':'수렴'}</b></div><div class="kpi"><span>시점 일관성</span><b>${LV.fault&&LV.fault.type==='지연'?'주기 혼재 의심':'단일 주기'}</b></div><div class="kpi"><span>수신 연결 구조</span><b>${tg.nodes}모선 · 섬 ${tg.isl}</b></div>`};
const _drd=dashRender;dashRender=function(){_drd();if(MODE==='live')$('dPlat').insertAdjacentHTML('afterbegin',`<div class="ps ${LV.trust<70?'':'ok'}"><span>트윈 신뢰도</span><b>${LV.trust}%</b><em>지연 ${LV.lat.toFixed(1)}s · 수신 ${LV.cov.toFixed(1)}%</em></div>`)};
/* --- 설계 메모 보강 --- */
NOTES.ops[2].push('AI 판단을 따랐을 때의 책임 소재와 기록 범위','EMS 경보와 플랫폼 해석 경보의 화면 분리 기준');NOTES.ml[2].push('실운영 전 섀도 운영 기간과 효과 지표 (응답 시간 단축 · 오판 감소)','데이터 소유 기관 · 과제 간 협약 범위');NOTES.sn[2].push('전용 인메모리 DB 위치: 테스트베드망 수신 영역 (망연계 직후)','보안 등급 분류 · 접근 통제 · 반출 승인 절차','EMS·SCADA → 전용 인메모리 DB 수신 항목: 상태추정 결과 · 연결 상태 · 원시 측정값');NOTES.db[2].push('트윈 신뢰도 기준치와 저하 시 화면 동작');

/* ===== 구조 수정 · 연동 보고서 · 계통도 검색 ===== */
LST.length=0;LST.push(['src','EMS · SCADA','운영망 · 실시간'],['im','인메모리 DB','전용 · 수신 영역'],['col','스냅샷 적재','주기 · 이벤트'],['val','판별 · 검증','시점·수렴·범위'],['tf','변환','CIM ID·그래프'],['lake','데이터 레이크','4개 구역']);
const N6=LST.length-1,LXa=i=>24+i*188,LWa=160,LABa=['송신','수신','적재','통과','변환','저장'],QI=3;
lkDraw=function(){const g=$('lkSvg');if(!g)return;const S=lkS(),Y=78,H=64;while(S.length<LST.length)S.push(0);let h=`<defs><marker id="lkarr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M1 1L9 5L1 9" fill="none" stroke="context-stroke" stroke-width="1.6" stroke-linecap="round"/></marker></defs>`;
  h+=`<rect class="lkzone" x="${LXa(1)-10}" y="${Y-H/2-34}" width="${LXa(5)+LWa-LXa(1)+20}" height="${H+70}" rx="18"/><text class="lkzt" x="${LXa(1)}" y="${Y-H/2-16}">테스트베드망</text><text class="lkzs" x="${LXa(0)+LWa/2}" y="${Y-H/2-16}" text-anchor="middle">운영망${MODE==='live'?'':' (모의)'}</text>`;
  h+=`<rect class="lkgrp" x="${LXa(2)-8}" y="${Y-H/2-8}" width="${LXa(4)+LWa-LXa(2)+16}" height="${H+16}" rx="14"/><text class="lkgt" x="${LXa(2)+LWa+14}" y="${Y+H/2+22}">적재 서버</text>`;
  h+=`<g class="oneway"><circle cx="${(LXa(0)+LWa+LXa(1))/2}" cy="${Y}" r="11"/><path d="M${(LXa(0)+LWa+LXa(1))/2-5} ${Y-6}L${(LXa(0)+LWa+LXa(1))/2+6} ${Y}L${(LXa(0)+LWa+LXa(1))/2-5} ${Y+6}Z"/></g><text class="lkzs" x="${(LXa(0)+LWa+LXa(1))/2-6}" y="${Y+H/2+48}" text-anchor="middle">망연계 단방향</text>`;
  for(let i=0;i<N6;i++)if(i!==0)h+=`<line class="lke${LK.on?' on':''}" x1="${LXa(i)+LWa}" y1="${Y}" x2="${LXa(i+1)}" y2="${Y}" marker-end="url(#lkarr)"/>`;
  h+=`<path class="lke q" d="M${LXa(QI)+LWa/2} ${Y+H/2} L${LXa(QI)+LWa/2} ${Y+H/2+44}" marker-end="url(#lkarr)"/><rect class="lkq" x="${LXa(QI)+LWa/2-58}" y="${Y+H/2+48}" width="116" height="32" rx="9"/><text class="lkqt" x="${LXa(QI)+LWa/2}" y="${Y+H/2+68}" text-anchor="middle">격리 ${LK.cnt.bad||0}건</text>`;
  LST.forEach(([id,t,s],i)=>{h+=`<g class="lks ${id}${LK.on?' on':''}"><rect x="${LXa(i)}" y="${Y-H/2}" width="${LWa}" height="${H}" rx="12"/><text class="lkt" x="${LXa(i)+12}" y="${Y-6}">${t}</text><text class="lkd" x="${LXa(i)+12}" y="${Y+14}">${s}</text><text class="lkn" x="${LXa(i)+LWa-10}" y="${Y-6}" text-anchor="end">${fmt(S[i]||0)}</text><text class="lkd" x="${LXa(i)+LWa-10}" y="${Y+14}" text-anchor="end">${LABa[i]}</text></g>`});
  h+=`<g id="lkPk"></g>`;g.setAttribute('viewBox','0 -20 1160 230');g.innerHTML=h};
lkAnim=function(nw){const g=$('lkPk'),D=3600,Y=78,S=lkS();let h='';LK.pk=LK.pk.filter(p=>{const k=(nw-p.t0)/D,x0=LXa(0)+LWa/2,x1=LXa(N6)+LWa/2,kv=QI/N6;let x,y=Y;
    if(p.bad&&k>kv){const q=Math.min(1,(k-kv)/.2);x=LXa(QI)+LWa/2;y=Y+q*90;if(p.stage<QI){p.stage=QI;LK.cnt.bad=(LK.cnt.bad||0)+1;lkDrawCounts()}if(k>kv+.3)return false}
    else{x=x0+(x1-x0)*Math.min(1,k);const st=Math.min(p.bad?QI-1:N6,Math.floor(Math.min(.999,k)*N6)+1);while(p.stage<st){p.stage++;S[p.stage]=(S[p.stage]||0)+1;lkDrawCounts()}if(k>=1){LK.cnt.ld=(LK.cnt.ld||0)+1;return false}}
    h+=`<circle class="lkp ${p.bad&&k>kv?'bad':p.kind==='사건'?'ev':p.kind==='판단'?'dc':'pr'}" cx="${x}" cy="${y}" r="6.5"/>`;return true});if(g)g.innerHTML=h;LK.raf=LK.pk.length?requestAnimationFrame(lkAnim):0};
/* 연동 보고서: 연동 개시 이후 기록 */
rpBuild=function(){if(MODE==='live'){if(RP.days.length)return;const sw=WDN.slice();WDN.push(...WDN.splice(0,2));_rpb();WDN.length=0;WDN.push(...sw);RP.days.forEach(d=>{if(d.past)d.live=true});RP.sel=RP.days[RP.days.length-1].key;return}_rpb()};
/* --- 계통도 검색 --- */
let MIDX=[],SELHL=null,msI=-1;
const CHO='ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ';
const cho=s=>[...s].map(c=>{const k=c.charCodeAt(0)-44032;return k>=0&&k<11172?CHO[Math.floor(k/588)]:c}).join('');
const nrm=s=>String(s).toLowerCase().replace(/[\s–\-~·#]/g,'');
function buildMapIdx(){MIDX=[];M.buses.forEach((b,i)=>MIDX.push({t:'변전소',l:nameOf(i),s:`${b.kv?b.kv+'kV · ':''}부하 ${fmt(b.load)}MW`,k:{b:i}}));
  allLines(twin).forEach(l=>MIDX.push({t:'선로',l:lname(l),s:`${l.kv?l.kv+'kV · ':''}${l.cx.length}회선 · ${l.eq}`,k:{l:l.id}}));
  const pl={};M.gens.forEach((g,i)=>{MIDX.push({t:'발전기',l:g.name,s:`${g.type} · 최대 ${fmt(g.max)}MW · ${nameOf(g.b)}`,k:{g:i}});(pl[g.plant]=pl[g.plant]||[]).push(i)});
  if(M.naming!=='num')Object.entries(pl).forEach(([p,gs])=>{if(gs.length>1)MIDX.push({t:'발전소',l:p,s:`${gs.length}기 · ${nameOf(M.gens[gs[0]].b)}`,k:{p,gs}})});
  MIDX.forEach(e=>{e.n=nrm(e.l);e.c=cho(e.n)})}
function mapSearch(q){const n=nrm(q),c=cho(n),isCho=/^[ㄱ-ㅎ]+$/.test(n);if(!n)return [];const out=[];
  for(const e of MIDX){let sc=-1;if(e.n.startsWith(n))sc=0;else{const i=e.n.indexOf(n);if(i>=0)sc=1+i/50;else if(isCho&&e.c.includes(n))sc=2;else if(e.s&&nrm(e.s).includes(n)&&n.length>2)sc=3}if(sc>=0)out.push([sc+e.l.length/200,e])}
  return out.sort((a,b)=>a[0]-b[0]).slice(0,14).map(x=>x[1])}
function msRender(){const q=$('mapQ').value,box=$('mapRes'),r=mapSearch(q);msI=r.length?0:-1;if(!q.trim()){box.hidden=true;return}
  box.hidden=false;const grp={};r.forEach(e=>(grp[e.t]=grp[e.t]||[]).push(e));let i=0;
  box.innerHTML=r.length?Object.entries(grp).map(([t,a])=>`<div class="msg">${t}</div>`+a.map(e=>`<button type="button" class="msr${i===msI?' on':''}" data-i="${i++}"><b>${hilite(e.l,q)}</b><span>${esc(e.s)}</span></button>`).join('')).join(''):'<p class="d" style="padding:8px 10px;margin:0">결과 없음 · 초성 검색 가능 (예: ㅅㅊ)</p>';
  box._r=Object.values(grp).flat();box.querySelectorAll('.msr').forEach(b=>{b.onmousedown=e=>{e.preventDefault();msPick(box._r[+b.dataset.i])};b.onmouseenter=()=>{msI=+b.dataset.i;msMark()}})}
function hilite(l,q){const n=nrm(q);const s=String(l);let out='',j=0,ln=s.toLowerCase();const ix=nrm(ln).indexOf(n);if(ix<0||!n)return esc(s);let cnt=-1,st=-1,en=-1;for(let k=0;k<s.length;k++){if(!/[\s–\-~·#]/.test(s[k]))cnt++;if(cnt===ix&&st<0)st=k;if(cnt===ix+n.length-1){en=k;break}}return st<0?esc(s):esc(s.slice(0,st))+'<mark>'+esc(s.slice(st,en+1))+'</mark>'+esc(s.slice(en+1))}
function msMark(){$('mapRes').querySelectorAll('.msr').forEach(b=>b.classList.toggle('on',+b.dataset.i===msI));const on=$('mapRes').querySelector('.msr.on');if(on)on.scrollIntoView({block:'nearest'})}
function msPick(e){if(!e)return;$('mapRes').hidden=true;$('mapQ').blur();if(MAPLV==='rg')MAPLV='bus';const r=dcpf(shown());let focus=[],lines=new Set(),buses=[],card='',acts=[];
  if(e.k.b!=null){const i=e.k.b;buses=[i];focus=[i,...allLines(twin).filter(l=>l.f===i||l.t===i).flatMap(l=>[l.f,l.t])];const gs=gensOf(twin).filter(g=>g.b===i&&g.on);card=`<div class="mcr"><span>부하</span><b>${fmt(twin.load[i])}MW</b></div><div class="mcr"><span>발전</span><b>${fmt(gs.reduce((a,g)=>a+g.p,0))}MW</b></div><div class="mcr"><span>연결 선로</span><b>${allLines(twin).filter(l=>l.f===i||l.t===i).length}개</b></div>`;acts=[[`${M.naming==='num'?M.buses[i].n+'번 모선':M.buses[i].n} 부하 10% 늘면?`,'부하 변화 해석']]}
  else if(e.k.l){const l=allLines(twin).find(x=>x.id===e.k.l),p=r&&!isOut(r,l)?ld(r,l):0;lines=new Set([l.id]);buses=[l.f,l.t];focus=[l.f,l.t];card=`<div class="mcr"><span>부하율</span><b class="${p>100?'a':p>=90?'w':''}">${Math.round(p)}%</b></div><div class="mcr"><span>정격</span><b>${fmt(r?r.R[l.id]:0)}MW</b></div><div class="mcr"><span>회선</span><b>${remC(l,twin)}/${l.cx.length}</b></div>`;acts=[[`${M.buses[l.f].n}–${M.buses[l.t].n} 선로가 고장 나면?`,'고장 해석'],[`${M.buses[l.f].n}–${M.buses[l.t].n} 선로 휴전하면?`,'휴전 검토']]}
  else{const gi=e.k.gs||[e.k.g],g0=M.gens[gi[0]],out=gi.reduce((a,k)=>a+(twin.gen[k].on?twin.gen[k].p:0),0),mx=gi.reduce((a,k)=>a+M.gens[k].max,0);buses=[g0.b];focus=[g0.b,...allLines(twin).filter(l=>l.f===g0.b||l.t===g0.b).flatMap(l=>[l.f,l.t])];
    card=`<div class="mcr"><span>현재 출력</span><b>${fmt(out)}MW</b></div><div class="mcr"><span>최대</span><b>${fmt(mx)}MW</b></div><div class="mcr"><span>상태</span><b>${gi.some(k=>maintGen(M.gens[k]))?'계획 제약':gi.some(k=>twin.gen[k].on)?'운전':'정지'}</b></div>`;const nm=e.k.p||unitQ(g0);acts=[[`${nm} 트립되면?`,'정지 해석'],[`${nm} 출력 100MW 줄이면?`,'출력 변경']]}
  SELHL={lines,buses};fitTo(focus);drawOverlay();
  $('mapCard').innerHTML=`<div class="mch"><span class="kty">${e.t}</span><b>${esc(e.l)}</b><button type="button" class="lnk" id="mcX" aria-label="닫기">✕</button></div><p class="d">${esc(e.s)}</p><div class="mcg">${card}</div><div class="mca">${acts.map(([q,t])=>`<button type="button" class="btn" data-q="${esc(q)}">${t}</button>`).join('')}<button type="button" class="btn" data-kg="${e.k.l?'line:'+e.k.l:e.k.b!=null?'bus:'+e.k.b:'plant:'+(e.k.p||M.gens[e.k.g].plant)}">지식그래프</button></div>`;$('mapCard').hidden=false;
  $('mcX').onclick=()=>{$('mapCard').hidden=true;SELHL=null;drawOverlay()};$('mapCard').querySelectorAll('[data-q]').forEach(b=>b.onclick=()=>{$('q').value=b.dataset.q;$('q').focus()})}
const _dov2=drawOverlay;drawOverlay=function(){_dov2();if(!SELHL||!SC)return;const g=$('gOver');if(!g)return;const k=scaleK();let h='';SELHL.lines.forEach(id=>{const l=allLines(SC.S0).find(x=>x.id===id);if(l)h+=`<line class="selhl" vector-effect="non-scaling-stroke" x1="${M.buses[l.f].x}" y1="${M.buses[l.f].y}" x2="${M.buses[l.t].x}" y2="${M.buses[l.t].y}"/>`});
  SELHL.buses.forEach(i=>{const b=M.buses[i];h+=`<circle class="selr" vector-effect="non-scaling-stroke" cx="${b.x}" cy="${b.y}" r="${14*k}"/>`});g.insertAdjacentHTML('afterbegin',h)};
function msKey(e){const box=$('mapRes'),n=box._r?box._r.length:0;if(e.key==='ArrowDown'){e.preventDefault();msI=Math.min(n-1,msI+1);msMark()}else if(e.key==='ArrowUp'){e.preventDefault();msI=Math.max(0,msI-1);msMark()}else if(e.key==='Enter'){e.preventDefault();if(box._r&&box._r[msI])msPick(box._r[msI])}else if(e.key==='Escape'){box.hidden=true;$('mapQ').blur()}}
let msT=0;

/* ===== 존재 의의 정렬: 작업 공간 · 공유 · 데이터 기준 시각 · 되감기 · 실계통 대비 · 예측 대 관측 ===== */
let LASTQ='',REW={on:false,s:null};const PRED=[];
function pinWS(q,label,state,st){lastScen=null;setView({state:cloneS(state),ref:twin,label:`작업 공간: ${label||q} (현 계통 아님)`,hl:{lines:new Set(Object.keys(state.out)),marks:[],focus:[]}},true);
  logCall('save_workspace',`${q}${label?' + '+label:''}`,'pinned (no effect on live twin)','0.00s');addJN('apply',`작업 공간 고정: ${q}${label?' + '+label:''}`);if(st)st.textContent='작업 공간에 고정됨';toast('작업 공간에 고정 · 실계통 트윈은 그대로')}
function shareAdd(q,label){PEND.push({id:'SH-'+String(PEND.length+1).padStart(3,'0'),t:now(),q,label:label||'분석',trace:curTrace,by:ROLE,notes:[]});pendRender();addJN('apply',`분석 공유: ${q}`);toast('공유됨 · 상단 “공유 분석”에서 확인')}
function pendRender(){const n=PEND.filter(p=>!p.notes.length).length;$('apBtn').innerHTML=`공유 분석 <span class="apn${n?' on':''}">${n?n:PEND.length}</span>`;$('apBtn').title=PEND.length?`공유 ${PEND.length}건 · 검토 대기 ${n}건`:'답변의 “공유”로 올린 분석 목록 · 책임자가 검토 의견을 남김';$('apBtn').classList.toggle('on',!!n)}
function pendOpen(){const L=PEND.slice().reverse();drawer('공유 분석',L.length?L.map(p=>`<div class="apr"><div style="flex:1"><b>${p.id}</b> <span class="d">${p.t} · ${p.trace} · ${p.by}</span><p>${esc(p.q)}</p><span class="kty">${esc(p.label)}</span>${p.notes.map(n=>`<div class="ntc" style="margin-top:6px"><p>${esc(n.t)}</p><span class="d">${n.by} · ${n.at}</span></div>`).join('')}<div class="shi"><input placeholder="검토 의견" data-n="${p.id}"><button class="btn" type="button" data-s="${p.id}">남기기</button></div></div></div>`).join(''):'<p class="d">공유된 분석 없음 · 답변 아래 “공유”로 추가</p>');
  $('drBody').querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>{const p=PEND.find(x=>x.id===b.dataset.s),i=$('drBody').querySelector(`[data-n="${p.id}"]`);if(!i.value.trim())return;p.notes.push({t:i.value.trim(),by:ROLE,at:now()});pendRender();pendOpen()})}
/* 게이트웨이 정렬 */
{const t=TOOLS.find(x=>x[0]==='save_workspace');if(t){t[1]='1.1.0';t[2]='작업 공간 고정 · 공유 기록 (실계통 영향 없음)';t[3]='c'}SCH.save_workspace='scenario, 대안, 작성자 → workspace_id';}
function gwSeed(){if(GWY.seeded)return;GWY.seeded=true;GWY.audit.push({t:'09:41:12',trace:'T-0R2',who:'LLM 오케스트레이터',appr:'제어 경로 없음 · 차단',act:'EMS 제어 인터페이스 호출 시도',ok:false,ex:true},{t:'08:15:40',trace:'T-0R1',who:'연구자 계정',appr:'실시간 트윈은 EMS 데이터만 반영 · 차단',act:'실계통 트윈 상태 덮어쓰기 시도',ok:false,ex:true});GWY.jobs.push({id:'JOB-0000',tool:'run_n1_screening',trace:'T-0R3',inp:'전체 사고 365건',st:'실패 → 재시도 성공',t:'07:02:10',dur:9.4,res:'1차 시간 초과, 2차 완료',retry:1,ex:true})}
/* 데이터 기준 시각 */
const _engTag=engTag;engTag=function(){return _engTag()+` · <span class="age" data-ts="${Date.now()}" data-q="${esc(LASTQ)}"></span>`};
const _ask=ask;ask=function(q){LASTQ=q;return _ask(q)};
function ageTick(){const n=Date.now();document.querySelectorAll('.engtag .age').forEach(a=>{const ts=+a.dataset.ts,s=Math.round((n-ts)/1000),d=new Date(ts);const old=MODE==='live'&&s>45&&!REW.on;a.innerHTML=`기준 스냅샷 ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())} · ${s<60?s+'초':Math.floor(s/60)+'분'} 전${old?` · <button type="button" class="lnk rq">다시 해석</button>`:''}`;const b=a.querySelector('.rq');if(b)b.onclick=()=>ask(a.dataset.q)})}
setInterval(ageTick,5000);
/* 실계통: 고장 주입은 가정으로, 구성 변경은 EMS에서만 */
const _lt2=liveTick;liveTick=function(){if(MODE!=='live')return;if(REW.on){hdrUpdate();return}
  if(M.id==='kpg193'&&(LV.tick===18||LV.tick===70)){const l=allLines(twin).find(x=>M.buses[x.f].n==='양평'&&M.buses[x.t].n==='태백'||M.buses[x.f].n==='태백'&&M.buses[x.t].n==='양평');if(l){if(LV.tick===18){twin.out[l.id]=1;LV.nextWhy='EMS: 양평–태백 765kV 1회선 개방 (계획 휴전)'}else{delete twin.out[l.id];LV.nextWhy='EMS: 양평–태백 765kV 복구'}}}
  _lt2();rwSync()};
/* 되감기 */
function rwList(){return SNAP.filter(s=>s.model===M.id&&s.kind==='정기'&&s.status!=='격리').sort((a,b)=>a.date-b.date)}
function rwSync(){const R=$('rwR');if(!R||MODE!=='live')return;const L=rwList();const atMax=+R.value>=+R.max;R.max=L.length;if(!REW.on||atMax)R.value=L.length;if(!REW.on)$('rwT').textContent=`실시간 · 기록 ${L.length}개`}
function rwInput(){const R=$('rwR'),L=rwList(),i=+R.value;if(i>=L.length){rwLive();return}const s=L[i];REW={on:true,s};twin=cloneS(s.state);view=null;lastScen=null;n1Cache=null;drawSLD();const m=Math.round((Date.now()-s.date)/60000);$('rwT').textContent=`${pad(s.date.getHours())}:${pad(s.date.getMinutes())} · ${m}분 전 · 이 시점 기준으로 질의 가능`;hdrUpdate()}
function rwLive(){REW={on:false,s:null};view=null;lastScen=null;liveTick();rwSync();toast('실시간으로 복귀')}
const _hu2=hdrUpdate;hdrUpdate=function(){_hu2();if(MODE==='live'&&REW.on&&REW.s&&(!view||view.live)){const tw=$('twMode'),d=REW.s.date;tw.textContent=`되감기 ${pad(d.getHours())}:${pad(d.getMinutes())}`;tw.className='twm tw-rp'}};
const _sm=switchMode;switchMode=async function(m){REW={on:false,s:null};await _sm(m);if(m==='live'&&!LV.hist2){LV.hist2=true;const n=new Date();for(let k=24;k>=1;k--){const d=new Date(n.getTime()-k*300000);snapPush('정기',`실시간 동기 ${pad(d.getHours())}:${pad(d.getMinutes())}`,liveState(d),'-',{date:d,src:'EMS 스냅샷 (모의 피드)',silent:true,sim:`${pad(d.getHours())}:${pad(d.getMinutes())} 실시간`})}SNAP.sort((a,b)=>b.date-a.date)}rwSync()};
/* 실계통 대비 표시 */
const _dbn2=drawBanner;drawBanner=function(){_dbn2();const b=$('vbanner');if(!b||!view||view.live||!view.state)return;const r0=dcpf(twin),r1=dcpf(view.state);if(!r0||!r1)return;const m=r=>Math.round(Math.max(...live(r).map(l=>ld(r,l))));
  {const old=b.querySelector('.cmpv');if(old)old.remove();const s=document.createElement('span');s.className='cmpv';s.textContent=` · 현 계통 대비 정격 초과 ${viol(r0).length}→${viol(r1).length}건 · 최대 부하율 ${m(r0)}→${m(r1)}%`;b.appendChild(s)}};
/* 예측 대 관측 */
const _wf=wireFb;wireFb=function(box,q,act){_wf(box,q,act);const ok=box.querySelector('[data-f="ok"]');if(ok&&act&&act.alt&&act.state){const f=ok.onclick;ok.onclick=e=>{f(e);predTrack(q,act)}}};
function predTrack(q,act){const r0=dcpf(act.state),r1=dcpf(act.alt.st);if(!r0||!r1)return;const v=viol(r0);if(!v.length)return;const l=v[0].l||v[0],pre=Math.round(ld(r0,l)),pred=Math.round(ld(r1,l));
  setTimeout(()=>{const obs=Math.round((pred+(Math.random()-.4)*5)*10)/10,err=Math.round((obs-pred)*10)/10;PRED.push({t:now(),q,line:lname(l),pred,obs,err,mode:MODE});
    const d=document.createElement('div');d.className='a pv';d.innerHTML=`<div class="ans"><p style="margin:0"><b>예측 대 관측</b> · ${esc(lname(l))} · 조치 전 ${pre}% → 예측 ${pred}% → 관측 <b>${obs}%</b> <span class="${Math.abs(err)<=3?'pvok':'pvng'}">(오차 ${err>0?'+':''}${err}%p)</span></p><p class="d" style="margin:4px 0 0">${MODE==='live'?'EMS 관측 (모의 피드)':'시뮬레이션 관측'} · 관제사가 EMS에서 조치했다고 가정 · 예측 정확도 지표에 누적</p></div>`;$('msgs').appendChild(d);scroll();
    snapLabel(s=>s.trigger===q&&s.kind==='판단',`관측 · 예측 오차 ${err>0?'+':''}${err}%p`);addJN('apply',`예측 대 관측: ${lname(l)} 예측 ${pred}% / 관측 ${obs}%`)},9000)}
const _kr=kpiRender;kpiRender=function(){_kr();const e=PRED.length?PRED.reduce((a,p)=>a+Math.abs(p.err),0)/PRED.length:null;TGT.pe=TGT.pe||3;const ok=e==null?null:e<=TGT.pe;
  $('kpiT').insertAdjacentHTML('beforeend',`<tr><td>조치 예측 오차 (평균)</td><td><input class="tgt" data-k="pe" type="number" value="${TGT.pe}" step="0.1">%p</td><td><b>${e==null?'-':e.toFixed(1)+'%p'}</b>${e==null?'<div class="d">적절 판정 후 관측 대기</div>':`<div class="d">${PRED.length}건</div>`}</td><td><span class="d">연결 전 · 같은 문제지로 비교 예정</span></td><td>${ok==null?'<span class="snst" style="background:var(--bg);color:var(--ink-3)">측정 전</span>':ok?'<span class="snst">충족</span>':'<span class="snst b">미달</span>'}</td></tr>`);
  $('kpiT').querySelector('[data-k="pe"]').onchange=ev=>{TGT.pe=+ev.target.value;kpiRender()}};

/* ===== 시나리오 → 문제지 편입 ===== */
const BMS=[];
const actKind=a=>(a||'').split(' ')[0];
const _so=scnOpen;scnOpen=function(){_so();document.querySelectorAll('#drBody [data-run]').forEach(b=>{const id=b.dataset.run;if(b.parentElement.querySelector('[data-bm]'))return;const has=BMS.some(x=>x.src===id);const a=document.createElement('button');a.type='button';a.className='btn';a.dataset.bm=id;a.textContent=has?'문제지에 있음':'문제지에 추가';a.disabled=has;
  a.onclick=()=>{const sc=SCN.find(x=>x.id===id);scnRunBatch([sc]);const r=sc.res;BMS.push({id:'Q-'+String(BMS.length+1).padStart(3,'0'),src:id,n:sc.n,steps:sc.steps.slice(),model:M.id,exp:{v:r.v,act:actKind(r.act),isl:r.isl,ok:r.ok},t:now()});addJN('apply',`시나리오 문항 추가: ${sc.n}`);toast('성능 평가 문제지에 추가 · 지금 결과를 정답으로 고정');scnOpen()};b.parentElement.appendChild(a)})};
function runScnBm(){const L=BMS.filter(x=>x.model===M.id);const rows=L.map(q=>{const sc={steps:q.steps};scnRunBatch([sc]);const r=sc.res,ok=r.ok===q.exp.ok&&r.isl===q.exp.isl&&r.v===q.exp.v&&actKind(r.act)===q.exp.act;return {q,r,ok}});
  $('scnT').innerHTML=rows.length?'<tr><th>문항</th><th>단계</th><th>고정된 정답</th><th>현재 엔진 결과</th><th>판정</th></tr>'+rows.map(({q,r,ok})=>`<tr><td><b>${esc(q.n)}</b><div class="d">${q.id} · ${q.t}</div></td><td><ol class="scs">${q.steps.map(s=>`<li>${esc(s)}</li>`).join('')}</ol></td><td>위반 ${q.exp.v}건 · ${q.exp.act}</td><td>위반 ${r.v}건 · ${esc(r.act)}</td><td><span class="res ${ok?'p':'f'}">${ok?'일치':'달라짐'}</span></td></tr>`).join(''):'<tr><td class="d">시나리오 문항 없음 · 관제 화면 “시나리오”에서 “문제지에 추가”</td></tr>';
  const p=rows.filter(x=>x.ok).length;$('scnP').textContent=rows.length?`${p}/${rows.length}`:'-';BM.last.scn=rows.length?`${p}/${rows.length} 일치`:'';bmRender()}
const _bmr=bmRender;bmRender=function(){_bmr();const n=BMS.filter(x=>x.model===M.id).length;$('bmSuites').insertAdjacentHTML('beforeend',`<div class="bmc"><div class="bmh"><b>시나리오 문항</b><span class="kty">시나리오 라이브러리</span></div><div class="bmn">${n}문항</div><p>관제사가 만든 시나리오를 시험 문제로 고정 · 엔진이 바뀌어도 같은 판단을 내리는지 채점</p><div class="bmf"><span>${BM.last.scn||'아직 채점 전'}</span><button class="btn" type="button" id="bmScn">채점</button></div></div>`);$('bmScn').onclick=()=>{runScnBm();$('scnPanel').scrollIntoView({behavior:'smooth'})}};

/* ===== 지식 갱신 · 재점검 ===== */
const KUPD=[];
function kuAdd(src,items){items.forEach(([t,why])=>KUPD.unshift({id:'RC-'+String(KUPD.length+1).padStart(3,'0'),src,t,why,st:'대기',at:now()}));kuRender()}
function docRevise(id){const d=kbDoc(id);d.rev=(d.rev||0)+1;const t=new Date();const v=`${t.getFullYear()}.${t.getMonth()+1}.${t.getDate()} 개정 반영 (시연 ${d.rev}차)`;d.hist=(d.hist||[]).concat([d.ver2||d.ver]);d.ver2=v;
  const cons=KCON.filter(c=>c.docs.includes(id)),uk=UKB.filter(u=>u.rules.includes(id)&&u.trust!=='개인 메모'),secs=KSEC.filter(s=>s.doc===id);
  const items=[[`지식 베이스 색인 · ${d.t}`,'새 버전 본문 재색인 · 이전 버전은 “개정 전”으로 보관'],...secs.map(s=>[`조항 · ${s.t}`,'조항 내용 변경 여부 확인']),...cons.map(c=>[`업무 연결 · ${c.t}`,'이 규정을 근거로 쓰는 답변 형식 점검']),...uk.map(u=>[`사용자 지식 · ${u.t}`,'규정 근거가 바뀌어 내용 재검토 필요']),
    ...regItems().slice(0,3).map(r=>[`재발 방지 문항 · ${r.q}`,'정답(기대 행동)이 새 규정과 맞는지 확인']),...BMS.filter(x=>x.model===M.id).slice(0,3).map(q=>[`시나리오 문항 · ${q.n}`,'고정된 정답 재확인'])];
  kuAdd(`규정 개정 · ${d.t}`,items);uk.forEach(u=>u.trust='팀 검토 (재검토)');addJN('apply',`규정 개정 반영: ${d.t} → 재점검 ${items.length}건`);toast(`개정 반영 · 재점검 대상 ${items.length}건`);kbRender()}
function ukRevise(id){const u=UKB.find(x=>x.id===id);const v=prompt('교훈 · 내용 수정',u.c.l);if(!v||v===u.c.l)return;u.hist=(u.hist||[]).concat([{l:u.c.l,trust:u.trust,at:now()}]);u.c.l=v;u.b=[u.c.s,u.c.a,u.c.r,v].filter(x=>x&&x!=='-').join('. ');const was=u.trust;u.trust='팀 검토 (재검토)';
  kuAdd(`사용자 지식 수정 · ${u.t}`,[[`사용자 지식 · ${u.t}`,`${was} → 재검토 · 수정 전 내용 보관`],['답변 인용',`이 지식을 “관제사 메모”로 붙이는 답변 (${u.tags.map(nameOf).join(', ')||'설비 없음'}) 확인`],...regItems().filter(r=>u.tags.some(t=>r.q.includes(M.buses[t].n))).map(r=>[`재발 방지 문항 · ${r.q}`,'정답 재확인'])]);addJN('apply',`사용자 지식 수정: ${u.t}`);kbRender()}
function kuRender(){const el=$('kuT');if(!el)return;const n=KUPD.filter(x=>x.st==='대기').length;$('kuN').textContent=n?`대기 ${n}건`:'대기 없음';
  el.innerHTML=KUPD.length?'<tr><th>대상</th><th>점검 내용</th><th>발생</th><th>상태</th></tr>'+KUPD.slice(0,40).map(x=>`<tr><td><b>${esc(x.t)}</b><div class="d">${x.id}</div></td><td>${esc(x.why)}</td><td class="d">${esc(x.src)}<br>${x.at}</td><td>${x.st==='대기'?`<button class="btn" type="button" data-ku="${x.id}">확인 완료</button>`:`<span class="snst">확인 완료</span><div class="d">${x.by}</div>`}</td></tr>`).join(''):'<tr><td class="d">재점검 대상 없음 · 문서 상세의 “개정 반영”이나 사용자 지식 “내용 수정” 시 생성</td></tr>';
  el.querySelectorAll('[data-ku]').forEach(b=>b.onclick=()=>{const x=KUPD.find(y=>y.id===b.dataset.ku);x.st='완료';x.by=`${ROLE} · ${now()}`;kuRender()})}
const _kbr=kbRender;kbRender=function(){_kbr();const d=kbDoc(KSEL);const bx=$('kbDet').querySelector('.kdbtn');if(bx&&d&&!bx.querySelector('[data-rv]')){const b=document.createElement('button');b.className='btn';b.type='button';b.dataset.rv=d.id;b.textContent='개정 반영 (시연)';b.onclick=()=>docRevise(d.id);bx.appendChild(b);if(d.hist&&d.hist.length)bx.insertAdjacentHTML('afterend',`<p class="d" style="margin-top:8px">이전 버전: ${d.hist.map(esc).join(' → ')}</p>`)}kuRender()};
const _ukr=ukRender;ukRender=function(){_ukr();document.querySelectorAll('#ukList .uk').forEach(c=>{const g=c.querySelector('[data-kg^="note:"]');if(!g)return;const id=g.dataset.kg.slice(5),u=UKB.find(x=>x.id===id);if(!u||u.trust==='개인 메모'||c.querySelector('[data-rv2]'))return;const b=document.createElement('button');b.className='btn';b.type='button';b.dataset.rv2=id;b.textContent='내용 수정';b.onclick=()=>ukRevise(id);c.querySelector('.ukb').appendChild(b);if(u.hist)c.querySelector('.kmeta').insertAdjacentHTML('beforeend',` · 수정 ${u.hist.length}회`)})};
/* ===== 데이터 요구 명세 (초안) ===== */
const DSPEC=[['계통 상태 (상태추정 결과)','EMS → 전용 인메모리 DB','모선 전압·위상, 지로 P·Q, 발전기 P·Q, 모선 부하, 추정 수렴 여부','1~5초 · 지연 5초 이내','스냅샷 수 MB · 5분 저장 시 하루 1~2GB','원본 1년 · 표준화 3년','트윈 · 해석 · 대시보드 · 되감기 · 예측 대 관측','협의 중'],
 ['연결 상태','EMS 토폴로지','차단기·단로기 개폐, 모선 구성, 계통 섬','변경 시 즉시','작음','상태와 함께','해석 · 연결 그래프 · 구성 버전','협의 중'],
 ['원시 측정값','SCADA','측정점별 값 · 품질 플래그 · 시각','2~4초','상태 데이터의 수 배','원본 6개월','타 과제 AI 상태추정 학습 · 불량 측정 분석','미정'],
 ['설비 모델 (CIM)','CIM 계통 모델','설비 ID(mRID), 정격, 임피던스, 연결 관계, 지명','변경 시','수십 MB','버전 전체','변환 · ID 매핑 · 지식그래프 · 검색','협의 중'],
 ['EMS 경보 · 이벤트','EMS','시각, 설비 ID, 종류, 등급, 해제 시각','즉시','하루 수천 건','3년','경보 해석 · 사건 분석 보고서','미정'],
 ['정비 · 휴전 계획','계획 시스템','대상 설비, 기간, 종류(정비·휴전·출력 제약)','일 1회 + 변경 시','작음','3년','대안에서 제외 · 휴전 검토','미정'],
 ['발전 계획 · 급전 지시','급전 시스템','발전기별 계획·지시 출력, 시각','지시 시 · 5분','작음','3년','예측 대 관측 · 예방 조치','미정'],
 ['수요 · 재생 예측','예측 시스템','시간대별 수요·태양광·풍력 예측','15분 · 24시간 범위','작음','1년','선제 경보 · 시간대별 해석','미정'],
 ['규정 · 절차 문서','KPX 게시판 · 내부 절차','문서, 버전, 시행일, 조항','개정 시','수백 MB','전 버전','지식 베이스 · 답변 근거','협의 중'],
 ['관제 활동 기록','플랫폼 내부 생성','질의, 툴 호출, 판정(적절·부적절), 사유, 작업 공간','즉시','하루 수 MB','3년','재발 방지 문항 · 학습 · 보고서','확정']];
function dsLoad(){try{return JSON.parse(localStorage.getItem('tb-dspec-v1')||'{}')}catch(e){return {}}}
function dsSave(o){try{localStorage.setItem('tb-dspec-v1',JSON.stringify(o))}catch(e){}}
function dsRender(){const el=$('dsT');if(!el)return;const S=dsLoad();const st=i=>(S[i]&&S[i].st)||DSPEC[i][7];const cnt=k=>DSPEC.filter((_,i)=>st(i)===k).length;
  $('dsSum').innerHTML=`<span class="dss ok">확정 ${cnt('확정')}</span><span class="dss w">협의 중 ${cnt('협의 중')}</span><span class="dss b">미정 ${cnt('미정')}</span><span class="d">먼저 정할 것: 계통 상태 필드·주기, 설비 ID 체계(CIM mRID), 경보 이벤트 형식</span>`;
  el.innerHTML='<tr><th>데이터</th><th>출처</th><th>주요 필드</th><th>주기 · 지연</th><th>규모 (추정)</th><th>보존 (안)</th><th>쓰는 기능</th><th>상태</th><th>협의 메모</th></tr>'+DSPEC.map((r,i)=>`<tr><td><b>${r[0]}</b></td><td>${r[1]}</td><td class="dsf">${r[2]}</td><td>${r[3]}</td><td>${r[4]}</td><td>${r[5]}</td><td class="dsf">${r[6]}</td><td><select data-ds="${i}" class="dssel s-${st(i).replace(/\s/g,'')}"><option${st(i)==='확정'?' selected':''}>확정</option><option${st(i)==='협의 중'?' selected':''}>협의 중</option><option${st(i)==='미정'?' selected':''}>미정</option></select></td><td><input data-dm="${i}" value="${esc((S[i]&&S[i].m)||'')}" placeholder="메모"></td></tr>`).join('');
  el.querySelectorAll('[data-ds]').forEach(s=>s.onchange=()=>{const o=dsLoad();o[s.dataset.ds]=Object.assign(o[s.dataset.ds]||{},{st:s.value});dsSave(o);dsRender()});
  el.querySelectorAll('[data-dm]').forEach(s=>s.onchange=()=>{const o=dsLoad();o[s.dataset.dm]=Object.assign(o[s.dataset.dm]||{},{m:s.value});dsSave(o)})}

/* ===== 시점 질의: "10분 전", "30분 전 상태에서", "14:20에" ===== */
function timeParse(q){let m,t=null,src='';const n=new Date();
  if(m=q.match(/(\d+)\s*분\s*전/)){t=new Date(n.getTime()-(+m[1])*60000);src=m[0]}
  else if(m=q.match(/(\d+)\s*시간\s*전/)){t=new Date(n.getTime()-(+m[1])*3600000);src=m[0]}
  else if(m=q.match(/(\d{1,2})\s*(?::|시)\s*(\d{1,2})?\s*분?/)){if(+m[1]<24){t=new Date(n);t.setHours(+m[1],m[2]?+m[2]:0,0,0);if(t>n)t.setDate(t.getDate()-1);src=m[0]}}
  if(!t)return null;let rest=q.replace(src,'').replace(/^\s*(에|의|기준|기준으로|당시|때|상태에서|시점에서|에는)\s*/,'').replace(/^(에|의|기준으로|당시에?|때에?|상태에서|시점에서)\s*/,'').trim();
  if(/^(선로\s*)?(과부하|부하율|위반|정격)/.test(rest)||/(과부하|정격 초과|위반).*(있었|있어|어땠|였)/.test(rest))rest='현재 과부하 설비 있어?';
  else if(/N-?1/i.test(rest)&&!/고장|트립|휴전|늘|줄/.test(rest))rest='N-1 상정사고 전체 점검해줘';
  else if(/^(상태|계통|어땠)/.test(rest)||!rest)rest='현재 과부하 설비 있어?';
  return {t,rest,src}}
function sysMsg(t){const d=document.createElement('div');d.className='sys';d.textContent=t;$('msgs').appendChild(d);scroll()}
const _ask3=ask;ask=async function(q){const q0=q;q=q.replace(/과부화/g,'과부하');const tm=/예상|예방|예보|이후|뒤에/.test(q)?null:timeParse(q);if(!tm)return _ask3(q);if(MODE!=='live'&&!/전/.test(tm.src))return _ask3(q);
  const fixQ=()=>{const qs=[...$('msgs').querySelectorAll('.q')];const b=qs[qs.length-1];if(b)b.textContent=q0};
  if(MODE!=='live'){sysMsg('시점 질의는 실계통 연동 모드의 되감기 기록에서 동작 · 공개 계통은 “하루 흐름 재생”의 시각 사용');const r0=await _ask3(tm.rest);fixQ();return r0}
  const L=rwList();if(!L.length){sysMsg('되감기 기록 없음');return}let bi=0,bd=Infinity;L.forEach((s,i)=>{const d=Math.abs(s.date-tm.t);if(d<bd){bd=d;bi=i}});
  if(bd>15*60000){sysMsg(`${pad(tm.t.getHours())}:${pad(tm.t.getMinutes())} 근처 기록 없음 · 가장 가까운 기록은 ${Math.round(bd/60000)}분 차이`);return}
  const R=$('rwR');R.value=bi;rwInput();const s=L[bi];sysMsg(`되감기 · ${pad(s.date.getHours())}:${pad(s.date.getMinutes())} 기록 기준 (요청 “${tm.src.trim()}” · 차이 ${Math.round(bd/60000)}분) · 해석: ${tm.rest}`);
  const res=await _ask3(tm.rest);fixQ();const as=[...$('msgs').querySelectorAll('.a .ans')];const last=as[as.length-1];if(last){const p=last.querySelector('p');if(p&&p.innerHTML.startsWith('현재'))p.innerHTML=`${pad(s.date.getHours())}:${pad(s.date.getMinutes())} 시점에`+p.innerHTML.slice(2)}return res};
const _sm2=switchMode;switchMode=async function(m){await _sm2(m);if(m==='live')setChips(['10분 전 과부하 설비 있었어?','30분 전 상태에서 양평–태백 선로가 고장 나면?','1시간 전 N-1 점검해줘','현재 과부하 설비 있어?'])};

/* ===== "지금" = 실시간 · 두 시점 비교 ===== */
TOOLS.push(['compare_snapshots','1.0.0','두 시점 계통 상태 비교 (과부하 지속·해소·신규)','r','동기']);SCH.compare_snapshots='snapshot_a, snapshot_b → 설비별 부하율 변화[]';
function cmpAnswer(q0,past){const pd=past.date,ps=`${pad(pd.getHours())}:${pad(pd.getMinutes())}`;
  const qd=document.createElement('div');qd.className='q';qd.textContent=q0;$('msgs').appendChild(qd);addJN('query',q0);curTrace='T-'+String(++traceSeq).padStart(3,'0');GWQ[curTrace]=q0;
  const r0=dcpf(past.state),r1=dcpf(twin);logCall('get_measurements',`snapshot ${past.id}`,'past state','0.01s');logCall('get_measurements','live','current state','0.01s');
  const vid=x=>(x.l||x).id,v0=new Map((r0?viol(r0):[]).map(x=>[vid(x),x])),v1=new Map((r1?viol(r1):[]).map(x=>[vid(x),x]));const ids=[...new Set([...v0.keys(),...v1.keys()])];
  const L=ids.map(id=>{const l=allLines(twin).find(x=>x.id===id)||allLines(past.state).find(x=>x.id===id);const a=r0&&!isOut(r0,l)?Math.round(ld(r0,l)):null,b=r1&&!isOut(r1,l)?Math.round(ld(r1,l)):null;return {l,a,b,k:v0.has(id)&&v1.has(id)?'지속':v0.has(id)?'해소':'신규'}});
  logCall('compare_snapshots',`${past.id} vs live`,`지속 ${L.filter(x=>x.k==='지속').length} · 해소 ${L.filter(x=>x.k==='해소').length} · 신규 ${L.filter(x=>x.k==='신규').length}`,'0.01s');
  const c=k=>L.filter(x=>x.k===k);const sum=!L.length?`${ps} 기록과 지금 모두 과부하 설비가 없습니다.`:c('지속').length===L.length?`${ps}에 과부하였던 설비가 지금도 그대로 과부하입니다 (${c('지속').map(x=>`${lname(x.l)} ${x.a}% → ${x.b}%`).join(', ')}).`:`${ps} 기록 대비 지속 ${c('지속').length}건 · 해소 ${c('해소').length}건 · 신규 ${c('신규').length}건입니다.`;
  const pre=/해소/.test(q0)&&L.length?(c('지속').length?`아직 해소되지 않았습니다 (${c('지속').length}건 지속). `:'모두 해소됐습니다. '):/동일|같/.test(q0)&&L.length?(c('지속').length===L.length?'네, 동일합니다. ':'아니요, 달라졌습니다. '):'';
  const d=document.createElement('div');d.className='a';d.innerHTML=`<ul class="steps"><li><i class="ok"></i><span>질의 분류 · 두 시점 비교</span><em>0.01s</em></li><li><i class="ok"></i><span>상태 조회 <code>get_measurements</code> × 2</span><em>0.01s</em></li><li><i class="ok"></i><span>비교 <code>compare_snapshots</code></span><em>0.01s</em></li></ul><div class="ans"><p>${pre}${sum}</p>${L.length?`<div class="lab">과부하 설비 비교</div><div class="tw"><table class="tbl"><tr><th>선로</th><th>${ps}</th><th>지금</th><th>판정</th></tr>${L.map(x=>`<tr><td>${lname(x.l)}${KV(x.l.kv)}</td><td>${x.a==null?'탈락':x.a+'%'}</td><td>${x.b==null?'탈락':x.b+'%'}</td><td><span class="cmpk k-${x.k}">${x.k}</span></td></tr>`).join('')}</table></div>`:''}<div class="engtag">${engTag()}</div></div>`;$('msgs').appendChild(d);scroll();
  snapPush('판단',q0,twin,curTrace);setChips(L.length?[`${lname(L[0].l).replace(' ','')} 선로 과부하 어떻게 해소해?`.replace('선로 선로','선로'),'예방 조치 추천해줘','30분 전과 지금 비교해줘']:['N-1 상정사고 전체 점검해줘'])}
const _ask4=ask;ask=async function(q){if(MODE!=='live')return _ask4(q);const q1=q.replace(/과부화/g,'과부하'),nowRef=/지금|현재|실시간/.test(q1),cmp=/동일|같|비교|차이|달라|변했|해소|그대로|아직/.test(q1),tm=/예상|예방|예보|이후|뒤에/.test(q1)?null:timeParse(q1);
  if(cmp&&(nowRef||/해소|아직|그대로/.test(q1))&&(tm||REW.on)){let past=null;if(tm){const Lr=rwList();let bd=Infinity;Lr.forEach(s=>{const d=Math.abs(s.date-tm.t);if(d<bd){bd=d;past=s}});if(bd>15*60000)past=null}else past=REW.s;
    if(past){if(REW.on){REW={on:false,s:null};view=null;lastScen=null;liveTick();rwSync()}sysMsg(`비교 · ${pad(past.date.getHours())}:${pad(past.date.getMinutes())} 기록 ↔ 실시간`);cmpAnswer(q,past);return}}
  if(nowRef&&!tm&&REW.on){REW={on:false,s:null};view=null;lastScen=null;liveTick();rwSync();sysMsg('“지금” → 되감기 해제 · 실시간 기준');}
  return _ask4(q)};
/* 현재 과부하 해소 방안 */
function remedyAnswer(q0){const qd=document.createElement('div');qd.className='q';qd.textContent=q0;$('msgs').appendChild(qd);addJN('query',q0);curTrace='T-'+String(++traceSeq).padStart(3,'0');GWQ[curTrace]=q0;
  const base=cloneS(twin),r0=dcpf(base),v=r0?viol(r0):[];logCall('get_measurements','live','current state','0.01s');let body='',alt=null;
  if(!v.length)body='<p>현재 정격을 넘는 설비가 없어 해소 조치가 필요 없습니다.</p>';
  else{const rd=redispatch(base);logCall('run_redispatch_opf',`${v.length} overloads`,rd?`${fmt(rd.total)}MW`:'infeasible','0.04s');
    if(rd){alt={st:rd.state,h:`발전 재급전 ${fmt(rd.total)}MW`};const r1=dcpf(rd.state),G0=gensOf(base),G1=gensOf(rd.state),mv=G1.map((g,i)=>({g,d:g.p-(G0[i]?G0[i].p:0)})).filter(x=>Math.abs(x.d)>5).sort((a,b)=>Math.abs(b.d)-Math.abs(a.d)).slice(0,6);
      body=`<p>현재 과부하 ${v.length}건은 발전 재급전 ${fmt(rd.total)}MW로 해소됩니다. 실제 조치는 EMS · 발전사 연락으로 진행하세요.</p><div class="lab">대상 선로</div><div class="tw"><table class="tbl"><tr><th>선로</th><th>지금</th><th>조치 후</th></tr>${v.map(x=>{const l=x.l||x;return `<tr><td>${lname(l)}${KV(l.kv)}</td><td>${Math.round(ld(r0,l))}%</td><td>${Math.round(ld(r1,l))}%</td></tr>`}).join('')}</table></div><div class="lab">재급전 안</div><div class="tw"><table class="tbl"><tr><th>발전기</th><th>조정</th></tr>${mv.map(x=>`<tr><td>${esc(x.g.name)}${maintGen(x.g)?' (정비·제약)':''}</td><td>${x.d>0?'증발 +':'감발 −'}${fmt(Math.abs(x.d))}MW</td></tr>`).join('')}</table></div>`}
    else{const sh=shed(base);if(sh){alt={st:sh.state,h:`부하 차단 ${fmt(sh.total)}MW`};const r1=dcpf(sh.state);body=`<p>현재 과부하 ${v.length}건은 재급전만으로는 해소되지 않고${maintCount()?` (정비·제약 발전기 ${maintCount()}기 제외)`:''}, 부하 차단 ${fmt(sh.total)}MW가 필요합니다. 차단은 책임자 판단과 EMS 조치로 진행하세요.</p><div class="tw"><table class="tbl"><tr><th>선로</th><th>지금</th><th>차단 후</th></tr>${v.map(x=>{const l=x.l||x;return `<tr><td>${lname(l)}${KV(l.kv)}</td><td>${Math.round(ld(r0,l))}%</td><td>${r1?Math.round(ld(r1,l)):'-'}%</td></tr>`}).join('')}</table></div>`}else body=`<p>현재 과부하 ${v.length}건은 재급전만으로 해소되지 않습니다${maintCount()?` (정비·제약 발전기 ${maintCount()}기 제외 상태)`:''}. 계통 재구성이나 부하 차단 검토가 필요해 관제사 판단으로 넘깁니다.</p>`}}
  const d=document.createElement('div');d.className='a';d.innerHTML=`<ul class="steps"><li><i class="ok"></i><span>질의 분류 · 현재 과부하 해소</span><em>0.01s</em></li><li><i class="ok"></i><span>상태 조회 <code>get_measurements</code></span><em>0.01s</em></li><li><i class="ok"></i><span>재급전 계산 <code>run_redispatch_opf</code></span><em>0.04s</em></li></ul><div class="ans">${body}${fbHtml(false,alt,false)}</div>`;$('msgs').appendChild(d);
  snapPush('판단',q0,base,curTrace);wireFb(d,q0,{evs:[],state:base,alt});scroll();setChips(['N-1 상정사고 전체 점검해줘','예방 조치 추천해줘'])}
const _ask5=ask;ask=async function(q){const q1=q.replace(/과부화/g,'과부하');if(/(과부하|부하율|정격)/.test(q1)&&/(해소|해결|조치|낮추|줄이)/.test(q1)&&!/고장|탈락|트립|휴전|신설|늘|줄면|올리면|올려|전\b|분 전|시간 전|지금과|비교/.test(q1)&&!busy){remedyAnswer(q);return}return _ask5(q)};

/* ===== 디자인 정리: 답변 구조 · 등장 효과 ===== */
function tidyAns(a){if(a.dataset.tidy)return;const ans=a.querySelector('.ans');const steps=a.querySelector(':scope > ul.steps');if(!ans)return;
  const extra=[...ans.querySelectorAll(':scope > .cite, :scope > .badges')];if(!steps&&!extra.length){a.dataset.tidy=1;return}
  const ms=steps?[...steps.querySelectorAll('.ms,em')].reduce((s,e)=>s+(parseFloat(e.textContent)||0),0):0;const n=steps?steps.children.length:0,c=ans.querySelectorAll(':scope > .cite').length;
  const d=document.createElement('details');d.className='more';d.innerHTML=`<summary>${n?`처리 ${n}단계 · ${ms.toFixed(2)}s`:''}${n&&c?' · ':''}${c?`근거 ${c}건`:''}</summary>`;
  if(steps)d.appendChild(steps);extra.forEach(x=>d.appendChild(x));const anchor=ans.querySelector(':scope > .vrow')||ans.querySelector(':scope > .engtag')||ans.querySelector(':scope > .fb');ans.insertBefore(d,anchor);a.dataset.tidy=1}
function watchA(n){if(n.querySelector('.ans .engtag')){tidyAns(n);return}const o=new MutationObserver(()=>{if(n.querySelector('.ans .engtag')){o.disconnect();setTimeout(()=>tidyAns(n),30)}});o.observe(n,{subtree:true,childList:true})}
new MutationObserver(ms=>{ms.forEach(m=>m.addedNodes.forEach(n=>{if(n.nodeType===1&&n.classList.contains('a')&&!n.classList.contains('pv'))watchA(n)}))}).observe(document.getElementById('msgs'),{childList:true});
pendRender();

const _bmr2=bmRender;bmRender=function(){_bmr2();if(typeof kpiRender==='function'&&$('kpiT'))kpiRender()};
const _rn=typeof runN1==='function'?runN1:null;if(_rn)runN1=async function(){const r=await _rn.apply(this,arguments);bmRender();return r};
const _tlo=tlOpen;tlOpen=function(){view=null;lastScen=null;return _tlo.apply(this,arguments)};

const _hu3=hdrUpdate;hdrUpdate=function(){_hu3();const t=$('twMode');if(!t)return;const x=t.textContent;t.title=/되감기|재현/.test(x)?'이력 경로: 데이터 레이크 기록에서 불러온 과거 상태':/가정/.test(x)?'가정: 내 작업 공간의 시나리오 (실계통 영향 없음)':MODE==='live'?'실시간 경로: 전용 인메모리 DB → 디지털 트윈':'공개 계통 기준 운전점'};

(()=>{const meta=document.querySelector('.top .meta'),r=document.querySelector('.ctxr');if(meta&&r){[...meta.children].reverse().forEach(c=>r.prepend(c));meta.remove();const sc=[...r.children].find(c=>/스냅샷/.test(c.textContent));if(sc)sc.classList.add('snapclk')}})();

/* 역할별 메뉴 */
const RMENU={'관제사':['ops','db','kb','rp'],'책임자':['ops','db','kb','rp','sn','gw','ev','ml'],'연구자':['ops','db','kb','rp','sn','gw','ev','ml']};
function applyMenus(){const all=$('menuAll').checked,ok=new Set(all?RMENU['연구자']:RMENU[ROLE]||RMENU['연구자']);
  document.querySelectorAll('nav button[data-v]').forEach(b=>b.hidden=!ok.has(b.dataset.v));document.querySelectorAll('nav .ng').forEach(g=>g.hidden=![...g.querySelectorAll('button')].some(b=>!b.hidden));
  const cur=document.querySelector('nav button[aria-selected="true"]');if(cur&&cur.hidden)document.querySelector('nav button[data-v="ops"]').click()}
const _sr=setRole;setRole=function(r){_sr(r);applyMenus()};

(()=>{const r=document.querySelector('.ctxr');if(!r)return;const mk=()=>{const s=document.createElement('span');s.className='ctxdiv';return s};const ml=r.querySelector('.mall'),mt=r.querySelector('.mtog');if(ml)r.insertBefore(mk(),ml);if(mt)r.insertBefore(mk(),mt)})();
/* 상태 줄 자동 맞춤: 넘치면 덜 중요한 항목부터 숨김 */
function fitCtx(){const ci=$('ctxInfo');if(!ci)return;const ms=[...ci.querySelectorAll('.ctxm,.ctxs')];ms.forEach(m=>m.hidden=false);
  const order=ms.filter(m=>m.classList.contains('ctxm')).slice(0,-1).reverse().concat(ms.filter(m=>m.classList.contains('ctxs')));let i=0;while(ci.scrollWidth>ci.clientWidth+1&&i<order.length){order[i++].hidden=true}}
const _hu4=hdrUpdate;hdrUpdate=function(){_hu4();fitCtx()};window.addEventListener('resize',()=>{clearTimeout(window._fc);window._fc=setTimeout(fitCtx,80)});

(()=>{const md=$('modelDesc');if(!md)return;new MutationObserver(()=>{md.title=md.textContent}).observe(md,{childList:true,characterData:true,subtree:true});const s=$('sld');if(s&&!s.querySelector('title'))s.setAttribute('aria-label','계통도 · 휠·드래그로 확대·이동 · 마우스 오버로 상세')})();

/* ===== 3~7단계 구조 정리 ===== */
/* 3. 데이터 연계 탭 분리 */
(()=>{const dl=$('v-dl');if(!dl)return;const mv=el=>{if(el)dl.appendChild(el)};mv($('dsT')&&$('dsT').closest('.panel'));mv($('planBox'));mv($('liveSrc'))})();
const _lsr3=liveSrcRender;liveSrcRender=function(){const box=$('liveSrc');if(!box)return;if(MODE==='live'){_lsr3();box.hidden=false;return}box.hidden=false;pubSrcRender()};
function pubSrcRender(){const r=dcpf(twin),cim=(LV.cim[LV.cim.length-1]||{v:'1.0.0'}).v;
  const S=[['KPG 193 계통 모델','agm-center/kpg-testgrid · ODbL 1.0','로드됨',[['모선',M.buses.length],['선로 경로',allLines(twin).length],['발전기',M.gens.length],['형식','MATPOWER 변환']]],['하루 운전 프로필','2024-04-19 실측 기반','로드됨',[['시간대','24'],['수요 최대',fmt(Math.max(...(MODELS.kpg193.profile.hours.map(h=>h.dem))))+'MW'],['태양광','포함'],['용도','하루 흐름 재생']]],
    ['계획 시스템 (예시)','정비 · 휴전 · 출력 제약','예시 데이터',[['등록 계획',MAINT.length+'건'],['계산 제외',maintCount()+'기'],['출처','시연용'],['반영',PLAN_ON?'켬':'끔']]],['실시간 피드','EMS · SCADA','실계통 연동 모드',[['상태','미사용'],['전환','상단 토글'],['주기','3초 (모의)'],['경로','인메모리 DB']]]];
  $('srcCards').innerHTML=S.map(([n,d,st,kv])=>`<div class="src2 ${st==='미사용'?'':''}"><div class="s2h"><span class="s2d ${st==='실계통 연동 모드'?'off':''}"></span><b>${n}</b><span class="s2s ${st==='실계통 연동 모드'?'off':''}">${st}</span></div><p>${d} · <em>공개 계통</em></p><div class="s2k">${kv.map(([k,v])=>`<div><span>${k}</span><b>${v}</b></div>`).join('')}</div></div>`).join('');
  const ln=allLines(twin).slice(0,4),gs=M.gens.filter((g,i)=>twin.gen[i].on).slice(0,2);
  $('srcMap').innerHTML='<tr><th>원본 필드 (KPG CSV)</th><th>표준 필드 (CIM)</th><th>현재 값</th></tr>'+[...ln.map(l=>[`branch.csv · ${M.buses[l.f].n}→${M.buses[l.t].n}`,`${l.eq} · P_from`,r?Math.round(r.fl[l.id])+' MW':'-']),...gs.map(g=>[`gen.csv · ${g.name}`,`${g.id} · P_gen`,Math.round(twin.gen[M.gens.indexOf(g)].p)+' MW'])].map(x=>`<tr><td><code>${esc(x[0])}</code></td><td><code>${x[1]}</code></td><td>${x[2]}</td></tr>`).join('');
  $('cimList').innerHTML=`<div class="cimr"><b>v${cim}</b><span>KPG v2.0</span><em>공개 계통 모델 · 실계통 연동 시 EMS 구성 변경으로 갱신</em><code>${topoVer(twin)}</code></div>`}
document.querySelector('nav button[data-v="dl"]').addEventListener('click',()=>{dsRender();planRender();liveSrcRender()});
RMENU['책임자']=RMENU['연구자']=['ops','db','kb','rp','dl','sn','gw','ev','ml'];
/* 4. 대시보드 = 상황판, 테스트베드 상태는 게이트웨이로 */
(()=>{const p=$('dPlat')&&$('dPlat').closest('.panel'),gw=$('v-gw');if(p&&gw){const h=p.querySelector('h2');if(h)h.textContent='테스트베드 상태';const s=p.querySelector('.sub');if(s)s.textContent='수집 · 성능 평가 · 학습 · 게이트웨이 · 운영 모델 버전';const vh=gw.querySelector('.vhead');vh?vh.after(p):gw.prepend(p)}})();
const _dr4=dashRender;dashRender=function(){_dr4();const n=$('dTrendNote');if(n&&MODE==='live'&&!n.querySelector('.trt'))n.insertAdjacentHTML('beforeend',` · <span class="trt ${LV.trust<70?'w':''}">트윈 신뢰도 ${LV.trust}%</span>`)};
document.querySelector('nav button[data-v="gw"]').addEventListener('click',()=>{try{dashRender()}catch(e){}});
/* 5. 학습 범위: 본 과제 / 타 과제 */
TASKS.filter(t=>/^tr_(fc|rd)$/.test(t.id)).forEach(t=>{t.ti=MLM[t.id.slice(3)].short+' (소유 과제)';t.full=`소유 과제 학습 · ${MLM[t.id.slice(3)].name}`;t.desc='소유 과제가 본 과제의 보강 데이터로 학습 · 본 과제는 데이터와 평가만 제공';t.tool='소유 과제 학습 환경'});
TASKS.filter(t=>/^ds_(fc|rd)$/.test(t.id)).forEach(t=>{t.full='보강 데이터 제공 · '+MLM[t.id.slice(3)].name;t.desc='부적절 판정 시점 스냅샷 · 정답 재계산 데이터를 소유 과제에 제공'});
/* 6. 관제사 작성 문항 */
const OPQ=[['지금 넘치는 선로 있나?','상태 조회'],['현재 과부하 걸린 데 알려줘','상태 조회'],['과부화 있음?','상태 조회'],['서천 군산 선로 떨어지면 어떻게 돼','고장 해석'],['양평-태백 765 한 회선 나가면?','고장 해석'],['신한울 1호기 트립 시 영향은?','고장 해석'],['당진화력 3호기 세우면?','고장 해석'],['강남 부하 20% 증가 시 문제 없나','고장 해석'],['서울 부하 두배면?','고장 해석'],['보령 출력 300MW 줄이면','고장 해석'],['N-1 돌려줘','N-1 점검'],['상정고장 전체 점검 부탁','N-1 점검'],['예방 재급전 추천','예방 조치'],['미리 손써야 할 거 있어?','예방 조치'],['그거 어떻게 됐어?','되묻기'],['선로 나가면?','되묻기']].map(([q,e],i)=>({id:'OP-'+String(i+1).padStart(2,'0'),q,e,by:'관제사 (예시)'}));
const KMAP={status:'상태 조회',scen:'고장 해석',n1:'N-1 점검',prev:'예방 조치'};
function opqJudge(o){const it=parse(o.q.replace(/과부화/g,'과부하'));let got=KMAP[it.k]||'되묻기';if(got==='고장 해석'){const p=parseEvents(o.q,BASE);if(!p.evs.length||p.evs.some(e=>e.k==='amb')||p.issues.length)got='되묻기'}return {got,ok:got===o.e}}
function runOpq(){const rows=OPQ.map(o=>({o,...opqJudge(o)}));const p=rows.filter(x=>x.ok).length;BM.last.opq=`${p}/${rows.length} 통과`;BM.opq={p,n:rows.length,pct:Math.round(p/rows.length*1000)/10};
  $('opqT').innerHTML='<tr><th>문항</th><th>기대 동작</th><th>엔진 판단</th><th>판정</th></tr>'+rows.map(x=>`<tr><td><b>${esc(x.o.q)}</b><div class="d">${x.o.id} · ${x.o.by}</div></td><td>${x.o.e}</td><td>${x.got}</td><td><span class="res ${x.ok?'p':'f'}">${x.ok?'통과':'실패'}</span></td></tr>`).join('');$('opqP').textContent=`${p}/${rows.length}`;bmRender()}
function opqAdd(){const q=$('opqIn').value.trim();if(!q)return;OPQ.push({id:'OP-'+String(OPQ.length+1).padStart(2,'0'),q,e:$('opqE').value,by:ROLE});$('opqIn').value='';runOpq()}
const _bmr6=bmRender;bmRender=function(){_bmr6();if(!$('bmSuites').querySelector('.bmc.opq'))$('bmSuites').insertAdjacentHTML('afterbegin',`<div class="bmc opq"><div class="bmh"><b>관제사 작성 문항</b><span class="kty pri">주 평가</span></div><div class="bmn">${OPQ.length}문항</div><p>현장 말투 · 맞춤법 실수 · 애매한 표현 포함 · 관제사가 직접 작성 · 문제를 만든 쪽과 푸는 쪽 분리</p><div class="bmf"><span>${BM.last.opq||'아직 채점 전'}</span><button class="btn" type="button" id="bmOpq">채점</button></div></div>`);const b=$('bmOpq');if(b)b.onclick=()=>{runOpq();$('opqPanel').scrollIntoView({behavior:'smooth'})};
  const c=$('bmSuites').querySelectorAll('.bmc');c.forEach(x=>{const k=x.querySelector('.kty');if(k&&/자동 생성/.test(k.textContent))k.textContent='자동 생성 · 보조'})};
const _kr6=kpiRender;kpiRender=function(){_kr6();const o=BM.opq;TGT.opq=TGT.opq||80;const ok=o?o.pct>=TGT.opq:null;$('kpiT').querySelector('tr').insertAdjacentHTML('afterend',`<tr class="kmain"><td><b>관제사 작성 문항 통과율</b><div class="d">주 평가</div></td><td><input class="tgt" data-k="opq" type="number" value="${TGT.opq}" step="1">%</td><td><b>${o?o.pct.toFixed(1)+'%':'-'}</b>${o?`<div class="d">${o.p}/${o.n}</div>`:'<div class="d">채점 필요</div>'}</td><td><span class="d">연결 전 · 같은 문제지로 비교 예정</span></td><td>${ok==null?'<span class="snst" style="background:var(--bg);color:var(--ink-3)">측정 전</span>':ok?'<span class="snst">충족</span>':'<span class="snst b">미달</span>'}</td></tr>`);const t=$('kpiT').querySelector('[data-k="opq"]');t.onchange=()=>{TGT.opq=+t.value;kpiRender()}};
NOTES.dl=['데이터 소유 기관 · 인메모리 DB 팀과의 연계 협의 화면','데이터 요구 명세 · 소스 · 매핑 · 망 구성',['명세 미정 항목의 결정 순서','연계 시험 일정과 판정 기준','실시간 경로 지연 허용치']];

/* ===== 학습 트랙: LLM / 해석 모델 (모두 본 과제) ===== */
function retrack(){MLK.forEach(k=>{MLM[k].owner='본 과제'});MLM.se.ext=false;
  const fix=s=>String(s||'').replace(/원본 \(외부 과제 제공\)/,'초기 학습').replace(/ · 동의 대기 보류/,' · 평가 보류').replace(/ · 최적화 과제 검토 중/,' · 평가 대기').replace(/^원본$/,'초기 학습');
  MLK.forEach(k=>MLM[k].vers.forEach(v=>{v.lin=fix(v.lin);v.by=k==='llm'?'LLM 트랙':'해석 모델 트랙'}));
  TASKS.forEach(t=>{const m=t.id.match(/^(ds|tr|reg)_(\w+)$/);if(!m||m[2]==='llm')return;const M2=MLM[m[2]];
    if(m[1]==='tr'){t.ti=M2.short;t.sub='train_'+m[2];t.full='학습 · '+M2.name;t.desc='데이터 레이크 스냅샷과 기존 EMS 프로그램 정답으로 전이학습 · 재학습';t.tool='Airflow → GPU 노드'}
    if(m[1]==='ds'){t.full='데이터셋 생성 · '+M2.name;t.desc='판정 시점 스냅샷 + 기존 프로그램으로 재계산한 정답 · 시험 데이터 고정'}});
  const a=ADP.find(x=>x.id==='se');if(a){a.ext=false;a.d='해석 모델 · 학습 파이프라인 배포'}if(typeof mlAll==='function')mlAll()}
const _mi=mlInit;mlInit=function(){_mi();retrack()};
const _mf=mlFinish;mlFinish=function(){_mf();MLK.forEach(k=>MLM[k].vers.forEach(v=>{if(v.by==='본 과제')v.by=k==='llm'?'LLM 트랙':'해석 모델 트랙'}));mlReg()};

(()=>{const v=$('v-ml'),sc=v&&v.querySelector('.panel.scope'),note=v&&v.querySelector('.mlnote');if(sc&&note)note.after(sc)})();
(()=>{const k=$('mlK');if(k){const p=k.closest('.panel');if(p)p.style.display='none'}
  const ev=$('v-ev');if(!ev)return;const P=id=>{const e=$(id);return e&&e.closest('.panel')};const order=[P('opqPanel')||$('opqPanel'),$('regPanel'),$('scnPanel'),$('n1Panel'),$('qbPanel')].filter(Boolean);const anchor=$('bmSuites').closest('.panel');let cur=anchor;order.forEach(x=>{cur.after(x);cur=x});
  const q=$('qbPanel');if(q){const h=q.querySelector('h2');if(h)h.innerHTML='질의 이해 <span class="kty">자동 생성 · 보조</span>'}
  ev.querySelectorAll('.panel h2').forEach(h=>{if(h.textContent.trim()==='실행 이력')h.textContent='채점 이력'})})();

/* ===== 학습 운영 설계 (모델하우스) ===== */
const OPS={tab:'lab',lab:null,deps:[],audit:[]};const MN=k=>MLM[k].name;
function labItems(){if(!OPS.lab){const seed=[['서천–군산 선로가 고장 나면?','대안이 현장 사정에 안 맞음','rd','신서천화력 제외 후 재급전 또는 계통 재구성'],['현재 과부하 설비 있어?','질문을 잘못 이해함','llm','과부하 설비 목록 조회로 분류'],['신보령화력 2호기 트립되면?','대안이 현장 사정에 안 맞음','rd','보령화력 #5 제외'],['내일 오전 예측 맞아?','예측이 빗나감','fc',''],['아산–청양 선로가 고장 나면?','근거·설명이 부족함','llm','규정 근거 · 조항 제시']];
  OPS.lab=seed.map(([q,r,m,a],i)=>({id:'LB-'+String(i+1).padStart(3,'0'),q,r,m,a,st:i<2?'확정':'대기',by:i<2?'검토자 박OO':'',t:`10월 ${20+i}일`,ex:true}))}
  feedback.filter(f=>f.d==='부적절').forEach(f=>{if(!OPS.lab.some(x=>x.q===f.q&&x.t===f.t)){const rr=REASONS.find(x=>x.id===f.r);OPS.lab.unshift({id:'LB-'+String(OPS.lab.length+1).padStart(3,'0'),q:f.q,r:rr?rr.t:'사유 미지정',m:rr?(rr.m==='pf'?'llm':rr.m):'llm',a:'',st:'대기',by:'',t:f.t})}});return OPS.lab}
const OPSUB=[['lab','라벨링 대기함'],['sch','실행 일정'],['tch','정답 재계산'],['ds','데이터셋'],['env','학습 환경'],['crit','평가 기준표'],['shd','섀도 · 배포'],['mon','모니터링'],['aud','감사 기록']];
function opsRender(){const el=$('opsBody');if(!el)return;$('opsTabs').innerHTML=OPSUB.map(([k,t])=>`${k==='lab'?'<span class="og">운영</span>':k==='ds'?'<span class="og g2">연구</span>':''}<button type="button" class="${OPS.tab===k?'on':''}" data-o="${k}">${t}${k==='lab'?` <span class="apn ${labItems().some(x=>x.st==='대기')?'on':''}">${labItems().filter(x=>x.st==='대기').length}</span>`:''}</button>`).join('');
  $('opsTabs').querySelectorAll('[data-o]').forEach(b=>b.onclick=()=>{OPS.tab=b.dataset.o;opsRender()});el.innerHTML=({lab:opsLab,sch:opsSch,tch:opsTch,ds:opsDs,env:opsEnv,crit:opsCrit,shd:opsShd,mon:opsMon,aud:opsAud,exp:opsExp,err:opsErr}[OPS.tab])();opsWire()}
const mopt=sel=>MLK.map(k=>`<option value="${k}"${k===sel?' selected':''}>${MN(k)}</option>`).join('');
function opsLab(){const L=labItems();return `<p class="opsd">부적절 판정을 학습 재료로 바꾸는 곳 · 검토자가 원인 모델을 확정하고 정답(기대 행동)을 지정하면 해당 트랙 데이터셋에 들어감</p><div class="tw"><table class="reg"><tr><th>판정</th><th>사유</th><th>원인 모델</th><th>정답 · 기대 행동</th><th>상태</th></tr>${L.map(x=>`<tr><td><b>${esc(x.q)}</b><div class="d">${x.id} · ${x.t}${x.ex?' · 예시':''}</div></td><td>${esc(x.r)}</td><td>${x.st==='확정'?MN(x.m):`<select data-lm="${x.id}">${mopt(x.m)}</select>`}</td><td>${x.st==='확정'?esc(x.a||'-'):`<input data-la="${x.id}" value="${esc(x.a)}" placeholder="예: 정비 중 발전기 제외 후 재급전">`}</td><td>${x.st==='확정'?`<span class="snst">확정</span><div class="d">${x.by}</div>`:`<button class="btn pri" type="button" data-lc="${x.id}">확정</button>`}</td></tr>`).join('')}</table></div>`}
function opsSch(){const R=[['LLM','프롬프트 · 검색 갱신','매주 월 02:00','부적절 누적 5건','CPU','다음 11월 2일'],['LLM','파인튜닝','매월 첫째 토 01:00','관제사 작성 문항 통과율 < 80%','GPU A100 ×2 · 6시간','다음 11월 7일'],['해석 모델','예측 재학습','분기 · 계절 전환','MAPE 4주 연속 기준 초과','GPU ×1 · 2시간','트리거 대기 (현재 초과)'],['해석 모델','재급전 · 상태추정 재학습','분기','계산 정확도 < 99%','GPU ×1 · 3시간','다음 1월 5일']];
  return `<p class="opsd">트랙 · 모델별로 나뉜 파이프라인 · 정기 일정과 트리거로 자동 실행 · 실패 시 2회 재시도 후 담당자 알림</p><div class="tw"><table class="reg"><tr><th>트랙</th><th>작업</th><th>정기 일정</th><th>트리거</th><th>자원</th><th>상태</th></tr>${R.map(r=>`<tr>${r.map((c,i)=>`<td>${i===0?`<span class="kty">${c}</span>`:i===5&&/트리거/.test(c)?`<span class="snst b">${c}</span>`:c}</td>`).join('')}</tr>`).join('')}</table></div><div class="gpu"><b>GPU 예약 (학습망)</b>${['월','화','수','목','금','토','일'].map((d,i)=>`<div><span>${d}</span><i style="width:${[10,0,0,0,0,85,0][i]}%" class="${i===5?'w':''}"></i></div>`).join('')}</div>`}
function opsTch(){const J=[['SCED (EMS)','스냅샷 120건','완료','4분 12초'],['AGC 응답 (EMS)','사건 스냅샷 18건','실행 중 62%','-'],['상태추정 (EMS)','격리 객체 9건','대기','-'],['조류계산 (기존 프로그램)','시나리오 문항 6건','완료','38초']];
  return `<p class="opsd">해석 모델 트랙의 정답은 기존 EMS 프로그램이 같은 스냅샷을 다시 계산해 만듦 · 일괄 자동 실행 환경 필요</p><div class="tw"><table class="reg"><tr><th>프로그램</th><th>입력</th><th>상태</th><th>소요</th></tr>${J.map(j=>`<tr><td><b>${j[0]}</b></td><td>${j[1]}</td><td><span class="jst ${j[2]==='완료'?'ok':/실행/.test(j[2])?'run':'q'}">${j[2]}</span></td><td>${j[3]}</td></tr>`).join('')}</table></div><p class="opsw">확인 필요: EMS 프로그램 라이선스와 오프라인 일괄 실행 허용 여부 · 실행 서버 위치(학습망)</p>`}
function opsDs(){const S=MLK.map(k=>{const n=k==='llm'?2814:k==='fc'?17520:k==='rd'?12400:8760;return {k,n}});
  return `<p class="opsd">버전 관리 데이터셋 · 학습 70 / 검증 15 / 시험 15 · 계절 · 시간대 · 사고 유형 균형 · 시험 문항은 학습에서 자동 제외</p>${S.map(({k,n})=>`<div class="dsr"><div><b>${MN(k)}</b><span class="d">${fmt(n)}건 · ds-${k}-v${Math.max(...MLM[k].vers.map(v=>v.v))}</span></div><div class="dsb"><i style="width:70%" class="tr">학습</i><i style="width:15%" class="va">검증</i><i style="width:15%" class="te">시험</i></div><div class="dsc"><span>봄 24%</span><span>여름 27%</span><span>가을 25%</span><span>겨울 24%</span><span class="ok">격리 확인 ✓</span></div></div>`).join('')}<p class="opsd">격리 목록: 관제사 작성 문항 ${OPQ.length} · 재발 방지 ${regItems().length} · 시나리오 ${BMS.length} → 학습 데이터에서 제외</p>`}
function opsEnv(){return `<p class="opsd">같은 데이터 · 같은 설정이면 같은 결과 · 모든 실행은 실험 번호로 추적</p><div class="envg">${[['컨테이너 이미지','grid-train:2026.10.2'],['기반 LLM','온프레미스 32B (가정)'],['고정 시드','42'],['데이터셋','ds-llm-v5 · ds-fc-v7'],['실험 기록','MLflow · 실험 214건'],['전이학습 규칙','이전 데이터 30% 혼합 · 앞단 층 고정']].map(([a,b])=>`<div><span>${a}</span><b>${b}</b></div>`).join('')}</div>`}
function opsCrit(){const C={llm:[['관제사 작성 문항 통과율','≥ 80%'],['재발 방지 문항','100%'],['근거 없는 수치','0건'],['표본 사람 검토 (20건)','부적절 ≤ 2건'],['응답 시간 (p95)','≤ 8초']],fc:[['MAPE','≤ 4.0%'],['피크 시간 오차','≤ 5%'],['겨울 한파 구간 MAPE','≤ 6%'],['학습 범위 밖 입력 경고','100%']],rd:[['조치 성공률','≥ 92%'],['정비 · 제약 설비 사용','0건'],['재발 방지 문항','100%'],['계산 시간','≤ 3초']],se:[['잔차 RMSE','≤ 12MW'],['불량 측정 탐지율','≥ 95%'],['구성 변경 직후 성능','기준 대비 -5% 이내']]};
  return `<p class="opsd">모델별로 합의된 기준표 · 유형별(계절 · 사고 종류)로 나눠 평가 · 하나라도 미달이면 불합격</p><div class="crg">${MLK.map(k=>`<div class="crc"><b>${MN(k)}</b>${C[k].map(([a,b])=>`<div><span>${a}</span><em>${b}</em></div>`).join('')}</div>`).join('')}</div>`}
function opsShd(){const prod=MLK.map(k=>({k,v:MLM[k].vers.find(x=>x.stage==='Production'),prev:MLM[k].vers.filter(x=>x.stage==='Archived'&&x.v<(MLM[k].vers.find(y=>y.stage==='Production')||{v:0}).v).sort((a,b)=>b.v-a.v)[0]}));
  return `<p class="opsd">섀도 운영: 실제 질의에 기존 · 후보 모델 병행 7일 · 일치율 95% 미만이거나 지표 악화 시 자동 되돌리기 · 단계적 배포 10% → 50% → 100%</p><div class="shg">${[96.2,97.1,97.8,95.4,97.9,98.3,97.6].map((v,i)=>`<div><i style="height:${(v-90)*10}%" class="${v<96?'w':''}"></i><span>${i+1}일</span><em>${v}</em></div>`).join('')}</div>
   <div class="tw"><table class="reg"><tr><th>모델</th><th>운영 버전</th><th>되돌릴 버전</th><th></th></tr>${prod.map(p=>`<tr><td><b>${MN(p.k)}</b></td><td>v${p.v.v}</td><td>${p.prev?'v'+p.prev.v:'-'}</td><td>${p.prev?`<button class="btn" type="button" data-rb="${p.k}">v${p.prev.v}로 되돌리기</button>`:''}</td></tr>`).join('')}</table></div>${OPS.deps.length?`<div class="d" style="margin-top:8px">${OPS.deps.map(esc).join('<br>')}</div>`:''}`}
function opsMon(){const M2=[['LLM 부적절 비율','%',[4,5,4,6,5,7,8,9],8],['예측 MAPE','%',[3.6,3.7,3.8,3.9,4.1,4.3,4.4,4.6],4],['재급전 조치 성공률','%',[94,93,94,93,92,93,93,92],92],['입력 분포 변화 (PSI)','',[.05,.06,.05,.08,.12,.15,.18,.21],.2]];
  return `<p class="opsd">입력 분포 변화와 성능 저하를 상시 감시 · 기준선을 넘으면 담당자 알림과 재학습 트리거</p><div class="mng">${M2.map(([n,u,v,th])=>{const mx=Math.max(...v,th)*1.1,mn=Math.min(...v,th)*.9,Y=x=>40-(x-mn)/(mx-mn)*36,bad=n.includes('성공률')?v[v.length-1]<th:v[v.length-1]>th;return `<div class="mnc ${bad?'bad':''}"><div><b>${n}</b><span>${v[v.length-1]}${u}</span></div><svg viewBox="0 0 160 44"><line x1="0" x2="160" y1="${Y(th)}" y2="${Y(th)}" class="th"/><path d="${v.map((x,i)=>`${i?'L':'M'}${i*160/7} ${Y(x)}`).join(' ')}"/></svg><em>${bad?'기준 초과 · 알림 발송':'정상'}</em></div>`}).join('')}</div>`}
function opsAud(){const A=[...OPS.audit,['10월 25일','예측 v4','배포 승인','운영 책임자','평가 게이트 6/6 통과 · 섀도 7일 97.6%'],['10월 02일','재급전 v4','불합격 보관','평가 게이트','재발 방지: 2023 겨울 피크 2건 실패'],['09월 18일','LLM v4','배포 승인','운영 책임자','관제사 작성 문항 82% · 표본 검토 부적절 1건']];
  return `<p class="opsd">모델마다 어떤 데이터로 · 누가 · 어떤 시험 결과로 승인했는지 기록 · 모델 카드와 함께 보관</p><div class="tw"><table class="reg"><tr><th>일시</th><th>모델</th><th>결정</th><th>결정자</th><th>근거</th></tr>${A.map(a=>`<tr>${a.map((c,i)=>`<td>${i===2?`<span class="snst ${/불합격|되돌/.test(c)?'b':''}">${c}</span>`:esc(c)}</td>`).join('')}</tr>`).join('')}</table></div>`}
function opsWire(){const el=$('opsBody');el.querySelectorAll('[data-lc]').forEach(b=>b.onclick=()=>{const x=labItems().find(y=>y.id===b.dataset.lc),m=el.querySelector(`[data-lm="${x.id}"]`),a=el.querySelector(`[data-la="${x.id}"]`);x.m=m.value;x.a=a.value.trim();if(!x.a){toast('정답 · 기대 행동을 입력하세요');return}x.st='확정';x.by=`${ROLE} · ${now()}`;OPS.audit.unshift([now(),MN(x.m),'라벨 확정',ROLE,`${x.q} → ${x.a}`]);toast(`${MN(x.m)} 데이터셋에 추가`);opsRender()});
  el.querySelectorAll('[data-rb]').forEach(b=>b.onclick=()=>{if(ROLE!=='책임자'){toast('되돌리기: 책임자 역할 필요');return}const k=b.dataset.rb,m=MLM[k],cur=m.vers.find(v=>v.stage==='Production'),prev=m.vers.filter(v=>v.stage==='Archived'&&v.v<cur.v).sort((a,c)=>c.v-a.v)[0];cur.stage='Archived';prev.stage='Production';OPS.deps.unshift(`${now()} · ${m.name} v${cur.v} → v${prev.v} 되돌림 (${ROLE})`);OPS.audit.unshift([now(),`${m.short} v${prev.v}`,'되돌리기',ROLE,`v${cur.v}에서 복귀`]);mlReg();toast(`${m.name} v${prev.v}로 되돌림`);opsRender()})}
document.querySelector('nav button[data-v="ml"]').addEventListener('click',opsRender);

/* ===== 운영 설계 정합성 보강 ===== */
OPSUB.splice(OPSUB.length-1,0,['exp','실험 비교'],['err','오류 분석']);
const DUE=(i)=>{const d=new Date();d.setDate(d.getDate()+2-i);return `${d.getMonth()+1}/${d.getDate()}`};
function opsLab(){const L=labItems();const late=x=>x.st==='대기'&&x.due&&x.due<`${new Date().getMonth()+1}/${new Date().getDate()}`;L.forEach((x,i)=>{x.who=x.who||['검토자 박OO','검토자 김OO','검토자 이OO'][i%3];x.due=x.due||DUE(i%4)});
  return `<p class="opsd">부적절 판정을 학습 재료로 바꾸는 곳 · 담당 검토자가 기한 안에 원인 모델과 정답(기대 행동)을 확정 · 확정분만 해당 트랙 데이터셋에 들어감</p><div class="lq"><span>라벨 품질</span><b>검토자 간 일치율 92%</b><em>이중 검토 20건 기준 · 불일치 건은 책임자 최종 판정</em></div><div class="tw"><table class="reg"><tr><th>판정</th><th>사유</th><th>원인 모델</th><th>정답 · 기대 행동</th><th>담당 · 기한</th><th>상태</th></tr>${L.map(x=>`<tr><td><b>${esc(x.q)}</b><div class="d">${x.id} · ${x.t}${x.ex?' · 예시':''}</div></td><td>${esc(x.r)}</td><td>${x.st==='확정'?MN(x.m):`<select data-lm="${x.id}">${mopt(x.m)}</select>`}</td><td>${x.st==='확정'?esc(x.a||'-'):`<input data-la="${x.id}" value="${esc(x.a)}" placeholder="예: 정비 중 발전기 제외 후 재급전">`}</td><td>${x.who}<div class="d ${late(x)?'late':''}">기한 ${x.due}${late(x)?' · 지남':''}</div></td><td>${x.st==='확정'?`<span class="snst">확정</span><div class="d">${x.by}</div>`:`<button class="btn pri" type="button" data-lc="${x.id}">확정</button>`}</td></tr>`).join('')}</table></div>`}
function opsSch(){const tg=mlTriggers();const R=[['LLM','프롬프트 · 검색 갱신','매주 월 02:00','llm','CPU'],['LLM','파인튜닝','매월 첫째 토 01:00','llm','GPU A100 ×2 · 6시간'],['해석 모델','예측 재학습','분기 · 계절 전환','fc','GPU ×1 · 2시간'],['해석 모델','재급전 재학습','분기','rd','GPU ×1 · 3시간'],['해석 모델','상태추정 재학습','분기','se','GPU ×1 · 3시간']];
  return `<p class="opsd">정기 일정과 트리거로 자동 실행 · 트리거 상태는 아래 “재학습 트리거”와 같은 값 · 실패 시 2회 재시도 후 담당자 알림</p><div class="tw"><table class="reg"><tr><th>트랙</th><th>작업</th><th>정기 일정</th><th>현재 트리거</th><th>자원</th></tr>${R.map(r=>`<tr><td><span class="kty">${r[0]}</span></td><td>${r[1]}</td><td>${r[2]}</td><td>${tg[r[3]].on?`<span class="snst b">발동 · ${esc(tg[r[3]].why)}</span>`:`<span class="d">${esc(tg[r[3]].why)}</span>`}</td><td>${r[4]}</td></tr>`).join('')}</table></div><div class="gpu"><b>GPU 예약 (학습망)</b>${['월','화','수','목','금','토','일'].map((d,i)=>`<div><span>${d}</span><i style="width:${[10,0,0,0,0,85,0][i]}%" class="${i===5?'w':''}"></i></div>`).join('')}</div>`}
function opsDs(){const sn=SNAP.filter(s=>s.status!=='격리'),jd=sn.filter(s=>s.kind==='판단').length,pr=sn.filter(s=>s.kind!=='판단').length,lab=labItems().filter(x=>x.st==='확정');
  const row=(k,n,src)=>`<div class="dsr"><div><b>${MN(k)}</b><span class="d">${fmt(n)}건 · ${src}</span></div><div class="dsb"><i style="width:70%" class="tr">학습 ~ 6월</i><i style="width:15%" class="va">검증 7~8월</i><i style="width:15%" class="te">시험 9~10월</i></div><div class="dsc"><span>시간 순서 분할</span><span class="ok">격리 확인 ✓</span></div></div>`;
  return `<p class="opsd">시간 순서로 분할 (과거로 학습 · 최근으로 시험) · 무작위 분할은 인접 시각 데이터가 섞여 점수가 부풀려지므로 사용하지 않음 · 건수는 데이터 레이크와 라벨링 확정분에서 집계</p>${row('llm',jd+lab.filter(x=>x.m==='llm').length,`판단 스냅샷 ${jd} + 라벨 ${lab.filter(x=>x.m==='llm').length}`)}${['fc','rd','se'].map(k=>row(k,pr+lab.filter(x=>x.m===k).length,`정기·사건 스냅샷 ${pr} + 라벨 ${lab.filter(x=>x.m===k).length}`)).join('')}<p class="opsd">격리 목록: 관제사 작성 문항 ${OPQ.length} · 재발 방지 ${regItems().length} · 시나리오 ${BMS.length} → 학습 데이터에서 제외</p>`}
function opsCrit(){const H={llm:[['표본 사람 검토 (20건)','부적절 ≤ 2건'],['응답 시간 (p95)','≤ 8초']],fc:[['학습 범위 밖 입력 경고','100%']],rd:[['정비 · 제약 설비 사용','0건']],se:[['구성 변경 직후 성능','기준 대비 -5% 이내']]};
  return `<p class="opsd">평가 게이트가 실제로 쓰는 기준을 그대로 표시 · 기준을 바꾸면 게이트 판정도 바뀜 · 아래 “사람 검토”는 게이트 통과 후 책임자 확인 항목</p><div class="crg">${MLK.map(k=>`<div class="crc"><b>${MN(k)}</b>${MLM[k].suites.map(s=>`<div><span>${s[0]}</span><em>${s[2]==='up'?'≥':'≤'} ${s[3]}${s[1]}</em></div>`).join('')}${(H[k]||[]).map(([a,b])=>`<div class="hm"><span>${a}</span><em>${b}</em></div>`).join('')}</div>`).join('')}</div>`}
function opsExp(){const E=[['EXP-214','LLM','LoRA lr 2e-5 · epoch 2 · ds-llm-v5','관제사 문항 81% · 근거 0건','채택'],['EXP-213','LLM','LoRA lr 5e-5 · epoch 3 · ds-llm-v5','관제사 문항 79% · 근거 1건','기각 (과적합)'],['EXP-209','예측','이전 데이터 30% 혼합 · 앞단 고정','MAPE 3.4%','채택'],['EXP-208','예측','최근 데이터만 · 전체 미세조정','MAPE 3.2% · 겨울 7.9%','기각 (망각)'],['EXP-201','재급전','GNN 3층 · 제약 손실 0.5','성공률 95.1% · 재발 방지 실패 2','기각']];
  return `<p class="opsd">같은 데이터셋으로 설정만 바꾼 후보끼리 비교 · 채택 이유와 기각 이유를 남김</p><div class="tw"><table class="reg"><tr><th>실험</th><th>모델</th><th>설정</th><th>결과</th><th>판단</th></tr>${E.map(e=>`<tr>${e.map((c,i)=>`<td>${i===4?`<span class="snst ${/기각/.test(c)?'b':''}">${c}</span>`:i===0?`<code>${c}</code>`:c}</td>`).join('')}</tr>`).join('')}</table></div>`}
function opsErr(){const F=OPQ.map(o=>({o,...opqJudge(o)})).filter(x=>!x.ok);
  return `<p class="opsd">후보·운영 모델이 틀린 사례를 모아 원인을 분류 · 다음 학습 데이터와 문항 보강의 근거</p><div class="tw"><table class="reg"><tr><th>사례</th><th>기대</th><th>결과</th><th>원인 분류</th></tr>${F.map(x=>`<tr><td><b>${esc(x.o.q)}</b><div class="d">관제사 작성 문항 ${x.o.id}</div></td><td>${x.o.e}</td><td>${x.got}</td><td>${/세우|손써|넘치/.test(x.o.q)?'구어 표현 미인식':/트립 시|떨어지면/.test(x.o.q)?'설비명 축약 · 띄어쓰기':'의도 분류 오류'}</td></tr>`).join('')}<tr><td><b>재급전 v4 · 2023 겨울 피크</b><div class="d">재발 방지 문항</div></td><td>위반 해소</td><td>위반 2건 남음</td><td>겨울 고부하 학습 사례 부족</td></tr></table></div>`}
const _ow=opsWire;opsWire=function(){const el=$('opsBody');_ow();
  el.querySelectorAll('[data-rb]').forEach(b=>{const orig=b.onclick;b.onclick=()=>{if(ROLE!=='책임자'){toast('되돌리기: 책임자 역할 필요');return}const why=prompt('되돌리기 사유 (감사 기록에 남음)');if(!why)return;orig();OPS.audit[0][4]+=` · 사유: ${why}`;modelNotice(`${MLM[b.dataset.rb].name} 이전 버전으로 되돌림 · 사유: ${why}`);opsRender()}})};
function modelNotice(t){const d=document.createElement('div');d.className='sys mn';d.textContent='모델 변경 안내 · '+t;$('msgs').appendChild(d);addJN('model',t)}
const _mf2=mlFinish;mlFinish=function(){const g=ML.gate;_mf2();if(g&&g.items){const dep=g.items.filter(i=>MLM[i.k].vers.find(v=>v.v===i.v&&v.stage==='Production'));if(dep.length)modelNotice(dep.map(i=>`${MLM[i.k].name} v${i.v}`).join(', ')+' 운영 시작 · 답변 방식이 달라질 수 있음')}};
const _ma=mlApprove;mlApprove=async function(ok){const h=new Date().getHours();if(ok&&((h>=8&&h<11)||(h>=17&&h<20)))toast('피크 시간대 배포 금지 규칙 (08~11 · 17~20시) · 시연에서는 진행');return _ma(ok)};

if(!MLM.llm.suites.some(s=>/관제사 작성/.test(s[0])))MLM.llm.suites.push(['관제사 작성 문항 통과율','%','up',80,75,81]);

/* ===== 정리 1~5 ===== */
/* 1. 실계통 하루 곡선: 오늘 실적 + 이후 예측 */
const _dr5=dashRender;dashRender=function(){_dr5();if(MODE!=='live')return;const P=MODELS.kpg193.profile.hours,L=rwList(),W=1000,H=200,now2=new Date(),hn=now2.getHours()+now2.getMinutes()/60;
  const act=L.filter(s=>s.dkey===dkey(now2)).map(s=>({h:s.date.getHours()+s.date.getMinutes()/60,v:s.state.load.reduce((a,b)=>a+b,0)}));act.push({h:hn,v:twin.load.reduce((a,b)=>a+b,0)});
  const fc=P.map((x,h)=>({h,v:x.dem})).filter(x=>x.h>=Math.floor(hn));const mx=Math.max(...P.map(x=>x.dem),...act.map(a=>a.v))*1.05,X=h=>30+h*(W-40)/23,Y=v=>H-20-v/mx*(H-30);
  let g=act.length>1?`<path class="ddem" d="${act.map((a,i)=>`${i?'L':'M'}${X(a.h)} ${Y(a.v)}`).join(' ')}"/>`:'';g+=`<path class="dfc" d="M${X(hn)} ${Y(act[act.length-1].v)} ${fc.map(x=>`L${X(x.h)} ${Y(x.v)}`).join(' ')}"/>`;
  for(let h=0;h<24;h+=3)g+=`<text class="dax" x="${X(h)}" y="${H-4}" text-anchor="middle">${pad(h)}</text>`;g+=`<line class="dcur" x1="${X(hn)}" x2="${X(hn)}" y1="8" y2="${H-18}"/><text class="dax" x="${X(hn)+6}" y="16">지금</text>`;
  $('dCurve').innerHTML=g;$('dCurveT').textContent='오늘 수요 · 실적 (데이터 레이크) · 이후 예측 (모의)'};
/* 2·3. 훈련 모드 → 성능 평가 관제사 훈련 */
(()=>{const ob=[...document.querySelectorAll('#v-ops button')].find(b=>b.textContent.trim()==='훈련 모드');if(ob)ob.style.display='none';
  const tp=$('trt')&&$('trt').closest('.panel'),ev=$('v-ev');if(!tp||!ev)return;const h=tp.querySelector('h2');if(h)h.textContent='관제사 훈련';const s=tp.querySelector('.sub');if(s)s.textContent='같은 유형의 문제를 관제사가 풀고 시스템과 비교 · 교육 담당용';
  const ph=tp.querySelector('.ph');const b=document.createElement('button');b.className='btn pri';b.type='button';b.textContent='훈련 시작';b.onclick=()=>{document.querySelector('nav button[data-v="ops"]').click();trainStart()};ph.appendChild(b);ev.appendChild(tp)})();
/* 4. 운영 설계 하위 탭 묶기 */
OPSUB.splice(0,OPSUB.length,['lab','라벨링 대기함'],['sch','실행 일정'],['tch','정답 재계산'],['crit','평가 기준표'],['shd','섀도 · 배포'],['mon','모니터링'],['aud','감사 기록'],['ds','데이터셋'],['env','학습 환경'],['exp','실험 비교'],['err','오류 분석']);
/* 5. 설비 구성 버전: 실계통 연동에서만 */
(()=>{const c=$('cimList');if(c)c.closest('.panel').classList.add('liveonly')})();

/* ===== 관제 화면: 한 화면 배치 · 경보 팝업 ===== */
setTimeout(()=>{const al=$('alarms');if(!al)return;const p=al.closest('.panel');const pop=document.createElement('div');pop.className='almpop';pop.id='almPop';pop.hidden=true;pop.appendChild(p);document.body.appendChild(pop);
  const vh=document.querySelector('#v-ops .vhead');const b=document.createElement('button');b.className='btn almbtn';b.type='button';b.id='almBtn';b.innerHTML='경보 <span class="apn" id="almN">0</span>';const ntb=vh.querySelector('.ntb');ntb?vh.insertBefore(b,ntb):vh.appendChild(b);
  b.onclick=e=>{e.stopPropagation();pop.hidden=!pop.hidden;if(!pop.hidden){const r=b.getBoundingClientRect();pop.style.top=(r.bottom+8)+'px';pop.style.right=(innerWidth-r.right)+'px'}};
  document.addEventListener('click',e=>{if(!pop.hidden&&!pop.contains(e.target)&&e.target!==b)pop.hidden=true});document.addEventListener('keydown',e=>{if(e.key==='Escape')pop.hidden=true});drawAlarms(dcpf(twin))},30);
const _da6=drawAlarms;drawAlarms=function(r){_da6(r);const n=$('almN');if(!n)return;const lis=[...$('alarms').querySelectorAll('li:not(.empty)')];const hi=lis.filter(li=>li.querySelector('.lv.a,.lv.alarm,.lv.e')||/경보/.test((li.querySelector('.lv')||{}).textContent||'')).length;n.textContent=lis.length;n.classList.toggle('on',hi>0)};
function opsFit(){const t=document.querySelector('.top');if(t)document.documentElement.style.setProperty('--hdr',t.getBoundingClientRect().height+'px')}window.addEventListener('resize',opsFit);setTimeout(opsFit,50);
const _nav=[...document.querySelectorAll('nav button[data-v]')];_nav.forEach(b=>b.addEventListener('click',()=>{document.documentElement.classList.toggle('opsfit',b.dataset.v==='ops');opsFit()}));document.documentElement.classList.add('opsfit');

/* ===== 경보: 계획 기동·정지 제외 (실계통 연동) ===== */
const _da7=drawAlarms;drawAlarms=function(r){_da7(r);if(MODE!=='live')return;const ul=$('alarms');let n=0;[...ul.querySelectorAll('li')].forEach(li=>{if(/ 정지$/.test((li.children[1]||{}).textContent||'')&&!/고장|트립/.test(li.textContent)){li.remove();n++}});
  const left=ul.querySelectorAll('li:not(.empty)').length;if($('almN'))$('almN').textContent=left;if($('almCnt'))$('almCnt').innerHTML=`전체 ${left} · 계획 기동·정지 ${n}건 제외 · 경보 처리는 EMS`;if(!left&&!ul.querySelector('.empty'))ul.innerHTML='<li class="empty">현재 경보 없음 · 계획 기동·정지는 경보에서 제외</li>'};
/* ===== 일일 보고: 자동 요약 + 사건 분석 ===== */
function evSnap(dk,h){const L=SNAP.filter(s=>s.dkey===dk&&s.kind==='정기'&&s.status!=='격리');let best=null,bd=1e9;L.forEach(s=>{const d=Math.abs(s.date.getHours()+s.date.getMinutes()/60-h);if(d<bd){bd=d;best=s}});return best}
function evCards(D){const today=!D.past;const dk=D.key;let evs=[];
  if(!today)evs=D.nar.ev.filter(e=>['고장','경보','휴전','감시'].includes(e[1])).map(e=>({t:e[0],k:e[1],x:e[2],rel:D.nar.ev.filter(z=>z[0]>=e[0]&&['조치','검토','확인'].includes(z[1])).slice(0,3)}));
  else evs=JN.filter(j=>['inject','forecast'].includes(j.type)).slice(-6).map(j=>({t:(j.st||j.t||'').slice(0,5),k:j.type==='inject'?'고장':'경보',x:j.text,rel:[]}));
  return evs.map((e,i)=>{const [hh,mm]=(e.t||'00:00').split(':').map(Number),h=hh+(mm||0)/60;const b=evSnap(dk,h-0.5),a=evSnap(dk,h+1);const sb=b?snSum(b):null,sa=a?snSum(a):null;
    const jd=SNAP.filter(s=>s.dkey===dk&&s.kind==='판단'&&Math.abs(s.date.getHours()-hh)<=2);const pv=today?PRED.slice(-2):[];
    return `<div class="evc"><div class="evh"><span class="cmpk k-${e.k==='고장'?'지속':e.k==='휴전'?'해소':'신규'}">${e.k}</span><b>${e.t} · ${esc(e.x)}</b><button type="button" class="lnk" data-evcp="${i}">이 사건만 복사</button></div>
     <div class="evg"><div><span>사건 전후 계통</span><b>${sb?`최대 부하율 ${sb.max}% → ${sa?sa.max:'-'}%`:'기록 없음'}</b><em>${sb?`정격 초과 ${sb.viol}건 → ${sa?sa.viol:'-'}건`:''}</em></div>
     <div><span>조치 · 확인 경과</span><b>${e.rel.length?e.rel.map(r=>`${r[0]} ${esc(r[2])}`).join('<br>'):'기록 없음'}</b></div>
     <div><span>플랫폼 해석 · 관제사 판정</span><b>${jd.length?jd.slice(0,2).map(s=>`${esc(s.trigger.slice(0,24))} → ${esc((s.labels[0]||'판정 없음').split(' · ')[0])}`).join('<br>'):'관련 질의 없음'}</b></div>
     <div><span>예측 대 관측</span><b>${pv.length?pv.map(p=>`${esc(p.line)} 예측 ${p.pred}% / 관측 ${p.obs}%`).join('<br>'):'해당 없음'}</b></div></div>
     <div class="evs">근거 데이터 · ${b?`<code>${b.id}</code>`:'-'} → ${a?`<code>${a.id}</code>`:'-'}${jd[0]?` · 추적 ${jd[0].trace}`:''}</div></div>`}).join('')}
const _rr3=renderReport;renderReport=function(){try{lakeSeed()}catch(e){}_rr3();const D=RP.days.find(x=>x.key===RP.sel);if(!D)return;const el=$('report');const N=D.past?D.nar:liveDay(),met=D.past?D.met:N.met;
  const evN=D.past?N.ev.filter(e=>['고장','경보','휴전','감시'].includes(e[1])).length:JN.filter(j=>['inject','forecast'].includes(j.type)).length;const rej=D.past?(N.sys[2]||0):feedback.filter(f=>f.d==='부적절').length;
  const lines=[met?`최대 수요 ${fmt(met.peak.dem)}MW(${pad(met.peak.h)}시) · 최저 예비율 ${met.rate.toFixed(1)}%`:'수급 실적: 기록 누적 중',`사건 ${evN}건 · 정격 초과 ${N.viol}건 · N-1 위반 최대 ${N.n1}건`,`의사결정지원 질의 ${N.sys[0]}건 · 부적절 판정 ${rej}건`,`익일 주의: ${N.next}`];
  const sum=`<div class="asum"><div class="ash"><b>자동 요약</b><span>기록 기반 자동 작성 · LLM 연결 시 문장 요약으로 교체</span></div><ul>${lines.map(l=>`<li>${esc(l)}</li>`).join('')}</ul></div>`;
  const rph=el.querySelector('.rph');if(rph)rph.insertAdjacentHTML('afterend',sum);
  const cards=evCards(D);const sec=`<h2>8. 사건 분석</h2>${cards||'<p>분석 대상 사건 없음 · 고장 · 경보 · 휴전 사건이 들어오면 자동 생성</p>'}`;const tail=[...el.querySelectorAll('p.d')].pop();tail?tail.insertAdjacentHTML('beforebegin',sec):el.insertAdjacentHTML('beforeend',sec);
  el.querySelectorAll('[data-evcp]').forEach(b=>b.onclick=async()=>{const c=b.closest('.evc');try{await navigator.clipboard.writeText(c.innerText);toast('사건 내용 복사됨')}catch(e){toast('복사 불가 · 직접 선택')}})};

/* ===== 피드백 → 모델 학습 연결 ===== */
REASONS.forEach(r=>r.ui=false);[['und','질문을 잘못 이해함','llm'],['api','엉뚱한 계산을 함','llm'],['calc','계산 결과가 이상함','rd'],['site','추천 조치가 현장에 안 맞음','rd'],['exp','근거·설명이 부족함','llm']].forEach(([id,t,m])=>{let r=REASONS.find(x=>x.id===id);if(!r){r={id,t,m};REASONS.push(r)}r.t=t;r.m=m;r.ui=true});
/* ① 툴 ↔ 모델 */
const TMODEL={run_redispatch_opf:'rd',eval_load_shedding:'rd',identify_equipment:'llm',get_measurements:'se'};const tmv=k=>{const p=MLM[k].vers.find(v=>v.stage==='Production');return `${MLM[k].short} v${p?p.v:'-'}`};
const _gw7=gwRender;gwRender=function(){_gw7();const t=$('reg');if(!t)return;const rows=t.querySelectorAll('tr');if(!rows.length)return;rows[0].insertAdjacentHTML('beforeend','<th>사용 모델</th>');[...rows].slice(1).forEach(tr=>{const n=(tr.querySelector('code')||{}).textContent||'';const k=TMODEL[n];tr.insertAdjacentHTML('beforeend',`<td>${k?`<span class="tmv">${tmv(k)}</span>`:/run_(power_flow|contingency|n1)/.test(n)?'<span class="d">해석 엔진 (DC)</span>':'<span class="d">-</span>'}</td>`)})};
const _lc8=logCall;logCall=function(t,i,r,ms){const k=TMODEL[t];_lc8(t,i,k?`${r} · model ${tmv(k)}`:r,ms)};
/* ② 자동 정답 생성 */
function autoLab(x){if(x.st==='확정'||x.auto)return;const r=REASONS.find(z=>z.t===x.r);if(!r||x.r==='사유 미지정'){x.st='대기';x.why='사유 미지정 · 원인 판정 불가';return}
  if(r.m==='llm'){x.st='확정';x.auto=true;x.src='질의 분류 · 응답 규칙 수정 예시';x.by='자동 확정';return}
  if(r.m==='fc'||r.m==='se'){x.st='확정';x.auto=true;x.src=r.m==='fc'?'사후 실측값 (예측 대상 시각 도래 후)':'측정값 재확인 · 불량 측정 표시';x.a=x.a||'실측 기준 정답';x.by='자동 확정';return}
  const unit=(feedback.find(f=>f.q===x.q&&f.unit)||{}).unit||'';if(r.id==='site'&&!unit&&!x.a){x.st='대기';x.why='제외할 설비 미지정 (두 번째 탭 건너뜀) · 재계산 조건 부족';return}
  x.st='확정';x.auto=true;x.src=`기존 EMS 프로그램 재계산${unit?` (제외: ${unit})`:''}`;x.a=x.a||(unit?`${unit} 제외 후 재급전`:'재계산 결과');x.by='자동 확정'}
function opsLab(){const L=labItems();L.forEach(autoLab);const nA=L.filter(x=>x.auto).length,nR=L.filter(x=>x.st==='대기').length;
  return `<p class="opsd">관제사의 두 번 탭 피드백과 호출 기록으로 원인 모델을 자동 판정하고, 기존 EMS 프로그램 재계산 · 실측 확인으로 정답을 자동 생성 · 자동으로 못 만든 건만 연구자가 검토</p><div class="lq"><span>자동 확정</span><b>${nA}건</b><em>· 검토 필요 ${nR}건 · 관제사 추가 작업 없음</em></div><div class="tw"><table class="reg"><tr><th>판정</th><th>사유</th><th>원인 모델</th><th>정답 · 출처</th><th>상태</th></tr>${L.map(x=>`<tr><td><b>${esc(x.q)}</b><div class="d">${x.id} · ${x.t}${x.ex?' · 예시':''}</div></td><td>${esc(x.r)}</td><td>${x.st==='확정'?MN(x.m):`<select data-lm="${x.id}">${mopt(x.m)}</select>`}</td><td>${x.st==='확정'?`${esc(x.a||'-')}<div class="d">${esc(x.src||'검토자 지정')}</div>`:`<input data-la="${x.id}" value="${esc(x.a||'')}" placeholder="정답 · 기대 행동"><div class="d late">${esc(x.why||'')}</div>`}</td><td>${x.st==='확정'?`<span class="snst">${x.auto?'자동 확정':'확정'}</span><div class="d">${x.by}</div>`:`<span class="snst b">검토 필요</span><div style="margin-top:4px"><button class="btn" type="button" data-lc="${x.id}">연구자 확정</button></div>`}</td></tr>`).join('')}</table></div>`}
OPSUB[0]=['lab','자동 정답 생성'];
/* ③ 모델별 피드백 기반 데이터 */
const _ds8=opsDs;opsDs=function(){let h=_ds8();const L=labItems().filter(x=>x.st==='확정');MLK.forEach(k=>{const n=L.filter(x=>x.m===k).length;h=h.replace(`<b>${MN(k)}</b>`,`<b>${MN(k)}</b><span class="fbn">관제 피드백 기반 ${n}건</span>`)});return h};
const _li9=labItems;labItems=function(){const L=_li9();L.forEach(x=>{if(x.r==='대안이 현장 사정에 안 맞음')x.r='추천 조치가 현장에 안 맞음';if(x.r==='질문을 잘못 이해함'||x.r==='근거·설명이 부족함'){}if(x.id==='LB-003'&&!x.fx){x.a='';x.fx=1;x.note='관제사가 설비를 고르지 않고 건너뜀'}});return L};

document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>x.setAttribute('aria-selected',x===b));
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('on',v.id==='v-'+b.dataset.v));
  try{history.replaceState(null,'','#'+b.dataset.v)}catch(e){}});
(()=>{const H={"ops": ["관제", "계통 감시 · 시나리오 해석 · 조치 승인"], "db": ["대시보드", "상황판 · 읽기 전용 · 항목을 누르면 관제 화면으로"], "gw": ["게이트웨이", "MCP 툴 · 권한 · 비동기 작업 · 호출 기록"], "ev": ["성능 평가", "문제지 · 채점 · 재발 방지 시험 · 관제사 훈련"], "kb": ["지식 베이스", "규정 · 내부 절차 · 관제사 지식 · 사례"], "rp": ["보고서", "운영 보고 초안 · 공식 보고서 작성 지원 · 검토 후 공식 반영"], "dl": ["데이터 연계", "데이터 요구 명세 · 소스 · 매핑 · 망 구성 · 계획 · 트윈 신뢰도"], "sn": ["데이터 레이크", "계통 상태 수집 · 검증 · 데이터 레이크"], "ml": ["학습 파이프라인", "재학습 · 평가 게이트 · 모델 배포"]};Object.entries(H).forEach(([k,[t,d]])=>{const v=document.getElementById('v-'+k);if(!v)return;const x=document.createElement('div');x.className='vhead';x.innerHTML=`<div><h1>${t}</h1><p>${d}</p></div>`;v.prepend(x)})})();
document.body.dataset.role='관제사';mlInit();document.querySelector('nav button[data-v="ml"]').addEventListener('click',mlAll);
document.querySelector('nav button[data-v="db"]').addEventListener('click',dashRender);document.querySelector('nav button[data-v="gw"]').addEventListener('click',gwRender);document.querySelector('nav button[data-v="kb"]').addEventListener('click',kbRender);
$('kbGo').onclick=kbDoSearch;['kfSrc','kfArea','kfTrust','kfCur'].forEach(i=>$(i).onchange=kbDoSearch);$('ukAdd').onclick=ukAdd;$('kbQ').onkeydown=e=>{if(e.key==='Enter')kbDoSearch()};$('kgQ').oninput=kgSearch;$('kgQ').onkeydown=e=>{if(e.key==='Enter'){const b=$('kgRes').querySelector('button');if(b)b.click()}if(e.key==='Escape')$('kgRes').hidden=true};$('kgHome').onclick=()=>{KG.focus='root';KG.path=['root'];kgDraw()};$('ukFType').onchange=ukRender;$('ukFAu').onchange=ukRender;
document.addEventListener('click',e=>{const b=e.target.closest('[data-kg]');if(b)kbFocus(b.dataset.kg)});
document.querySelectorAll('.kglg i').forEach(i=>i.style.background=KGC[i.dataset.t]);
const hh=location.hash.slice(1);if(['ops','db','gw','ev','kb','rp','dl','sn','ml'].includes(hh))document.querySelector(`nav button[data-v="${hh}"]`).click();
$('injBtn').onclick=()=>{if(MODE==='live'){const l=allLines(twin).find(x=>x.id===$('inj').value);toast('실계통 연동: 고장 주입은 가정 시나리오로 해석');ask(`${M.buses[l.f].n}–${M.buses[l.t].n} 선로가 고장 나면?`);return}if(ROLE!=='연구자'){toast('고장 주입: 연구자 역할 필요 (테스트베드 조작)');return}const id=$('inj').value;const l=allLines(twin).find(x=>x.id===id);if(!l||remC(l,twin)<=0)return;const t=cloneS(twin);t.out[id]=(t.out[id]||0)+1;
  if(!dcpf(t)){alert('이 회선을 빼면 계통이 분리되어 주입하지 않았습니다.');return}twin=t;lastScen=null;view=null;curTrace='SIM';logCall('save_workspace',`scenario inject ${l.eq} 1 circuit`,'testbed','0.00s');snapPush('사건',`고장 주입: ${lname(l)} 1회선`,t,'SIM');addJN('inject',`${lname(l)} 1회선 고장 주입`);drawSLD();if(TL.on)tlRecalc()};
$('resetBtn').onclick=()=>{tlClose();TL.plans={};twin=cloneS(BASE);alarmsT={};lastScen=null;addJN('apply','디지털 트윈 초기화');goLive()};$('liveBtn').onclick=goLive;$('splay').onclick=()=>{if(playT)stopPlay();else playStory()};
$('zin').onclick=()=>{stopPlay();zoom(.7)};$('zout').onclick=()=>{stopPlay();zoom(1/.7)};$('zfit').onclick=()=>{vb={x:0,y:0,w:M.w,h:M.h};setVB()};
const S=$('sld');
S.addEventListener('wheel',e=>{e.preventDefault();stopPlay();const p=svgPt(e);zoom(e.deltaY>0?1.15:1/1.15,p.x,p.y)},{passive:false});
S.addEventListener('pointerdown',e=>{stopPlay();drag={x:e.clientX,y:e.clientY,vb:{...vb}};dragMoved=false});
window.addEventListener('pointermove',e=>{if(!drag)return;const rc=S.getBoundingClientRect();const dx=(e.clientX-drag.x)/rc.width*vb.w,dy=(e.clientY-drag.y)/rc.height*vb.h;if(Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)>4){dragMoved=true;S.classList.add('dragging')}if(dragMoved){vb={...vb,x:drag.vb.x-dx,y:drag.vb.y-dy};setVB()}});
window.addEventListener('pointerup',()=>{drag=null;S.classList.remove('dragging');setTimeout(()=>dragMoved=false,0)});
window.addEventListener('resize',()=>drawOverlay());
$('snK').onchange=snRender;$('snL').onchange=snRender;$('snNow').onclick=()=>{const s=snapPush('정기','수동 저장',twin,'-');LK.part=s.dkey;SNSEL=s.id;lakeRender()};$('lkRun').onclick=lkToggle;document.querySelector('nav button[data-v="sn"]').addEventListener('click',()=>{SNSEL=null;snRender()});
document.querySelectorAll('.mtog button').forEach(b=>b.onclick=()=>switchMode(b.dataset.m));document.querySelector('nav button[data-v="sn"]').addEventListener('click',liveSrcRender);
$('roleSel').onchange=e=>setRole(e.target.value);$('apBtn').onclick=pendOpen;$('scnBtn').onclick=scnOpen;$('drClose').onclick=drawerClose;$('drScrim').onclick=drawerClose;document.addEventListener('keydown',e=>{if(e.key==='Escape')drawerClose()});$('kpiRe').onclick=kpiRender;document.querySelector('nav button[data-v="ev"]').addEventListener('click',kpiRender);
document.querySelectorAll('#lvTog button').forEach(b=>b.onclick=()=>{stopPlay();MAPLV=b.dataset.lv;if(MAPLV==='rg'){vb={x:0,y:0,w:M.w,h:M.h};setVB()}drawOverlay()});$('planTog').onchange=e=>{PLAN_ON=e.target.checked;planRender();addJN('apply',`계획 연계 ${PLAN_ON?'켬':'끔'}`);drawSLD()};$('snZ').onchange=lakeRender;document.querySelectorAll('[data-ft]').forEach(b=>b.onclick=()=>faultTest(b.dataset.ft));
$('mapQ').oninput=()=>{clearTimeout(msT);msT=setTimeout(msRender,60)};$('mapQ').onkeydown=msKey;$('mapQ').onfocus=()=>{if($('mapQ').value)msRender()};$('mapQ').onblur=()=>setTimeout(()=>$('mapRes').hidden=true,150);document.addEventListener('keydown',e=>{if(e.key==='/'&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)&&$('v-ops').classList.contains('on')){e.preventDefault();$('mapQ').focus()}});
$('rwR').oninput=rwInput;$('rwLive').onclick=rwLive;
$('runScnB').onclick=runScnBm;
document.querySelector('nav button[data-v="sn"]').addEventListener('click',dsRender);
$('menuAll').onchange=applyMenus;
$('runOpqB').onclick=runOpq;$('opqAddB').onclick=opqAdd;
$('model').onchange=()=>{loadModel($('model').value)};
$('form').onsubmit=e=>{e.preventDefault();ask($('q').value)};
$('runQB').onclick=runQB;$('runReg').onclick=runReg;document.querySelector('nav button[data-v="ev"]').addEventListener('click',bmRender);
$('tlOpen').onclick=tlOpen;$('tlClose').onclick=tlClose;$('tlPlay').onclick=tlPlay;$('tlPrev').onclick=()=>{tlPause();if(TL.h>0)setHour(TL.h-1)};$('tlNext').onclick=()=>{tlPause();if(TL.h<23)setHour(TL.h+1)};$('tlSlider').oninput=e=>{tlPause();setHour(+e.target.value)};
$('trainBtn').onclick=()=>{if(!TRN.on)trainStart()};
$('clearBtn').onclick=()=>{if(busy)return;TRN.on=false;stopPlay();$('msgs').innerHTML='';views.length=0;lastScen=null;goLive();const m=document.createElement('div');m.className='sys';m.textContent=`대화를 초기화했습니다 · 계통 모델 ${M.name}`;$('msgs').appendChild(m);$('msgs').appendChild(hello);chipsDefault();$('q').value='';$('q').focus()};$('rpRe').onclick=renderReport;
$('rpCopy').onclick=async()=>{const t=$('report').innerText;try{await navigator.clipboard.writeText(t);$('rpCopy').textContent='복사됨'}catch(e){const r=document.createRange();r.selectNodeContents($('report'));const s=getSelection();s.removeAllRanges();s.addRange(r);$('rpCopy').textContent='선택됨, Ctrl+C로 복사'}setTimeout(()=>$('rpCopy').textContent='텍스트 복사',1800)};
document.querySelector('nav button[data-v="rp"]').addEventListener('click',renderReport);$('runBench').onclick=runN1;
setInterval(()=>$('snap').textContent=now(),1000);$('snap').textContent=now();
drawReg();
const hello=document.createElement('div');hello.className='a';
hello.innerHTML='<div class="ans"><p style="margin:0"><b>질의 예</b> 선로 고장 · 휴전 · 발전기 정지 · 부하 변화 · 여러 이벤트 동시 · “그 상태에서”로 이어서 질의</p></div>';
$('msgs').appendChild(hello);
loadModel($('model').value);
