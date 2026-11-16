import os,json
from datetime import datetime,timezone
import redis
from fastapi import APIRouter,Depends,HTTPException,Response,Body
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Reader,Segment,Outreach,Audit,Bundle
from src.schemas.business import Login,ReaderInput,SegmentInput,OutreachInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.bundle import WORKBENCH,LOCK,build_package,verify_package,package_id,diff as bundle_diff,store as bundle_store
from src.controllers.business import ensure_segment
router=APIRouter()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/readers')
def readers(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Reader).order_by(Reader.id).all()]
@router.post('/readers')
def create(data:ReaderInput,u=Depends(editor),s=Depends(db)):
 r=Reader(**data.model_dump());s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'会员编号重复')
 audit(s,u,'录入阅读档案 '+r.code);return dump(r)
@router.put('/readers/{i}')
def edit(i:int,data:ReaderInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Reader,i)
 if not r:raise HTTPException(404,'会员档案不存在')
 for k,v in data.model_dump().items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'会员编号重复')
 audit(s,u,'修订档案 '+str(i));return dump(r)
@router.get('/segments')
def segments(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Segment).order_by(Segment.id.desc()).all()]
@router.post('/segments')
def train(data:SegmentInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Reader).order_by(Reader.id).all()];r=Segment(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('reader:segments',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重试';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交分群 '+str(r.id));return dump(r)
@router.get('/segments/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_segment(s,i))
@router.get('/segments/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_segment(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功分群可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-segments-{i}.json"'})
@router.get('/outreaches')
def outreaches(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Outreach).order_by(Outreach.id.desc()).all()]
@router.post('/outreaches')
def outreach(data:OutreachInput,u=Depends(editor),s=Depends(db)):
 r=ensure_segment(s,data.segment_id)
 if r.state!='done':raise HTTPException(409,'分群完成后才能制定触达方案')
 members=[m['code'] for m in r.result['members'] if m['cluster_id']==data.cluster_id]
 if not members:raise HTTPException(422,'该版本没有所选群组')
 o=Outreach(**data.model_dump(),members=members);s.add(o);s.commit();audit(s,u,'起草触达方案 '+str(o.id));return dump(o)
@router.post('/outreaches/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=s.get(Outreach,i)
 if not r:raise HTTPException(404,'触达方案不存在')
 if r.status!='draft':raise HTTPException(409,'已签核方案不可重复签核')
 r.status='approved';s.commit();audit(s,u,'签核触达方案 '+str(i));return dump(r)
@router.get('/outreaches/{i}/export')
def export_outreach(i:int,u=Depends(current),s=Depends(db)):
 r=s.get(Outreach,i)
 if not r:raise HTTPException(404,'触达方案不存在')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-outreach-{i}.json"'})
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
@router.get('/meta')
def meta():return {'workbench':WORKBENCH}
@router.get('/segments/{i}/bundle')
def export_bundle(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_segment(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功分群可打包')
 outs=s.query(Outreach).filter_by(segment_id=r.id).order_by(Outreach.id).all()
 pkg=build_package(r,outs);audit(s,u,'打包分群 '+str(i)+' 及关联方案 '+str(len(outs))+' 份')
 return Response(json.dumps(pkg,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-bundle-{i}.json"'})
def bundle_view(b):return {c.name:getattr(b,c.name) for c in b.__table__.columns if c.name!='payload'}
@router.get('/bundles')
def bundles(u=Depends(current),s=Depends(db)):return [bundle_view(b) for b in s.query(Bundle).order_by(Bundle.id.desc()).limit(100)]
@router.get('/bundles/{i}')
def bundle_detail(i:int,u=Depends(current),s=Depends(db)):
 b=s.get(Bundle,i)
 if not b:raise HTTPException(404,'迁移任务不存在')
 return bundle_view(b)
@router.post('/bundles/preview')
def preview_bundle(data:dict=Body(...),u=Depends(editor),s=Depends(db)):
 bid=package_id(data)
 existing=s.query(Bundle).filter_by(bundle_id=bid).first()
 if existing:
  if existing.state=='rejected':raise HTTPException(422,existing.error)
  return bundle_view(existing)
 b=Bundle(bundle_id=bid,source=str(data.get('source',''))[:60],state='pending',payload=data,created=datetime.now(timezone.utc).isoformat())
 try:verify_package(data)
 except ValueError as e:
  b.state='rejected';b.error=str(e);s.add(b);s.commit();audit(s,u,'回传分群包未通过核验：'+str(e));raise HTTPException(422,str(e))
 b.preview=bundle_diff(s,data);s.add(b)
 try:s.commit()
 except IntegrityError:
  s.rollback();existing=s.query(Bundle).filter_by(bundle_id=bid).first()
  if existing:return bundle_view(existing)
  raise HTTPException(409,'并发回传冲突，请重试')
 audit(s,u,'回传分群包 '+bid[:12]+' 待核验');return bundle_view(b)
@router.post('/bundles/{i}/confirm')
def confirm_bundle(i:int,u=Depends(editor),s=Depends(db)):
 with LOCK:
  b=s.get(Bundle,i)
  if not b:raise HTTPException(404,'迁移任务不存在')
  if b.state=='stored':return bundle_view(b)
  if b.state=='rejected':raise HTTPException(409,'该包未通过核验，不能入库')
  try:
   verify_package(b.payload)
   preview=bundle_diff(s,b.payload)
   if preview['segments']['conflicts'] or preview['outreaches']['conflicts']:raise ValueError('存在同源异文冲突，整包拒绝入库')
   bundle_store(s,b.payload);b.state='stored';b.preview=bundle_diff(s,b.payload);s.commit()
  except ValueError as e:
   s.rollback();fresh=s.get(Bundle,i);fresh.state='rejected';fresh.error=str(e);s.commit();audit(s,u,'分群包 '+str(i)+' 入库核验失败：'+str(e));raise HTTPException(422,str(e))
  except IntegrityError:
   s.rollback();raise HTTPException(409,'并发导入冲突，请重试')
  audit(s,u,'分群包 '+str(i)+' 已入库');return bundle_view(b)
