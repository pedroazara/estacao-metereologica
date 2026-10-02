import pcbnew,pathlib,sys,math,json,xml.etree.ElementTree as ET
from array import array
BASE=pathlib.Path(r'C:\Users\pedro\OneDrive\Documentos\programação\estacao-metereologica\hardware\skylog');OUT=BASE/'documentacao/ens160-aht21-2026-10-02';DEST=BASE/'versoes/monocamada'
sys.path.insert(0,str(BASE/'documentacao/roteamento-2026-10-02'));import route_single as rt
rt.X0=155.;rt.Y0=15.;rt.W=451;rt.H=571;rt.N=rt.W*rt.H
b=pcbnew.LoadBoard(str(OUT/'fixed-0.kicad_pcb'));fps={f.GetReference():f for f in b.GetFootprints()};pads={(ref,p.GetNumber()):p for ref,f in fps.items() for p in f.Pads()}
xml=ET.parse(DEST/'netlist-monocamada.xml');comps={c.attrib['ref']:c for c in xml.findall('.//components/comp')}
for ref,uid,pos,key,label in [('J2','8d87177c-f641-43e9-9adc-a9dc22c07c2a',(168,94),('U1','19'),'5V'),('J3','c6ae385a-ea91-4c1e-972f-9dbe5d2d097b',(162,94),('U1','14'),'GND')]:
 f=pcbnew.FootprintLoad(str(BASE/'skylog.pretty'),'Wire_Pad_D1.2mm');f.SetReference(ref);f.SetValue(comps[ref].findtext('value'));f.SetFPID(pcbnew.LIB_ID('skylog','Wire_Pad_D1.2mm'));f.SetPosition(rt.iv(pos));f.SetPath(pcbnew.KIID_PATH('/350f3427-bcec-49b0-ac2f-a03c28574f3d/'+uid));b.Add(f);fps[ref]=f
 for name in ['Description','Datasheet']:
  node=comps[ref].find("property[@name='%s']"%name);text=(node.attrib.get('value','') if node is not None else comps[ref].findtext(name.lower(),''));f.GetField(name).SetText(text)
 for p in f.Pads():p.SetNet(pads[key].GetNet());pads[ref,p.GetNumber()]=p
 text=pcbnew.PCB_TEXT(b);text.SetText(label);text.SetPosition(rt.iv((pos[0],pos[1]+3)));text.SetTextSize(rt.iv((1.2,1.2)));text.SetTextThickness(pcbnew.FromMM(.18));text.SetLayer(pcbnew.F_SilkS);b.Add(text)
# Pad/track obstacles account for 0.8 mm power branches plus clearance and grid margin.
grid=array('Q',[0])*rt.N
for p in pads.values():
 box=p.GetBoundingBox();a=(pcbnew.ToMM(box.GetLeft()),pcbnew.ToMM(box.GetTop()));z=(pcbnew.ToMM(box.GetRight()),pcbnew.ToMM(box.GetBottom()));xmin,xmax,ymin,ymax=rt.bounds(a,z,.7)
 for y in range(ymin,ymax+1):
  for x in range(xmin,xmax+1):
   u,v=rt.loc(y*rt.W+x);dx=max(a[0]-u,0,u-z[0]);dy=max(a[1]-v,0,v-z[1])
   if dx*dx+dy*dy<=.7**2:grid[y*rt.W+x]|=1<<p.GetNetCode()
for t in b.GetTracks():rt.segpaint(grid,rt.xy(t.GetStart()),rt.xy(t.GetEnd()),pcbnew.ToMM(t.GetWidth())/2+.7,1<<t.GetNetCode())
xmin,xmax,ymin,ymax=rt.bounds((177.03012,38.08344),(195.01332,43.97624),.7)
for y in range(ymin,ymax+1):
 for x in range(xmin,xmax+1):grid[y*rt.W+x]|=1
log=[]
for ka,kz in [(('J2','1'),('U1','19')),(('J3','1'),('U1','14'))]:
 a=rt.xy(pads[ka].GetPosition());z=rt.xy(pads[kz].GetPosition());code=pads[ka].GetNetCode();path=rt.astar(grid,a,z,code)
 if path is None:raise RuntimeError('No route '+str(ka))
 pts=rt.simpl(path,a,z);log.append({'from':ka,'to':kz,'points':pts})
 for v,w in zip(pts,pts[1:]):
  if rt.dst(v,w)<1e-6:continue
  t=pcbnew.PCB_TRACK(b);t.SetStart(rt.iv(v));t.SetEnd(rt.iv(w));t.SetWidth(pcbnew.FromMM(.8));t.SetLayer(pcbnew.F_Cu);t.SetNetCode(code);b.Add(t);rt.segpaint(grid,v,w,1.1,1<<code)
for a,z in [((158,17),(244,17)),((244,17),(244,128)),((244,128),(158,128)),((158,128),(158,17))]:
 s=pcbnew.PCB_SHAPE(b);s.SetShape(pcbnew.SHAPE_T_SEGMENT);s.SetStart(rt.iv(a));s.SetEnd(rt.iv(z));s.SetLayer(pcbnew.Edge_Cuts);s.SetWidth(pcbnew.FromMM(.05));b.Add(s)
b.BuildConnectivity();assert b.GetConnectivity().GetUnconnectedCount(False)==0
pcbnew.SaveBoard(str(OUT/'mono-final-main.kicad_pcb'),b)
raw=(OUT/'mono-final-main.kicad_pcb').read_text(encoding='utf-8').replace('${KIPRJMOD}/bibliotecas/','${KIPRJMOD}/../../bibliotecas/')
(DEST/'skylog-monocamada.kicad_pcb').write_text(raw,encoding='utf-8')
(OUT/'battery-routing-mono.json').write_text(json.dumps(log,indent=2));print('Mono saved',len(list(b.GetTracks())),len(list(b.GetFootprints())))
