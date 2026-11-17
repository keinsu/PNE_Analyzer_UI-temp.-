from pathlib import Path
import re
folder=Path(__file__).parent

def raw(path,var):
 s=(folder/path).read_text()
 m=re.search(r'const '+re.escape(var)+r' = `(.*?)`',s,re.S)
 assert m,(path,var)
 return m.group(1)
html=raw('gui_html.go','guiHTML'); css=raw('full_gui.go','fullCSS');nav=raw('full_gui.go','fullNav');ext=raw('full_gui.go','fullHTML');js=raw('full_gui.go','fullJS')+raw('plot_enhance.go','plotEnhanceJS')
html=html.replace('</style>',css+'</style>')
html=html.replace('<section class="panel" id="analysis"',nav+'<section class="panel" id="analysis"')
html=html.replace('</main>',ext+'</main>')
html=html.replace('</script>',js+'</script>')
html=html.replace('__TOKEN__','localTestToken').replace('__INITIAL__','""')
(folder/'test_render_preview.html').write_text(html,encoding='utf8')
(Path('/tmp')/'pne_combined_v23.js').write_text(html.split('<script>')[1].split('</script>')[0],encoding='utf8')
print('html bytes',len(html.encode()))
print('html expected IDs',[(x,x in html) for x in ['rawX','summaryRetentionPlot','summaryEnergyPlot','exportRawSelected']]);
