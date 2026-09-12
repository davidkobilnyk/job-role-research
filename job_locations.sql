-- One row per (job, source_field, location string). The primary key defines the grain;
-- `insert or ignore` drops legitimate repeats (e.g. the same office listed twice) silently.

drop table if exists job_locations;
create table job_locations (
    source       text not null,
    slug         text not null,
    job_id       text not null,
    source_field text not null,
    location     text not null,
    primary key (source, slug, job_id, source_field, location)
);

-- Greenhouse
insert or ignore into job_locations
select source, slug, job_id, 'location.name', json_extract(payload, '$.location.name')
from raw_jobs_filtered5 where source = 'greenhouse';

insert or ignore into job_locations
select r.source, r.slug, r.job_id, 'offices.name', json_extract(o.value, '$.name')
from raw_jobs_filtered5 r, json_each(r.payload, '$.offices') o
where r.source = 'greenhouse';

insert or ignore into job_locations
select r.source, r.slug, r.job_id, 'offices.location', json_extract(o.value, '$.location')
from raw_jobs_filtered5 r, json_each(r.payload, '$.offices') o
where r.source = 'greenhouse';

insert or ignore into job_locations
select r.source, r.slug, r.job_id, 'metadata:' || json_extract(m.value, '$.name'), json_extract(m.value, '$.value')
from raw_jobs_filtered5 r, json_each(r.payload, '$.metadata') m
where r.source = 'greenhouse'
  and lower(json_extract(m.value, '$.name')) in
      ('location', 'locations', 'office', 'office location', 'work location', 'state', 'your state',
       'country', 'region', 'city', 'job location', 'work environment', 'job model', 'workplace type');

-- Lever
insert or ignore into job_locations
select source, slug, job_id, 'categories.location', json_extract(payload, '$.categories.location')
from raw_jobs_filtered5 where source = 'lever';

insert or ignore into job_locations
select r.source, r.slug, r.job_id, 'categories.allLocations', l.value
from raw_jobs_filtered5 r, json_each(r.payload, '$.categories.allLocations') l
where r.source = 'lever';

insert or ignore into job_locations
select source, slug, job_id, 'country', json_extract(payload, '$.country')
from raw_jobs_filtered5 where source = 'lever';

insert or ignore into job_locations
select source, slug, job_id, 'workplaceType', json_extract(payload, '$.workplaceType')
from raw_jobs_filtered5 where source = 'lever';

-- Ashby
insert or ignore into job_locations
select source, slug, job_id, 'location', json_extract(payload, '$.location')
from raw_jobs_filtered5 where source = 'ashby';

insert or ignore into job_locations
select source, slug, job_id, 'address.country', json_extract(payload, '$.address.postalAddress.addressCountry')
from raw_jobs_filtered5 where source = 'ashby';

insert or ignore into job_locations
select source, slug, job_id, 'address.region', json_extract(payload, '$.address.postalAddress.addressRegion')
from raw_jobs_filtered5 where source = 'ashby';

insert or ignore into job_locations
select source, slug, job_id, 'address.locality', json_extract(payload, '$.address.postalAddress.addressLocality')
from raw_jobs_filtered5 where source = 'ashby';

insert or ignore into job_locations
select r.source, r.slug, r.job_id, 'secondaryLocations.location', json_extract(s.value, '$.location')
from raw_jobs_filtered5 r, json_each(r.payload, '$.secondaryLocations') s
where r.source = 'ashby';

insert or ignore into job_locations
select r.source, r.slug, r.job_id, 'secondaryLocations.country', json_extract(s.value, '$.address.postalAddress.addressCountry')
from raw_jobs_filtered5 r, json_each(r.payload, '$.secondaryLocations') s
where r.source = 'ashby';

insert or ignore into job_locations
select source, slug, job_id, 'workplaceType', json_extract(payload, '$.workplaceType')
from raw_jobs_filtered5 where source = 'ashby';

insert or ignore into job_locations
select source, slug, job_id, 'isRemote', 'remote'
from raw_jobs_filtered5 where source = 'ashby' and json_extract(payload, '$.isRemote') = 1;

-- Empty strings can't be excluded by the key, so this one delete stays.
delete from job_locations where trim(location) = '';

create index idx_job_locations_loc on job_locations(location);

-- Distinct location strings by frequency: the input for the split-and-resolve rules.
select location, source_field, count(*) as jobs
from job_locations
group by location, source_field
order by jobs desc;