import os
from sqlalchemy import create_engine,inspect,text
from sqlalchemy.orm import sessionmaker, declarative_base
engine=create_engine(os.environ['DATABASE_URL'],pool_pre_ping=True)
Session=sessionmaker(bind=engine,expire_on_commit=False)
Base=declarative_base()
def db():
 with Session() as s: yield s
def ensure_schema():
 # 为既有库补齐迁移来源列与唯一索引；新库由create_all一并建出，此处自动跳过
 existing={t:{c['name'] for c in inspect(engine).get_columns(t)} for t in ('segments','outreaches')}
 with engine.begin() as c:
  for t in ('segments','outreaches'):
   if 'origin' not in existing[t]:c.execute(text(f"ALTER TABLE {t} ADD COLUMN origin VARCHAR(60) NOT NULL DEFAULT ''"))
   if 'origin_id' not in existing[t]:c.execute(text(f"ALTER TABLE {t} ADD COLUMN origin_id INTEGER"))
   c.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS ix_{t}_origin ON {t}(origin,origin_id)'))
