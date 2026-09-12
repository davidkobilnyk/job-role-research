select r.source, r.slug, r.job_id,
       coalesce(json_extract(r.payload, '$.title'), json_extract(r.payload, '$.text')) as title,
       group_concat(distinct x.source_field || ': ' || x.location) as locations,
       g.decision, g.n_us, g.n_non_us, g.n_unknown
from job_location_resolved x
join raw_jobs_filtered5 r on r.source = x.source and r.slug = x.slug and r.job_id = x.job_id
join job_geo g on g.source = x.source and g.slug = x.slug and g.job_id = x.job_id
where x.part = 'paris'
group by r.source, r.slug, r.job_id
order by random()
limit 25;