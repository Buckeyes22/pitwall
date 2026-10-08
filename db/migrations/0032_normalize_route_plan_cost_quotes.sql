-- COST-01 follow-up: route-plan admission quotes persisted before the
-- producer fix carried a top-level plan_id and attempt components without
-- the canonical unit/rate/count fields. WorkloadCostRead rejects that shape.
-- Rewrite those rows into the canonical CostQuote shape. The plan id is
-- already persisted in workloads.route_plan_id (migration 0031).

UPDATE pitwall.workloads
SET cost_quote = (cost_quote - 'plan_id')
  || jsonb_build_object(
       'components',
       COALESCE(
         (
           SELECT jsonb_agg(
                    jsonb_build_object(
                      'name', component ->> 'name',
                      'unit', 'attempt',
                      'rate', component ->> 'estimate',
                      'ceiling_rate', component ->> 'ceiling',
                      'count', '1',
                      'ceiling_count', '1',
                      'estimate', component ->> 'estimate',
                      'ceiling', component ->> 'ceiling'
                    )
                  )
           FROM jsonb_array_elements(cost_quote -> 'components') AS component
         ),
         '[]'::jsonb
       ),
       'assumptions',
       COALESCE(cost_quote -> 'assumptions', '[]'::jsonb)
         || to_jsonb(ARRAY['route plan ' || (cost_quote ->> 'plan_id')])
     )
WHERE cost_quote IS NOT NULL
  AND cost_quote ->> 'model' = 'route_plan'
  AND cost_quote ? 'plan_id';
