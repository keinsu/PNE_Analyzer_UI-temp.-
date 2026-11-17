package main

// The charting layer is self-contained and does not access external networks.
// All data passed to it is already decoded by the local Go backend.
const plotEnhanceJS = `
// v2.5: fixed-sized chart viewport, outside legends, cycle color spectrum,
// cursor-centered wheel zoom, drag-pan, double-click reset, precise hover.
const pneChartStates=new Map();
const pneOverlays=new Map();
const pnePalette=['#166c9b','#d08131','#318569','#9064b3','#c75672','#5c7e31','#5e7682','#2e98a4'];
function cycleGradientColor(cycle,min,max){
 const t=max>min?Math.max(0,Math.min(1,(cycle-min)/(max-min))):0.5;
 const hue=220-200*t;
 return 'hsl('+hue.toFixed(1)+' 74% 42%)';
}
function updateDQGradientLegend(curves,lo,hi){
 const el=$('dqCycleLegend');if(!el)return;
 if(!curves.length){el.textContent='선택한 Cycle에 유효한 곡선이 없습니다. Cycle 번호, 방향, Step을 확인하세요.';return}
 const cycles=[...new Set(curves.map(c=>Number(c.cycle)).filter(Number.isFinite))].sort((a,b)=>a-b);
 const channels=[...new Set(curves.map(c=>c.name))].length;
 const hasCharge=curves.some(c=>c.direction==='charge'),hasDis=curves.some(c=>c.direction==='discharge');
 const colorStart=cycleGradientColor(lo,lo,hi),colorEnd=cycleGradientColor(hi,lo,hi);
 const bar=lo===hi?colorStart:'linear-gradient(90deg, '+Array.from({length:9},(_,i)=>cycleGradientColor(lo+(hi-lo)*i/8,lo,hi)).join(', ')+')';
 el.innerHTML='<div class="cycle-spectrum-row"><strong>Cycle별 연속 색상</strong><span>'+cycles.length+'개 Cycle · '+curves.length+'개 곡선 · '+channels+'개 채널</span></div>'+
 '<div class="cycle-spectrum-row" style="margin-top:9px"><b>C'+lo+'</b><div class="cycle-spectrum-bar" style="background:'+bar+'"></div><b>C'+hi+'</b></div>'+
 '<div class="cycle-spectrum-row" style="margin-top:8px"><span>앞 Cycle → 뒤 Cycle</span>'+
 (hasCharge?'<span><i class="key-solid"></i> 충전 (실선)</span>':'')+
 (hasDis?'<span><i class="key-dash"></i> 방전 (점선)</span>':'')+
 '<span style="color:#718694">범례가 그래프를 가리지 않으며, 마우스를 올리면 개별 곡선을 확인할 수 있습니다.</span></div>';
}
function pneChartSig(lines,xLabel,yLabel){
 // Quickly detect plot datasets without hashing all points; resetting on a changed view is intentional.
 return xLabel+'|'+yLabel+'|'+lines.map(l=>{
  const p=l.points||[], first=p[0]||[],last=p[p.length-1]||[];
  return l.name+':'+p.length+':'+first.join('/')+':'+last.join('/');
 }).join('|');
}
function pneDomain(lines){
 let x0=Infinity,x1=-Infinity,y0=Infinity,y1=-Infinity,total=0;
 for(const l of lines){for(const p of (l.points||[])){
  const x=Number(p[0]),y=Number(p[1]);if(!Number.isFinite(x)||!Number.isFinite(y))continue;
  if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y;total++;
 }}
 if(!total)return null;
 if(x0===x1){x0-=.5;x1+=.5}
 if(y0===y1){y0-=.5;y1+=.5}
 let ypad=(y1-y0)*.045;
 return {x0,x1,y0:y0-ypad,y1:y1+ypad};
}
function pneMakeLegend(state){
 const el=$(state.id),root=el.closest('.plotbox');
 if(!root||state.id==='fullDQPlot')return;
 let legend=document.getElementById('plotLegend-'+state.id);
 if(!legend){legend=document.createElement('div');legend.id='plotLegend-'+state.id;legend.className='plot-legend';root.after(legend)}
 legend.replaceChildren();
 const lines=state.lines.filter(l=>(l.points||[]).length);
 if(lines.length<=1){legend.hidden=true;return}
 legend.hidden=false;
 const shown=lines.slice(0,8);
 for(let i=0;i<shown.length;i++){
  const line=shown[i],chip=document.createElement('span');chip.className='plot-legend-chip';
  const swatch=document.createElement('i');swatch.className='swatch';swatch.style.setProperty('--swatch',line.color||pnePalette[i%pnePalette.length]);
  const label=document.createElement('span');label.textContent=line.name;
  chip.append(swatch,label);legend.appendChild(chip);
 }
 if(lines.length>8){const more=document.createElement('span');more.className='plot-legend-more';more.textContent='외 '+(lines.length-8)+'개 · 마우스를 올리면 정확한 곡선 정보 표시';legend.appendChild(more)}
}
function pneTickText(n,span){
 if(!Number.isFinite(n))return '—';
 if(Math.abs(n)>=1e6 || (Math.abs(n)>0&&Math.abs(n)<.0001))return n.toExponential(2);
 const digits=span<.01?5:span<1?3:span<20?2:1;
 return Number(n.toFixed(digits)).toString();
}
function pneRenderChart(id){
 const state=pneChartStates.get(id),el=$(id);if(!state||!el)return;
 const box=el.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
 const W=Math.max(400,box.width),H=Math.max(180,box.height);
 el.width=Math.round(W*dpr);el.height=Math.round(H*dpr);
 const c=el.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);
 c.fillStyle='#fff';c.fillRect(0,0,W,H);
 const v=state.view,margin={l:82,r:25,t:id==='fullDQPlot'?59:40,b:52};
 const pw=W-margin.l-margin.r,ph=H-margin.t-margin.b;
 const good=state.lines.some(l=>(l.points||[]).length);
 if(!v||!good){c.fillStyle='#697f8b';c.font='13px Segoe UI';c.fillText('표시할 데이터가 없습니다',margin.l,H/2);pneHoverInfo.delete(id);return}
 const sx=(x)=>margin.l+(x-v.x0)/(v.x1-v.x0)*pw;
 const sy=(y)=>margin.t+ph-(y-v.y0)/(v.y1-v.y0)*ph;
 c.lineWidth=1;c.strokeStyle='#e6ecf1';c.fillStyle='#556b7b';c.font='12px Segoe UI';
 for(let j=0;j<=4;j++){
  const yp=margin.t+j*ph/4, val=v.y1-(v.y1-v.y0)*j/4;
  c.beginPath();c.moveTo(margin.l,yp);c.lineTo(W-margin.r,yp);c.stroke();
  c.fillText(pneTickText(val,v.y1-v.y0),5,yp+4);
 }
 for(let j=0;j<=5;j++){
  const xp=margin.l+j*pw/5,val=v.x0+(v.x1-v.x0)*j/5;
  c.fillText(pneTickText(val,v.x1-v.x0),Math.max(margin.l-12,xp-16),H-22);
 }
 c.strokeStyle='#cbdbe4';c.strokeRect(margin.l,margin.t,pw,ph);
 c.fillStyle='#466578';c.font='12px Segoe UI';
 c.fillText(state.yLabel,margin.l,18);c.fillText(state.xLabel,W/2-40,H-5);
 if(id==='fullDQPlot'&&Number.isFinite(state.cycleMin)){
  const min=state.cycleMin,max=state.cycleMax;
  const bx=margin.l+6,by=28,bw=Math.min(185,Math.max(70,W*.25));
  for(let j=0;j<40;j++){
   const color=cycleGradientColor(min+(max-min)*j/39,min,max);
   c.fillStyle=color;c.fillRect(bx+j*bw/40,by,bw/40+1,8);
  }
  c.fillStyle='#426173';c.font='11px Segoe UI';
  c.fillText('Cycle '+min+' → '+max,bx+bw+9,by+8);
 }
 c.save();c.beginPath();c.rect(margin.l,margin.t,pw,ph);c.clip();
 const hover=[];
 for(let k=0;k<state.lines.length;k++){
  const line=state.lines[k],pts=line.points||[];if(!pts.length)continue;
  c.strokeStyle=line.color||pnePalette[k%pnePalette.length];c.globalAlpha=line.muted?.15:1;c.lineWidth=line.selected?3.6:(state.lines.length>50?1.05:1.65);
  c.setLineDash(line.dash?[6,4]:[]);c.beginPath();
  let started=false;
  for(let i=0;i<pts.length;i++){
   const p=pts[i],x=Number(p[0]),y=Number(p[1]);
   if(!Number.isFinite(x)||!Number.isFinite(y)){started=false;continue}
   const xx=sx(x),yy=sy(y);if(!started){c.moveTo(xx,yy);started=true}else c.lineTo(xx,yy);
   // Sample displayed points for fast hover even with hundreds of Cycle curves.
   const sampleStep=Math.max(1,Math.ceil(pts.length/350));
   if(i%sampleStep===0 && xx>=margin.l && xx<=W-margin.r && yy>=margin.t && yy<=H-margin.b){
    hover.push({name:line.name,x,y,px:xx,py:yy,color:line.color||pnePalette[k%pnePalette.length],lineIndex:k,pointIndex:i});
   }
  }
  c.stroke();c.setLineDash([]);c.globalAlpha=1;
 }
 c.restore();
 pneHoverInfo.set(id,{items:hover,left:margin.l,right:W-margin.r,top:margin.t,bottom:H-margin.b,xLabel:state.xLabel,yLabel:state.yLabel});
 state.margin=margin;state.W=W;state.H=H;
 pneSizeOverlay(id,W,H,dpr);
}
// Override the v2.2 canvas painter; fixed plot margins avoid legend overflow.
drawLines=function(id,lines,xLabel,yLabel){
 const sig=pneChartSig(lines,xLabel,yLabel),old=pneChartStates.get(id);
 const domain=pneDomain(lines);
 const state=old||{id,view:null,signature:''};
 if(!old||state.signature!==sig){state.signature=sig;state.view=domain?{...domain}:null;state.domain=domain}
 state.lines=lines;state.xLabel=xLabel;state.yLabel=yLabel;
 if(state.axisManual){const a=state.axisManual;const base=state.view||state.domain;
 if(base){state.view={x0:Number.isFinite(a.x0)?a.x0:base.x0,x1:Number.isFinite(a.x1)?a.x1:base.x1,y0:Number.isFinite(a.y0)?a.y0:base.y0,y1:Number.isFinite(a.y1)?a.y1:base.y1}}}
 if(id==='fullDQPlot'){
  const cs=lines.map(l=>l.cycle).filter(Number.isFinite);
  state.cycleMin=cs.length?Math.min(...cs):NaN;
  state.cycleMax=cs.length?Math.max(...cs):NaN;
 }else{state.cycleMin=NaN;state.cycleMax=NaN}
 pneChartStates.set(id,state);pneRenderChart(id);pneMakeLegend(state);
};
// Original raw graph is also rendered through the same interactive engine.
plot=function(){drawLines('plot',[{name:'선택된 원본 데이터',points:points||[]}],$('rawX').selectedOptions[0].textContent,$('metric').selectedOptions[0].textContent)};
function pneResetPlot(id){const s=pneChartStates.get(id);if(!s||!s.domain)return;s.view={...s.domain};pneRenderChart(id)}
for(const b of document.querySelectorAll('[data-reset-plot]'))b.addEventListener('click',()=>pneResetPlot(b.dataset.resetPlot));
function pneChartPointerXY(el,e){const r=el.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top}}
function pnePointInside(s,p){return s.margin&&p.x>=s.margin.l&&p.x<=s.W-s.margin.r&&p.y>=s.margin.t&&p.y<=s.H-s.margin.b}

// v2.5: separate transparent overlay keeps hover crosshair and drag preview visible
// without repainting all source curves on each pointer movement.
function pneSizeOverlay(id,W,H,dpr){
 const o=pneOverlays.get(id);if(!o)return;
 const canvas=o.canvas;
 if(canvas.width!==Math.round(W*dpr)||canvas.height!==Math.round(H*dpr)){
  canvas.width=Math.round(W*dpr);canvas.height=Math.round(H*dpr);
 }
 canvas.style.width=W+'px';canvas.style.height=H+'px';
 o.ctx.setTransform(dpr,0,0,dpr,0,0);
}
function pneGetOverlay(id){
 if(pneOverlays.has(id))return pneOverlays.get(id);
 const source=$(id);if(!source)return null;
 const root=source.parentElement;
 if(getComputedStyle(root).position==='static')root.style.position='relative';
 const overlay=document.createElement('canvas');overlay.className='pne-overlay';
 overlay.setAttribute('aria-hidden','true');
 Object.assign(overlay.style,{position:'absolute',pointerEvents:'none',zIndex:'3',top:'0',left:'0',width:'100%',height:'100%'});
 // position relative to canvas, not its toolbar or surrounding card
 const frame=document.createElement('div');frame.className='pne-canvas-frame';
 Object.assign(frame.style,{position:'relative',width:'100%'});
 root.insertBefore(frame,source);frame.appendChild(source);frame.appendChild(overlay);
 const tip=document.createElement('div');tip.className='pne-coordinate-tip';
 Object.assign(tip.style,{position:'absolute',pointerEvents:'none',zIndex:'4',background:'#173951',color:'#fff',padding:'8px 11px',borderRadius:'7px',font:'12px Segoe UI,sans-serif',whiteSpace:'pre-line',boxShadow:'0 4px 14px #142f4659',display:'none',maxWidth:'330px'});
 frame.appendChild(tip);
 const result={canvas:overlay,ctx:overlay.getContext('2d'),tip,frame};
 pneOverlays.set(id,result);
 const rect=source.getBoundingClientRect();pneSizeOverlay(id,rect.width,rect.height,window.devicePixelRatio||1);
 return result;
}
function pneHoverPoint(id,p){
 const s=pneChartStates.get(id),info=pneHoverInfo.get(id);
 if(!s||!s.view||!info||!info.items.length||!pnePointInside(s,p))return null;
 let best=null,distance=Infinity;
 // Coarse point search is already downsampled for large multi-cycle datasets.
 for(const item of info.items){const dx=item.px-p.x,dy=item.py-p.y,d=dx*dx+dy*dy;
  if(d<distance){distance=d;best=item;}}
 if(!best||distance>42*42)return null;
 // Refine to a real point in the original line around the candidate index.
 const line=s.lines[best.lineIndex],pts=line&&line.points||[];
 const radius=Math.max(3,Math.ceil(pts.length/350)*2);
 const m=s.margin,v=s.view,pw=s.W-m.l-m.r,ph=s.H-m.t-m.b;
 for(let i=Math.max(0,best.pointIndex-radius);i<=Math.min(pts.length-1,best.pointIndex+radius);i++){
  const value=pts[i],x=Number(value[0]),y=Number(value[1]);if(!Number.isFinite(x)||!Number.isFinite(y))continue;
  const px=m.l+(x-v.x0)/(v.x1-v.x0)*pw,py=m.t+ph-(y-v.y0)/(v.y1-v.y0)*ph;
  const d=(px-p.x)**2+(py-p.y)**2;if(d<distance){distance=d;best={...best,x,y,px,py,pointIndex:i};}
 }
 return best;
}
function pneNice(n){
 if(!Number.isFinite(n))return '—';
 return n.toLocaleString('ko-KR',{maximumFractionDigits:6});
}
function pneCoords(s,p){
 const m=s.margin,v=s.view;
 return {x:v.x0+(p.x-m.l)/(s.W-m.l-m.r)*(v.x1-v.x0),
 y:v.y1-(p.y-m.t)/(s.H-m.t-m.b)*(v.y1-v.y0)};
}
function pneOverlayUpdate(id,p,selection){
 const s=pneChartStates.get(id),o=pneGetOverlay(id);if(!s||!o||!s.margin)return;
 const ctx=o.ctx,W=s.W,H=s.H,m=s.margin;
 ctx.clearRect(0,0,W,H);
 if(selection&&selection.mode==='rect'){
  const x0=Math.max(m.l,Math.min(W-m.r,selection.start.x)),y0=Math.max(m.t,Math.min(H-m.b,selection.start.y));
  const x1=Math.max(m.l,Math.min(W-m.r,p.x)),y1=Math.max(m.t,Math.min(H-m.b,p.y));
  ctx.fillStyle='rgba(31,126,190,.16)';ctx.fillRect(Math.min(x0,x1),Math.min(y0,y1),Math.abs(x1-x0),Math.abs(y1-y0));
  ctx.strokeStyle='#167db4';ctx.lineWidth=1.8;ctx.setLineDash([5,3]);ctx.strokeRect(Math.min(x0,x1),Math.min(y0,y1),Math.abs(x1-x0),Math.abs(y1-y0));ctx.setLineDash([]);
  const a=pneCoords(s,{x:x0,y:y0}),b=pneCoords(s,{x:x1,y:y1});
  o.tip.textContent='확대 영역 선택\nX: '+pneNice(Math.min(a.x,b.x))+' ~ '+pneNice(Math.max(a.x,b.x))+' '+s.xLabel+'\nY: '+pneNice(Math.min(a.y,b.y))+' ~ '+pneNice(Math.max(a.y,b.y))+' '+s.yLabel;
  o.tip.style.display='block';o.tip.style.left=Math.max(4,Math.min(W-320,p.x+14))+'px';o.tip.style.top=Math.max(4,Math.min(H-82,p.y-78))+'px';
  return;
 }
 if(!pnePointInside(s,p)){o.tip.style.display='none';return}
 const item=pneHoverPoint(id,p);
 if(!item){o.tip.style.display='none';return}
 // Crosshair snaps to the actual displayed point, rather than the mouse pixel.
 ctx.save();ctx.strokeStyle='rgba(39,112,152,.63)';ctx.setLineDash([4,4]);ctx.lineWidth=1;
 ctx.beginPath();ctx.moveTo(item.px,m.t);ctx.lineTo(item.px,H-m.b);ctx.moveTo(m.l,item.py);ctx.lineTo(W-m.r,item.py);ctx.stroke();ctx.restore();
 ctx.beginPath();ctx.arc(item.px,item.py,5.5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.strokeStyle=item.color||'#167db4';ctx.lineWidth=2.6;ctx.stroke();
 o.tip.textContent=item.name+'\n'+s.xLabel+': '+pneNice(item.x)+'\n'+s.yLabel+': '+pneNice(item.y)+'\n원본 포인트 #'+(item.pointIndex+1);
 o.tip.style.display='block';o.tip.style.left=Math.max(4,Math.min(W-300,item.px+14))+'px';o.tip.style.top=Math.max(4,Math.min(H-92,item.py-92))+'px';
}
function pneOverlayClear(id){const o=pneOverlays.get(id);if(o){o.ctx.clearRect(0,0,o.canvas.width,o.canvas.height);o.tip.style.display='none'}}

// Graph interaction inspired by the supplied PyQtGraph UI: rectangle zoom + manual axes.
const pneInteractionModes=new Map();
for(const holder of document.querySelectorAll('[data-plot-axis]')){
 const id=holder.dataset.plotAxis;
 holder.innerHTML='<label>드래그 방식 <select class="pne-drag-mode"><option value="rect">영역 확대</option><option value="pan">이동</option></select></label>'+
 '<label>X 최소 <input class="axis-x0" type="number" step="any" placeholder="자동"></label><label>X 최대 <input class="axis-x1" type="number" step="any" placeholder="자동"></label>'+
 '<label>Y 최소 <input class="axis-y0" type="number" step="any" placeholder="자동"></label><label>Y 최대 <input class="axis-y1" type="number" step="any" placeholder="자동"></label>'+
 '<button class="minor axis-apply">축 적용</button><button class="minor axis-reset">축 자동</button>';
 holder.querySelector('.pne-drag-mode').addEventListener('change',e=>pneInteractionModes.set(id,e.target.value));
 holder.querySelector('.axis-apply').addEventListener('click',()=>{
  const s=pneChartStates.get(id);if(!s||!s.domain)return;
  const a={};for(const k of ['x0','x1','y0','y1']){const val=holder.querySelector('.axis-'+k).value.trim();a[k]=val===''?NaN:Number(val)};
  const v={x0:Number.isFinite(a.x0)?a.x0:s.domain.x0,x1:Number.isFinite(a.x1)?a.x1:s.domain.x1,y0:Number.isFinite(a.y0)?a.y0:s.domain.y0,y1:Number.isFinite(a.y1)?a.y1:s.domain.y1};
  if(v.x0>=v.x1||v.y0>=v.y1){alert('축 최소값은 최대값보다 작아야 합니다.');return}
  s.axisManual=a;s.view=v;pneRenderChart(id);
 });
 holder.querySelector('.axis-reset').addEventListener('click',()=>{const s=pneChartStates.get(id);if(s)s.axisManual=null;holder.querySelectorAll('input').forEach(x=>x.value='');pneResetPlot(id)});
}
for(const id of ['plot','lifePlot','dqPlot','fullLifePlot','fullDQPlot','summaryRetentionPlot','summaryEnergyPlot','summaryCEPlot','summaryTempPlot']){
 const el=$(id);if(!el)continue;
 pneGetOverlay(id);
 el.style.touchAction='none';
 el.addEventListener('wheel',e=>{
  if(!e.ctrlKey)return;
  const s=pneChartStates.get(id);if(!s||!s.view)return;
  const p=pneChartPointerXY(el,e);if(!pnePointInside(s,p))return;
  e.preventDefault();
  const v=s.view,m=s.margin,factor=Math.max(.7,Math.min(1.43,Math.exp(e.deltaY*.0013)));
  const fx=(p.x-m.l)/(s.W-m.l-m.r),fy=1-(p.y-m.t)/(s.H-m.t-m.b);
  const anchorX=v.x0+fx*(v.x1-v.x0),anchorY=v.y0+fy*(v.y1-v.y0);
  s.view={x0:anchorX+(v.x0-anchorX)*factor,x1:anchorX+(v.x1-anchorX)*factor,
          y0:anchorY+(v.y0-anchorY)*factor,y1:anchorY+(v.y1-anchorY)*factor};
  pneRenderChart(id);
 },{passive:false});
 el.addEventListener('pointerdown',e=>{
  if(e.button!==0)return;
  const s=pneChartStates.get(id);if(!s||!s.view)return;
  const p=pneChartPointerXY(el,e);if(!pnePointInside(s,p))return;
  s.pan={x:e.clientX,y:e.clientY,view:{...s.view},mode:pneInteractionModes.get(id)||'rect',start:p};
  pneOverlayUpdate(id,p,s.pan);
  try{el.setPointerCapture(e.pointerId)}catch(_e){}
  el.classList.add('is-panning');e.preventDefault();
 });
 el.addEventListener('pointermove',e=>{
  const s=pneChartStates.get(id);if(!s||!s.margin)return;
  const p=pneChartPointerXY(el,e);
  if(!s.pan){pneOverlayUpdate(id,p,null);return;}
  const v=s.pan.view,m=s.margin;
  if(s.pan.mode==='rect'){pneOverlayUpdate(id,p,s.pan);return;}
  const dx=(e.clientX-s.pan.x)/(s.W-m.l-m.r)*(v.x1-v.x0);
  const dy=(e.clientY-s.pan.y)/(s.H-m.t-m.b)*(v.y1-v.y0);
  s.view={x0:v.x0-dx,x1:v.x1-dx,y0:v.y0+dy,y1:v.y1+dy};
  pneRenderChart(id);
  pneOverlayUpdate(id,p,null);
 });
 const finish=e=>{
  const s=pneChartStates.get(id);
  if(s&&s.pan&&s.pan.mode==='rect'&&s.margin){
   const start=s.pan.start,stop=pneChartPointerXY(el,e),m=s.margin,v=s.pan.view;
   const dx=Math.abs(stop.x-start.x),dy=Math.abs(stop.y-start.y);
   if(dx>8&&dy>8){
    const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
    const xx=x=>v.x0+(clamp(x,m.l,s.W-m.r)-m.l)/(s.W-m.l-m.r)*(v.x1-v.x0);
    const yy=y=>v.y1-(clamp(y,m.t,s.H-m.b)-m.t)/(s.H-m.t-m.b)*(v.y1-v.y0);
    s.view={x0:Math.min(xx(start.x),xx(stop.x)),x1:Math.max(xx(start.x),xx(stop.x)),y0:Math.min(yy(start.y),yy(stop.y)),y1:Math.max(yy(start.y),yy(stop.y))};
    pneRenderChart(id);
   }else if(id==='fullDQPlot'){
    const info=pneHoverInfo.get(id),nearest=info&&info.items.reduce((best,p)=>{const d=(p.px-stop.x)**2+(p.py-stop.y)**2;return d<(best?.distance??Infinity)?{distance:d,point:p}:best},null);
    if(nearest&&nearest.distance<220&&nearest.point){const m=nearest.point.name.match(/ C(\d+) /);if(m){$('dqHighlight').value=m[1];drawFullDQ()}}
   }
  }
  if(s)s.pan=null;el.classList.remove('is-panning');
  pneOverlayClear(id);
 };
 el.addEventListener('pointerup',finish);el.addEventListener('pointercancel',finish);
 el.addEventListener('pointerleave',()=>{if(!pneChartStates.get(id)?.pan)pneOverlayClear(id)});
 el.addEventListener('dblclick',e=>{e.preventDefault();pneResetPlot(id)});
}
// Redraw the existing view after viewport resizing without querying the server again.
let pneResizeTimer=null;
window.addEventListener('resize',()=>{
 clearTimeout(pneResizeTimer);
 pneResizeTimer=setTimeout(()=>{for(const id of pneChartStates.keys()){
  const el=$(id);if(el&&!el.closest('[hidden]'))pneRenderChart(id);pneOverlayClear(id);
 }},180);
});
`
