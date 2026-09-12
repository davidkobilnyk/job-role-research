select lower(location), count(*) as jobs
from job_locations
group by lower(location)
order by jobs desc;