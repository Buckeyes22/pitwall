-- 0025: opt subscriptions into explicit outbound event types.

ALTER TABLE pitwall.webhook_subscriptions
  ADD COLUMN event_types TEXT[] NOT NULL
    DEFAULT ARRAY['workload.completed']::TEXT[],
  ADD CONSTRAINT webhook_subscription_event_types_allowed CHECK (
    event_types <@ ARRAY[
      'workload.completed',
      'lease.ready',
      'lease.renewed',
      'lease.stopped',
      'lease.expiring'
    ]::TEXT[]
    AND cardinality(event_types) > 0
  );

CREATE INDEX idx_webhook_subscriptions_event_types
  ON pitwall.webhook_subscriptions USING GIN(event_types);
