create table raw_jobs_filtered7 as
  select r.* from raw_jobs_filtered5 r
  join job_remote_us t on t.source = r.source and t.slug = r.slug and t.job_id = r.job_id
  where t.tier between 1 and 3;