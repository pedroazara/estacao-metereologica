import pcbnew,pathlib,sys,json,math,random
from array import array
BASE=pathlib.Path(r"C:\Users\pedro\OneDrive\Documentos\programação\estacao-metereologica\hardware\skylog")
OUT=BASE/'documentacao'/'ens160-aht21-2026-10-02'
sys.path.insert(0,str(BASE/'documentacao'/'roteamento-2026-10-02'));import route_single as rt
rt.X0=167.;rt.Y0=15.;rt.W=426;rt.H=556;rt.N=rt.W*rt.H
b=pcbnew.LoadBoard(str(OUT/'antes-skylog.kicad_pcb'));fps={f.GetReference():f for f in b.GetFootprints()};pads={(ref,p.GetNumber()):p for ref,f in fps.items() for p in f.Pads()}
root='/350f3427-bcec-49b0-ac2f-a03c28574f3d/'
def fp(ref,value,lib,pos,angle,uid):
 f=pcbnew.FootprintLoad(str(BASE/'skylog.pretty'),lib);f.SetReference(ref);f.SetValue(value);f.SetFPID(pcbnew.LIB_ID('skylog',lib));f.SetOrientationDegrees(angle);f.SetPosition(rt.iv(pos));f.SetPath(pcbnew.KIID_PATH(root+uid));b.Add(f);fps[ref]=f
 for p in f.Pads():pads[ref,p.GetNumber()]=p
 return f
j=fp('J1','ENS160+AHT21','ENS160_AHT21_1x04_P2.54mm',(207,25.5),90,'fb0e3071-73d6-4a05-b199-b7047537637a')
p1=fp('JP1','FIO / 0R','Wire_Jumper_P10.00mm',(238.4,39.8),180,'c608831c-a2bd-4b20-95a6-20081a5bc7c2')
p2=fp('JP2','FIO / 0R','Wire_Jumper_P10.00mm',(211.4,30),0,'55930f7f-ddc0-481b-83d4-a3b932f40fe8')
p1.Reference().SetPosition(rt.iv((239.5,42.5)))
for item in fps['U4'].GraphicalItems():
 if hasattr(item,'GetText') and item.GetLayer()==pcbnew.F_SilkS:item.SetPosition(rt.iv((rt.xy(item.GetPosition())[0],30.7)))
for item in fps['U3'].GraphicalItems():
 if hasattr(item,'GetText') and item.GetLayer()==pcbnew.F_SilkS:item.SetPosition(rt.iv((rt.xy(item.GetPosition())[0],19.7)))
j.GetField('Description').SetText('Interface por cabo para modulo externo ENS160+AHT21, alimentado em 3V3. Numeros pertencem ao conector J1 desta PCB, nao a pinagem fisica do modulo: 1=3V3, 2=GND, 3=SDA, 4=SCL. Conferir rotulos do modulo ao montar o cabo.')
j.GetField('Datasheet').SetText('https://github.com/pedroazara/estacao-metereologica/tree/wip/checkpoint-app-base/firmware/teste_ens160_aht21')
for jum in [p1,p2]:jum.GetField('Description').SetText('Jumper montado com fio isolado ou resistor zero ohm compativel; distancia entre furos de 10 mm. Ponte para manter todas as trilhas na mesma face da PCB.')
for name,pin,target,ref in [('/ENS_SDA','3',('U4','2'),'JP1'),('/ENS_3V3','1',('U4','5'),'JP2')]:
 new=pcbnew.NETINFO_ITEM(b,name);b.Add(new);pads['J1',pin].SetNet(new);pads[ref,'1'].SetNet(new);pads[ref,'2'].SetNet(pads[target].GetNet())
pads['J1','2'].SetNet(pads['U4','4'].GetNet());pads['J1','4'].SetNet(pads['U4','3'].GetNet())
grid=array('Q',[0])*rt.N
ant=(177.03012,38.08344,195.01332,43.97624);xmin,xmax,ymin,ymax=rt.bounds((ant[0],ant[1]),(ant[2],ant[3]),.45)
for y in range(ymin,ymax+1):
 for x in range(xmin,xmax+1):grid[y*rt.W+x]|=1
for f in b.GetFootprints():
 for p in f.Pads():rt.padpaint(grid,p)
for t in b.GetTracks():rt.segpaint(grid,rt.xy(t.GetStart()),rt.xy(t.GetEnd()),.5,1<<t.GetNetCode())
edges=[(('J1','1'),('JP2','1')),(('JP2','2'),('U4','5')),(('J1','3'),('JP1','1')),(('JP1','2'),('U4','2')),(('J1','4'),('U4','3')),(('J1','2'),('U4','4'))]
seed=int(sys.argv[1]) if len(sys.argv)>1 else 0
orders=[[1,3,0,2,4,5],[0,2,1,3,4,5],[4,5,0,1,2,3],[2,0,3,1,5,4],[1,0,3,2,5,4],[3,2,1,0,4,5]]
order=orders[seed] if seed<len(orders) else random.Random(seed).sample(range(6),6)
fail=[];log=[]
for k in order:
 ka,kb=edges[k];a=rt.xy(pads[ka].GetPosition());z=rt.xy(pads[kb].GetPosition());code=pads[ka].GetNetCode();path=rt.astar(grid,a,z,code)
 if path is None:fail.append(k);continue
 pts=rt.simpl(path,a,z)
 for v,w in zip(pts,pts[1:]):
  if rt.dst(v,w)<.000001:continue
  t=pcbnew.PCB_TRACK(b);t.SetStart(rt.iv(v));t.SetEnd(rt.iv(w));t.SetLayer(pcbnew.F_Cu);t.SetNetCode(code);t.SetWidth(pcbnew.FromMM(.2));b.Add(t);rt.segpaint(grid,v,w,.5,1<<code)
 log.append({'from':ka,'to':kb,'net':pads[ka].GetNetname(),'points':pts})
b.BuildConnectivity();count=b.GetConnectivity().GetUnconnectedCount(False);print('fixed',seed,fail,count,flush=True)
if not fail and count==0:
 pcbnew.SaveBoard(str(OUT/f'fixed-{seed}.kicad_pcb'),b);(OUT/f'fixed-{seed}.json').write_text(json.dumps(log,indent=2));sys.exit(0)
sys.exit(1)





