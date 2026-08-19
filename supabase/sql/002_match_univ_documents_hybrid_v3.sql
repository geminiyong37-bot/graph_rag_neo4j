create or replace function match_univ_documents_hybrid_v3 (
  query_embedding vector(1536),
  match_count int,
  filter jsonb default '{}',
  query_text text default '',
  core_keywords text[] default '{}',
  optional_keywords text[] default '{}'
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
  core_group_queries text[] := array[]::text[];
  optional_group_queries text[] := array[]::text[];
  strict_query tsquery := null;
  expanded_query tsquery := null;
  core_count int := 0;
begin
  select coalesce(array_agg(group_query order by first_ordinal), array[]::text[])
  into core_group_queries
  from (
    select group_query, min(ordinality) as first_ordinal
    from (
      select
        source.ordinality,
        '(' || string_agg(quote_literal(tokens.lexeme), ' & ' order by tokens.lexeme) || ')'
          as group_query
      from (
        select input.keyword, input.ordinality
        from unnest(coalesce(core_keywords, array[]::text[]))
          with ordinality as input(keyword, ordinality)
        where nullif(btrim(input.keyword), '') is not null
        order by input.ordinality
        limit 3
      ) as source
      cross join lateral (
        select token.lexeme
        from unnest(tsvector_to_array(to_tsvector('simple', btrim(source.keyword))))
          as token(lexeme)
        where char_length(token.lexeme) >= 2
        group by token.lexeme
        order by char_length(token.lexeme) desc, token.lexeme
        limit 4
      ) as tokens
      group by source.ordinality
    ) as raw_groups
    group by group_query
  ) as deduplicated_groups;

  select coalesce(array_agg(group_query order by first_ordinal), array[]::text[])
  into optional_group_queries
  from (
    select group_query, min(ordinality) as first_ordinal
    from (
      select
        source.ordinality,
        '(' || string_agg(quote_literal(tokens.lexeme), ' & ' order by tokens.lexeme) || ')'
          as group_query
      from (
        select input.keyword, input.ordinality
        from unnest(coalesce(optional_keywords, array[]::text[]))
          with ordinality as input(keyword, ordinality)
        where nullif(btrim(input.keyword), '') is not null
        order by input.ordinality
        limit 6
      ) as source
      cross join lateral (
        select token.lexeme
        from unnest(tsvector_to_array(to_tsvector('simple', btrim(source.keyword))))
          as token(lexeme)
        where char_length(token.lexeme) >= 2
        group by token.lexeme
        order by char_length(token.lexeme) desc, token.lexeme
        limit 4
      ) as tokens
      group by source.ordinality
    ) as raw_groups
    group by group_query
  ) as deduplicated_groups;

  core_count := cardinality(core_group_queries);
  if core_count > 0 then
    strict_query := array_to_string(core_group_queries, ' & ')::tsquery;
  end if;

  if cardinality(optional_group_queries) > 0 then
    case core_count
      when 2 then
        expanded_query := (
          '(' || array_to_string(core_group_queries, ' | ') || ') & ('
          || array_to_string(optional_group_queries, ' | ') || ')'
        )::tsquery;
      when 3 then
        expanded_query := (
          '((' || core_group_queries[1] || ' & ' || core_group_queries[2] || ') | '
          || '(' || core_group_queries[1] || ' & ' || core_group_queries[3] || ') | '
          || '(' || core_group_queries[2] || ' & ' || core_group_queries[3] || ')) & ('
          || array_to_string(optional_group_queries, ' | ') || ')'
        )::tsquery;
    end case;
  end if;

  return query
  with vector_search as (
    select
      d.id,
      row_number() over (order by d.embedding <=> query_embedding) as rank
    from "대학 온라인 상담용 데이터" d
    where query_embedding is not null
      and d.embedding is not null
      and d.metadata @> coalesce(filter, '{}'::jsonb)
    order by d.embedding <=> query_embedding
    limit safe_match_count * 3
  ),
  strict_search as (
    select
      d.id,
      row_number() over (order by ts_rank_cd(d.fts, strict_query) desc) as rank
    from "대학 온라인 상담용 데이터" d
    where strict_query is not null
      and d.fts @@ strict_query
      and d.metadata @> coalesce(filter, '{}'::jsonb)
    order by ts_rank_cd(d.fts, strict_query) desc
    limit safe_match_count * 3
  ),
  expanded_search as (
    select
      d.id,
      row_number() over (order by ts_rank_cd(d.fts, expanded_query) desc) as rank
    from "대학 온라인 상담용 데이터" d
    where expanded_query is not null
      and d.fts @@ expanded_query
      and d.metadata @> coalesce(filter, '{}'::jsonb)
    order by ts_rank_cd(d.fts, expanded_query) desc
    limit safe_match_count * 3
  ),
  weighted_candidates as (
    select v.id, 0.70::float8 / (60 + v.rank) as score from vector_search v
    union all
    select s.id, 1.00::float8 / (60 + s.rank) as score from strict_search s
    union all
    select e.id, 0.40::float8 / (60 + e.rank) as score from expanded_search e
  )
  select
    d.id,
    d.content,
    d.metadata,
    sum(w.score)::float as similarity
  from weighted_candidates w
  join "대학 온라인 상담용 데이터" d on d.id = w.id
  group by d.id, d.content, d.metadata
  order by sum(w.score) desc, d.id asc
  limit safe_match_count;
end;
$$;
