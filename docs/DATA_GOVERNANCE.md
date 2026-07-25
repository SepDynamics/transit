# Data Governance

Sentinel classifies public GTFS/GTFS-RT as operational data and keeps durable
evidence separate from low-latency Valkey state. Durable records are versioned
JSONL partitions today (`agency=<key>/date=<UTC day>`), suitable for approved
object storage and later Postgres/Timescale or Parquet/DuckDB analysis.

Every retained snapshot includes the feed-quality result, archive manifest,
source metadata, scoring evidence, and errors. Sensitive integrations require:

- agency authorization and a documented purpose before ingest;
- least-privilege roles, audit events, retention limits, and deletion process;
- de-identification review for rider, operator, incident, and demographic data;
- replayable validation before a scoring-rule or model release;
- operator acknowledgement, dismissal/cause, action, and outcome feedback.

No recommendation is a command to dispatch, alter a signal, or act on a person.
