-- One row per (job, department-or-team name), all three sources.
create temp table job_dept as
  select r.source, r.slug, r.job_id, lower(json_extract(d.value, '$.name')) as name
  from raw_jobs_filtered2 r, json_each(r.payload, '$.departments') d
  where r.source = 'greenhouse'
  union all
  select source, slug, job_id, lower(json_extract(payload, '$.categories.department'))
  from raw_jobs_filtered2 where source = 'lever'
  union all
  select source, slug, job_id, lower(json_extract(payload, '$.categories.team'))
  from raw_jobs_filtered2 where source = 'lever'
  union all
  select source, slug, job_id, lower(json_extract(payload, '$.department'))
  from raw_jobs_filtered2 where source = 'ashby'
  union all
  select source, slug, job_id, lower(json_extract(payload, '$.team'))
  from raw_jobs_filtered2 where source = 'ashby';

-- Keep every job that has no department/team matching an excluded term.
create table raw_jobs_filtered3 as
  select r.* from raw_jobs_filtered2 r
  where not exists (
    select 1 from job_dept jd
    where jd.source = r.source and jd.slug = r.slug and jd.job_id = r.job_id
      and jd.name regexp 'language & linguistics|\bsales\b|marketing|\bpeople\b|legal|customer support|field|hardware|manufacturing|collection|propulsion|malaysia|mechanical|air dominance & strike|biology|recruiting|water'
  );

-- How many went, and which terms did the work.
select count(*) as kept from raw_jobs_filtered3;
select e.term, count(distinct jd.source || jd.slug || jd.job_id) as jobs_removed
from job_dept jd join exclude_terms e on jd.name = e.term
group by e.term order by jobs_removed desc;