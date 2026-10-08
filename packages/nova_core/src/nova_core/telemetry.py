from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_tracing(service: str, otlp_endpoint: str) -> bool:
    """No-op unless an OTLP endpoint is set (Jaeger in dev; the collector + Langfuse join in M4).
    SQL spans come from the asyncpg driver (the SQLAlchemy instrumentor doesn't support 2.1 yet);
    Temporal spans from the client interceptor in nova_core.temporal."""
    if not otlp_endpoint:
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")))
    trace.set_tracer_provider(provider)
    AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
    return True
