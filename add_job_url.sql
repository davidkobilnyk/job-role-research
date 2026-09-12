alter table jobs add column url text;

update jobs
set url = (
  select coalesce(
           json_extract(r.payload, '$.absolute_url'),   -- greenhouse
           json_extract(r.payload, '$.hostedUrl'),      -- lever
           json_extract(r.payload, '$.jobUrl'),         -- ashby
           json_extract(r.payload, '$.applyUrl'))       -- lever/ashby fallback
  from raw_jobs_filtered5 r
  where r.source = jobs.source and r.slug = jobs.slug and r.job_id = jobs.job_id
);

select source, count(*) as jobs, sum(url is null) as missing_url from jobs group by source;