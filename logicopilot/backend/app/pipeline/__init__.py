"""Phase 2 pipeline: mail -> classification -> extraction through Redis stage queues.

The idea (see the architecture plan): workers do the reading and the AI work and keep their
results as JSON files in the job's work folder - they never write to the database. A single
finalizer saves each job in one short transaction. A restart loses nothing: an item stays
reserved in Redis until its work is done, and a page or document whose result file already
exists is skipped, so nothing is paid for twice.

Built in pieces, each switched off until it is ready (settings.pipeline_v2):
  workdir  - the job's work folder and its versioned JSON files               (2a)
  snapshot - the template frozen per job, so an admin edit mid-job cannot mix (2a)
  queues   - durable stage queues, reserved/handed back, fan-in counters      (2b)
  runner   - worker threads per stage, retries, a dead-letter list            (2b)
"""
