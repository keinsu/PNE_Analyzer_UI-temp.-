"""Creates synthetic fixtures ONLY; no PNE/real company data."""
from pathlib import Path
import struct, zipfile, io, csv
out=Path(__file__).parent/'synthetic_test_nested.zip'
headers=['Module','Channel','StartTime','EndTime','StepNo','StepType','StateCode','StepTime','TotalTime','Voltage','Current','Capacity','Power','WattHour','CurCycle','TotalCycle']
steps=[
 (2,'Rest','00:10:00.00',[(0,3000,0,0),(300,2995,0,0),(600,2985,0,0)],'STEP 시간',1,1),
 (3,'방전','00:04:18.00',[(0,2985,0,0),(.1,2964,-6060,0.0001),(1,2958,-6063,1.6),(60,2800,-6060,101),(258,2500,-6060,435)],'STEP 전압',1,1),
 (4,'Rest','00:30:00.00',[(0,2500,0,0),(300,2650,0,0),(1800,2781,0,0)],'STEP 시간',1,1),
 (7,'충전','00:05:00.00',[(0,2781,0,0),(100,3600,5000,139),(200,4000,5000,278),(300,4200,5000,417)],'STEP 전압',1,2),
]
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
 for ch in (13,14,15):
  folder=f'Outer/TestSession/M01Ch{ch:03d}[{ch:03d}]'
  stem=f'Synthetic_M01Ch{ch:03d}'
  recs=[];ix=0
  csvrows=[]
  for sno,typ,dur,measurements,state,curcyc,totcyc in steps:
   for t,v,i,cap in measurements:
    rb=bytearray(176)
    vals={0:float(ix),4:float(t),8:0.,16:float(v),20:float(i),24:float(cap),32:float(cap) if i>0 else 0.,36:float(cap) if i<0 else 0.,40:float(cap*v/1000),64:24.1,68:2.,72:float(i*v/1000),112:1.3,116:1.6,120:1.9,124:2.7,128:3.0}
    for offset,value in vals.items():struct.pack_into('<f',rb,offset,value)
    recs.append(rb);ix+=1
   et,v,i,c=measurements[-1]
   csvrows.append([1,ch,'2026-11-01 10:00','2026-11-01 11:00',sno,typ,state,dur,dur,f'{v/1000:.5f}',f'{i/1000:.4f}',f'{c/1000:.5f}',0,0,curcyc,totcyc])
  z.writestr(f'{folder}/{stem}.cyc',bytes(72)+b''.join(recs))
  string=io.StringIO(newline='');writer=csv.writer(string);writer.writerow(headers);writer.writerows(csvrows)
  # Test cp949 fallback for second channel
  codec='cp949' if ch==14 else 'utf-8-sig'
  z.writestr(f'{folder}/{stem}_StepEnd.csv',string.getvalue().encode(codec))
  z.writestr(f'{folder}/{stem}.sch','fake schedule not used')
  z.writestr(f'{folder}/StepStart/something.txt','ignored')
 z.writestr('../unsafe.cyc',b'bad path')
print(out)
