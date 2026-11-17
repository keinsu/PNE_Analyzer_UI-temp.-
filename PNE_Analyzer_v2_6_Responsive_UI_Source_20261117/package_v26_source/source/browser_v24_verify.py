import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
async def run():
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':1600,'height':950})
  errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.set_content(Path(__file__).with_name('test_render_preview.html').read_text())
  await page.evaluate('''() => {
   window.fetch=async(path,opt)=>{
    let result={};
    if(String(path).includes('/api/lifeFull')){
     result={series:[{name:'M02Ch038',points:Array.from({length:50},(_,i)=>({cycle:i+1,ce:99+i/100,retention:100-i/10,hasCharge:true,hasDischarge:true}))}],note:'synthetic'};
    }else if(String(path).includes('/api/dqFull')){
     result={curves:Array.from({length:48},(_,j)=>["charge","discharge"].map(dir=>({name:'M02Ch038[038]',cycle:j+3,direction:dir,step:'7',points:Array.from({length:100},(_,i)=>({v:2.5+i/60,q:i/100,smooth:Math.exp(-Math.pow((i-48)/15,2))*(dir==='charge'?1:-1)})),peaks:[]}))).flat(),note:'synthetic'};
    } else result={path:'D:/Downloads/test.csv'};
    return {ok:true,json:async()=>result,text:async()=>JSON.stringify(result)};
   };
   data=[{id:0,name:'M02Ch038',cycles:Array.from({length:55},(_,i)=>String(i+1)),steps:['2','3','7']}];onDataReady();updateChannel();
  }''')
  await page.locator('[data-page=dqdv]').click()
  await page.locator('#fullDQCycles').fill('3-50')
  await page.locator('#analyzeFullDQ').click()
  await page.wait_for_timeout(600)
  assert await page.locator('#dqCycleList input').count()==48
  assert await page.evaluate('pneChartStates.get("fullDQPlot").lines.length')==96
  await page.locator('#dqCycleList input[data-cycle="12"]').uncheck()
  assert await page.evaluate('pneChartStates.get("fullDQPlot").lines.length')==94
  await page.locator('#dqHighlight').fill('30')
  await page.locator('#dqFocusMode').select_option('dim')
  assert await page.evaluate('pneChartStates.get("fullDQPlot").lines.filter(l=>l.selected).length')==2
  assert await page.evaluate('pneChartStates.get("fullDQPlot").lines.filter(l=>l.muted).length')==92
  await page.locator('#dqFocusMode').select_option('solo')
  assert await page.evaluate('pneChartStates.get("fullDQPlot").lines.length')==2
  await page.locator('#dqResetVisible').click()
  assert await page.evaluate('pneChartStates.get("fullDQPlot").lines.length')==96
  box=page.locator('#fullDQPlot');await box.scroll_into_view_if_needed();rect=await box.bounding_box()
  original=await page.evaluate('pneChartStates.get("fullDQPlot").view')
  await page.mouse.move(rect['x']+rect['width']*.30,rect['y']+rect['height']*.32)
  await page.mouse.down();await page.mouse.move(rect['x']+rect['width']*.72,rect['y']+rect['height']*.75,steps=8);await page.mouse.up()
  await page.wait_for_timeout(100)
  zoom=await page.evaluate('pneChartStates.get("fullDQPlot").view')
  assert zoom['x1']-zoom['x0']<original['x1']-original['x0'],(original,zoom)
  await page.locator('[data-reset-plot="fullDQPlot"]').click()
  await page.locator('[data-plot-axis="fullDQPlot"] .axis-x0').fill('3.1')
  await page.locator('[data-plot-axis="fullDQPlot"] .axis-x1').fill('3.9')
  await page.locator('[data-plot-axis="fullDQPlot"] .axis-apply').click()
  x=await page.evaluate('pneChartStates.get("fullDQPlot").view')
  assert x['x0']==3.1 and x['x1']==3.9,x
  await page.locator('[data-plot-axis="fullDQPlot"] .axis-reset').click()
  await page.locator('[data-page=cycle]').click()
  await page.wait_for_timeout(100)
  await page.locator('#fullLifeMetric').select_option('ce')
  await page.locator('#lifeBase').fill('5')
  await page.wait_for_timeout(150)
  assert await page.locator('#fullLifeMetric').input_value()=='ce'
  await page.locator('[data-page=dqdv]').click()
  await page.screenshot(path=str(Path(__file__).with_name('browser_v24.png')),full_page=False)
  assert not errors,errors
  print('PASS: 48 cycles, 96 curves, hide/highlight/solo, zoom, custom axis, life metric, 0 JS errors')
  await browser.close()
if __name__=='__main__':asyncio.run(run())
