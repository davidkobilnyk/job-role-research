-- Classify every location part as us / non_us / nongeo / unknown using geo_lookup,
-- roll up per job, and drop only jobs with a known non-US signal, no US signal,
-- and no unknown parts. Run after split_locations.sql and build_geo_lookup.py.

-- ---------------------------------------------------------------------------
-- 1. Per-part classification
-- ---------------------------------------------------------------------------
drop table if exists job_location_resolved;
create table job_location_resolved (
    source       text not null,
    slug         text not null,
    job_id       text not null,
    source_field text not null,
    location     text not null,
    alt_index    integer not null,
    comp_index   integer not null,
    part         text not null,      -- cleaned (brackets/quotes from metadata arrays stripped)
    part_class   text not null,      -- us | non_us | nongeo | unknown
    job_level    integer not null,   -- 1 if the field describes the job's own location
    primary key (source, slug, job_id, source_field, location, alt_index, comp_index)
);

insert into job_location_resolved
with parts as (
  select source, slug, job_id, source_field, location, alt_index, comp_index,
         trim(part, ' []"''') as part,
         case when source_field in ('location.name',
                                    'categories.location', 'categories.allLocations', 'country',
                                    'location', 'address.country', 'address.region', 'address.locality',
                                    'secondaryLocations.location', 'secondaryLocations.country')
              then 1 else 0 end as job_level
  from job_location_parts
),
scored as (
  select source, slug, job_id, source_field, location, alt_index, comp_index, part, job_level,
    -- Lever's `country` field holds ISO codes: read only country_code rows there.
    -- Every other field: read every kind except country_code (so "in" is Indiana, "de" is Delaware).
    max(case when allowed and us_flag in ('us', 'includes_us') then 1 else 0 end)         as has_us,
    -- A non-US city that shares its name with a place in another country counts as
    -- non-US only when it is large (>= 400k): big enough to dwarf any US namesake.
    -- Smaller ambiguous cities (Cambridge, Reading) are unknown.
    max(case when allowed and us_flag = 'non_us'
              and (kind != 'city' or ambiguous = 0
                   or coalesce(population, 0) >= 400000) then 1 else 0 end)                as has_non_us,
    max(case when allowed and kind = 'city' and us_flag = 'non_us' and ambiguous = 1
              and coalesce(population, 0) < 400000 then 1 else 0 end)                      as has_ambiguous,
    max(case when allowed and (kind in ('remote', 'nongeo')
              or (kind = 'timezone' and us_flag = 'unknown')) then 1 else 0 end)        as has_nongeo
  from (
    select p.*, g.kind, g.us_flag, g.ambiguous, g.population,
           case when p.source_field = 'country' then g.kind = 'country_code'
                else g.kind != 'country_code' end as allowed
    from parts p
    left join geo_lookup g on g.term = p.part
    where p.part != ''
  ) pg
  group by source, slug, job_id, source_field, location, alt_index, comp_index, part, job_level
)
select source, slug, job_id, source_field, location, alt_index, comp_index, part,
       case when has_us = 1        then 'us'
            when has_non_us = 1    then 'non_us'
            when has_ambiguous = 1 then 'unknown'    -- ambiguous city too small to be sure it's not the US one
            when has_nongeo = 1    then 'nongeo'
            else 'unknown' end as part_class,
       job_level
from scored;

create index idx_jlr_job on job_location_resolved(source, slug, job_id);
create index idx_jlr_class on job_location_resolved(part_class);

-- ---------------------------------------------------------------------------
-- 2. Per-job roll-up and decision
-- ---------------------------------------------------------------------------
drop table if exists job_geo;
create table job_geo (
    source     text not null,
    slug       text not null,
    job_id     text not null,
    n_us       integer not null,   -- US parts, any field
    n_non_us   integer not null,   -- non-US parts, job-level fields only
    n_unknown  integer not null,   -- unresolved parts, any field
    n_nongeo   integer not null,
    decision   text not null,      -- keep | drop
    primary key (source, slug, job_id)
);

insert into job_geo
select r.source, r.slug, r.job_id,
       coalesce(sum(x.part_class = 'us'), 0),
       coalesce(sum(x.part_class = 'non_us' and x.job_level = 1), 0),
       coalesce(sum(x.part_class = 'unknown'), 0),
       coalesce(sum(x.part_class = 'nongeo'), 0),
       case when coalesce(sum(x.part_class = 'non_us' and x.job_level = 1), 0) > 0
             and coalesce(sum(x.part_class = 'us'), 0) = 0
             and coalesce(sum(x.part_class = 'unknown'), 0) = 0
            then 'drop' else 'keep' end
from raw_jobs_filtered5 r
left join job_location_resolved x on x.source = r.source and x.slug = r.slug and x.job_id = r.job_id
group by r.source, r.slug, r.job_id;

-- ---------------------------------------------------------------------------
-- 3. Output tables
-- ---------------------------------------------------------------------------
drop table if exists raw_jobs_filtered6;
create table raw_jobs_filtered6 as
  select r.* from raw_jobs_filtered5 r
  join job_geo g on g.source = r.source and g.slug = r.slug and g.job_id = r.job_id
  where g.decision = 'keep';

drop table if exists raw_jobs_dropped_geo;
create table raw_jobs_dropped_geo as
  select r.* from raw_jobs_filtered5 r
  join job_geo g on g.source = r.source and g.slug = r.slug and g.job_id = r.job_id
  where g.decision = 'drop';

-- ---------------------------------------------------------------------------
-- 4. Reports
-- ---------------------------------------------------------------------------
-- Sanity: one resolved row per non-empty part.
select (select count(*) from job_location_parts where trim(part, ' []"''') != '') as parts,
       (select count(*) from job_location_resolved) as resolved;

-- Before / kept / dropped.
select (select count(*) from raw_jobs_filtered5) as before,
       (select count(*) from raw_jobs_filtered6) as kept,
       (select count(*) from raw_jobs_dropped_geo) as dropped;

-- Why jobs were kept: how many are held by unknown parts alone.
select
  sum(decision = 'keep' and n_us > 0)                                   as kept_us_signal,
  sum(decision = 'keep' and n_us = 0 and n_non_us > 0 and n_unknown > 0) as kept_only_by_unknown,
  sum(decision = 'keep' and n_us = 0 and n_non_us = 0)                  as kept_no_geo_signal
from job_geo;

-- Unresolved parts by jobs affected: the input to the alias file.
select part, source_field, count(distinct source || '/' || slug || '/' || job_id) as jobs
from job_location_resolved
where part_class = 'unknown'
group by part, source_field
order by jobs desc
limit 200;

-- Which non-US parts drove the drops.
select x.part, count(distinct x.source || '/' || x.slug || '/' || x.job_id) as jobs_dropped
from job_location_resolved x
join job_geo g on g.source = x.source and g.slug = x.slug and g.job_id = x.job_id
where g.decision = 'drop' and x.part_class = 'non_us' and x.job_level = 1
group by x.part order by jobs_dropped desc limit 50;

-- Review sample of the drop set, at most 3 per company.
select source, slug, job_id, location
from (
  select r.source, r.slug, r.job_id,
         (select group_concat(distinct location) from job_locations l
           where l.source = r.source and l.slug = r.slug and l.job_id = r.job_id) as location,
         row_number() over (partition by r.source, r.slug order by random()) as rn
  from raw_jobs_dropped_geo r
) where rn <= 3
order by random() limit 100;
