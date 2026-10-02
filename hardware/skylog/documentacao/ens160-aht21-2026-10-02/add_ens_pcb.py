import pcbnew,pathlib,sys,json,time,itertools
from array import array
BASE=pathlib.Path(r"C:\Users\pedro\OneDrive\Documentos\programação\estacao-metereologica\hardware\skylog")
OUT=BASE/'documentacao'/'ens160-aht21-2026-10-02'
sys.path.insert(0,str(BASE/'documentacao'/'roteamento-2026-10-02'))
import route_single as rt
rt.X0=167.;rt.Y0=15.;rt.W=426;rt.H=556;rt.N=rt.W*rt.H
SRC=OUT/'antes-skylog.kicad_pcb'

def attempt(pos,angle,order,tag):
 b=pcbnew.LoadBoard(str(SRC));fs={f.GetReference():f for f in b.GetFootprints()};pads={(ref,p.GetNumber()):p for ref,f in fs.items() for p in f.Pads()}
 f=pcbnew.FootprintLoad(str(BASE/'skylog.pretty'),'ENS160_AHT21_1x04_P2.54mm');f.SetReference('J1');f.SetValue('ENS160+AHT21');f.SetFPID(pcbnew.LIB_ID('skylog','ENS160_AHT21_1x04_P2.54mm'));f.SetOrientationDegrees(angle);f.SetPosition(rt.iv(pos));b.Add(f)
 links={'1':('U4','5'),'2':('U4','4'),'3':('U4','2'),'4':('U4','3')}
 for p in f.Pads():p.SetNet(pads[links[p.GetNumber()]].GetNet());pads['J1',p.GetNumber()]=p
 grid=array('Q',[0])*rt.N
 ant=(177.03012,38.08344,195.01332,43.97624)
 xmin,xmax,ymin,ymax=rt.bounds((ant[0],ant[1]),(ant[2],ant[3]),.45)
 for y in range(ymin,ymax+1):
  for x in range(xmin,xmax+1):grid[y*rt.W+x]|=1
 for fp in b.GetFootprints():
  for p in fp.Pads():rt.padpaint(grid,p)
 for t in b.GetTracks():rt.segpaint(grid,rt.xy(t.GetStart()),rt.xy(t.GetEnd()),.5,1<<t.GetNetCode())
 fail=[];added=[]
 for num in order:
  pa=pads['J1',num];pb=pads[links[num]];a=rt.xy(pa.GetPosition());z=rt.xy(pb.GetPosition());code=pa.GetNetCode();path=rt.astar(grid,a,z,code)
  if path is None:fail.append(num);continue
  pts=rt.simpl(path,a,z)
  for v,w in zip(pts,pts[1:]):
   if rt.dst(v,w)<.000001:continue
   t=pcbnew.PCB_TRACK(b);t.SetStart(rt.iv(v));t.SetEnd(rt.iv(w));t.SetLayer(pcbnew.F_Cu);t.SetNetCode(code);t.SetWidth(pcbnew.FromMM(.2));b.Add(t);rt.segpaint(grid,v,w,.5,1<<code)
  added.append({'pin':num,'net':pa.GetNetname(),'points':pts})
 b.BuildConnectivity();cnt=b.GetConnectivity().GetUnconnectedCount(False)
 if not fail and cnt==0:
  dest=OUT/f'candidate-{tag}.kicad_pcb';pcbnew.SaveBoard(str(dest),b)
  (OUT/f'candidate-{tag}.json').write_text(json.dumps({'pos':pos,'angle':angle,'routes':added,'unconnected':cnt},indent=2))
  return str(dest),len(b.GetTracks()),sum(pcbnew.ToMM(t.GetLength()) for t in b.GetTracks())
 return None,fail,cnt
if __name__=='__main__':
 choices=[((207,25.5),90),((209,20),90),((212,29),90),((239,24),0),((240,29),0),((215,20),-90)]
 for idx,(pos,angle) in enumerate(choices):
  for j,order in enumerate([['4','3','1','2'],['1','2','4','3'],['2','1','3','4'],['3','4','2','1']]):
   r=attempt(pos,angle,order,f'{idx}-{j}');print(idx,j,r,flush=True)
   if r[0]:sys.exit(0)
 sys.exit(1)
