"""The evaluation framework (roadmap Phase 4, slice 4.4).

Metrics are SQL over the learning log (sql/queries/eval_*.sql), so the same numbers come
from Jason's real use or from a benchmark run on a copy of the database:

- metrics.py: vocabulary adherence, teaching completeness, reading sessions (predicted vs
  taught, studied before finishing), recommendation quality (take rate, finished by
  difficulty). Every rate is reported with its n and a 95% Wilson interval, because the
  samples are small.

    python -m spanish_tutor.evaluation report            # the real word bank
    python -m spanish_tutor.evaluation report --db copy.db --session 12
"""
