"""Optional Langfuse tracing for LLM calls.

Only active when LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are set and the
``langfuse`` package (v4) is installed.  Otherwise every call degrades to a
silent no-op so the pipeline never breaks when Langfuse is not configured.

Langfuse v4 API changes
-----------------------
In Langfuse SDK v4 the ``Langfuse`` client is still used, but the tracing
objects and their method signatures changed:

* ``Langfuse.trace()`` — returns a ``StatefulTraceClient``
* ``trace.generation()`` — creates a ``GenerationBody`` directly on the trace
  (no longer accessed through intermediate span objects)
* ``generation.end()`` — finalises the generation
* ``langfuse.flush()`` — must be called to flush events before process exit

The ``@observe`` decorator (``from langfuse.decorators import observe``) is the
v4 preferred pattern for new code, but the imperative API above is used here to
keep the tracing fully optional and non-intrusive.
"""
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------------------
# Import guard — v4 SDK
# ---------------------------------------------------------------------------

_LANGFUSE_AVAILABLE = False
try:
    from langfuse import Langfuse

    _LANGFUSE_AVAILABLE = True
except ImportError:
    pass

_client: Optional[Any] = None


def _get_client() -> Optional[Any]:
    global _client
    if _client is not None:
        return _client

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip()

    if not _LANGFUSE_AVAILABLE or not public_key or not secret_key:
        return None

    try:
        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[tracing] failed to initialise Langfuse client: {exc}")
        return None

    return _client


def is_enabled() -> bool:
    """Return True when Langfuse is configured and the SDK is available."""
    return _get_client() is not None


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@contextmanager
def trace_run(pipeline_name: str, metadata: Optional[dict] = None) -> Iterator:
    """Open a Langfuse trace, yield a *span* helper, then flush on exit.

    The yielded object has a single method:
        ``span.generation(name, model, prompt, output, usage)``

    When Langfuse is not configured, it yields a silent no-op instead so
    callers never need to check ``is_enabled()``.

    Example
    -------
    ::

        with trace_run("llm_enhance", metadata={"job_count": 5}) as span:
            result = call_llm(prompt)
            span.generation(
                name="job_match_summary",
                model="gemini-2.0-flash",
                prompt=prompt,
                output=result,
            )
    """
    client = _get_client()

    if client is None:
        yield _NullSpan()
        return

    try:
        trace = client.trace(name=pipeline_name, metadata=metadata or {})
    except Exception as exc:  # noqa: BLE001
        print(f"[tracing] failed to create trace: {exc}")
        yield _NullSpan()
        return

    try:
        yield _Span(trace)
    finally:
        # Langfuse v4 requires an explicit flush to send queued events.
        try:
            client.flush()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Span wrappers
# ---------------------------------------------------------------------------


class _Span:
    """Thin wrapper around a Langfuse v4 StatefulTraceClient."""

    def __init__(self, trace: Any) -> None:
        self._trace = trace

    def generation(
        self,
        name: str = "llm-call",
        model: str = "",
        prompt: str = "",
        output: str = "",
        usage: Optional[dict] = None,
        **kwargs: Any,
    ) -> None:
        """Record an LLM generation on the current trace."""
        try:
            gen = self._trace.generation(
                name=name,
                model=model,
                input=prompt,
                output=output,
                usage=usage or {},
            )
            # In v4 generation objects should be ended explicitly
            gen.end()
        except Exception as exc:  # noqa: BLE001
            print(f"[tracing] generation record failed: {exc}")


class _NullSpan:
    """Silent no-op span used when Langfuse is disabled."""

    def generation(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        pass


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("langfuse SDK available:", _LANGFUSE_AVAILABLE)
    print("tracing enabled:", is_enabled())

    with trace_run("smoke-test", metadata={"test": True}) as span:
        span.generation(
            name="test-call",
            model="test-model",
            prompt="Say hello",
            output="Hello!",
        )
    print("smoke-test complete (no errors mean no SDK / no-op mode)")
