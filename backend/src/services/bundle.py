import hashlib,json,math,os,threading
from datetime import datetime,timezone
from src.models.entities import Segment,Outreach
from src.services.analysis import cluster
from src.constants.enums import FEATURES,OUTREACH_STATES
FORMAT='shanlan-segment-bundle'
WORKBENCH=os.environ.get('WORKBENCH_ID','local') or 'local'
LOCK=threading.Lock()# 序列化本进程内的入库确认，配合唯一索引防并发重复
def _canon(pkg):return hashlib.sha256(json.dumps({k:pkg[k] for k in ('source','segments','outreaches')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def package_id(pkg):
 try:return _canon(pkg)
 except Exception:return hashlib.sha256(json.dumps(pkg,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
def build_package(segment,outreaches):
 pkg=dict(format=FORMAT,version=1,source=WORKBENCH,exported_at=datetime.now(timezone.utc).isoformat(),
  segments=[dict(origin_id=segment.id,name=segment.name,k=segment.k,features=segment.features,result=segment.result)],
  outreaches=[dict(origin_id=o.id,segment_origin_id=o.segment_id,cluster_id=o.cluster_id,title=o.title,note=o.note,members=o.members,status=o.status) for o in outreaches])
 pkg['bundle_id']=_canon(pkg);return pkg
def _close(a,b):
 try:return math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-12)
 except (TypeError,ValueError):return False
def _num(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def _check_snapshot(snap):
 if not isinstance(snap,list) or not snap:raise ValueError('冻结档案为空或格式不正确')
 rows=[]
 for r in snap:
  if not isinstance(r,dict) or not isinstance(r.get('code'),str) or not r['code'].strip():raise ValueError('冻结档案存在无效会员编号')
  row={'id':int(r.get('id') or 0),'code':r['code'],'note':str(r.get('note') or '')}
  for f,lo,hi in (('weekly_reading',0,1000),('completion',0,1),('night_share',0,1)):
   v=r.get(f)
   if not _num(v) or not lo<=float(v)<=hi:raise ValueError('冻结档案指标缺失或超出录入范围')
   row[f]=float(v)
  rows.append(row)
 return rows
def _cmp_members(got,want):
 if not isinstance(got,list) or len(got)!=len(want):raise ValueError('成员清单与冻结档案复算不一致')
 for g,w in zip(got,want):
  if not isinstance(g,dict) or g.get('code')!=w['code'] or g.get('cluster_id')!=w['cluster_id'] or g.get('id')!=w['id']:raise ValueError('成员清单与冻结档案复算不一致，疑似伪造')
  for f in list(FEATURES)+['x','y']:
   if not _close(g.get(f),w[f]):raise ValueError('成员指标与冻结档案复算不一致，疑似伪造')
def _cmp_profiles(got,want):
 if not isinstance(got,list) or len(got)!=len(want):raise ValueError('群组画像与复算结果不一致')
 for g,w in zip(got,want):
  if not isinstance(g,dict) or g.get('cluster_id')!=w['cluster_id'] or g.get('size')!=w['size']:raise ValueError('群组画像与复算结果不一致')
  if list(g.get('member_codes') or [])!=w['member_codes']:raise ValueError('群组成员编号与复算结果不一致')
  for f in FEATURES:
   if not _close((g.get('portrait') or {}).get(f),w['portrait'][f]):raise ValueError('群组画像均值与复算结果不一致')
  for f,v in w['center_inverse'].items():
   if not _close((g.get('center_inverse') or {}).get(f),v):raise ValueError('群组中心与复算结果不一致')
def verify_package(pkg):
 if not isinstance(pkg,dict) or pkg.get('format')!=FORMAT:raise ValueError('不是有效的分群迁移包')
 if pkg.get('version')!=1:raise ValueError('迁移包版本不受支持')
 source=pkg.get('source')
 if not isinstance(source,str) or not source.strip() or len(source)>60:raise ValueError('迁移包缺少来源工作台标识')
 segs=pkg.get('segments');outs=pkg.get('outreaches')
 if not isinstance(segs,list) or not segs:raise ValueError('迁移包内没有分群版本')
 if not isinstance(outs,list):raise ValueError('迁移包方案列表格式不正确')
 if 'bundle_id' in pkg and pkg['bundle_id']!=_canon(pkg):raise ValueError('包编号与内容不符，疑似篡改')
 by_origin={}
 for seg in segs:
  if not isinstance(seg,dict):raise ValueError('分群条目格式不正确')
  oid=seg.get('origin_id')
  if not isinstance(oid,int) or isinstance(oid,bool) or oid<1:raise ValueError('分群来源编号无效')
  if oid in by_origin:raise ValueError('包内分群来源编号重复')
  if not isinstance(seg.get('name'),str) or not 1<=len(seg['name'])<=100:raise ValueError('分群名称无效')
  if not isinstance(seg.get('k'),int) or isinstance(seg.get('k'),bool):raise ValueError('分群数无效')
  if not isinstance(seg.get('features'),list) or not all(isinstance(f,str) for f in seg['features']):raise ValueError('特征清单无效')
  res=seg.get('result')
  if not isinstance(res,dict):raise ValueError('分群结果缺失')
  snap=_check_snapshot(res.get('snapshot'))
  try:expected=cluster(snap,seg['features'],seg['k'])
  except Exception as e:raise ValueError('按冻结档案复算失败：'+str(e))
  if res.get('checksum')!=expected['checksum']:raise ValueError('冻结档案校验和不符，疑似篡改')
  if list(res.get('features') or [])!=expected['features']:raise ValueError('结果特征清单与复算不一致')
  for key in ('n','k','seed'):
   if res.get(key)!=expected[key]:raise ValueError('分群结果与冻结档案复算不一致')
  for key in ('silhouette','inertia'):
   if not _close(res.get(key),expected[key]):raise ValueError('轮廓系数或惯量与冻结档案复算不一致')
  for key in ('scaler_mean','scaler_scale'):
   got=res.get(key)
   if not isinstance(got,list) or len(got)!=len(expected[key]) or any(not _close(a,b) for a,b in zip(got,expected[key])):raise ValueError('标准化尺度与冻结档案复算不一致')
  _cmp_members(res.get('members'),expected['members'])
  _cmp_profiles(res.get('profiles'),expected['profiles'])
  by_origin[oid]=seg
 seen=set()
 for o in outs:
  if not isinstance(o,dict):raise ValueError('方案条目格式不正确')
  oid=o.get('origin_id')
  if not isinstance(oid,int) or isinstance(oid,bool) or oid<1:raise ValueError('方案来源编号无效')
  if oid in seen:raise ValueError('包内方案来源编号重复')
  seen.add(oid)
  seg=by_origin.get(o.get('segment_origin_id'))
  if not seg:raise ValueError('方案引用的分群不在包内，断引用')
  if not isinstance(o.get('cluster_id'),int) or isinstance(o.get('cluster_id'),bool) or not 0<=o['cluster_id']<=5:raise ValueError('方案群组号无效')
  members=o.get('members')
  if not isinstance(members,list) or not all(isinstance(m,str) for m in members):raise ValueError('方案名单格式不正确')
  expected_members=[m['code'] for m in seg['result']['members'] if m['cluster_id']==o['cluster_id']]
  if not expected_members:raise ValueError('方案引用的群组在分群中不存在')
  if sorted(members)!=sorted(expected_members):raise ValueError('方案名单与冻结分群成员不一致，疑似伪造')
  if o.get('status') not in OUTREACH_STATES:raise ValueError('方案签核状态无效')
  if not isinstance(o.get('title'),str) or not 2<=len(o['title'])<=100:raise ValueError('方案标题无效')
  if not isinstance(o.get('note'),str) or not 8<=len(o['note'])<=2000:raise ValueError('方案说明无效')
 return pkg
def _locate_segment(s,source,oid):
 if source==WORKBENCH:
  row=s.get(Segment,oid)
  return row if row and not row.origin else None
 return s.query(Segment).filter_by(origin=source,origin_id=oid).first()
def _locate_outreach(s,source,oid):
 if source==WORKBENCH:
  row=s.get(Outreach,oid)
  return row if row and not row.origin else None
 return s.query(Outreach).filter_by(origin=source,origin_id=oid).first()
def _outreach_key(o):return json.dumps([o['segment_origin_id'],o['cluster_id'],o['title'],o['note'],sorted(o['members']),o['status']],ensure_ascii=False,sort_keys=True)
def _seg_key(k,features,result):return json.dumps([k,features,result],ensure_ascii=False,sort_keys=True)
def _local_outreach_key(s,row,source):
 seg=s.get(Segment,row.segment_id)
 if not seg:return None
 origin_seg=seg.id if source==WORKBENCH else (seg.origin_id if seg.origin==source else None)
 if origin_seg is None:return None
 return json.dumps([origin_seg,row.cluster_id,row.title,row.note,sorted(row.members),row.status],ensure_ascii=False,sort_keys=True)
def diff(s,pkg):
 source=pkg['source'];out={'segments':{'added':[],'reused':[],'conflicts':[]},'outreaches':{'added':[],'reused':[],'conflicts':[]}}
 for seg in pkg['segments']:
  item={'origin_id':seg['origin_id'],'name':seg['name'],'k':seg['k'],'n':seg['result']['n']}
  row=_locate_segment(s,source,seg['origin_id'])
  if not row:
   if source==WORKBENCH:out['segments']['conflicts'].append({**item,'reason':'本工作台找不到原版本'})
   else:out['segments']['added'].append(item)
  elif _seg_key(row.k,row.features,row.result)==_seg_key(seg['k'],seg['features'],seg['result']):out['segments']['reused'].append({**item,'local_id':row.id})
  else:out['segments']['conflicts'].append({**item,'local_id':row.id,'reason':'同源异文：同一来源编号内容不一致'})
 for o in pkg['outreaches']:
  item={'origin_id':o['origin_id'],'title':o['title'],'status':o['status']}
  row=_locate_outreach(s,source,o['origin_id'])
  if not row:
   if source==WORKBENCH:out['outreaches']['conflicts'].append({**item,'reason':'本工作台找不到原方案'})
   else:out['outreaches']['added'].append(item)
  elif _local_outreach_key(s,row,source)==_outreach_key(o):out['outreaches']['reused'].append({**item,'local_id':row.id})
  else:out['outreaches']['conflicts'].append({**item,'local_id':row.id,'reason':'同源异文：同一来源编号内容不一致'})
 return out
def store(s,pkg):
 source=pkg['source'];seg_map={}
 for seg in pkg['segments']:
  row=_locate_segment(s,source,seg['origin_id'])
  if row:seg_map[seg['origin_id']]=row.id;continue
  row=Segment(name=seg['name'],k=seg['k'],features=seg['features'],state='done',result=seg['result'],origin=source,origin_id=seg['origin_id'])
  s.add(row);s.flush();seg_map[seg['origin_id']]=row.id
 for o in pkg['outreaches']:
  if _locate_outreach(s,source,o['origin_id']):continue
  s.add(Outreach(segment_id=seg_map[o['segment_origin_id']],cluster_id=o['cluster_id'],title=o['title'],note=o['note'],members=list(o['members']),status=o['status'],origin=source,origin_id=o['origin_id']))
 s.flush()
