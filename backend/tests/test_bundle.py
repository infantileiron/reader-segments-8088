import os,sys,copy,json,random
from pathlib import Path
os.environ.setdefault('DATABASE_URL','sqlite:///'+str(Path(__file__).with_name('test_bundle.db')))
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET','bundle-test-secret')
os.environ['WORKBENCH_ID']='bench-a'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.config.database import Base,engine,Session
from src.models.entities import User,Reader,Segment,Outreach,Bundle
from src.utils.security import hash_password
from src.services.analysis import cluster
from src.services.bundle import _canon
FEATURES=['weekly_reading','completion','night_share']
NOTE='最近完整自然周行为聚合；测试会员，不含个人联系方式'
@pytest.fixture()
def client():
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
 with Session() as s:
  for role in ('admin','analyst','viewer'):s.add(User(email=role+'@demo.local',role=role,password=hash_password('Demo123!')))
  rng=random.Random(3107)
  for i in range(30):
   mode=i//10
   s.add(Reader(code=f'SL-{i+1:03}',weekly_reading=round(max(0,rng.gauss([4,14,26][mode],[1.1,3,4][mode])),2),completion=round(max(0.01,min(1,rng.gauss([.32,.81,.61][mode],.06))),4),night_share=round(max(0,min(1,rng.gauss([.18,.23,.77][mode],.08))),4),note=NOTE))
  s.commit()
 with TestClient(app) as c:yield c
def login(client,role):
 r=client.post('/auth/login',json={'email':role+'@demo.local','password':'Demo123!'})
 assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}
def make_segment(name,k=3):
 with Session() as s:
  rows=[{'id':r.id,'code':r.code,'weekly_reading':r.weekly_reading,'completion':r.completion,'night_share':r.night_share,'note':r.note} for r in s.query(Reader).order_by(Reader.id).all()]
  seg=Segment(name=name,k=k,features=FEATURES,state='done',result={**cluster(rows,FEATURES,k),'snapshot':rows},origin='',origin_id=None)
  s.add(seg);s.commit();return seg.id
def make_outreach(auth,client,seg_id,title='低频读者轻阅读试点',cluster_id=0,approve=False):
 r=client.post('/outreaches',json={'segment_id':seg_id,'cluster_id':cluster_id,'title':title,'note':'每周一次短篇精选，控制频次不打扰'},headers=auth)
 assert r.status_code==200,r.text
 oid=r.json()['id']
 if approve:
  r=client.post(f'/outreaches/{oid}/approve',json={},headers=login(client,'admin'));assert r.status_code==200,r.text
 return oid
def pack(client,auth,seg_id):
 r=client.get(f'/segments/{seg_id}/bundle',headers=auth);assert r.status_code==200,r.text
 return r.json()
def foreign(pkg,source,seg_origin=None,out_origin=None):
 pkg=copy.deepcopy(pkg);pkg['source']=source;pkg.pop('bundle_id',None)
 if seg_origin is not None:pkg['segments'][0]['origin_id']=seg_origin
 if out_origin is not None:
  for o in pkg['outreaches']:o['origin_id']=out_origin
 pkg['bundle_id']=_canon(pkg);return pkg
def counts():
 with Session() as s:return {t.__tablename__:s.query(t).count() for t in (Reader,Segment,Outreach,Bundle)}
def test_pack_carries_frozen_scaler_centers(client):
 auth=login(client,'analyst');seg=make_segment('11月会员阅读分群');make_outreach(auth,client,seg,approve=True)
 pkg=pack(client,auth,seg)
 assert pkg['source']=='bench-a' and pkg['bundle_id'] and pkg['format']=='shanlan-segment-bundle'
 res=pkg['segments'][0]['result']
 assert len(res['snapshot'])==30 and res['scaler_mean'] and res['scaler_scale']
 assert all(p['center_inverse'] for p in res['profiles'])
 assert len(pkg['outreaches'])==1 and pkg['outreaches'][0]['status']=='approved'
 r=client.get(f'/segments/{seg}/bundle',headers=login(client,'viewer'));assert r.status_code==200
def test_same_number_foreign_source_coexists(client):
 auth=login(client,'analyst');seg=make_segment('11月会员阅读分群');make_outreach(auth,client,seg,approve=True)
 before=counts();pkg=foreign(pack(client,auth,seg),'bench-b')
 r=client.post('/bundles/preview',json=pkg,headers=auth);assert r.status_code==200,r.text
 task=r.json();assert task['state']=='pending'
 assert len(task['preview']['segments']['added'])==1 and len(task['preview']['outreaches']['added'])==1
 r=client.post(f"/bundles/{task['id']}/confirm",json={},headers=auth);assert r.status_code==200,r.text
 assert r.json()['state']=='stored'
 after=counts()
 assert after['segments']==before['segments']+1 and after['outreaches']==before['outreaches']+1
 assert after['readers']==before['readers'],'导入档案不得进入本地档案'
 with Session() as s:
  local=s.get(Segment,seg);assert local.origin==''and local.name=='11月会员阅读分群','本地同号分群必须原样保留'
  imported=s.query(Segment).filter_by(origin='bench-b',origin_id=seg).one()
  assert imported.id!=seg and imported.state=='done'
  plan=s.query(Outreach).filter_by(origin='bench-b').one()
  assert plan.status=='approved' and plan.segment_id==imported.id,'导入方案保留原名单与签核状态'
  assert sorted(plan.members)==sorted(m['code'] for m in imported.result['members'] if m['cluster_id']==0)
def test_same_source_different_content_rejected(client):
 auth=login(client,'analyst');seg=make_segment('11月会员阅读分群');other=make_segment('12月会员阅读分群',k=2)
 pkg=foreign(pack(client,auth,seg),'bench-b')
 task=client.post('/bundles/preview',json=pkg,headers=auth).json()
 assert client.post(f"/bundles/{task['id']}/confirm",json={},headers=auth).status_code==200
 clash=foreign(pack(client,auth,other),'bench-b',seg_origin=seg)
 r=client.post('/bundles/preview',json=clash,headers=auth);assert r.status_code==200,r.text
 task2=r.json();assert task2['state']=='pending'
 assert len(task2['preview']['segments']['conflicts'])==1,'预览必须列出同源异文冲突'
 before=counts()
 r=client.post(f"/bundles/{task2['id']}/confirm",json={},headers=auth)
 assert r.status_code==422 and '同源异文' in r.json()['detail']
 assert client.get(f"/bundles/{task2['id']}",headers=auth).json()['state']=='rejected'
 assert counts()==before,'冲突必须整包回滚，不入库'
def test_forged_members_rejected(client):
 auth=login(client,'analyst');seg=make_segment('11月会员阅读分群');make_outreach(auth,client,seg)
 base=foreign(pack(client,auth,seg),'bench-b');before=counts()
 forged=copy.deepcopy(base);forged['outreaches'][0]['members']=forged['outreaches'][0]['members']+['SL-999'];forged['bundle_id']=_canon(forged)
 r=client.post('/bundles/preview',json=forged,headers=auth);assert r.status_code==422 and '伪造' in r.json()['detail']
 tampered=copy.deepcopy(base);tampered['segments'][0]['result']['members'][0]['cluster_id']=(tampered['segments'][0]['result']['members'][0]['cluster_id']+1)%3;tampered['bundle_id']=_canon(tampered)
 assert client.post('/bundles/preview',json=tampered,headers=auth).status_code==422
 tampered=copy.deepcopy(base);tampered['segments'][0]['result']['silhouette']=0.999;tampered['bundle_id']=_canon(tampered)
 assert client.post('/bundles/preview',json=tampered,headers=auth).status_code==422
 tampered=copy.deepcopy(base);tampered['segments'][0]['result']['snapshot'][0]['weekly_reading']+=1;tampered['bundle_id']=_canon(tampered)
 assert client.post('/bundles/preview',json=tampered,headers=auth).status_code==422
 assert counts()['segments']==before['segments'],'篡改包一律不入库'
 rejected=[b for b in client.get('/bundles',headers=auth).json() if b['state']=='rejected']
 assert len(rejected)==4,'失败任务留痕可追溯'
def test_broken_reference_rejected(client):
 auth=login(client,'analyst');seg=make_segment('11月会员阅读分群');make_outreach(auth,client,seg)
 pkg=foreign(pack(client,auth,seg),'bench-b');pkg['outreaches'][0]['segment_origin_id']=999;pkg['bundle_id']=_canon(pkg)
 r=client.post('/bundles/preview',json=pkg,headers=auth);assert r.status_code==422 and '断引用' in r.json()['detail']
 assert counts()['segments']==1
def test_replay_and_retry_no_duplicates(client):
 auth=login(client,'analyst');seg=make_segment('11月会员阅读分群');make_outreach(auth,client,seg)
 pkg=foreign(pack(client,auth,seg),'bench-b')
 t1=client.post('/bundles/preview',json=pkg,headers=auth).json()
 t2=client.post('/bundles/preview',json=pkg,headers=auth).json()
 assert t1['id']==t2['id'],'重放同一包不重复建任务'
 assert client.post(f"/bundles/{t1['id']}/confirm",json={},headers=auth).status_code==200
 mid=counts()
 again=client.post('/bundles/preview',json=pkg,headers=auth).json()
 assert again['state']=='stored' and again['id']==t1['id']
 assert client.post(f"/bundles/{t1['id']}/confirm",json={},headers=auth).status_code==200,'已入库确认幂等'
 assert counts()==mid,'重放与重试不得重复建分群或方案'
 reused=client.post('/bundles/preview',json=pkg,headers=auth).json()['preview']
 assert len(reused['segments']['reused'])==1 and len(reused['outreaches']['reused'])==1
def test_self_package_reuses_local_rows(client):
 auth=login(client,'analyst');seg=make_segment('11月会员阅读分群');make_outreach(auth,client,seg)
 pkg=pack(client,auth,seg);before=counts()
 task=client.post('/bundles/preview',json=pkg,headers=auth).json()
 assert len(task['preview']['segments']['reused'])==1,'本工作台的包应识别为复用'
 assert client.post(f"/bundles/{task['id']}/confirm",json={},headers=auth).status_code==200
 assert counts()['segments']==before['segments'],'本工作台回传不得复制自己的分群'
def test_concurrent_import_no_duplicates(client):
 import threading
 auth=login(client,'analyst');seg=make_segment('11月会员阅读分群')
 pkg=foreign(pack(client,auth,seg),'bench-b');results=[]
 def prev():results.append(client.post('/bundles/preview',json=pkg,headers=auth).status_code)
 ts=[threading.Thread(target=prev) for _ in range(4)];[t.start() for t in ts];[t.join() for t in ts]
 assert results==[200]*4 and counts()['bundles']==1,'并发回传同一包只建一个任务'
 task=client.get('/bundles',headers=auth).json()[0];results.clear()
 def confirm():results.append(client.post(f"/bundles/{task['id']}/confirm",json={},headers=auth).status_code)
 ts=[threading.Thread(target=confirm) for _ in range(4)];[t.start() for t in ts];[t.join() for t in ts]
 assert results==[200]*4,results
 assert counts()['segments']==2,'并发确认只入库一个导入分群'
def test_permissions_and_traceability(client):
 auth=login(client,'analyst');viewer=login(client,'viewer');seg=make_segment('11月会员阅读分群')
 pkg=foreign(pack(client,auth,seg),'bench-b')
 assert client.post('/bundles/preview',json=pkg,headers=viewer).status_code==403
 task=client.post('/bundles/preview',json=pkg,headers=auth).json()
 assert client.post(f"/bundles/{task['id']}/confirm",json={},headers=viewer).status_code==403
 assert client.post(f"/bundles/{task['id']}/confirm",json={},headers=auth).status_code==200
 listed=client.get('/bundles',headers=viewer).json()
 assert listed[0]['source']=='bench-b' and listed[0]['state']=='stored' and 'payload' not in listed[0]
 detail=client.get(f"/segments/{seg+1}",headers=viewer).json()
 assert detail['origin']=='bench-b' and detail['origin_id']==seg,'导入分群带来源追溯'
 assert client.get('/meta').json()['workbench']=='bench-a'
 audits=client.get('/audits',headers=login(client,'admin')).json()
 assert any('入库' in a['action'] for a in audits)
