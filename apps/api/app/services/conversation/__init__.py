"""The conversational layer.

Four responsibilities, deliberately kept apart:

* **routing** - what is the user trying to do? (`normalise`, `questions`,
  `numbers`, `extractors`, `router`, `llm`)
* **answering** - what can be said, and from which source? (`knowledge`,
  `facts`, `answers`)
* **state** - may this mutation happen, and what does it invalidate?
  (`services/workflow.py`, `invalidation`)
* **provenance** - who actually handled this message? (`telemetry`)

The state machine still owns workflow correctness. It never decides how an
arbitrary question is answered, and the router never mutates anything.
"""
