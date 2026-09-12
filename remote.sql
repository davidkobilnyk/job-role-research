-- Recall-first remote filter: keep any job whose location, workplace type,
-- offices, metadata, or description mentions remote or a synonym.
-- Matches values only (never the raw JSON text, which would match Ashby's isRemote key on every row).

create index idx_filtered4_key on raw_jobs_filtered4(source, slug, job_id);

create temp table remote_terms(term text);
insert into remote_terms values
  ('remote'),                         -- covers remotely, remote-first, remote/us, etc.
  ('work from home'), ('work-from-home'), ('working from home'), ('wfh'),
  ('home-based'), ('home based'), ('home office'), ('from home'),
  ('work from anywhere'), ('anywhere in the us'), ('anywhere in the united states'),
  ('location independent'), ('location-independent'), ('location agnostic'),
  ('distributed team'), ('distributed company'), ('fully distributed'), ('globally distributed'),
  ('no office'), ('office-optional'), ('office optional'),
  ('virtual'), ('telecommut'), ('telework'), ('tele-work'),
  ('hybrid');                         -- kept for recall; classified out later

-- One lowercase text blob per job from the fields that can carry a remote signal.
create temp table job_remote_text as
  select r.source, r.slug, r.job_id, lower(
      coalesce(json_extract(r.payload, '$.location.name'), '') || ' | ' ||
      coalesce((select group_concat(json_extract(o.value, '$.name'), ' | ')
                from json_each(r.payload, '$.offices') o), '') || ' | ' ||
      coalesce((select group_concat(json_extract(m.value, '$.value'), ' | ')
                from json_each(r.payload, '$.metadata') m), '') || ' | ' ||
      coalesce(json_extract(r.payload, '$.content'), '')
  ) as txt
  from raw_jobs_filtered4 r where r.source = 'greenhouse'

  union all
  select r.source, r.slug, r.job_id, lower(
      coalesce(json_extract(r.payload, '$.workplaceType'), '') || ' | ' ||
      coalesce(json_extract(r.payload, '$.categories.location'), '') || ' | ' ||
      coalesce((select group_concat(l.value, ' | ')
                from json_each(r.payload, '$.categories.allLocations') l), '') || ' | ' ||
      coalesce(json_extract(r.payload, '$.descriptionPlain'), '') || ' | ' ||
      coalesce(json_extract(r.payload, '$.additionalPlain'), '') || ' | ' ||
      coalesce((select group_concat(json_extract(x.value, '$.content'), ' | ')
                from json_each(r.payload, '$.lists') x), '')
  )
  from raw_jobs_filtered4 r where r.source = 'lever'

  union all
  select r.source, r.slug, r.job_id, lower(
      coalesce(json_extract(r.payload, '$.workplaceType'), '') || ' | ' ||
      case when json_extract(r.payload, '$.isRemote') = 1 then 'remote' else '' end || ' | ' ||
      coalesce(json_extract(r.payload, '$.location'), '') || ' | ' ||
      coalesce((select group_concat(json_extract(s.value, '$.location'), ' | ')
                from json_each(r.payload, '$.secondaryLocations') s), '') || ' | ' ||
      coalesce(json_extract(r.payload, '$.descriptionPlain'), '')
  )
  from raw_jobs_filtered4 r where r.source = 'ashby';

create index idx_job_remote_text on job_remote_text(source, slug, job_id);

-- Keep jobs with at least one match.
create table raw_jobs_filtered5 as
  select r.*
  from raw_jobs_filtered4 r
  where exists (
    select 1 from job_remote_text t
    join remote_terms rt on instr(t.txt, rt.term) > 0
    where t.source = r.source and t.slug = r.slug and t.job_id = r.job_id
  );
create index idx_filtered5_key on raw_jobs_filtered5(source, slug, job_id);

-- The dropped set, kept for a review sample.
create table raw_jobs_dropped_remote as
  select r.* from raw_jobs_filtered4 r
  where not exists (select 1 from raw_jobs_filtered5 f
                    where f.source = r.source and f.slug = r.slug and f.job_id = r.job_id);

-- Summary.
select (select count(*) from raw_jobs_filtered4) as before,
       (select count(*) from raw_jobs_filtered5) as kept,
       (select count(*) from raw_jobs_dropped_remote) as dropped;

-- Which terms did the work (a job can count under several).
select rt.term, count(*) as jobs
from job_remote_text t join remote_terms rt on instr(t.txt, rt.term) > 0
group by rt.term order by jobs desc;

-- Review sample of the dropped set: location and first 200 chars.
select source, slug,
       coalesce(json_extract(payload, '$.location.name'),
                json_extract(payload, '$.categories.location'),
                json_extract(payload, '$.location')) as location,
       substr(coalesce(json_extract(payload, '$.descriptionPlain'),
                       json_extract(payload, '$.content')), 1, 200) as opening
from raw_jobs_dropped_remote
order by random() limit 100;