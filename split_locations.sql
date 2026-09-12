-- Split each location string into alternatives (separate places) and components
-- (parts of one place). Two levels:
--   alternatives: separated by ';'  '/'  '|'  ' or '  ' & '
--   components:   separated by ','  ' - '  ' – '  '('  ')'
-- Everything is normalized to one delimiter per level, then split with a recursive CTE.

drop table if exists job_location_parts;
create table job_location_parts (
    source       text not null,
    slug         text not null,
    job_id       text not null,
    source_field text not null,
    location     text not null,   -- original string
    alt_index    integer not null, -- which alternative place (0-based)
    comp_index   integer not null, -- which component within that place (0-based)
    part         text not null,    -- cleaned piece
    primary key (source, slug, job_id, source_field, location, alt_index, comp_index)
);

insert or ignore into job_location_parts
with
norm as (
  select source, slug, job_id, source_field, location,
         -- level 1: alternatives -> ';'
         replace(replace(replace(replace(replace(
           lower(location),
           ' or ', ';'), ' / ', ';'), '/', ';'), ' | ', ';'), ' & ', ';') as s
  from job_locations
),
alts(source, slug, job_id, source_field, location, alt_index, piece, rest) as (
  select source, slug, job_id, source_field, location, 0,
         case when instr(s, ';') > 0 then substr(s, 1, instr(s, ';') - 1) else s end,
         case when instr(s, ';') > 0 then substr(s, instr(s, ';') + 1) else '' end
  from norm
  union all
  select source, slug, job_id, source_field, location, alt_index + 1,
         case when instr(rest, ';') > 0 then substr(rest, 1, instr(rest, ';') - 1) else rest end,
         case when instr(rest, ';') > 0 then substr(rest, instr(rest, ';') + 1) else '' end
  from alts where rest != ''
),
alt_norm as (
  select source, slug, job_id, source_field, location, alt_index,
         -- level 2: components -> ','
         replace(replace(replace(replace(replace(replace(
           piece,
           ' - ', ','), ' – ', ','), ' — ', ','), '(', ','), ')', ','), ' | ', ',') as s
  from alts
),
comps(source, slug, job_id, source_field, location, alt_index, comp_index, piece, rest) as (
  select source, slug, job_id, source_field, location, alt_index, 0,
         case when instr(s, ',') > 0 then substr(s, 1, instr(s, ',') - 1) else s end,
         case when instr(s, ',') > 0 then substr(s, instr(s, ',') + 1) else '' end
  from alt_norm
  union all
  select source, slug, job_id, source_field, location, alt_index, comp_index + 1,
         case when instr(rest, ',') > 0 then substr(rest, 1, instr(rest, ',') - 1) else rest end,
         case when instr(rest, ',') > 0 then substr(rest, instr(rest, ',') + 1) else '' end
  from comps where rest != ''
)
select source, slug, job_id, source_field, location, alt_index, comp_index,
       trim(piece, ' -–—:') as part
from comps
where trim(piece, ' -–—:') != '';

create index idx_job_location_parts_part on job_location_parts(part);

-- Distinct parts by frequency: the input to the resolver.
select part, count(*) as rows_, count(distinct source || slug || job_id) as jobs
from job_location_parts
group by part order by jobs desc;
