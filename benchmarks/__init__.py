"""Model-neutral, unscripted tool-use benchmark harness (MAD-785).

The harness defines versioned suites whose prompts state user goals and
constraints without naming tools, response formats, or hidden steps.  Runs are
scored against hidden rubrics with evidence rules that make a failed or
unavailable tool impossible to report as success.

Engines:
  * ``fixture`` - deterministic offline replay of recorded assistant turns
    against mocked external services.  This is the runnable baseline.
  * ``capture`` - score a run transcript captured elsewhere (for example on
    CT103) with the same evaluator.  Live models are operator-run.

Authority and safety: this package never calls a model, never touches the
network, and never mutates application state.  All mock services are
in-process.
"""

SUITE_VERSION = "v1"
HARNESS_VERSION = "1.0.0"