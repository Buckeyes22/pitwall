-- A serve provider whose lease closed was stored as 'unhealthy', which doctor and the unhealthy
-- metric counted as a failure. Lease teardown now writes 'disarmed'; reclassify the rows it wrote
-- before: still 'unhealthy', no active lease in config, and the provider's latest audit entry is
-- the lease_closed disarm (so no operator or later change has touched it since).
UPDATE pitwall.providers AS p
   SET health_status = 'disarmed',
       updated_at = now()
 WHERE p.health_status = 'unhealthy'
   AND NOT (p.config ? 'active_lease_id')
   AND (
         SELECT a.action
           FROM pitwall.config_audit AS a
          WHERE a.entity_type = 'provider'
            AND a.entity_id = p.id
          ORDER BY a.created_at DESC, a.id DESC
          LIMIT 1
       ) = 'lease_closed';
