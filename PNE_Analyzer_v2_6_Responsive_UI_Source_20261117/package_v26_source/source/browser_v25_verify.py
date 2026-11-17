import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
async def run():
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':1480,'height':920}, device_scale_factor=1)
  errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.set_content(Path(__file__).with_name('test_render_preview.html').read_text())
  await page.evaluate('''() => {
    const curves=[{name:'CH038 C5 charge',cycle:5,points:Array.from({length:101},(_,i)=>[2.5+i*.017,Math.exp(-Math.pow((i-55)/15,2))])}];
    document.querySelector('#fullDQPanel').hidden=false;
    drawLines('fullDQPlot',curves,'Voltage (V)','dQ/dV (Ah/V)');
  }''')
  plot=page.locator('#fullDQPlot')
  await plot.scroll_into_view_if_needed()
  bounds=await plot.bounding_box()
  point=await page.evaluate('''() => {const s=pneChartStates.get('fullDQPlot');const h=pneHoverInfo.get('fullDQPlot');return h.items.find(p=>p.pointIndex===55)}''')
  assert point, 'point missing'
  await page.mouse.move(bounds['x']+point['px'],bounds['y']+point['py'])
  await page.wait_for_timeout(80)
  tip=page.locator('.pne-canvas-frame .pne-coordinate-tip').last
  assert await tip.is_visible(), 'hover tooltip hidden'
  assert 'CH038 C5 charge' in await tip.inner_text()
  overlay=page.locator('.pne-canvas-frame .pne-overlay').last
  assert await overlay.count()==1
  await page.mouse.move(bounds['x']+bounds['width']*.22,bounds['y']+bounds['height']*.28)
  await page.mouse.down()
  await page.mouse.move(bounds['x']+bounds['width']*.78,bounds['y']+bounds['height']*.7,steps=12)
  assert '확대 영역 선택' in await tip.inner_text(), await tip.inner_text()
  await page.screenshot(path=str(Path(__file__).with_name('browser_v25_drag.png')))
  await page.mouse.up()
  await page.wait_for_timeout(40)
  old=await page.evaluate('pneChartStates.get("fullDQPlot").domain')
  new=await page.evaluate('pneChartStates.get("fullDQPlot").view')
  assert new['x1']-new['x0'] < old['x1']-old['x0']
  assert not errors,errors
  print('PASS hover marker tooltip + live rectangle/coordinates + zoom + 0 JS errors')
  await browser.close()
if __name__=='__main__':asyncio.run(run())
