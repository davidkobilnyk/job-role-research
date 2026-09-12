drop table raw_jobs_filtered4;

create table raw_jobs_filtered4 as
select * from raw_jobs_filtered3
where job_id in (
    select job_id from job_term_counts where n_terms > 1
);