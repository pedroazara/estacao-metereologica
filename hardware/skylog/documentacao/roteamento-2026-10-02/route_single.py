import pcbnew, pathlib, random, math, heapq, time, json, re
from array import array
BASE=pathlib.Path(r'C:\Users\pedro\OneDrive\Documentos\programação\estacao-metereologica\hardware\skylog')
OUT=BASE/'documentacao'/'roteamento-2026-10-02'
SRC=OUT/'skylog-antes.kicad_pcb'
STEP=.2; X0=167.; Y0=20.; W=376; H=526; N=W*H
DIRS=[(1,0,1.),(0,1,1.),(-1,0,1.),(0,-1,1.),(1,1,1.41421356),(-1,1,1.41421356),(-1,-1,1.41421356),(1,-1,1.41421356)]
def mm(v):return pcbnew.ToMM(v)
def xy(v):return mm(v.x),mm(v.y)
def iv(p):return pcbnew.VECTOR2I(pcbnew.FromMM(p[0]),pcbnew.FromMM(p[1]))
def idx(p):return round((p[1]-Y0)/STEP)*W+round((p[0]-X0)/STEP)
def loc(i):return (X0+(i%W)*STEP,Y0+(i//W)*STEP)
def dst(a,b):return math.hypot(a[0]-b[0],a[1]-b[1])
def bounds(a,b,r):
 return max(0,int((min(a[0],b[0])-r-X0)/STEP)),min(W-1,int((max(a[0],b[0])+r-X0)/STEP)+1),max(0,int((min(a[1],b[1])-r-Y0)/STEP)),min(H-1,int((max(a[1],b[1])+r-Y0)/STEP)+1)
def segpaint(grid,a,b,r,bit):
 xmin,xmax,ymin,ymax=bounds(a,b,r); dx=b[0]-a[0];dy=b[1]-a[1];s=dx*dx+dy*dy
 for gy in range(ymin,ymax+1):
  y=Y0+gy*STEP
  for gx in range(xmin,xmax+1):
   x=X0+gx*STEP; t=max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/s)) if s else 0
   if (x-a[0]-t*dx)**2+(y-a[1]-t*dy)**2<=r*r: grid[gy*W+gx]|=bit

def padpaint(grid,p):
 box=p.GetBoundingBox(); a=(mm(box.GetLeft()),mm(box.GetTop()));b=(mm(box.GetRight()),mm(box.GetBottom()));r=.41
 bit=1<<p.GetNetCode();xmin,xmax,ymin,ymax=bounds(a,b,r)
 for gy in range(ymin,ymax+1):
  y=Y0+gy*STEP
  for gx in range(xmin,xmax+1):
   x=X0+gx*STEP; xx=max(a[0]-x,0,x-b[0]); yy=max(a[1]-y,0,y-b[1])
   if xx*xx+yy*yy<=r*r:grid[gy*W+gx]|=bit

def astar(grid,a,b,code):
 start=idx(a);goal=idx(b);mask=((1<<45)-1)^(1<<code)
 if grid[start]&mask or grid[goal]&mask:return None
 ex=goal%W;ey=goal//W
 def heur(i):
  dx=abs(i%W-ex);dy=abs(i//W-ey);return max(dx,dy)+.41421356*min(dx,dy)
 q=[(heur(start),0.,start)];cost={start:0.};prev={};expanded=0
 while q:
  _,g,i=heapq.heappop(q)
  if g!=cost.get(i):continue
  if i==goal:
   path=[i]
   while path[-1]!=start:path.append(prev[path[-1]])
   path.reverse();return path
  x=i%W;y=i//W;expanded+=1
  for dx,dy,d in DIRS:
   nx=x+dx;ny=y+dy
   if nx<1 or nx>=W-1 or ny<1 or ny>=H-1:continue
   j=ny*W+nx
   if grid[j]&mask:continue
   if dx and dy and ((grid[y*W+nx]&mask) or (grid[ny*W+x]&mask)):continue
   v=g+d
   if v+1e-8<cost.get(j,1e30):cost[j]=v;prev[j]=i;heapq.heappush(q,(v+heur(j),v,j))
 return None

def simpl(path,a,b):
 pts=[loc(i) for i in path];out=[a]
 if dst(a,pts[0])>.00001:out.append(pts[0])
 olddir=None
 for i in range(1,len(pts)):
  d=(round((pts[i][0]-pts[i-1][0])/STEP),round((pts[i][1]-pts[i-1][1])/STEP))
  if olddir is not None and d!=olddir:out.append(pts[i-1])
  olddir=d
 if dst(out[-1],pts[-1])>.00001:out.append(pts[-1])
 if dst(out[-1],b)>.00001:out.append(b)
 return out

def prepare():
 raw=SRC.read_text(encoding='utf-8');raw=re.sub(r'\t\(segment\n.*?\n\t\)',lambda m: '' if any(n in m.group(0) for n in ['(net "/GND_SENSOR")','(net "Net-(U1-3V3)")']) else m.group(0),raw,flags=re.S)
 tmp=OUT/'routing-input.kicad_pcb';tmp.write_text(raw,encoding='utf-8')
 b=pcbnew.LoadBoard(str(tmp));f={f.GetReference():f for f in b.GetFootprints()};p={(r,p.GetNumber()):p for r,v in f.items() for p in v.Pads()}
 # Synchronize explicitly approved schema changes: UART2, address low.
 n1=b.FindNet(1);n2=b.FindNet(2)
 n1.SetNetname('/RS485_TX');n2.SetNetname('/RS485_RX')
 nc28=b.FindNet(p['U1','28'].GetNetCode());nc27=b.FindNet(p['U1','27'].GetNetCode())
 nc28.SetNetname('unconnected-(U1-U0TXD{slash}GPIO1-Pad35)');nc27.SetNetname('unconnected-(U1-U0RXD{slash}GPIO3-Pad34)')
 p['U1','35'].SetNet(nc28);p['U1','34'].SetNet(nc27);p['U1','28'].SetNet(n1);p['U1','27'].SetNet(n2);p['U4','1'].SetNet(b.FindNet(3))
 # Keep power and bus traces; ground is redrawn to avoid blocking signal paths.
 return b,f,p

def route_attempt(order,seed):
 b,f,p=prepare();grid=array('Q',[0])*N
 # Preserve a copper-free region under the antenna shown by the DevKit footprint.
 ant=(177.03012,38.08344,195.01332,43.97624)
 xmin,xmax,ymin,ymax=bounds((ant[0],ant[1]),(ant[2],ant[3]),.45)
 for gy in range(ymin,ymax+1):
  for gx in range(xmin,xmax+1):grid[gy*W+gx]|=1
 keep=pcbnew.ZONE(b);keep.SetIsRuleArea(True);keep.SetDoNotAllowTracks(True);keep.SetDoNotAllowVias(True);keep.SetDoNotAllowZoneFills(True);keep.SetDoNotAllowPads(False);keep.SetDoNotAllowFootprints(False);keep.SetZoneName('ESP32_ANTENNA_NO_COPPER');ls=pcbnew.LSET();ls.AddLayer(pcbnew.F_Cu);ls.AddLayer(pcbnew.B_Cu);keep.SetLayerSet(ls)
 poly=keep.Outline();poly.NewOutline()
 for v in [(ant[0],ant[1]),(ant[2],ant[1]),(ant[2],ant[3]),(ant[0],ant[3])]:poly.Append(iv(v))
 b.Add(keep)
 for fp in b.GetFootprints():
  for pad in fp.Pads():padpaint(grid,pad)
 for t in b.GetTracks():segpaint(grid,xy(t.GetStart()),xy(t.GetEnd()),.5,1<<t.GetNetCode())
 edges={1:[(('U1','28'),('U5','CN1.1'))],2:[(('U1','27'),('U5','CN1.2'))],7:[(('U1','6'),('U3','1'))],8:[(('U1','1'),('U4','5')),(('U4','5'),('U3','3'))],37:[(('U1','33'),('U4','2'))],38:[(('U1','36'),('U4','3'))]}
 # Ground uses an MST among all ground pads, then all branches are routed.
 keys=[k for k,v in p.items() if v.GetNetCode()==3];parent={k:k for k in keys}
 def root(k):
  while parent[k]!=k:k=parent[k]
  return k
 allpairs=sorted((dst(xy(p[a].GetPosition()),xy(p[b].GetPosition())),a,b) for i,a in enumerate(keys) for b in keys[i+1:])
 edges[3]=[]
 for _,a,c in allpairs:
  if root(a)!=root(c):parent[root(a)]=root(c);edges[3].append((a,c))
 failed=[];added=[]
 for code in order:
  for ka,kb in edges[code]:
   a=xy(p[ka].GetPosition());z=xy(p[kb].GetPosition());path=astar(grid,a,z,code)
   if path is None:failed.append((code,ka,kb));continue
   pts=simpl(path,a,z)
   for v,w in zip(pts,pts[1:]):
    if dst(v,w)<1e-6:continue
    t=pcbnew.PCB_TRACK(b);t.SetStart(iv(v));t.SetEnd(iv(w));t.SetWidth(pcbnew.FromMM(.2));t.SetLayer(pcbnew.F_Cu);t.SetNetCode(code);b.Add(t);segpaint(grid,v,w,.5,1<<code)
   added.append({'net':code,'from':ka,'to':kb,'points':pts})
 b.BuildConnectivity();count=b.GetConnectivity().GetUnconnectedCount(False)
 dest=OUT/f'candidate-{seed}.kicad_pcb';pcbnew.SaveBoard(str(dest),b)
 # Remove duplicate generated track geometry without touching the SWIG board containers.
 raw=dest.read_text(encoding='utf-8');seen=set()
 def unique_segment(m):
  chunk=m.group(0)
  pts=[tuple(float(t) for t in re.search(r'\('+k+r' ([^)]*)\)',chunk).group(1).split()) for k in ['start','end']]
  key=(tuple(sorted(pts)),re.search(r'\(net ([^)]*|.*)\)',chunk).group(0) if False else re.search(r'\(net \"([^\"]*)\"\)',chunk).group(1),re.search(r'\(width ([^)]*)\)',chunk).group(1),re.search(r'\(layer \"([^\"]*)\"\)',chunk).group(1))
  if key in seen:return ''
  seen.add(key);return chunk
 raw=re.sub(r'\t\(segment\n.*?\n\t\)',unique_segment,raw,flags=re.S);dest.write_text(raw,encoding='utf-8')
 (OUT/f'candidate-{seed}.json').write_text(json.dumps({'failed':failed,'unconnected':count,'order':order,'routes':added},indent=2))
 return failed,count,dest
if __name__=='__main__':
 import sys
 seed=int(sys.argv[1]) if len(sys.argv)>1 else 0
 orders=[[1,2,38,37,7,8,3],[3,8,7,38,37,1,2],[7,8,38,37,1,2,3],[38,37,1,2,7,8,3]]
 order=orders[seed] if seed<len(orders) else [8,38,37,1,2,7,3] if seed==4 else random.Random(seed).sample([1,2,38,37,7,8,3],7)
 ts=time.time();r=route_attempt(order,seed);print('RESULT',r,'seconds',time.time()-ts,flush=True)


