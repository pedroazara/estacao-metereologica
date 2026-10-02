import pcbnew,pathlib,sys,json,heapq,math
from array import array
BASE=pathlib.Path(r"C:\Users\pedro\OneDrive\Documentos\programação\estacao-metereologica\hardware\skylog")
OUT=BASE/'documentacao'/'ens160-aht21-2026-10-02'
sys.path.insert(0,str(BASE/'documentacao'/'roteamento-2026-10-02'))
import route_single as rt
rt.X0=167.;rt.Y0=15.;rt.W=426;rt.H=556;rt.N=rt.W*rt.H
b=pcbnew.LoadBoard(str(OUT/'antes-skylog.kicad_pcb'))
fps={f.GetReference():f for f in b.GetFootprints()};pads={(ref,p.GetNumber()):p for ref,f in fps.items() for p in f.Pads()}
f=pcbnew.FootprintLoad(str(BASE/'skylog.pretty'),'ENS160_AHT21_1x04_P2.54mm');f.SetReference('J1');f.SetValue('ENS160+AHT21');f.SetFPID(pcbnew.LIB_ID('skylog','ENS160_AHT21_1x04_P2.54mm'));f.SetOrientationDegrees(90);f.SetPosition(rt.iv((207,25.5)));f.SetPath(pcbnew.KIID_PATH('/350f3427-bcec-49b0-ac2f-a03c28574f3d/fb0e3071-73d6-4a05-b199-b7047537637a'));b.Add(f);fps['J1']=f
links={'1':('U4','5'),'2':('U4','4'),'3':('U4','2'),'4':('U4','3')}
for p in f.Pads():p.SetNet(pads[links[p.GetNumber()]].GetNet());pads['J1',p.GetNumber()]=p

def buildgrid():
 grid=array('Q',[0])*rt.N
 ant=(177.03012,38.08344,195.01332,43.97624)
 xmin,xmax,ymin,ymax=rt.bounds((ant[0],ant[1]),(ant[2],ant[3]),.45)
 for y in range(ymin,ymax+1):
  for x in range(xmin,xmax+1):grid[y*rt.W+x]|=1
 for fp in b.GetFootprints():
  for p in fp.Pads():rt.padpaint(grid,p)
 for t in b.GetTracks():rt.segpaint(grid,rt.xy(t.GetStart()),rt.xy(t.GetEnd()),.5,1<<t.GetNetCode())
 return grid

def addpath(path,a,z,code):
 pts=rt.simpl(path,a,z)
 for v,w in zip(pts,pts[1:]):
  if rt.dst(v,w)<.000001:continue
  t=pcbnew.PCB_TRACK(b);t.SetStart(rt.iv(v));t.SetEnd(rt.iv(w));t.SetLayer(pcbnew.F_Cu);t.SetNetCode(code);t.SetWidth(pcbnew.FromMM(.2));b.Add(t)
 return pts

def bridgepath(grid,a,z,newcode,oldcode):
 start=rt.idx(a);goal=rt.idx(z);W=rt.W;N=rt.N;ex=goal%W;ey=goal//W
 masks=[((1<<63)-1)^(1<<c) for c in [newcode,oldcode]]
 physical=[]
 for fp in b.GetFootprints():
  box=fp.GetBoundingBox(True,False)
  physical.append((pcbnew.ToMM(box.GetLeft()),pcbnew.ToMM(box.GetTop()),pcbnew.ToMM(box.GetRight()),pcbnew.ToMM(box.GetBottom())))
 def bodyok(i,j):
  a=rt.loc(i);z=rt.loc(j)
  xmin=min(a[0],z[0])-3.;xmax=max(a[0],z[0])+3.;ymin=min(a[1],z[1])-3.;ymax=max(a[1],z[1])+3.
  return not any(xmin<r[2] and xmax>r[0] and ymin<r[3] and ymax>r[1] for r in physical)
 def h(i):
  dx=abs(i%W-ex);dy=abs(i//W-ey);return max(dx,dy)+.41421356*min(dx,dy)
 freecache={}
 def padfree(i,stage):
  key=(i,stage)
  if key in freecache:return freecache[key]
  x=i%W;y=i//W;mask=masks[stage]
  if not 6<x<W-6 or not 6<y<rt.H-6:freecache[key]=False;return False
  for dy in range(-4,5):
   for dx in range(-4,5):
    if dx*dx+dy*dy<=16 and grid[(y+dy)*W+x+dx]&mask:freecache[key]=False;return False
  freecache[key]=True;return True
 print('bridge debug',newcode,oldcode,'start',a,[ii for ii in range(63) if grid[start]&(1<<ii)],'goal',z,[ii for ii in range(63) if grid[goal]&(1<<ii)],'blocked',bool(grid[goal]&masks[1]),flush=True)
 q=[(h(start),0.,start)];cost={start:0.};prev={};done=0
 while q:
  _,g,k=heapq.heappop(q)
  if g!=cost.get(k):continue
  stage=k//N;i=k%N
  if stage==1 and i==goal:
   chain=[k]
   while chain[-1]!=start:chain.append(prev[chain[-1]])
   chain.reverse();split=next(j for j in range(1,len(chain)) if chain[j]>=N and chain[j-1]<N)
   return chain[:split],[v%N for v in chain[split:]]
  x=i%W;y=i//W;mask=masks[stage]
  for dx,dy,d in rt.DIRS:
   nx=x+dx;ny=y+dy
   if nx<1 or nx>=W-1 or ny<1 or ny>=rt.H-1:continue
   j=ny*W+nx
   if grid[j]&mask:continue
   if dx and dy and ((grid[y*W+nx]&mask) or (grid[ny*W+x]&mask)):continue
   nk=j+stage*N;ng=g+d
   if ng<cost.get(nk,1e30):cost[nk]=ng;prev[nk]=k;heapq.heappush(q,(ng+h(j),ng,nk))
  if stage==0 and padfree(i,0):
   for dx,dy in [(50,0),(-50,0),(0,50),(0,-50)]:
    nx=x+dx;ny=y+dy
    if not 6<nx<W-6 or not 6<ny<rt.H-6:continue
    j=ny*W+nx
    if not padfree(j,1) or not bodyok(i,j):continue
    nk=j+N;ng=g+70
    if ng<cost.get(nk,1e30):cost[nk]=ng;prev[nk]=k;heapq.heappush(q,(ng+h(j),ng,nk))
 return None

# Keep direct traces for ground and SCL.
log=[]
for ref,num,name in [('JP1','3','/ENS_SDA'),('JP2','1','/ENS_3V3')]:
 oldnet=pads[links[num]].GetNet();new=pcbnew.NETINFO_ITEM(b,name);b.Add(new);pads['J1',num].SetNet(new)
 print('PAD ASSIGNED',[(p.GetNumber(),p.GetNetCode(),p.GetNetname()) for p in f.Pads()],flush=True)
 grid=buildgrid();a=rt.xy(pads['J1',num].GetPosition());z=rt.xy(pads[links[num]].GetPosition());result=bridgepath(grid,a,z,new.GetNetCode(),oldnet.GetNetCode())
 if result is None:raise RuntimeError('No bridge '+ref)
 pre,post=result;u=rt.loc(pre[-1]);v=rt.loc(post[0]);print(ref,num,u,v,flush=True)
 fp=pcbnew.FootprintLoad(str(BASE/'skylog.pretty'),'Wire_Jumper_P10.00mm');fp.SetReference(ref);fp.SetValue('FIO / 0R');fp.SetFPID(pcbnew.LIB_ID('skylog','Wire_Jumper_P10.00mm'))
 angle=math.degrees(math.atan2(-(v[1]-u[1]),v[0]-u[0]));fp.SetOrientationDegrees(angle);fp.SetPosition(rt.iv(u));b.Add(fp)
 for p in fp.Pads():p.SetNet(new if p.GetNumber()=='1' else oldnet)
 log.append({'ref':ref,'pin':num,'net':name,'start':u,'end':v,'pre':addpath(pre,a,u,new.GetNetCode()),'post':addpath(post,v,z,oldnet.GetNetCode())})
for num in ['4','2']:
 grid=buildgrid();a=rt.xy(pads['J1',num].GetPosition());z=rt.xy(pads[links[num]].GetPosition());code=pads['J1',num].GetNetCode();path=rt.astar(grid,a,z,code)
 assert path is not None,num
 log.append({'pin':num,'points':addpath(path,a,z,code)})
b.BuildConnectivity();print('Unconnected',b.GetConnectivity().GetUnconnectedCount(False),flush=True)
pcbnew.SaveBoard(str(OUT/'candidate-jumpers.kicad_pcb'),b);(OUT/'candidate-jumpers.json').write_text(json.dumps(log,indent=2))
