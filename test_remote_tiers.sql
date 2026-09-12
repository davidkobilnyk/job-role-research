select t.tier, t.signal, r.source, r.slug, r.job_id,
       coalesce(json_extract(r.payload, '$.title'), json_extract(r.payload, '$.text')) as title,
       (select group_concat(distinct location) from job_locations l
         where l.source = r.source and l.slug = r.slug and l.job_id = r.job_id) as locations
from (select *, row_number() over (partition by tier order by random()) as rn from job_remote_us) t
join raw_jobs_filtered5 r on r.source = t.source and r.slug = t.slug and r.job_id = t.job_id
where t.rn <= 10
order by t.tier, t.rn;