package main

const guiHTML = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>PNE Battery Analyzer v2.6</title><style>
:root{font-family:'Malgun Gothic','Segoe UI',sans-serif;color:#19354c;background:#eef3f6}*{box-sizing:border-box}body{margin:0}header{background:#123e55;color:#fff;padding:25px 32px}h1{margin:0;font-size:25px}header small{color:#c9dbe6}main{max-width:1270px;margin:20px auto;padding:0 20px}.panel{background:white;border:1px solid #d5e1e8;border-radius:12px;padding:20px;margin-bottom:18px;box-shadow:0 3px 12px #13405b0b}h2{font-size:18px;margin:0 0 12px}.line{display:flex;align-items:center;gap:9px;margin-bottom:10px;flex-wrap:wrap}input[type=text]{border:1px solid #cedce4;border-radius:7px;min-width:100px;padding:11px;flex:1;font-size:13px}select{padding:10px;border:1px solid #cedce4;border-radius:7px;min-width:130px;background:#fff}button{border:1px solid #bfd4df;border-radius:7px;background:#eff6f9;color:#164663;padding:10px 14px;cursor:pointer;font-weight:600}button:hover{background:#dbeaf1}.primary{background:#166b93;color:white;border-color:#166b93}.primary:hover{background:#11567c}button:disabled{opacity:.5;cursor:default}.muted{color:#6e8190;font-size:12px;line-height:1.7}.pill{display:inline-block;background:#eaf4ec;color:#25734d;border-radius:999px;padding:5px 10px;font-size:12px}.warning{background:#fff5df;border-left:3px solid #e2a12f;padding:12px 15px;font-size:12px;line-height:1.7;margin-top:12px}.metrics{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.stat{background:#f0f5f8;border-radius:9px;padding:10px 14px;min-width:116px;flex:1}.stat b{display:block;font-size:22px}.stat span{font-size:11px;color:#677e8b}.dropzone{border:2px dashed #8fb5c8;border-radius:10px;background:#f4f9fc;padding:25px;text-align:center;margin-bottom:14px;color:#215c78;cursor:pointer}.dropzone.over{background:#d9edf8;border-color:#1278ab}.plot{width:100%;height:390px;border:1px solid #dbe6ec;border-radius:8px;background:white;display:block}.error{color:#b32727}.success{color:#197347}.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:12px 0}label{font-size:13px}#status{white-space:pre-wrap}footer{padding:18px;text-align:center;color:#758692;font-size:12px}

/* v2.1 GUI: compact analysis workspace and keyboard-friendly controls */
main{max-width:1550px}
.importTitle{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}
.importTitle h2{flex:1;margin:0}.importTitle button{flex:none}
#loadCompact{padding:8px 0}.panel{padding:23px 26px}.panel h2{border-bottom:1px solid #e7eef3;padding-bottom:13px;margin-bottom:15px}
select,input,button{transition:border-color .12s,box-shadow .12s}select:focus-visible,input:focus-visible,button:focus-visible{outline:2px solid #428bb5;outline-offset:2px}
.controls label{display:flex;flex-direction:column;gap:6px;font-size:12px;font-weight:600;color:#4e6678}.controls{gap:12px 16px}
.plotbox{position:relative}.hoverlabel{position:absolute;pointer-events:none;background:#173951ed;color:white;border-radius:6px;padding:7px 10px;font-size:12px;line-height:1.5;z-index:2;box-shadow:0 5px 14px #18334b30;white-space:nowrap}
.plot{height:455px}.filecheck{max-height:165px}.reportline{margin-top:10px}
#toolNav{gap:10px;top:4px}#toolNav button{min-width:125px}
</style></head><body><header><h1>PNE Battery Analyzer <small>v2.6.0</small></h1><small>멀티채널 CYC + StepEnd · 레코드 검증 · 로컬 그래프 · 외부 데이터 전송 없음</small></header><main>
<section class="panel" id="importPanel"><div class="importTitle"><h2>1. 시험 데이터 불러오기</h2><button id="toggleImport" class="minor">입력 설정 접기</button></div><div id="loadCompact" class="muted" hidden>분석 결과가 준비됐습니다. 다른 ZIP을 분석하려면 입력 설정을 펼치세요.</div><div id="importContent"><div class="section-kicker"><span>1</span>파일 선택</div><div class="dropzone" id="dropzone">여기에 PNE ZIP 파일을 드래그해서 놓으세요<div class="muted">또는 아래의 ZIP 선택 버튼을 사용하세요. 브라우저 드롭은 원래 폴더 경로를 알 수 없습니다.</div></div>
<input id="browserzip" type="file" accept=".zip,application/zip" hidden><div class="line"><button class="primary" id="pickzip">ZIP 선택</button><input type="text" id="input" placeholder="선택한 PNE ZIP 또는 직접 입력한 경로"></div>
<div class="line"><button id="pickout">결과 폴더 선택</button><input type="text" id="output" placeholder="결과를 저장할 폴더"></div>
<details class="advanced-settings"><summary>고급 입력 옵션 · 폴더 선택, Raw 자동 생성</summary><div class="line"><button id="pickfolder">상위 폴더 선택</button><label><input type="checkbox" id="raw"> 채널별 전체 Raw CSV 자동 생성</label></div></details>
<div class="line"><button class="primary" id="run">분석 시작</button><button id="report" disabled>전체 검증 보고서 열기</button><button id="quit">프로그램 종료</button></div>
<div class="muted" id="status">ZIP 안의 상위 폴더 및 여러 채널 폴더를 자동 탐색합니다. 시작 전에 회사 보안정책을 확인하세요.</div>
<div class="warning">72B 헤더 + 176B 레코드와 Offset 배치는 현재 시험 데이터에 기반한 가설입니다. 첫 번째 비정상 단일 레코드는 INITIALIZATION으로 표시하며 원본은 보존합니다. 그래프에서 MATCHED/REVIEW 구간만 사용하고 미매칭 데이터는 제외합니다.</div></div></section>
<section class="panel" id="analysis" hidden><h2>2. 채널별 매칭 현황</h2><div class="metrics" id="metrics"></div><div class="muted" id="channels"></div></section>
<section class="panel" id="graphPanel" hidden><h2>3. 충방전 그래프</h2><div class="controls"><label>채널 <select id="channel"></select></label><label>Cycle <select id="cycle"></select></label><label>Cycle 번호 직접 선택 <input type="text" id="rawCycleText" placeholder="예: 5" style="width:83px;min-width:83px;padding:10px;flex:none" inputmode="numeric" title="Cycle 번호를 입력한 뒤 Enter"></label><label>Step <select id="step"></select></label><label>구간 <select id="rawMode"><option value="all">전체</option><option value="charge">충전</option><option value="discharge">방전</option><option value="rest">Rest</option></select></label><label>X축 <select id="rawX"><option value="time">선택 구간 누적시간 (s)</option><option value="capacity">Step 용량 (Ah)</option><option value="voltage">전압 (V)</option></select></label><label>Y축 <select id="metric"><option value="voltage">전압 (V)</option><option value="current">전류 (A)</option><option value="capacity">용량 (Ah)</option><option value="energy">에너지 (Wh)</option><option value="temperature">온도 (°C)</option></select></label><button id="exportRawSelected" disabled>현재 채널 Raw CSV 저장</button></div>
<div class="plot-toolbar"><span>휠: 확대/축소 · 드래그: 이동 · 더블클릭: 초기화</span><button class="minor" data-reset-plot="plot">전체 보기</button></div><div class="plotbox"><canvas id="plot" class="plot"></canvas><div id="rawHover" class="hoverlabel" hidden></div></div><div class="muted" id="graphNote">X·Y축을 자유롭게 선택할 수 있습니다. 용량은 Step별 누적 용량이므로 Step 전환 시 0으로 돌아갈 수 있습니다. 최대 약 2,400포인트를 화면에 표시합니다.</div></section>
<section class="panel" id="lifePanel" hidden><h2>4. Cycle Life · 다중 채널 비교</h2>
<div class="controls"><label>지표 <select id="lifeMetric"><option value="discharge">방전용량 (Ah)</option><option value="retention">용량 유지율 (%)</option><option value="ce">CE (%)</option><option value="charge">충전용량 (Ah)</option><option value="imp10">10s DCIR (mΩ, 후보)</option></select></label><button id="refreshLife">채널 비교 갱신</button><button id="saveLife">전체 채널 CSV 저장</button><button id="saveLifePNG">그래프 PNG 저장</button></div>
<canvas id="lifePlot" class="plot"></canvas><div class="muted" id="lifeNote">StepEnd Cycle별 대표 Step을 사용한 예비 그래프입니다. 여러 방전 Step이 존재할 때 유효 전체 용량과 다를 수 있습니다.</div></section>
<section class="panel" id="dqdvPanel" hidden><h2>5. dQ/dV 자동 분석</h2>
<div class="controls"><label>방향 <select id="dqMode"><option value="charge">충전</option><option value="discharge">방전</option></select></label>
<label>전압 보간 간격 <select id="dqInterval"><option value="0.001">1 mV</option><option value="0.002">2 mV</option><option value="0.005">5 mV</option><option value="0.0005">0.5 mV</option></select></label>
<label>SG Window <select id="dqWindow"><option value="11">11</option><option value="15">15</option><option value="21">21</option><option value="31">31</option></select></label>
<button id="calculateDQ" class="primary">dQ/dV 계산</button><button id="saveDQ">결과 CSV 저장</button><button id="saveDQPNG">그래프 PNG 저장</button></div>
<canvas id="dqPlot" class="plot"></canvas><div class="muted" id="dqNote">위 3번 그래프에서 채널·Cycle·Step을 먼저 고르세요. Cycle 전체를 선택하면 가장 긴 해당 방향 Step 하나를 분석합니다. 원본 데이터는 수정하지 않습니다.</div></section>
</main><footer>회사 PC 내부의 127.0.0.1 로컬 화면이며 클라우드 업로드를 하지 않습니다. 프로그램 종료는 실행 프로세스를 닫으면 됩니다.</footer><script>
const TOKEN='__TOKEN__';const INITIAL=__INITIAL__;let data=null;let points=[];let currentError='';
const $=id=>document.getElementById(id);
async function req(path,method='GET',body){const opt={method,headers:{'X-PNE-Token':TOKEN}};if(body){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body)}const r=await fetch(path,opt);if(!r.ok)throw Error(await r.text());return r.json()}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function pick(t,target){try{const a=await req('/api/pick?type='+t,'POST');if(a.error)throw Error(a.error);if(a.path){$(target).value=a.path;if(target==='input'&&a.suggestedOutput)$('output').value=a.suggestedOutput}}catch(e){alert('파일 선택 실패: '+e.message+'\n경로를 직접 붙여넣을 수 있습니다.')}}
// Browser's own file picker is more robust than a hidden PowerShell dialog for ZIP selection.
$('pickzip').onclick=()=>$('browserzip').click();
$('browserzip').addEventListener('change',async e=>{
 const f=e.target.files&&e.target.files[0];if(f)await acceptZip(f);
 e.target.value='';
});
$('pickfolder').onclick=()=>pick('folder','input');
$('pickout').onclick=()=>pick('out','output');
$('input').value=INITIAL;$('output').value='D:\\Downloads';
async function acceptZip(file){
  if(!file.name.toLowerCase().endsWith('.zip')){alert('ZIP 파일을 선택해 주세요.');return}
  const dz=$('dropzone');
  try {
    dz.textContent='로컬 분석기로 ZIP을 복사 중… ('+file.name+')';
    const response=await fetch('/api/drop',{method:'POST',headers:{'X-PNE-Token':TOKEN,'X-PNE-Filename':encodeURIComponent(file.name)},body:file});
    if(!response.ok)throw Error(await response.text());
    const v=await response.json();$('input').value=v.path;
    $('output').value='D:\\Downloads';
    dz.textContent='ZIP 준비됨: '+file.name;
    $('status').textContent='ZIP 준비 완료. 결과 폴더는 D:\\Downloads 입니다. 분석 시작을 눌러주세요.';
  }catch(err){dz.textContent='ZIP 읽기 오류: '+err.message;$('status').textContent=dz.textContent}
}
const dz=$('dropzone');
for (const eventName of ['dragenter','dragover']) dz.addEventListener(eventName,e=>{e.preventDefault();e.stopPropagation();dz.classList.add('over')});
for (const eventName of ['dragleave','drop']) dz.addEventListener(eventName,e=>{e.preventDefault();e.stopPropagation();dz.classList.remove('over')});
dz.addEventListener('drop',async e=>{
const files=e.dataTransfer.files;if(!files||files.length!==1){alert('ZIP 파일 하나를 드롭해 주세요.');return}
await acceptZip(files[0]);
});
$('toggleImport').onclick=()=>{const content=$('importContent');content.hidden=!content.hidden;$('toggleImport').textContent=content.hidden?'입력 설정 펼치기':'입력 설정 접기';$('loadCompact').hidden=!content.hidden};
$('run').onclick=async()=>{const input=$('input').value.trim(),output=$('output').value.trim();if(!input||!output){alert('입력/결과 저장 경로를 모두 선택하세요.');return}try{$('run').disabled=true;$('status').textContent='분석 시작 중…';await req('/api/start','POST',{input,output,raw:$('raw').checked});await poll()}catch(e){$('run').disabled=false;$('status').textContent='오류: '+e.message}};
async function poll(){try{const s=await req('/api/status');$('status').textContent=s.progress+(s.error?'\n오류: '+s.error:'');if(s.busy){setTimeout(poll,850);return}$('run').disabled=false;if(s.error)return;data=s.channels;$('analysis').hidden=false;$('graphPanel').hidden=false;$('lifePanel').hidden=false;$('dqdvPanel').hidden=false;$('report').disabled=false;$('exportRawSelected').disabled=false;$('importContent').hidden=true;$('loadCompact').hidden=false;$('toggleImport').textContent='입력 설정 펼치기';drawSummary();updateChannel();refreshLife();if(typeof onDataReady==='function')onDataReady()}catch(e){$('run').disabled=false;$('status').textContent='상태 확인 실패: '+e.message}}
$('exportRawSelected').onclick=async()=>{try{if(!data||!data.length)throw Error('먼저 ZIP을 분석하세요');$('status').textContent='선택한 채널의 전체 Raw CSV를 생성하는 중…';const res=await req('/api/exportRaw','POST',{channel:Number($('channel').value)});$('status').textContent='Raw CSV 저장 완료: '+res.path}catch(e){$('status').textContent='Raw CSV 저장 오류: '+e.message}};
$('quit').onclick=async()=>{if(confirm('PNE Analyzer를 종료할까요?')){await req('/api/quit','POST');window.close()}};
$('report').onclick=async()=>{try{await req('/api/openReport','POST')}catch(e){alert(e.message)}};
function drawSummary(){const keys=['채널','MATCHED','REVIEW','UNMATCHED','INITIALIZATION'];const values=[data.length,data.reduce((a,c)=>a+c.matched,0),data.reduce((a,c)=>a+c.review,0),data.reduce((a,c)=>a+c.unmatched,0),data.reduce((a,c)=>a+c.initialized,0)];$('metrics').innerHTML=keys.map((k,i)=>'<div class="stat"><b>'+values[i]+'</b><span>'+k+'</span></div>').join('');$('channels').textContent=data.map(c=>c.name+': '+c.records+' Raw / '+c.matched+' 일치 / '+c.initialized+' 초기화').join('   |   ')}
function opts(id,arr,all){$(id).innerHTML=(all?'<option value="">전체</option>':'')+arr.map(x=>'<option value="'+esc(x.value)+'">'+esc(x.label)+'</option>').join('')}
function updateChannel(){if(!data||!data.length){$('graphNote').textContent='분석된 채널이 없습니다';return}opts('channel',data.map(c=>({value:c.id,label:c.name})));updateCycle()}
function updateCycle(){const c=data.find(c=>String(c.id)==$('channel').value)||data[0];let a=[...c.cycles].sort((x,y)=>Number(x)-Number(y));opts('cycle',a.map(x=>({value:x,label:'Cycle '+x})),true);updateStep()}
function updateStep(){const c=data.find(c=>String(c.id)==$('channel').value)||data[0];let a=[...c.steps].sort((x,y)=>Number(x)-Number(y));opts('step',a.map(x=>({value:x,label:'Step '+x})),true);updatePlot()}
$('rawCycleText').addEventListener('keydown',e=>{if(e.key!=='Enter')return;e.preventDefault();const cyc=$('rawCycleText').value.trim();if(![...$('cycle').options].some(o=>o.value===cyc)){$('graphNote').textContent='선택한 Cycle '+cyc+'이(가) 해당 채널에 없습니다.';return}$('cycle').value=cyc;updateStep()});
$('channel').onchange=updateCycle;$('cycle').onchange=()=>{$('rawCycleText').value=$('cycle').value;updateStep()};$('step').onchange=updatePlot;$('metric').onchange=updatePlot;$('rawMode').onchange=updatePlot;$('rawX').onchange=updatePlot;
let plotRequestVersion=0;
async function updatePlot(){if(!data||!data.length)return;const version=++plotRequestVersion;try{const q=new URLSearchParams({channel:$('channel').value,cycle:$('cycle').value,step:$('step').value,metric:$('metric').value,mode:$('rawMode').value,x:$('rawX').value});$('graphNote').textContent='데이터를 불러오는 중…';const r=await req('/api/chart?'+q);if(version!==plotRequestVersion)return;points=r.points||[];plot();$('graphNote').textContent='표시 포인트 '+points.length+'개 · X축 '+$('rawX').selectedOptions[0].textContent+' · 선택한 구간에 따라 용량 또는 시간이 초기화될 수 있습니다 · 원본 불변'}catch(e){if(version!==plotRequestVersion)return;$('graphNote').textContent='그래프 오류: '+e.message+'\nZIP 파일이 이동되거나 접근 불가능한 경우 다시 선택해 주세요.'}}
function plot(){const el=$('plot'),rect=el.getBoundingClientRect(),dpr=devicePixelRatio||1;el.width=Math.max(400,Math.floor(rect.width*dpr));el.height=Math.floor(rect.height*dpr);const ctx=el.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);const W=el.width/dpr,H=el.height/dpr;ctx.fillStyle='#fff';ctx.fillRect(0,0,W,H);ctx.font='12px Segoe UI';if(!points.length){ctx.fillStyle='#627887';ctx.fillText('선택한 조건에 일치하는 데이터가 없습니다',40,H/2);return}
const margin={l:78,r:22,t:25,b:50},pw=W-margin.l-margin.r,ph=H-margin.t-margin.b;const xs=points.map(x=>x[0]),ys=points.map(x=>x[1]);let minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);if(minx===maxx)maxx++;if(miny===maxy){miny-=1;maxy+=1}const range=maxy-miny;miny-=range*.06;maxy+=range*.06;const px=x=>margin.l+(x-minx)/(maxx-minx)*pw,py=y=>margin.t+ph-(y-miny)/(maxy-miny)*ph;ctx.strokeStyle='#e4ebf0';ctx.fillStyle='#586c79';ctx.lineWidth=1;for(let j=0;j<5;j++){let y=margin.t+j*ph/4;ctx.beginPath();ctx.moveTo(margin.l,y);ctx.lineTo(margin.l+pw,y);ctx.stroke();ctx.fillText((maxy-(maxy-miny)*j/4).toPrecision(5),5,y+4)}for(let j=0;j<6;j++){let x=margin.l+j*pw/5;ctx.beginPath();ctx.moveTo(x,margin.t);ctx.lineTo(x,margin.t+ph);ctx.stroke();ctx.fillText((minx+(maxx-minx)*j/5).toPrecision(5),x-12,H-20)}ctx.strokeStyle='#176c99';ctx.lineWidth=1.8;ctx.beginPath();points.forEach(([x,y],i)=>{if(!i)ctx.moveTo(px(x),py(y));else ctx.lineTo(px(x),py(y))});ctx.stroke();ctx.fillStyle='#526a7a';ctx.fillText($('rawX').selectedOptions[0].textContent,W/2-50,H-4);ctx.fillText($('metric').selectedOptions[0].textContent,margin.l,15)}
let lifeData=[];let lastDQ=[];
async function refreshLife(){try{const r=await req('/api/life');lifeData=r.series||[];drawLife();$('lifeNote').textContent=r.note}catch(e){$('lifeNote').textContent='수명 분석 오류: '+e.message}}
$('refreshLife').onclick=refreshLife;$('lifeMetric').onchange=drawLife;
$('saveLife').onclick=async()=>{try{let r=await req('/api/exportLife','POST');$('lifeNote').textContent='저장 완료: '+r.path+' · 분석 대표 Step 선택 규칙을 검증하세요.'}catch(e){$('lifeNote').textContent='CSV 저장 오류: '+e.message}};
function savePNG(id,filename){const a=document.createElement('a');a.download=filename;a.href=$(id).toDataURL('image/png');a.click()}
$('saveLifePNG').onclick=()=>savePNG('lifePlot','PNE_Cycle_Life.png');$('saveDQPNG').onclick=()=>savePNG('dqPlot','PNE_dQdV.png');
// Use a shared canvas plotter for multichannel and dQ/dV overlays.
function drawLines(canvasId,lines,xLabel,yLabel){const el=$(canvasId),rect=el.getBoundingClientRect(),dpr=devicePixelRatio||1;el.width=Math.max(400,Math.floor(rect.width*dpr));el.height=Math.floor(rect.height*dpr);const c=el.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);const W=el.width/dpr,H=el.height/dpr;c.fillStyle='white';c.fillRect(0,0,W,H);let arr=lines.flatMap(l=>l.points).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));if(!arr.length){c.fillStyle='#687e8b';c.fillText('표시할 데이터가 없습니다',30,H/2);return}
const xs=arr.map(p=>p[0]),ys=arr.map(p=>p[1]);let minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);if(minx===maxx){minx-=1;maxx+=1}if(miny===maxy){miny-=1;maxy+=1}let r=maxy-miny;miny-=r*.05;maxy+=r*.05;const legendRows=Math.ceil(lines.length/2);const M={l:80,r:26,t:Math.max(60,18+legendRows*14),b:50},PW=W-M.l-M.r,PH=H-M.t-M.b;let px=x=>M.l+(x-minx)/(maxx-minx)*PW,py=y=>M.t+PH-(y-miny)/(maxy-miny)*PH;c.font='12px Segoe UI';c.strokeStyle='#dce5ec';c.fillStyle='#657987';for(let j=0;j<5;j++){let y=M.t+j*PH/4;c.beginPath();c.moveTo(M.l,y);c.lineTo(W-M.r,y);c.stroke();c.fillText((maxy-(maxy-miny)*j/4).toPrecision(4),4,y+4)}for(let j=0;j<6;j++){let x=M.l+j*PW/5;c.fillText((minx+(maxx-minx)*j/5).toFixed(2),x-15,H-20)}const palette=['#176c99','#c57424','#32926b','#8c5aad','#be4e66','#607d22','#6b777f','#249da4'];lines.forEach((line,k)=>{if(!line.points.length)return;c.strokeStyle=palette[k%palette.length];c.lineWidth=1.7;c.beginPath();line.points.forEach((p,i)=>{if(!i)c.moveTo(px(p[0]),py(p[1]));else c.lineTo(px(p[0]),py(p[1]))});c.stroke();c.fillStyle=palette[k%palette.length];c.fillRect(M.l+(k%2)*Math.min(PW/2,245),9+Math.floor(k/2)*14,9,3);c.fillText(line.name,M.l+14+(k%2)*Math.min(PW/2,245),14+Math.floor(k/2)*14)});c.fillStyle='#526a7a';c.fillText(xLabel,W/2-30,H-3);if(!canvasId.startsWith('summary'))c.fillText(yLabel,M.l,M.t-8)}
function drawLife(){if(!lifeData.length)return;const m=$('lifeMetric').value;let lines=lifeData.map(s=>({name:s.name,points:(s.points||[]).filter(p=>m==='charge'?p.hasCharge:m==='discharge'||m==='retention'?p.hasDischarge:true).map(p=>[Number(p.cycle),Number(p[m])]).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]))}));drawLines('lifePlot',lines,'Cycle',$('lifeMetric').selectedOptions[0].textContent)}
$('calculateDQ').onclick=async()=>{try{if(!$('cycle').value){$('dqNote').textContent='dQ/dV에는 Cycle 하나를 선택해 주세요.';return}const q=new URLSearchParams({channel:$('channel').value,cycle:$('cycle').value,step:$('step').value,mode:$('dqMode').value,interval:$('dqInterval').value,window:$('dqWindow').value});$('dqNote').textContent='원본 CYC를 읽고 dQ/dV 계산 중…';const r=await req('/api/dqdv?'+q);lastDQ=r.points||[];drawLines('dqPlot',[{name:'원본 미분',points:lastDQ.map(p=>[p.v,p.raw])},{name:'Savitzky–Golay',points:lastDQ.map(p=>[p.v,p.smooth])}],'Voltage (V)','dQ/dV (Ah/V)');$('dqNote').textContent=r.count+'개 전압 포인트 · '+r.stepSelection+' · '+r.note}catch(e){$('dqNote').textContent='dQ/dV 오류: '+e.message}}
$('saveDQ').onclick=async()=>{try{if(!$('cycle').value){alert('Cycle을 먼저 선택해 주세요.');return}const q={channel:Number($('channel').value),cycle:$('cycle').value,step:$('step').value,mode:$('dqMode').value,interval:Number($('dqInterval').value),window:Number($('dqWindow').value)};const r=await req('/api/exportDQ','POST',q);$('dqNote').textContent='CSV 저장 완료: '+r.path+' ('+r.count+'포인트)'}catch(e){$('dqNote').textContent='CSV 저장 오류: '+e.message}};
window.addEventListener('resize',()=>{if(points.length)plot()});

// v2.1 — precise hover inspection for native canvas charts.
const pneHoverInfo = new Map();
function pneHover(canvasId,event){
 if(typeof pneOverlays!=="undefined" && pneOverlays.has(canvasId)){ const prior=$(canvasId==='plot'?'rawHover':canvasId==='fullLifePlot'?'lifeHover':({summaryRetentionPlot:'summaryRetentionHover',summaryEnergyPlot:'summaryEnergyHover',summaryCEPlot:'summaryCEHover',summaryTempPlot:'summaryTempHover',fullDQPlot:'fullDQHover'}[canvasId]));if(prior)prior.hidden=true;return; }
 const info=pneHoverInfo.get(canvasId),el=$(canvasId);if(!info||!info.items.length)return;
 const rect=el.getBoundingClientRect(), x=event.clientX-rect.left,y=event.clientY-rect.top;
 let best=null,ds=Infinity;
 for(const item of info.items){const d=(item.px-x)**2+(item.py-y)**2;if(d<ds){ds=d;best=item}}
 const labelId=canvasId==='plot'?'rawHover':canvasId==='fullLifePlot'?'lifeHover':({'summaryRetentionPlot':'summaryRetentionHover','summaryEnergyPlot':'summaryEnergyHover','summaryCEPlot':'summaryCEHover','summaryTempPlot':'summaryTempHover','fullDQPlot':'fullDQHover'}[canvasId]);const tip=$(labelId);if(!tip)return;
 if(!best||x<info.left||x>info.right||y<info.top-5||y>info.bottom+5 || ds>150*150){tip.hidden=true;return}
 tip.textContent=best.name+' · '+info.xLabel+': '+best.x.toFixed(2)+' · '+info.yLabel+': '+best.y.toFixed(4);
 tip.style.left=Math.max(8,Math.min(rect.width-260,x+12))+'px';tip.style.top=Math.max(8,y-43)+'px';tip.hidden=false;
}
for(const cid of ['plot','fullLifePlot']){$(cid).addEventListener('mousemove',e=>pneHover(cid,e));$(cid).addEventListener('mouseleave',()=>{const t=$(cid==='plot'?'rawHover':'lifeHover');if(t)t.hidden=true})}
// Raw curve: build coordinate cache from chart domain identical to renderer.
const oldPlot=plot;
plot=function(){oldPlot();const el=$('plot'),W=el.getBoundingClientRect().width,H=el.getBoundingClientRect().height,good=points.filter(p=>p.every(Number.isFinite));
 if(!good.length){pneHoverInfo.delete('plot');return}pneHoverInfo.set('plot',pneCoordCache(good.map(p=>({name:'Cycle '+$('cycle').value,x:p[0],y:p[1]})),W,H,{l:78,r:22,t:25,b:50},$('rawX').selectedOptions[0].textContent,$('metric').selectedOptions[0].textContent,.06));
};
function pneCoordCache(data,W,H,m,xLabel,yLabel,padding=.05){
 let xs=data.map(p=>p.x),ys=data.map(p=>p.y),minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);
 if(minx===maxx)maxx++;if(miny===maxy){miny-=1;maxy+=1}const dr=maxy-miny;miny-=dr*padding;maxy+=dr*padding;
 const pw=W-m.l-m.r,ph=H-m.t-m.b;return {items:data.map(p=>({...p,px:m.l+(p.x-minx)/(maxx-minx)*pw,py:m.t+ph-(p.y-miny)/(maxy-miny)*ph})),left:m.l,right:W-m.r,top:m.t,bottom:H-m.b,xLabel,yLabel};
}
</script></body></html>`
