"""Authenticated writer context for tests that stub only graph stream output."""

from contextlib import contextmanager
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from src.agent.request_context import REQUEST_GRAPH_BINDING_KEY, new_request_graph_binding
from src.agent.writer_lifecycle import (
    acquire_writer_lease,
    bind_writer_lease,
    mint_graph_provenance,
)


@contextmanager
def authenticated_test_stream(monkeypatch, stream_double, *, business_session_id):
    """Use the real lease lifecycle while a test double supplies stream events."""
    request_id = uuid4().hex
    session_id = f"{business_session_id}-{request_id}"
    binding = new_request_graph_binding(session_id, request_id)
    config = {
        "configurable": {
            "thread_id": binding.graph_thread_id,
            REQUEST_GRAPH_BINDING_KEY: binding.as_config_value(),
        },
    }

    builder = StateGraph(MessagesState)
    builder.add_node("test_stream_owner", lambda _state: {})
    builder.add_edge(START, "test_stream_owner")
    builder.add_edge("test_stream_owner", END)
    agent = builder.compile(checkpointer=InMemorySaver())
    if not mint_graph_provenance(agent):
        raise RuntimeError("Test graph writer provenance could not be minted")

    expected_identity = (session_id, request_id, binding.graph_thread_id)

    def validate_epochs() -> None:
        actual_identity = (
            binding.business_session_id, binding.request_id, binding.graph_thread_id,
        )
        if actual_identity != expected_identity:
            raise RuntimeError("Synthetic test writer identity changed")

    lease = acquire_writer_lease(
        agent,
        config,
        business_session_id=session_id,
        request_id=request_id,
        session_epoch=uuid4().hex,
        memory_epoch=None,
        validate_epochs=validate_epochs,
    )

    graph_type = type(agent)
    original_astream = graph_type.astream

    def _test_astream(graph, *args, **kwargs):
        if graph is agent:
            events = args[0] if args else kwargs.pop("input")
            return stream_double.astream(
                events, config=kwargs["config"], stream_mode=kwargs["stream_mode"],
            )
        return original_astream(graph, *args, **kwargs)

    monkeypatch.setattr(graph_type, "astream", _test_astream)
    with bind_writer_lease(lease):
        yield agent, config, binding.graph_thread_id
