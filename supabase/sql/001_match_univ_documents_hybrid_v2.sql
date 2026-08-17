-- 기존 V1 함수는 보존하고 한국어 OR 전문검색을 사용하는 V2만 추가한다.
create or replace function match_univ_documents_hybrid_v2 (
  query_embedding vector(1536),
  match_count int,
  filter jsonb default '{}',
  query_text text default ''
)
returns table (
  id int8,
  content text,
  metadata jsonb,
  similarity float
)
language plpgsql
stable
as $$
declare
  safe_match_count int := greatest(1, least(coalesce(match_count, 20), 50));
  fts_query tsquery := null;
begin
  -- 긴 질문 전체를 OR로 만들면 후보가 폭증하므로 긴 핵심어 8개만 사용한다.
  if nullif(btrim(query_text), '') is not null then
    select string_agg(quote_literal(lexeme), ' | ')::tsquery
    into fts_query
    from (
      select lexeme
      from unnest(
        tsvector_to_array(to_tsvector('simple', query_text))
      ) as lexeme
      where char_length(lexeme) >= 2
      group by lexeme
      order by char_length(lexeme) desc, lexeme
      limit 8
    ) as keywords;
  end if;

  return query
  with vector_search as (
    select
      d.id,
      row_number() over (order by d.embedding <=> query_embedding) as rank
    from "대학 온라인 상담용 데이터" d
    where d.metadata @> coalesce(filter, '{}'::jsonb)
      and d.embedding is not null
    order by d.embedding <=> query_embedding
    limit safe_match_count * 3
  ),
  fts_search as (
    select
      d.id,
      row_number() over (order by ts_rank_cd(d.fts, fts_query) desc) as rank
    from "대학 온라인 상담용 데이터" d
    where fts_query is not null
      and d.fts @@ fts_query
      and d.metadata @> coalesce(filter, '{}'::jsonb)
    order by ts_rank_cd(d.fts, fts_query) desc
    limit safe_match_count * 3
  )
  select
    d.id,
    d.content,
    d.metadata,
    (
      coalesce(1.0 / (60 + v.rank), 0.0)
      + coalesce(1.0 / (60 + f.rank), 0.0)
    )::float as similarity
  from "대학 온라인 상담용 데이터" d
  left join vector_search v on d.id = v.id
  left join fts_search f on d.id = f.id
  where v.id is not null or f.id is not null
  order by similarity desc
  limit safe_match_count;
end;
$$;
