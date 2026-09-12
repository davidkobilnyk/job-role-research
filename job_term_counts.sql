drop table job_term_counts;

create table job_term_counts as
select source, slug, job_id,
       count(*)                                    as n_terms,
       sum(category = 'language')                  as n_languages,
       sum(category = 'data_ml')                   as n_data_ml,
       sum(category in ('cloud_infra', 'database')) as n_infra_db,
       max(term = 'AI training gig')                             as is_ai_training_gig,
       max(term = 'Freelance / hourly')                          as is_freelance
from job_terms
group by source, slug, job_id;

create unique index idx_job_term_counts on job_term_counts(source, slug, job_id);
