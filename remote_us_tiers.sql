-- Positive US-remote filter: each job gets a tier by the strongest evidence that it is
-- remote AND available from the US. Run after resolve_geo.sql (needs job_location_resolved
-- and job_geo). Reads raw_jobs_filtered5 so the geo drop is applied here, not assumed.
--
--   1  location field itself says remote + US, or structured type=remote with country=US
--   2  remote in a location field + US signal in another field (offices, metadata, secondary)
--   3  remote in a location field + US phrase / USD pay-range language in the description
--   4  remote in a location field + company's other postings are mostly US
--   5  remote in a location field, no other evidence
--   0  excluded: no remote signal, hybrid/onsite only, or dropped by the geo filter

drop table if exists job_remote_us;
create table job_remote_us (
    source  text not null,
    slug    text not null,
    job_id  text not null,
    tier    integer not null,
    signal  text not null,
    primary key (source, slug, job_id)
);

insert into job_remote_us
with
-- ------------------------------------------------------------------ per-part flags
parts as (
  select source, slug, job_id, source_field, location, job_level, part, part_class,
         case when part in ('remote', 'fully remote', 'remote-first', '100% remote', 'remote work',
                            'remote-friendly', 'remote friendly', 'work from home', 'wfh', 'telecommute',
                            'distributed', 'remote optional', 'remote-optional')
                or part like 'remote %' or part like '% remote' then 1 else 0 end as is_remote,
         case when part in ('hybrid', 'onsite', 'on-site', 'on site', 'in-office', 'in office', 'in-person')
              then 1 else 0 end as is_hybrid_onsite,
         case when source_field in ('country', 'address.country', 'address.region', 'address.locality')
              then 1 else 0 end as is_structured
  from job_location_resolved
),
-- ------------------------------------------------------------------ per-location-string: remote and US together
same_string as (
  select distinct source, slug, job_id
  from (
    select source, slug, job_id
    from parts
    group by source, slug, job_id, source_field, location
    having max(is_remote) = 1 and max(part_class = 'us') = 1
  )
),
-- ------------------------------------------------------------------ per-job flags
job_flags as (
  select source, slug, job_id,
         max(is_remote)                                        as any_remote,
         max(is_remote and job_level = 1)                      as remote_joblevel,
         max(is_hybrid_onsite)                                 as any_hybrid,
         max(part_class = 'us' and is_structured = 1)          as us_structured,
         max(part_class = 'us' and job_level = 1)              as us_joblevel,
         max(part_class = 'us')                                as us_any
  from parts
  group by source, slug, job_id
),
-- ------------------------------------------------------------------ description text
job_text as (
  select source, slug, job_id, lower(json_extract(payload, '$.content')) as txt
  from raw_jobs_filtered5 where source = 'greenhouse'
  union all
  select r.source, r.slug, r.job_id, lower(
      coalesce(json_extract(r.payload, '$.descriptionPlain'), '') || ' ' ||
      coalesce(json_extract(r.payload, '$.additionalPlain'), '') || ' ' ||
      coalesce((select group_concat(json_extract(l.value, '$.content'), ' ')
                from json_each(r.payload, '$.lists') l), ''))
  from raw_jobs_filtered5 r where r.source = 'lever'
  union all
  select source, slug, job_id, lower(json_extract(payload, '$.descriptionPlain'))
  from raw_jobs_filtered5 where source = 'ashby'
),
desc_signal as (
  select source, slug, job_id,
    case
      when instr(txt, 'remote within the us') > 0 or instr(txt, 'remote in the us') > 0
        or instr(txt, 'anywhere in the us') > 0 or instr(txt, 'anywhere in the united states') > 0
        or instr(txt, 'remote (us') > 0 or instr(txt, 'remote - us') > 0
        or instr(txt, 'us-remote') > 0 or instr(txt, 'us remote') > 0 or instr(txt, 'remote us') > 0
        then 'desc:remote-us phrase'
      when instr(txt, 'us-based') > 0 or instr(txt, 'us based') > 0
        or instr(txt, 'based in the united states') > 0 or instr(txt, 'based in the us') > 0
        or instr(txt, 'located in the united states') > 0 or instr(txt, 'located in the us') > 0
        or instr(txt, 'residing in the united states') > 0 or instr(txt, 'reside in the united states') > 0
        or instr(txt, 'live in the united states') > 0 or instr(txt, 'within the united states') > 0
        then 'desc:us-based phrase'
      when instr(txt, 'authorized to work in the united states') > 0 or instr(txt, 'authorized to work in the u.s') > 0
        or instr(txt, 'eligible to work in the united states') > 0 or instr(txt, 'eligible to work in the u.s') > 0
        or instr(txt, 'work authorization in the united states') > 0 or instr(txt, 'us work authorization') > 0
        or instr(txt, 'u.s. work authorization') > 0 or instr(txt, 'us citizen') > 0 or instr(txt, 'u.s. citizen') > 0
        then 'desc:us work authorization'
      when instr(txt, 'us time zone') > 0 or instr(txt, 'u.s. time zone') > 0 or instr(txt, 'us timezone') > 0
        or instr(txt, 'eastern time') > 0 or instr(txt, 'pacific time') > 0 or instr(txt, 'central time') > 0
        or instr(txt, 'mountain time') > 0
        then 'desc:us time zone'
      when instr(txt, 'the following states') > 0 or instr(txt, 'these states') > 0
        or instr(txt, 'approved states') > 0 or instr(txt, 'eligible states') > 0
        then 'desc:state list'
      when instr(txt, '$') > 0 and (instr(txt, 'salary range') > 0 or instr(txt, 'pay range') > 0
        or instr(txt, 'base salary') > 0 or instr(txt, 'compensation range') > 0 or instr(txt, 'base pay') > 0)
        then 'desc:usd pay range'
    end as signal
  from job_text
),
-- ------------------------------------------------------------------ company context from job_geo
company_us as (
  select source, slug,
         sum(n_us > 0) as us_jobs,
         sum(n_us > 0 or n_non_us > 0) as located_jobs
  from job_geo
  group by source, slug
),
-- ------------------------------------------------------------------ assemble
scored as (
  select r.source, r.slug, r.job_id,
         g.decision,
         coalesce(f.any_remote, 0) as any_remote, coalesce(f.remote_joblevel, 0) as remote_joblevel,
         coalesce(f.any_hybrid, 0) as any_hybrid,
         coalesce(f.us_structured, 0) as us_structured, coalesce(f.us_joblevel, 0) as us_joblevel,
         coalesce(f.us_any, 0) as us_any,
         case when ss.job_id is not null then 1 else 0 end as remote_us_same_string,
         d.signal as desc_signal,
         c.us_jobs, c.located_jobs
  from raw_jobs_filtered5 r
  left join job_geo g on g.source = r.source and g.slug = r.slug and g.job_id = r.job_id
  left join job_flags f on f.source = r.source and f.slug = r.slug and f.job_id = r.job_id
  left join same_string ss on ss.source = r.source and ss.slug = r.slug and ss.job_id = r.job_id
  left join desc_signal d on d.source = r.source and d.slug = r.slug and d.job_id = r.job_id
  left join company_us c on c.source = r.source and c.slug = r.slug
)
select source, slug, job_id,
  case
    when decision = 'drop'                                  then 0
    when any_hybrid = 1 and remote_joblevel = 0             then 0
    when any_remote = 0                                     then 0
    when remote_us_same_string = 1                          then 1
    when remote_joblevel = 1 and us_structured = 1          then 1
    when us_any = 1                                         then 2
    when desc_signal is not null                            then 3
    when located_jobs >= 5 and us_jobs * 1.0 / located_jobs >= 0.8 then 4
    else 5
  end as tier,
  case
    when decision = 'drop'                                  then 'geo filter dropped'
    when any_hybrid = 1 and remote_joblevel = 0             then 'hybrid/onsite only'
    when any_remote = 0                                     then 'no remote signal'
    when remote_us_same_string = 1                          then 'remote + US in same location string'
    when remote_joblevel = 1 and us_structured = 1          then 'type=remote + structured country=US'
    when us_any = 1                                         then 'remote + US in another field'
    when desc_signal is not null                            then desc_signal
    when located_jobs >= 5 and us_jobs * 1.0 / located_jobs >= 0.8
         then 'company ' || us_jobs || '/' || located_jobs || ' US'
    else 'bare remote'
  end as signal
from scored;

-- ------------------------------------------------------------------ reports
select tier, count(*) as jobs,
       sum(source = 'greenhouse') as greenhouse, sum(source = 'lever') as lever, sum(source = 'ashby') as ashby
from job_remote_us group by tier order by tier;

select tier, signal, count(*) as jobs
from job_remote_us group by tier, signal order by tier, jobs desc;

-- 10 random per tier with title and locations, for eyeballing precision
select t.tier, t.signal, r.source, r.slug,
       coalesce(json_extract(r.payload, '$.title'), json_extract(r.payload, '$.text')) as title,
       (select group_concat(distinct location) from job_locations l
         where l.source = r.source and l.slug = r.slug and l.job_id = r.job_id) as locations
from (select *, row_number() over (partition by tier order by random()) as rn from job_remote_us) t
join raw_jobs_filtered5 r on r.source = t.source and r.slug = t.slug and r.job_id = t.job_id
where t.rn <= 10
order by t.tier, t.rn;
