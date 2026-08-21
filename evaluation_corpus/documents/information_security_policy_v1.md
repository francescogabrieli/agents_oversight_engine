# Information security incident policy v1

This is a fictional controlled document for the thesis evaluation corpus. It is
not an organizational policy and contains no live operational information.

## Incident handling obligations

1. For a `critical` incident, the on-call incident commander must create an
   incident record within 30 minutes of detection. The accountable owner is the
   Reliability Lead.
2. For every incident with `customer_impact = yes`, the Service Owner must
   prepare a stakeholder update within four hours of detection. The update must
   state confirmed impact, known workaround status, and the next update time.
3. For a `high` or `critical` incident that remains unresolved for 24 hours,
   the Security Manager must review whether a regulatory notification assessment
   is required. The review record is due by the end of the next business day.
4. The Incident Commander must record a root-cause hypothesis separately from
   confirmed facts. A hypothesis must be labelled as provisional until evidence
   supports it.

## Retention and escalation

Incident records and stakeholder-update approvals must be retained for 24 months.
When a record includes customer impact, the assigned owner and the next update
time are mandatory fields; missing values must be escalated as an information gap.
