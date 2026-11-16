-- 002_bundles：跨工作台分群迁移
-- 分群/方案增加来源标识（origin 为空串表示本工作台自建），迁移任务表记录回传与核验状态。
-- 应用启动时由 ensure_schema 幂等执行等价语句；本文件保留实际 DDL 备查。

ALTER TABLE public.segments ADD COLUMN origin character varying(60) NOT NULL DEFAULT '';
ALTER TABLE public.segments ADD COLUMN origin_id integer;
ALTER TABLE public.outreaches ADD COLUMN origin character varying(60) NOT NULL DEFAULT '';
ALTER TABLE public.outreaches ADD COLUMN origin_id integer;

CREATE UNIQUE INDEX ix_segments_origin ON public.segments (origin, origin_id);
CREATE UNIQUE INDEX ix_outreaches_origin ON public.outreaches (origin, origin_id);

CREATE TABLE public.bundles (
    id integer NOT NULL,
    bundle_id character varying(64),
    source character varying(60),
    state character varying(20),
    preview json,
    payload json,
    error text,
    created character varying(40)
);

CREATE SEQUENCE public.bundles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.bundles_id_seq OWNED BY public.bundles.id;
ALTER TABLE ONLY public.bundles ALTER COLUMN id SET DEFAULT nextval('public.bundles_id_seq'::regclass);

ALTER TABLE ONLY public.bundles
    ADD CONSTRAINT bundles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bundles
    ADD CONSTRAINT bundles_bundle_id_key UNIQUE (bundle_id);
