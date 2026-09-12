select source, slug,
       coalesce(json_extract(payload, '$.location.name'),
                json_extract(payload, '$.categories.location'),
                json_extract(payload, '$.location')) as location,
       substr(coalesce(json_extract(payload, '$.descriptionPlain'),
                       json_extract(payload, '$.content')), 1, 200) as opening
from raw_jobs_filtered5
order by random() limit 100;