package main

const fullCSS = `
/* v2.5: PyQtGraph-inspired analysis workspace, never changes data processing */
#toolNav{position:sticky;top:8px;align-self:start;display:flex;flex-direction:column;gap:6px;max-width:190px;min-width:175px;padding:12px}
#toolNav button{text-align:left;min-height:36px}
#toolNav .muted{line-height:1.5;font-size:11px}
body:has(#toolNav:not([hidden])) main{display:grid;grid-template-columns:190px minmax(0,1fr);align-items:start;gap:16px}
body:has(#toolNav:not([hidden])) main > section.panel{grid-column:2;min-width:0;margin-top:0}
body:has(#toolNav:not([hidden])) main > #toolNav{grid-column:1;grid-row:1 / span 20}
@media(max-width:940px){body:has(#toolNav:not([hidden])) main{display:block}#toolNav{max-width:none;min-width:0;flex-direction:row;position:static}#toolNav button{text-align:center}}
.pne-cycle-tools{padding:12px 14px;border:1px solid #d9e5eb;border-radius:10px;background:#f8fbfd;margin:10px 0}
.pne-cycle-tools h3{font-size:14px;margin:0 0 9px}
.pne-cycle-tools .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}
.pne-cycle-list{max-height:104px;overflow:auto;display:flex;flex-wrap:wrap;gap:6px;border:1px solid #d5e3eb;padding:7px;background:#fff;border-radius:7px}
.pne-cycle-list label{font-size:12px;border:1px solid #e1eaf0;border-radius:5px;padding:3px 6px}
.pne-axis-tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:#f7fafb;border-radius:7px;border:1px solid #dce7ed;padding:8px;margin:6px 0}
.pne-axis-tools input{width:88px}
.pne-axis-tools label{font-size:12px}
.pne-axis-tools select{font-size:12px}

.overviewGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:20px 0}
.overviewChart{border:1px solid #dbe6eb;background:#fff;border-radius:10px;padding:13px;min-width:0}
.overviewChart h3{font-size:13px;margin:3px 0 12px;color:#29546a}
.smallplot{height:260px}
.overviewThresholds{background:#f7fafb;border:1px solid #e1ebf0;padding:12px 16px;border-radius:9px;margin-top:15px}
.overviewThresholds input[type=number]{width:100px}
@media(max-width:880px){.overviewGrid{grid-template-columns:1fr}}
.plot-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;margin:8px 0;color:#667e8d;font-size:12px;flex-wrap:wrap}
.plotbox .plot{touch-action:none;cursor:crosshair;user-select:none}
.plotbox .plot.is-panning{cursor:grabbing}
.plot-legend{display:flex;flex-wrap:wrap;gap:8px 14px;margin:8px 0 4px;color:#4d6879;font-size:11px;align-items:center}
.plot-legend-chip{display:inline-flex;gap:6px;align-items:center;white-space:nowrap}
.plot-legend-chip .swatch{width:17px;border-top:3px solid var(--swatch);display:inline-block}
.plot-legend-more{color:#6c8593;font-size:11px}
.cycle-spectrum{border:1px solid #dce8ee;border-radius:9px;padding:12px 14px;background:#f8fbfd;font-size:12px;margin-top:12px}
.cycle-spectrum .cycle-spectrum-row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.cycle-spectrum .cycle-spectrum-bar{width:min(460px,100%);height:12px;border-radius:5px;background:linear-gradient(90deg,hsl(220 75% 43%),hsl(175 75% 40%),hsl(115 72% 37%),hsl(60 85% 43%),hsl(20 80% 47%))}
.cycle-spectrum .key-dash{display:inline-block;width:22px;border-top:2px dashed #657683;margin-left:6px}
.cycle-spectrum .key-solid{display:inline-block;width:22px;border-top:2px solid #657683;margin-left:6px}


#toolNav{position:sticky;top:0;z-index:3;background:#fff;border:1px solid #d9e5eb;border-radius:10px;padding:10px;display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;box-shadow:0 2px 8px #123e551c}
#toolNav[hidden]{display:none}
#toolNav button.active{background:#176c99;color:#fff}
.filecheck{max-height:125px;overflow:auto;border:1px solid #d8e3ea;border-radius:7px;padding:8px;background:#fafcfd;margin:8px 0}
.filecheck label{display:block;white-space:nowrap;width:max-content;max-width:100%;padding:4px}
.filecheck label:hover{background:#e9f4fa}
.tablewrap{max-height:360px;overflow:auto;border:1px solid #d9e5ec;border-radius:8px;margin-top:12px}
table.data{border-collapse:collapse;font-size:12px;width:100%;white-space:nowrap}
table.data th,table.data td{border-bottom:1px solid #d8e3ea;border-right:1px solid #ecf0f3;padding:7px 10px;text-align:right}
table.data th{position:sticky;top:0;background:#eaf3f7;z-index:1;color:#1b526f}table.data td:first-child,table.data th:first-child{text-align:left}
table.data input{padding:4px;width:72px;border:1px solid #bed0db;border-radius:4px}
input[type=number]{padding:9px;width:94px;border:1px solid #cbdce6;border-radius:7px}
input.smalltext{width:160px;flex:0 1 auto}button.minor{font-size:12px;padding:7px 10px}
.pnetitle{margin:0 0 12px}.filepanel{background:#f6fafc;border:1px solid #deebf0;border-radius:8px;padding:10px;margin:10px 0}.reportline{white-space:pre-wrap;line-height:1.6;font-size:12px;color:#546a7a}

/* v2.6 UI workspace: visual-only layer.  Existing element IDs and APIs stay intact. */
:root{--pne-ink:#17384c;--pne-blue:#176c99;--pne-blue-soft:#eaf5fa;--pne-line:#d7e4ea;--pne-surface:#fff;--pne-muted:#607988}
body{background:linear-gradient(180deg,#edf5f8 0,#f7fafb 360px,#eef3f6 100%)}
header{padding:22px clamp(16px,4vw,48px);background:linear-gradient(115deg,#103d55,#176c99)}
header h1{font-size:clamp(20px,2.6vw,28px)}
header small{display:block;margin-top:5px}
body:has(#toolNav:not([hidden])) main{display:block;max-width:1550px;padding:0 clamp(12px,3vw,34px)}
body:has(#toolNav:not([hidden])) main > section.panel{min-width:0;margin-top:0}
body:has(#toolNav:not([hidden])) main > #toolNav{display:flex}
#toolNav{position:sticky;top:8px;z-index:10;max-width:none;min-width:0;display:flex;flex-direction:row;align-items:center;gap:7px;flex-wrap:wrap;padding:9px 11px;margin:14px 0;background:color-mix(in srgb,#fff 94%,#e5f4fb);border:1px solid var(--pne-line);border-radius:12px;box-shadow:0 8px 22px #123e5518}
#toolNav button{min-width:0;min-height:35px;text-align:center;padding:8px 12px;background:#fff}
#toolNav .muted{margin-left:auto;max-width:360px;font-size:11px}
#toolNav button.active{background:var(--pne-blue);border-color:var(--pne-blue);color:#fff}
.workspace-context{display:grid;grid-template-columns:minmax(210px,1fr) minmax(260px,2fr) auto;gap:14px;align-items:center;margin:0 0 18px;padding:13px 16px;border:1px solid var(--pne-line);border-radius:12px;background:var(--pne-surface);box-shadow:0 3px 12px #13405b0b}
.workspace-context__title{display:flex;flex-direction:column;gap:3px}.workspace-context__title b{color:var(--pne-ink)}
.workspace-context .filecheck{display:flex;gap:5px;max-height:74px;margin:0;padding:6px;align-items:flex-start;flex-wrap:wrap}.workspace-context .filecheck label{width:auto;max-width:250px;overflow:hidden;text-overflow:ellipsis;border:1px solid #e2edf2;border-radius:5px;padding:3px 6px}.workspace-context .filecheck input{margin:0 3px 0 0}
.workspace-context__actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}.workspace-context button{font-size:12px;padding:7px 9px}
.section-kicker{display:flex;align-items:center;gap:8px;margin:-2px 0 10px;font-size:12px;font-weight:700;color:#2a718f}.section-kicker span{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:#dff2fb;color:#12638d}
.workflow-stage{border:1px solid var(--pne-line);border-radius:11px;padding:14px 15px;margin:14px 0;background:#fbfdfe}.workflow-stage h3{margin:0 0 6px;font-size:14px;color:var(--pne-ink)}.workflow-stage .controls:last-child{margin-bottom:0}
.advanced-settings{margin:10px 0;border:1px solid var(--pne-line);border-radius:9px;background:#fbfdfe}.advanced-settings summary{cursor:pointer;padding:10px 12px;color:#31586c;font-size:12px;font-weight:700}.advanced-settings .controls{padding:0 12px 10px;margin:0}
.filepanel{background:#f7fbfd;border-color:#dcebf1}.filepanel>b{display:block;margin-bottom:4px;color:#284f63}.filecheck{max-height:152px;background:#fff}.filecheck label{padding:5px 6px;border-radius:5px;overflow:hidden;text-overflow:ellipsis}.filecheck label input{margin-right:5px}
.controls{align-items:flex-end}.controls label{min-width:min(100%,132px)}.controls input,.controls select{max-width:100%}
.tablewrap{max-height:min(58vh,540px);background:#fff}.tablewrap table{min-width:max-content}.tablewrap table.data th{top:0;box-shadow:0 1px 0 #cbdce4}.tablewrap table.data tbody tr:hover{background:#f2f9fc}
.plot{height:clamp(300px,48vh,520px)}.smallplot{height:clamp(220px,32vh,310px)}
@media(max-width:900px){#toolNav{top:0;border-radius:0;margin:0 -12px 12px;padding:8px 12px;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:thin}#toolNav button{flex:0 0 auto}#toolNav .muted{display:none}.workspace-context{grid-template-columns:1fr}.workspace-context__actions{justify-content:flex-start}.workspace-context .filecheck{max-height:102px}.panel{padding:17px 15px}.controls{gap:10px}.controls label{flex:1 1 140px}.controls button{flex:0 0 auto}}
@media(max-width:560px){header{padding:17px 16px}.importTitle{align-items:flex-start;flex-direction:column}.line{align-items:stretch}.line input[type=text]{width:100%;flex:1 1 100%}.dropzone{padding:18px 12px}.overviewGrid{gap:10px}.metrics .stat{min-width:calc(50% - 5px)}.workspace-context{padding:12px}.workspace-context .filecheck{display:block;max-height:116px}.workspace-context .filecheck label{max-width:100%;margin:2px 0}.controls label{flex-basis:100%}.controls button{flex:1 1 auto}.pne-cycle-tools .row{align-items:stretch}.pne-cycle-tools .row label{width:100%}}
`
const fullNav = `<nav id="toolNav" hidden><button data-page="summary" class="active">Summary</button><button data-page="raw">원본 그래프</button><button data-page="cycle">Cycle Life</button><button data-page="dqdv">dQ/dV · dV/dQ</button><button data-page="dcr">DCR · SOC</button><span class="muted">탭별 선택은 유지됩니다. 아래 공통 선택을 필요할 때만 전체 탭에 적용하세요.</span></nav>
<section id="workspaceContext" class="workspace-context" hidden aria-label="분석 공통 설정"><div class="workspace-context__title"><b>현재 분석 데이터</b><span id="workspaceDataset" class="muted">파일을 불러오면 채널이 표시됩니다.</span></div><div><div id="globalFiles" class="filecheck" aria-label="공통 채널 선택"></div></div><div class="workspace-context__actions"><button class="minor" id="globalAll">전체 선택</button><button class="minor" id="globalNone">해제</button><button class="primary" id="applyGlobalFiles">선택을 모든 분석에 적용</button></div></section>`
const fullHTML = `

<section class="panel" id="overviewPanel" hidden>
<h2>시험 Summary · 전체 Cycle 모니터링</h2>
<p class="muted">채널별 용량 유지율·방전 에너지 유지율·CE·온도 추이를 한 화면에서 확인합니다. 각 채널의 기준 Cycle은 100%로 정규화됩니다. 이상 구간은 그래프를 확대하거나 마우스를 올려 직접 확인하세요.</p>
<div class="filepanel"><b>조회 채널</b><div id="summaryFiles" class="filecheck"></div><button class="minor" data-check="summaryFiles" data-value="1">전체 선택</button> <button class="minor" data-check="summaryFiles" data-value="0">해제</button></div>
<div class="controls"><label>기준 Cycle (비우면 첫 유효 방전 Cycle)<input type="number" min="1" id="summaryBase" class="smalltext" placeholder="자동 선택"></label><button id="refreshSummary" class="primary">Summary 갱신</button><button id="summaryExport">Summary 지표 CSV 저장</button></div>
<div class="metrics" id="summaryKpis"></div>
<div class="overviewGrid">
<div class="overviewChart"><h3>방전 용량 유지율 (%)</h3><div class="plotbox"><canvas id="summaryRetentionPlot" class="plot smallplot"></canvas><div id="summaryRetentionHover" class="hoverlabel" hidden></div></div></div>
<div class="overviewChart"><h3>방전 에너지 유지율 (%)</h3><div class="plotbox"><canvas id="summaryEnergyPlot" class="plot smallplot"></canvas><div id="summaryEnergyHover" class="hoverlabel" hidden></div></div></div>
<div class="overviewChart"><h3>CE (%)</h3><div class="plotbox"><canvas id="summaryCEPlot" class="plot smallplot"></canvas><div id="summaryCEHover" class="hoverlabel" hidden></div></div></div>
<div class="overviewChart"><h3>Step 종료 온도 최댓값 (°C)</h3><div class="plotbox"><canvas id="summaryTempPlot" class="plot smallplot"></canvas><div id="summaryTempHover" class="hoverlabel" hidden></div></div></div>
</div>
<div id="summaryNote" class="reportline">데이터 분석을 마친 뒤 Summary가 표시됩니다.</div>
</section>
<section class="panel" id="fullLifePanel" hidden><h2>Cycle Life · 다중 채널 비교</h2><div class="muted">여러 Step이 있는 Cycle은 대표값(max)과 합계(sum)를 모두 제공해요. RPT/펄스 혼합 시 합계가 실제 용량을 대표하지 않을 수 있습니다.</div>
<div class="filepanel"><b>분석 채널</b><div id="lifeFiles" class="filecheck"></div><button class="minor" data-check="lifeFiles" data-value="1">전체 선택</button> <button class="minor" data-check="lifeFiles" data-value="0">해제</button></div>
<div class="controls"><label>기준 Cycle (각 채널에서 100%) <input id="lifeBase" class="smalltext" type="text" placeholder="첫 유효 방전 Cycle"></label><label>지표 <select id="fullLifeMetric"><option value="discharge">방전용량 대표값 (Ah)</option><option value="charge">충전용량 대표값 (Ah)</option><option value="dischargeSum">방전 Step 합계 (Ah)</option><option value="chargeSum">충전 Step 합계 (Ah)</option><option value="retention">용량 유지율 (%)</option><option value="energyRetention">방전 에너지 유지율 (%)</option><option value="dischargeEnergyRep">방전 에너지 대표값 (Wh)</option><option value="ce">Coulombic Efficiency (%)</option><option value="ee">Energy Efficiency (%)</option><option value="chargeEnergy">충전 에너지 (Wh)</option><option value="dischargeEnergy">방전 에너지 (Wh)</option><option value="temperature">최고 온도 (°C)</option><option value="imp10">10s 저항 (mΩ)</option><option value="cvTime">CV 시간 합계 (s)</option><option value="dvdt">Rest dV/dt (mV/h)</option></select></label>
<button class="primary" id="refreshFullLife">분석</button><button id="saveFullLife">선택 채널 CSV</button><button id="saveFullLifePNG">PNG 저장</button></div><div class="pne-axis-tools" data-plot-axis="fullLifePlot"></div><div class="plot-toolbar"><span>드래그: 영역 확대/이동 선택 · 휠: 화면 스크롤 · 더블클릭: 전체 보기</span><button class="minor" data-reset-plot="fullLifePlot">전체 보기</button></div><div class="plotbox"><canvas class="plot" id="fullLifePlot"></canvas><div class="hoverlabel" id="lifeHover" hidden></div></div><div id="fullLifeNote" class="reportline"></div></section>
<section class="panel" id="fullDQPanel" hidden><h2>독립 dQ/dV · dV/dQ 분석</h2>
<div class="filepanel"><b>분석 채널</b><div id="dqFiles" class="filecheck"></div><button class="minor" data-check="dqFiles" data-value="1">전체 선택</button> <button class="minor" data-check="dqFiles" data-value="0">해제</button></div>
<div class="section-kicker"><span>1</span>분석 범위</div><div class="controls"><label>Cycle 직접 입력 <input class="smalltext" type="text" id="fullDQCycles" value="1" placeholder="예: 1,5,10-50:5 · 비우면 전체" spellcheck="false" autocomplete="off" title="쉼표: 개별 선택 · 하이픈: 범위 · 콜론: 간격 · 빈칸: 전체"></label><span class="muted" style="align-self:center">예: 1,5,10-50:5 · 비우면 전체 · Enter로 분석</span><label>StepNo (선택) <input class="smalltext" type="text" id="fullDQStep" placeholder="최장 Step 자동"></label><label>방향 <select id="fullDQMode"><option value="both">충전 + 방전</option><option value="charge">충전</option><option value="discharge">방전</option></select></label><label>그래프 <select id="fullDQView"><option value="dqdv">dQ/dV</option><option value="dvdq">dV/dQ</option><option value="vq">V–Q</option></select></label><button class="primary" id="analyzeFullDQ">조건 적용 / 분석</button></div>
<details class="advanced-settings"><summary>고급 파라미터 · 보간, SG 평활화, 피크 검출</summary><div class="controls"><label>보간 포인트 <input type="number" min="50" max="20000" value="500" id="fullDQPoints"></label><label><input type="checkbox" id="fullDQSG" checked> SG 평활화</label><label>윈도 <input type="number" min="0" max="19999" value="0" id="fullDQWindow" title="0=자동"></label><label>다항식 차수 <input type="number" min="1" max="5" value="3" id="fullDQOrder"></label><label>피크 민감도 (%) <input type="number" min="1" max="100" value="5" id="fullDQPeak"></label><button id="dqDefaults">기본 조건 복원</button></div></details>
<div class="controls"><button id="saveFullDQ">곡선·피크 CSV 저장</button><button id="saveFullDQPNG">PNG 저장</button></div>
<div class="pne-cycle-tools" id="dqCycleTools"><h3>Cycle 곡선 관리</h3>
<div class="row"><label>강조할 Cycle <input id="dqHighlight" type="number" min="1" placeholder="예: 20" style="width:90px"></label><label>표시 <select id="dqFocusMode"><option value="normal">모두 표시</option><option value="dim">나머지 흐리게</option><option value="solo">강조만 표시</option></select></label><button class="minor" id="dqAllVisible">전체 표시</button><button class="minor" id="dqNoneVisible">모두 숨기기</button><button class="minor" id="dqResetVisible">선택 초기화</button></div>
<div class="pne-cycle-list" id="dqCycleList" aria-label="Cycle 곡선 표시 선택">분석한 뒤 Cycle별 체크박스가 나타납니다.</div>
<div class="muted">체크 해제: 해당 Cycle 숨김 · 숫자 강조: 선택 Cycle 굵게 · 곡선 클릭: 해당 Cycle 강조 · 변경 시 데이터 재계산 없음</div></div>
<div class="pne-axis-tools" data-plot-axis="fullDQPlot"></div>
<div class="plot-toolbar"><span>드래그: 영역 확대/이동 선택 · 휠: 화면 스크롤 · 더블클릭: 전체 보기</span><button class="minor" data-reset-plot="fullDQPlot">전체 보기</button></div><div class="plotbox"><canvas id="fullDQPlot" class="plot" title="휠 확대 · 드래그 이동 · 더블클릭 초기화"></canvas><div id="fullDQHover" class="hoverlabel" hidden></div></div><div id="dqCycleLegend" class="cycle-spectrum" aria-live="polite">분석 후 선택된 Cycle의 색상 범위를 표시합니다.</div><div id="fullDQNote" class="reportline">전압 정렬·보간·SG·미분을 수행합니다. 실제 CV 구간/최종 피크는 원본 곡선으로 검토하세요.</div>
<div class="tablewrap"><table class="data" id="dqPeakTable"><thead><tr><th>채널</th><th>Cycle</th><th>방향</th><th>Step</th><th>Peak V</th><th>Peak dQ/dV</th></tr></thead><tbody></tbody></table></div><button id="copyDQPeaks" class="minor">피크 표 TSV 복사</button></section>
<section class="panel" id="fullDCRPanel" hidden><h2>DCR / SOC 용량 균형 분석</h2><p class="muted">Pulse 검출 → return 용량 확인 → SOC block 수동 검토 → 1/10/30초 등 지정 시점 DCIR. 전압은 SOC 묶음 기준으로 사용하지 않습니다.</p>
<div class="filepanel"><b>분석 채널</b><div id="dcrFiles" class="filecheck"></div><button class="minor" data-check="dcrFiles" data-value="1">전체 선택</button> <button class="minor" data-check="dcrFiles" data-value="0">해제</button></div>
<div class="workflow-stage"><div class="section-kicker"><span>1</span>Pulse 자동 검출</div><div class="controls"><label>SOC setting StepNo <input class="smalltext" type="text" id="dcrSetting" placeholder="2,10,20-22"></label><label>DCIR 시간(s) <input class="smalltext" type="text" id="dcrTimes" value="1,10,30"></label><label>최소 펄스 전류(A) <input type="number" id="dcrMinI" value="0.5" min="0.05" step="0.1"></label><label>최대 펄스 시간(s) <input type="number" id="dcrMaxT" value="130" min="1" max="600"></label><label>return 허용오차(%) <input type="number" id="dcrTol" value="12" min="1" max="99"></label><button class="primary" id="analyzeFullDCR">Pulse 자동 검출</button></div><div class="reportline" id="dcrNotes">SOC setting StepNo를 지정하면 해당 Step은 pulse 후보에서 제외됩니다. 자동 판정 뒤에는 반드시 검토하세요.</div></div>
<div class="workflow-stage"><div class="section-kicker"><span>2</span>검토 및 SOC 보정</div><div class="muted">포함 체크 해제는 결과 표에서만 제외합니다. SOC block 및 SOC% 수정값은 아래 정리표와 내보내기에 반영됩니다. return 용량 불균형은 삭제하지 않고 표시합니다.</div><div class="tablewrap"><table class="data" id="dcrReview"><thead><tr><th>포함</th><th>채널</th><th>Cycle</th><th>Step</th><th>방향</th><th>전류(A)</th><th>Block</th><th>SOC%</th><th>V0(V)</th><th>Return(mAh)</th><th>불균형(%)</th><th>검토</th></tr></thead><tbody></tbody></table></div></div>
<div class="workflow-stage"><div class="section-kicker"><span>3</span>지정 시점 DCIR 결과</div><div class="controls"><label>결과 시간 <select id="dcrTimeTab"></select></label><button id="rebuildDCR">정리표 갱신</button><button id="copyDCR">정리표 TSV 복사</button><button id="exportDCRSummary">현재 탭 CSV 저장</button><button id="exportDCRExcel">전체 Excel 저장</button><button id="exportDCRRaw">Raw 결과 CSV 저장</button></div><div class="tablewrap"><table id="dcrSummaryTable" class="data"><thead></thead><tbody></tbody></table></div><label><input type="checkbox" id="showDCRRaw"> Raw 결과 상세 표 펼치기</label><div id="dcrRawWrapper" hidden class="tablewrap"><table id="dcrRawTable" class="data"><thead></thead><tbody></tbody></table></div></div>
</section>
`
const fullJS = `
// v2.0 extension — independent Cycle, Differential, and DCR views.
let fullLifeSeries=[],fullDQCurves=[],fullDCR={pulses:[],times:[],notes:[]};
const pnePanels=['analysis','graphPanel','lifePanel','dqdvPanel','overviewPanel','fullLifePanel','fullDQPanel','fullDCRPanel'];
function selectPage(page){if(!data||!data.length)return;const visible=page==='summary'?['overviewPanel']:page==='raw'?['analysis','graphPanel']:page==='cycle'?['fullLifePanel']:page==='dqdv'?['fullDQPanel']:['fullDCRPanel'];
  pnePanels.forEach(id=>{const el=$(id);if(el)el.hidden=!visible.includes(id)});
  document.querySelectorAll('#toolNav [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  if(page==='summary'){if(!summarySeries.length)refreshSummary();else drawSummaryOverview();}else if(page==='raw')updatePlot();else if(page==='cycle'){if(!fullLifeSeries.length)$('refreshFullLife').click();else drawFullLife();}else if(page==='dqdv'&&fullDQCurves.length)drawFullDQ();}
for(const b of document.querySelectorAll('#toolNav [data-page]'))b.onclick=()=>selectPage(b.dataset.page);
function fillFiles(id){const el=$(id);el.innerHTML=data.map(c=>'<label title="'+esc(c.name)+'"><input type="checkbox" data-ch="'+c.id+'" checked> '+esc(c.name)+'</label>').join('')}
function selectedFiles(id){return [...$(id).querySelectorAll('input:checked')].map(el=>Number(el.dataset.ch))}
document.querySelectorAll('[data-check]').forEach(el=>el.onclick=()=>{$(el.dataset.check).querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=el.dataset.value==='1')});
function setGlobalChecked(value){$('globalFiles').querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=value)}
function applyGlobalSelection(){const chosen=new Set(selectedFiles('globalFiles'));if(!chosen.size){alert('최소 한 채널을 선택하세요.');return}for(const id of ['summaryFiles','lifeFiles','dqFiles','dcrFiles']){$(id).querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=chosen.has(Number(c.dataset.ch)))}const active=document.querySelector('#toolNav button.active')?.dataset.page;if(active==='summary')refreshSummary();else if(active==='cycle')$('refreshFullLife').click();$('workspaceDataset').textContent=chosen.size+' / '+data.length+'개 채널을 모든 분석 탭에 적용했습니다.'}
$('globalAll').onclick=()=>setGlobalChecked(true);$('globalNone').onclick=()=>setGlobalChecked(false);$('applyGlobalFiles').onclick=applyGlobalSelection;
function onDataReady(){if(!data||!data.length)return;fillFiles('summaryFiles');fillFiles('lifeFiles');fillFiles('dqFiles');fillFiles('dcrFiles');fillFiles('globalFiles');fullLifeSeries=[];summarySeries=[];$('toolNav').hidden=false;$('workspaceContext').hidden=false;$('workspaceDataset').textContent=data.length+'개 채널이 준비되었습니다. 필요하면 공통 선택을 모든 분석 탭에 적용하세요.';selectPage('summary')}
const mainDataObserver=new MutationObserver(()=>{if(!$('analysis').hidden&&$('toolNav').hidden){onDataReady()}});mainDataObserver.observe($('analysis'),{attributes:true,attributeFilter:['hidden']});

// Summary overview — user-defined screening, never a pass/fail diagnosis.
let summarySeries=[];
let summaryReqVersion=0;
function summaryCfg(){return {channels:selectedFiles('summaryFiles'),base:$('summaryBase').value.trim()}}
async function refreshSummary(){
 const version=++summaryReqVersion;
 try{
  const cfg=summaryCfg();
  if(!cfg.channels.length)throw Error('최소 한 채널을 선택하세요');
  $('summaryNote').textContent='전체 Cycle 지표 계산 중…';
  const r=await req('/api/lifeFull','POST',cfg);
  if(version!==summaryReqVersion)return;
  summarySeries=r.series||[];
  drawSummaryOverview();
  $('summaryNote').textContent='방전용량/에너지는 해당 Cycle의 가장 큰 방전용량 Step을 대표값으로 선택합니다. 온도는 각 Step 종료 시점 측정값들의 최댓값이므로 실제 전체 Raw 최고온도와 다를 수 있습니다. 여러 펄스·RPT Step이 혼합된 Cycle에서는 값의 의미를 검증하세요.\n'+r.note;
 }catch(e){if(version!==summaryReqVersion)return;summarySeries=[];clearSummaryPlots();$('summaryNote').textContent='Summary 오류: '+e.message}
}
function clearSummaryPlots(){
 for(const id of ['summaryRetentionPlot','summaryEnergyPlot','summaryCEPlot','summaryTempPlot'])drawLines(id,[],'Cycle No','');
 $('summaryKpis').innerHTML='';
}
$('refreshSummary').onclick=refreshSummary;
let summaryDebounce=null;
$('summaryBase').addEventListener('input',()=>{clearTimeout(summaryDebounce);summaryDebounce=setTimeout(refreshSummary,350)});
$('summaryFiles').addEventListener('change',refreshSummary);
$('summaryExport').onclick=async()=>{
 try{const r=await req('/api/lifeFullExport','POST',summaryCfg());$('summaryNote').textContent='Summary 지표 CSV 저장: '+r.path}
 catch(e){$('summaryNote').textContent='Summary 저장 오류: '+e.message}
};
function isSummaryMetricValid(p,field){
 if(field==='retention')return p.hasDischarge&&p.retention>0;
 if(field==='energyRetention')return p.hasDischargeEnergy&&p.energyRetention>0;
 if(field==='ce')return p.hasCharge&&p.hasDischarge&&p.ce>0;
 if(field==='temperature')return p.hasTemperature;
 return Number.isFinite(p[field]);
}
function drawSummaryOverview(){
 const defs=[['summaryRetentionPlot','retention','방전 용량 유지율 (%)'],['summaryEnergyPlot','energyRetention','방전 에너지 유지율 (%)'],['summaryCEPlot','ce','CE (%)'],['summaryTempPlot','temperature','Step 종료 온도 최댓값 (°C)']];
 for(const [id,key,label] of defs){
  const lines=summarySeries.map(s=>({name:s.name.split('/').pop(),points:(s.points||[]).filter(p=>isSummaryMetricValid(p,key)).map(p=>[Number(p.cycle),Number(p[key])]).filter(pt=>pt.every(Number.isFinite))}));
  drawLines(id,lines,'Cycle No',label);
 }
 let cycles=0, validCycles=0, complete=0;
 for(const row of summarySeries){for(const p of (row.points||[])){
  cycles++;if(p.hasDischarge)validCycles++;
  if(p.hasDischarge&&p.hasCharge&&p.hasDischargeEnergy&&p.hasTemperature)complete++;
 }}
 const channels=summarySeries.length;
 const lastGood=summarySeries.map(v=>(v.points||[]).filter(p=>p.hasDischarge&&p.retention>0).at(-1)).filter(Boolean);
 const minLast=lastGood.length?Math.min(...lastGood.map(p=>p.retention)):null;
 const cards=[['조회 채널',String(channels)],['조회 Cycle × 채널',String(cycles)],['방전용량 유효',String(validCycles)+' / '+String(cycles)],['마지막 유지율 최저',minLast===null?'N/A':minLast.toFixed(2)+'%'],['지표 누락 Cycle',String(cycles-complete)]];
 $('summaryKpis').innerHTML=cards.map(([k,v])=>'<div class="stat"><b>'+esc(v)+'</b><span>'+esc(k)+'</span></div>').join('');
}

function lifeCfg(){return {channels:selectedFiles('lifeFiles'),base:$('lifeBase').value.trim()}}
$('refreshFullLife').onclick=async()=>{try{$('fullLifeNote').textContent='Cycle 성능 계산 중…';const r=await req('/api/lifeFull','POST',lifeCfg());fullLifeSeries=r.series||[];drawFullLife();$('fullLifeNote').textContent=($('lifeBase').value.trim()?'기준 Cycle '+$('lifeBase').value.trim()+' = 100% (각 채널별 기준 방전용량) · ':'첫 유효 방전 Cycle = 100% · ')+r.note}catch(e){fullLifeSeries=[];drawLines('fullLifePlot',[],'Cycle No','');$('fullLifeNote').textContent='Cycle 분석 오류: '+e.message}};
$('fullLifeMetric').onchange=drawFullLife;
let baseRefreshTimer=null;
$('lifeBase').addEventListener('input',()=>{clearTimeout(baseRefreshTimer);baseRefreshTimer=setTimeout(()=>$('refreshFullLife').click(),400)});
$('lifeFiles').addEventListener('change',()=>$('refreshFullLife').click());
function drawFullLife(){const field=$('fullLifeMetric').value;const lines=fullLifeSeries.map(s=>({name:s.name.replace(/^.*\//,''),points:(s.points||[]).filter(p=>p[field]!==undefined && (!['retention','discharge','dischargeSum','energyRetention','dischargeEnergyRep','dischargeEnergy'].includes(field)||p.hasDischarge) && (field!=='energyRetention'||p.hasDischargeEnergy) && (field!=='ce'||(p.hasCharge&&p.hasDischarge)) && (field!=='ee'||(p.chargeEnergy>0&&p.dischargeEnergy>0)) && (field!=='temperature'||p.hasTemperature)).map(p=>[Number(p.cycle),Number(p[field])]).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]))}));drawLines('fullLifePlot',lines,'Cycle No',$('fullLifeMetric').selectedOptions[0].textContent);
}
$('saveFullLife').onclick=async()=>{try{const r=await req('/api/lifeFullExport','POST',lifeCfg());$('fullLifeNote').textContent='CSV 저장: '+r.path}catch(e){$('fullLifeNote').textContent='저장 오류: '+e.message}};
$('saveFullLifePNG').onclick=()=>savePNG('fullLifePlot','PNE_Cycle_Life_v23.png');
function dqCfg(){return {channels:selectedFiles('dqFiles'),cycles:$('fullDQCycles').value.trim(),step:$('fullDQStep').value.trim(),mode:$('fullDQMode').value,points:Number($('fullDQPoints').value),smooth:$('fullDQSG').checked,window:Number($('fullDQWindow').value),order:Number($('fullDQOrder').value),sensitivity:Number($('fullDQPeak').value)/100}}
$('dqDefaults').onclick=()=>{$('fullDQPoints').value='500';$('fullDQSG').checked=true;$('fullDQWindow').value='0';$('fullDQOrder').value='3';$('fullDQPeak').value='5';$('fullDQMode').value='both';$('fullDQNote').textContent='기본 설정으로 복원했어. 조건 적용 / 분석을 눌러줘.'};
$('analyzeFullDQ').onclick=async()=>{try{$('fullDQNote').textContent='선택한 Cycle의 Raw 데이터 계산 중…';const r=await req('/api/dqFull','POST',dqCfg());fullDQCurves=r.curves||[];dqShownCycles.clear();refreshDQCycleList();drawFullDQ();renderDQPeaks();const err=fullDQCurves.filter(c=>c.error).map(c=>c.name+' C'+c.cycle+' '+c.direction+': '+c.error);$('fullDQNote').textContent=r.note+'\n'+fullDQCurves.length+'개 곡선'+(err.length?'\n제외: '+err.join('; '):'')}catch(e){$('fullDQNote').textContent='dQ/dV 분석 오류: '+e.message}};
$('fullDQView').onchange=drawFullDQ;
$('fullDQCycles').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('analyzeFullDQ').click()}});
function drawFullDQ(){
 const view=$('fullDQView').value;
 const curves=fullDQCurves.filter(c=>(c.points||[]).length && dqShownCycles.has(Number(c.cycle)) && ($('dqFocusMode').value!=='solo' || $('dqHighlight').value.trim()==='' || Number(c.cycle)===Number($('dqHighlight').value)));
 const nums=curves.map(c=>Number(c.cycle)).filter(Number.isFinite);
 const lo=nums.length?Math.min(...nums):0,hi=nums.length?Math.max(...nums):0;
 const lines=curves.map(c=>({
  name:c.name.replace(/^.*\//,'')+' C'+c.cycle+' '+(c.direction==='charge'?'충전':'방전'),
  color:cycleGradientColor(Number(c.cycle),lo,hi),
  dash:c.direction==='discharge',
  cycle:Number(c.cycle),direction:c.direction,
  selected: Number($('dqHighlight').value)===Number(c.cycle) && $('dqHighlight').value.trim()!=='',
  muted: $('dqFocusMode').value==='dim' && $('dqHighlight').value.trim()!=='' && Number($('dqHighlight').value)!==Number(c.cycle),
  points:c.points.map(p=>{if(view==='vq')return [p.q,p.v];if(view==='dvdq')return [p.v,Math.abs(p.smooth)<1e-9?NaN:1/p.smooth];return [p.v,p.smooth]}).filter(p=>p.every(Number.isFinite))
 }));
 drawLines('fullDQPlot',lines,view==='vq'?'Capacity (Ah)':'Voltage (V)',view==='vq'?'Voltage (V)':view==='dvdq'?'dV/dQ (V/Ah)':'dQ/dV (Ah/V)');
 updateDQGradientLegend(curves,lo,hi);
}
const dqShownCycles=new Set();
function refreshDQCycleList(){
 const cycles=[...new Set(fullDQCurves.filter(c=>(c.points||[]).length).map(c=>Number(c.cycle)).filter(Number.isFinite))].sort((a,b)=>a-b);
 const old=new Set(dqShownCycles);
 dqShownCycles.clear();for(const c of cycles)if(!old.size||old.has(c))dqShownCycles.add(c);
 const el=$('dqCycleList');el.replaceChildren();
 for(const c of cycles){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=dqShownCycles.has(c);input.dataset.cycle=String(c);label.append(input,document.createTextNode(' C'+c));el.append(label)}
 if(!cycles.length)el.textContent='유효한 Cycle 곡선이 없습니다.';
}
$('dqCycleList').addEventListener('change',e=>{const c=Number(e.target.dataset.cycle);if(!Number.isFinite(c))return;if(e.target.checked)dqShownCycles.add(c);else dqShownCycles.delete(c);drawFullDQ()});
function dqSetAll(show){for(const el of $('dqCycleList').querySelectorAll('input[data-cycle]')){el.checked=show;if(show)dqShownCycles.add(Number(el.dataset.cycle));else dqShownCycles.delete(Number(el.dataset.cycle))}drawFullDQ()}
$('dqAllVisible').onclick=()=>dqSetAll(true);
$('dqNoneVisible').onclick=()=>dqSetAll(false);
$('dqResetVisible').onclick=()=>{$('dqHighlight').value='';$('dqFocusMode').value='normal';dqSetAll(true)};
$('dqHighlight').addEventListener('input',drawFullDQ);
$('dqFocusMode').addEventListener('change',drawFullDQ);
function renderDQPeaks(){const body=$('dqPeakTable').querySelector('tbody');body.innerHTML=fullDQCurves.flatMap(c=>(c.peaks||[]).map(p=>'<tr><td>'+esc(c.name)+'</td><td>'+esc(c.cycle)+'</td><td>'+esc(c.direction)+'</td><td>'+esc(c.step)+'</td><td>'+p.voltage.toFixed(5)+'</td><td>'+p.value.toFixed(5)+'</td></tr>')).join('')}
$('saveFullDQ').onclick=async()=>{try{const r=await req('/api/dqFullExport','POST',dqCfg());$('fullDQNote').textContent='곡선 CSV: '+r.path+'\n피크 CSV: '+r.peaks}catch(e){$('fullDQNote').textContent='CSV 저장 오류: '+e.message}};
$('saveFullDQPNG').onclick=()=>savePNG('fullDQPlot','PNE_dQdV_v23.png');
function tableTSV(id){const trs=[...$(id).querySelectorAll('tr')];return trs.map(tr=>[...tr.querySelectorAll('th,td')].map(td=>{const i=td.querySelector('input');return i?i.type==='checkbox'?i.checked?'Y':'N':i.value:td.textContent.trim()}).join('\t')).join('\n')}
async function copyTSV(id){const v=tableTSV(id);try{await navigator.clipboard.writeText(v)}catch(e){const t=document.createElement('textarea');t.value=v;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove()}}
$('copyDQPeaks').onclick=()=>copyTSV('dqPeakTable');
function dcrCfg(){const times=$('dcrTimes').value.split(',').map(x=>Number(x.trim()));if(!times.length||times.some(x=>!Number.isFinite(x)||x<.05||x>600))throw Error('시간은 1,10,30처럼 입력하세요');return {channels:selectedFiles('dcrFiles'),times,settingSteps:$('dcrSetting').value.trim(),minCurrentA:Number($('dcrMinI').value),maxPulseSec:Number($('dcrMaxT').value),balanceTolerance:Number($('dcrTol').value)/100}}
$('analyzeFullDCR').onclick=async()=>{try{$('dcrNotes').textContent='각 채널의 Pulse 및 return 용량 검사 중…';fullDCR=await req('/api/dcrFull','POST',dcrCfg());renderDCRReview();$('dcrTimeTab').innerHTML=fullDCR.times.map(t=>'<option value="'+t+'">DCIR '+t+'s</option>').join('');rebuildDCR();$('dcrNotes').textContent=(fullDCR.pulses||[]).length+'개 Pulse 후보 · '+(fullDCR.notes||[]).join('\n')}catch(e){$('dcrNotes').textContent='DCR 분석 오류: '+e.message}};
function renderDCRReview(){const t=$('dcrReview').querySelector('tbody');t.innerHTML=(fullDCR.pulses||[]).map((p,i)=>'<tr><td><input data-index="'+i+'" data-field="include" type="checkbox" '+(p.include?'checked':'')+'></td><td>'+esc(p.source)+'</td><td>'+esc(p.cycle)+'</td><td>'+esc(p.step)+'</td><td>'+esc(p.direction)+'</td><td>'+p.currentA.toFixed(2)+'</td><td><input type="number" data-index="'+i+'" data-field="block" min="1" value="'+p.block+'"></td><td><input data-index="'+i+'" data-field="soc" type="text" value="'+esc(p.soc||'')+'" placeholder="예: 50"></td><td>'+p.v0.toFixed(4)+'</td><td>'+p.returnCapacityMAh.toFixed(3)+'</td><td>'+p.balanceError.toFixed(1)+'</td><td>'+esc(p.review||'')+'</td></tr>').join('')}
$('dcrReview').addEventListener('change',e=>{const t=e.target;if(!t.dataset.field)return;const p=fullDCR.pulses[Number(t.dataset.index)];if(!p)return;switch(t.dataset.field){case 'include':p.include=t.checked;break;case 'block':p.block=Math.max(1,Number(t.value));break;case 'soc':p.soc=t.value.trim();break}rebuildDCR()});
function rebuildDCR(){const t=Number($('dcrTimeTab').value);if(!fullDCR.pulses)return;const rep={};const rows=fullDCR.pulses.filter(p=>p.include).map(p=>{const key=p.source+'/'+p.cycle+'/'+p.block+'/'+p.soc+'/'+p.direction+'/'+p.currentA;rep[key]=(rep[key]||0)+1;const x=p.values.find(v=>Math.abs(v.time-t)<1e-5);const val=x&&x.available?x.resistance.toFixed(3):'N/A';return '<tr><td>'+esc(p.source)+'</td><td>'+esc(p.cycle)+'</td><td>'+p.block+'</td><td>'+esc(p.soc||'미지정')+'</td><td>'+p.v0.toFixed(4)+'</td><td>'+esc(p.direction)+'</td><td>'+p.currentA.toFixed(2)+'</td><td>'+rep[key]+'</td><td>'+esc(p.step)+'</td><td>'+val+'</td><td>'+esc(p.review||'')+'</td></tr>'});
 $('dcrSummaryTable').querySelector('thead').innerHTML='<tr><th>파일</th><th>Cycle</th><th>Block</th><th>SOC%</th><th>V0(V)</th><th>방향</th><th>전류(A)</th><th>반복</th><th>Step</th><th>DCIR '+t+'s (mΩ)</th><th>검토</th></tr>';$('dcrSummaryTable').querySelector('tbody').innerHTML=rows.join('');
 const rawRows=fullDCR.pulses.filter(p=>p.include).flatMap(p=>p.values.map(v=>'<tr><td>'+esc(p.source)+'</td><td>'+esc(p.step)+'</td><td>'+esc(p.v0Source)+'</td><td>'+v.time+'</td><td>'+v.voltage.toFixed(5)+'</td><td>'+v.current.toFixed(2)+'</td><td>'+(v.available?v.resistance.toFixed(3):'N/A')+'</td></tr>'));
 $('dcrRawTable').querySelector('thead').innerHTML='<tr><th>파일</th><th>Step</th><th>V0 기준</th><th>시간(s)</th><th>전압(V)</th><th>전류(A)</th><th>DCIR(mΩ)</th></tr>';$('dcrRawTable').querySelector('tbody').innerHTML=rawRows.join('')}
$('dcrTimeTab').onchange=rebuildDCR;$('rebuildDCR').onclick=rebuildDCR;$('showDCRRaw').onchange=()=>$('dcrRawWrapper').hidden=!$('showDCRRaw').checked;
$('copyDCR').onclick=()=>copyTSV('dcrSummaryTable');
async function exportDCR(format){try{const r=await req('/api/dcrFullExport','POST',{items:fullDCR.pulses,times:fullDCR.times,format,time:Number($('dcrTimeTab').value)});$('dcrNotes').textContent='저장: '+r.path}catch(e){$('dcrNotes').textContent='DCR 저장 오류: '+e.message}}
$('exportDCRSummary').onclick=()=>exportDCR('summary');$('exportDCRExcel').onclick=()=>exportDCR('excel');$('exportDCRRaw').onclick=()=>exportDCR('raw');
// Chart drawing, hover coordinates, zoom and pan are handled by the v2.3 plot engine.
const overviewHoverIDs={summaryRetentionPlot:'summaryRetentionHover',summaryEnergyPlot:'summaryEnergyHover',summaryCEPlot:'summaryCEHover',summaryTempPlot:'summaryTempHover',fullDQPlot:'fullDQHover'};
for(const [id,tipid] of Object.entries(overviewHoverIDs)){
 const el=$(id);if(!el)continue;
 el.addEventListener('mousemove',e=>pneHover(id,e));
 el.addEventListener('mouseleave',()=>{const t=$(tipid);if(t)t.hidden=true});
}
let resizeSummaryTimer=null;
window.addEventListener('resize',()=>{
 clearTimeout(resizeSummaryTimer);
 resizeSummaryTimer=setTimeout(()=>{
  if(!$('overviewPanel').hidden&&summarySeries.length)drawSummaryOverview();
  if(!$('fullLifePanel').hidden&&fullLifeSeries.length)drawFullLife();
  if(!$('fullDQPanel').hidden&&fullDQCurves.length)drawFullDQ();
 },150);
});
`
