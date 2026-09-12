-- Departments and teams across all three sources, with counts.
-- Greenhouse stores departments as an array of objects, so it's unnested with json_each.
with dept as (
  select r.source, 'department' as kind, json_extract(d.value, '$.name') as name
  from raw_jobs_filtered2 r, json_each(r.payload, '$.departments') d
  where r.source = 'greenhouse'

  union all
  select source, 'department', json_extract(payload, '$.categories.department')
  from raw_jobs_filtered2 where source = 'lever'

  union all
  select source, 'team', json_extract(payload, '$.categories.team')
  from raw_jobs_filtered2 where source = 'lever'

  union all
  select source, 'department', json_extract(payload, '$.department')
  from raw_jobs_filtered2 where source = 'ashby'

  union all
  select source, 'team', json_extract(payload, '$.team')
  from raw_jobs_filtered2 where source = 'ashby'
)
select source, kind, lower(trim(name)) as name, count(*) as n
from dept
where name is not null and trim(name) != ''
group by source, kind, lower(trim(name))
order by n desc;
