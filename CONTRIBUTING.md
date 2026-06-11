# Contributing to `xdna-top`

Thank you for contributing! We welcome bug fixes, documentation improvements, and APU platform telemetry profiles (especially Phoenix/Hawk Point captures).

## The House Rule: Claims Precision

Our first duty is to not lie about what we measure.
- Always report NPU activity as *submission-counter deltas / active hardware contexts*, never as a *utilization percentage*.
- If a metrics source is unavailable, degrade gracefully and notify the user instead of displaying guess-work.
- Do not let documentation imply the hardware gives a signal it does not.

## AI-Assisted Development Disclosure

To maintain clear provenance and code-hygiene standards:
- If your pull request or commit was generated or assisted by an LLM (e.g., Gemini, Claude, ChatGPT, etc.), please explicitly state so in the PR description.

## Running Tests

To run the unit tests, install dependencies and invoke `pytest`:

```bash
pip install -e .
pytest tests/
```

We look forward to your contributions!
