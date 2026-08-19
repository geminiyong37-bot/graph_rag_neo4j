begin;

create or replace function documents_hybrid_update_fts()
returns trigger
language plpgsql
as $$
begin
  new.fts := to_tsvector('simple', coalesce(new.content, ''));
  return new;
end;
$$;

drop trigger if exists trg_documents_hybrid_update_fts
on "대학 온라인 상담용 데이터";

drop trigger if exists trg_univ_hybrid_update_fts
on "대학 온라인 상담용 데이터";

create trigger trg_documents_hybrid_update_fts
before insert or update on "대학 온라인 상담용 데이터"
for each row
execute function documents_hybrid_update_fts();

commit;
