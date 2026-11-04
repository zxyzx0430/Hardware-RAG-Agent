"""Source-level contract tests for the not-yet-wired writer lifecycle core."""

from concurrent.futures import ThreadPoolExecutor
import pickle
from threading import Barrier

import pytest

from src.agent.compact.writer_lifecycle import (
    LeaseState,
    _MAX_LIVE_NATIVE_CONTROL_REFS,
    _MAX_RESIDENT_OWNERS,
    QuarantineReason,
    RunControlState,
    RunOutcome,
    WriterLifecycleRegistry,
    WriterCapacityError,
    WriterLeaseError,
    WriterOwnerConflictError,
    WriterQuarantinedError,
    WriterRunError,
    WriterRunHandle,
    WriterScope,
    WriterScopeError,
)


class _EqualReference:
    def __eq__(self, _other):
        return True


class _FakeNativeRunControl:
    def __init__(self, *, fail_after_request=False, mark_requested=True):
        self.drain_requested = False
        self.drain_reason = None
        self.requests = []
        self.fail_after_request = fail_after_request
        self.mark_requested = mark_requested

    def request_drain(self, reason):
        self.requests.append(reason)
        if self.mark_requested:
            self.drain_requested = True
            self.drain_reason = reason
        if self.fail_after_request:
            raise RuntimeError("native drain request outcome is uncertain")


class _NonWeakrefableNativeRunControl:
    __slots__ = ("drain_requested", "drain_reason", "requests")

    def __init__(self):
        self.drain_requested = False
        self.drain_reason = None
        self.requests = []

    def request_drain(self, reason):
        self.requests.append(reason)
        self.drain_requested = True
        self.drain_reason = reason


def _native_control(**kwargs):
    return _FakeNativeRunControl(**kwargs)


def _scope(
    *,
    owner_key="thread-1",
    graph=None,
    saver=None,
    request_scope=None,
    epoch=None,
    epoch_generation=1,
):
    return WriterScope(
        owner_key=owner_key,
        graph=graph if graph is not None else object(),
        saver=saver if saver is not None else object(),
        request_scope=request_scope if request_scope is not None else object(),
        epoch=epoch if epoch is not None else object(),
        epoch_generation=epoch_generation,
    )


def _registry_for(scope):
    return WriterLifecycleRegistry(known_savers=(scope.saver,))


@pytest.mark.parametrize(
    "field",
    ["owner_key", "graph", "saver", "request_scope", "epoch", "epoch_generation"],
)
def test_scope_binding_requires_exact_references_and_epoch_generation(field):
    values = {
        "owner_key": "thread-1",
        "graph": _EqualReference(),
        "saver": _EqualReference(),
        "request_scope": _EqualReference(),
        "epoch": _EqualReference(),
        "epoch_generation": 1,
    }
    scope = WriterScope(**values)
    registry = _registry_for(scope)
    lease = registry.acquire(scope)

    registry.assert_scope(lease, scope)

    mismatched_values = dict(values)
    if field == "owner_key":
        mismatched_values[field] = "thread-2"
    elif field == "epoch_generation":
        mismatched_values[field] = 2
    else:
        mismatched_values[field] = _EqualReference()
    mismatch = WriterScope(**mismatched_values)
    with pytest.raises(WriterScopeError):
        registry.assert_scope(lease, mismatch)
    with pytest.raises(WriterQuarantinedError):
        registry.release(lease, scope)


def test_missing_or_unknown_saver_fails_closed():
    with pytest.raises(WriterScopeError):
        WriterScope(
            owner_key="thread-1",
            graph=object(),
            saver=None,
            request_scope=object(),
            epoch=object(),
            epoch_generation=1,
        )

    scope = _scope()
    registry = WriterLifecycleRegistry(known_savers=(object(),))

    with pytest.raises(WriterScopeError):
        registry.acquire(scope)


def test_duplicate_and_concurrent_owner_acquisition_is_refused():
    scope = _scope()
    registry = _registry_for(scope)
    first_lease = registry.acquire(scope)

    with pytest.raises(WriterOwnerConflictError):
        registry.acquire(scope)

    registry.release(first_lease, scope)
    fresh_scope = _scope(
        owner_key=scope.owner_key,
        saver=scope.saver,
        epoch_generation=scope.epoch_generation + 1,
    )
    barrier = Barrier(2)

    def acquire_after_barrier():
        barrier.wait()
        try:
            return registry.acquire(fresh_scope)
        except WriterOwnerConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: acquire_after_barrier(), range(2)))

    assert sum(result is not None for result in results) == 1
    with pytest.raises(WriterOwnerConflictError):
        registry.acquire(fresh_scope)


def test_resident_owner_capacity_is_freed_by_normal_release():
    scope = _scope()
    registry = _registry_for(scope)
    leases = []
    owner_scopes = []

    for owner_index in range(_MAX_RESIDENT_OWNERS):
        owner_scope = _scope(
            owner_key=f"thread-{owner_index}",
            saver=scope.saver,
        )
        lease = registry.acquire(owner_scope)
        owner_scopes.append(owner_scope)
        leases.append(lease)

    with pytest.raises(WriterCapacityError, match="capacity"):
        registry.acquire(_scope(owner_key="new-owner", saver=scope.saver))

    released_owner_key = owner_scopes[0].owner_key
    registry.release(leases[0], owner_scopes[0])

    new_owner = _scope(owner_key="new-owner", saver=scope.saver)
    new_lease = registry.acquire(new_owner)

    assert released_owner_key not in registry._owners
    assert registry._owners[new_owner.owner_key].lease is new_lease
    assert len(registry._owners) == _MAX_RESIDENT_OWNERS


def test_quarantined_owner_remains_after_resident_capacity_refusal():
    scope = _scope()
    registry = _registry_for(scope)
    quarantined_record = None

    for owner_index in range(_MAX_RESIDENT_OWNERS):
        owner_scope = _scope(
            owner_key=f"thread-{owner_index}",
            saver=scope.saver,
        )
        lease = registry.acquire(owner_scope)
        if owner_index == 0:
            registry.quarantine(
                lease,
                owner_scope,
                reason=QuarantineReason.WRITE_OUTCOME_UNKNOWN,
            )
            quarantined_record = registry._owners[owner_scope.owner_key]

    with pytest.raises(WriterCapacityError, match="capacity"):
        registry.acquire(_scope(owner_key="new-owner", saver=scope.saver))

    assert len(registry._owners) == _MAX_RESIDENT_OWNERS
    assert registry._owners["thread-0"] is quarantined_record
    assert registry._owners["thread-0"].state is LeaseState.QUARANTINED
    with pytest.raises(WriterQuarantinedError):
        registry.acquire(_scope(owner_key="thread-0", saver=scope.saver))


@pytest.mark.parametrize("epoch_generation", [True, -1, 1.0])
def test_epoch_generation_must_be_a_non_negative_integer(epoch_generation):
    with pytest.raises(WriterScopeError):
        _scope(epoch_generation=epoch_generation)


def test_foreign_registry_cannot_release_an_owner_lease():
    scope = _scope()
    owner_registry = _registry_for(scope)
    foreign_registry = _registry_for(scope)
    lease = owner_registry.acquire(scope)

    with pytest.raises(WriterLeaseError):
        foreign_registry.release(lease, scope)

    owner_registry.assert_scope(lease, scope)


def test_active_run_blocks_release_and_reentrant_run():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    handle = registry.begin_run(lease, scope, _native_control())

    with pytest.raises(WriterRunError):
        registry.begin_run(lease, scope, _native_control())
    with pytest.raises(WriterRunError):
        registry.release(lease, scope)

    registry.finish_run(
        lease,
        scope,
        handle,
        joined=True,
        outcome=RunOutcome.KNOWN_TERMINAL,
    )
    registry.release(lease, scope)


def test_drain_is_only_a_cooperative_request_and_does_not_release_or_join():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    native_control = _native_control()
    handle = registry.begin_run(lease, scope, native_control)

    assert registry.request_drain(lease, scope, handle, "budget") is True
    assert registry.request_drain(lease, scope, handle, "later-reason") is False
    assert handle.state is RunControlState.DRAIN_REQUESTED
    assert handle.drain_requested is True
    assert handle.drain_reason == "budget"
    assert native_control.requests == ["budget"]
    with pytest.raises(WriterRunError):
        registry.release(lease, scope)
    with pytest.raises(WriterRunError):
        registry.begin_run(lease, scope, _native_control())

    registry.finish_run(
        lease,
        scope,
        handle,
        joined=True,
        outcome=RunOutcome.KNOWN_TERMINAL,
    )
    assert handle.state is RunControlState.JOINED


def test_joined_run_consumes_control_and_serial_run_gets_a_fresh_control():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    first_native = _native_control()
    first = registry.begin_run(lease, scope, first_native)
    registry.finish_run(
        lease,
        scope,
        first,
        joined=True,
        outcome=RunOutcome.KNOWN_TERMINAL,
    )

    second_native = _native_control()
    second = registry.begin_run(lease, scope, second_native)

    assert second is not first
    assert first.native_control is first_native
    assert second.native_control is second_native
    assert second.state is RunControlState.RUNNING
    with pytest.raises(WriterRunError):
        registry.request_drain(lease, scope, first, "stale")


def test_native_control_cannot_be_reused_for_another_run():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    native_control = _native_control()
    first = registry.begin_run(lease, scope, native_control)
    registry.finish_run(
        lease,
        scope,
        first,
        joined=True,
        outcome=RunOutcome.KNOWN_TERMINAL,
    )

    with pytest.raises(WriterRunError):
        registry.begin_run(lease, scope, native_control)

    fresh = registry.begin_run(lease, scope, _native_control())
    assert fresh.state is RunControlState.RUNNING


def test_live_native_control_capacity_refuses_runs_without_evicting_evidence():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    controls = [_native_control() for _ in range(_MAX_LIVE_NATIVE_CONTROL_REFS)]

    for native_control in controls:
        handle = registry.begin_run(lease, scope, native_control)
        registry.finish_run(
            lease,
            scope,
            handle,
            joined=True,
            outcome=RunOutcome.KNOWN_TERMINAL,
        )

    assert len(registry._native_control_refs) == _MAX_LIVE_NATIVE_CONTROL_REFS

    with pytest.raises(WriterCapacityError, match="capacity"):
        registry.begin_run(lease, scope, _native_control())

    assert lease._run_generation == _MAX_LIVE_NATIVE_CONTROL_REFS
    assert len(registry._native_control_refs) == _MAX_LIVE_NATIVE_CONTROL_REFS
    assert all(
        native_ref() is native_control
        for native_ref, native_control in zip(registry._native_control_refs, controls)
    )


def test_non_weakrefable_native_controls_are_rejected_without_retention():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)

    with pytest.raises(WriterRunError, match="support weak references"):
        registry.begin_run(lease, scope, _NonWeakrefableNativeRunControl())

    assert lease._run_generation == 0
    assert registry._owners[scope.owner_key].active_run is None
    assert registry._native_control_refs == []
    assert not hasattr(registry, "_native_controls_without_weakrefs")


def test_native_control_contract_is_required_before_starting_a_run():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)

    with pytest.raises(WriterRunError):
        registry.begin_run(lease, scope, object())

    already_draining = _native_control()
    already_draining.drain_requested = True
    with pytest.raises(WriterRunError):
        registry.begin_run(lease, scope, already_draining)

    handle = registry.begin_run(lease, scope, _native_control())
    assert isinstance(handle, WriterRunHandle)


def test_native_control_must_confirm_a_forwarded_drain_request():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    native_control = _native_control(mark_requested=False)
    handle = registry.begin_run(lease, scope, native_control)

    with pytest.raises(WriterRunError):
        registry.request_drain(lease, scope, handle, "budget")

    assert native_control.requests == ["budget"]
    assert handle.state is RunControlState.QUARANTINED
    with pytest.raises(WriterQuarantinedError):
        registry.release(lease, scope)


def test_uncertain_native_drain_failure_quarantines_the_owner():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    native_control = _native_control(fail_after_request=True)
    handle = registry.begin_run(lease, scope, native_control)

    with pytest.raises(RuntimeError):
        registry.request_drain(lease, scope, handle, "budget")

    assert native_control.requests == ["budget"]
    assert handle.state is RunControlState.QUARANTINED
    with pytest.raises(WriterQuarantinedError):
        registry.release(lease, scope)


@pytest.mark.parametrize(
    ("joined", "outcome", "reason"),
    [
        (False, RunOutcome.KNOWN_TERMINAL, QuarantineReason.JOIN_UNCONFIRMED),
        (True, RunOutcome.UNKNOWN, QuarantineReason.RUN_OUTCOME_UNKNOWN),
    ],
)
def test_unconfirmed_join_or_unknown_run_quarantines_without_recovery(
    joined,
    outcome,
    reason,
):
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    handle = registry.begin_run(lease, scope, _native_control())

    with pytest.raises(WriterRunError):
        registry.finish_run(
            lease,
            scope,
            handle,
            joined=joined,
            outcome=outcome,
        )

    assert handle.state is RunControlState.QUARANTINED
    with pytest.raises(WriterQuarantinedError):
        registry.acquire(_scope(owner_key=scope.owner_key, saver=scope.saver))
    with pytest.raises(WriterQuarantinedError):
        registry.release(lease, scope)


@pytest.mark.parametrize(
    "reason",
    [
        QuarantineReason.TASK_OUTCOME_UNKNOWN,
        QuarantineReason.WRITE_OUTCOME_UNKNOWN,
    ],
)
def test_unknown_task_or_write_outcome_is_quarantined_without_ttl(reason):
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    registry.quarantine(lease, scope, reason=reason)

    with pytest.raises(WriterQuarantinedError):
        registry.acquire(_scope(owner_key=scope.owner_key, saver=scope.saver))
    with pytest.raises(WriterQuarantinedError):
        registry.release(lease, scope)


def test_scope_mismatch_quarantines_and_blocks_acquire():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    mismatch = _scope(
        owner_key=scope.owner_key,
        graph=scope.graph,
        saver=scope.saver,
        request_scope=scope.request_scope,
        epoch=object(),
    )

    with pytest.raises(WriterScopeError):
        registry.assert_scope(lease, mismatch)
    with pytest.raises(WriterQuarantinedError):
        registry.acquire(_scope(owner_key=scope.owner_key, saver=scope.saver))


def test_quarantine_has_no_expiry_or_public_release_path():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    registry.quarantine(
        lease,
        scope,
        reason=QuarantineReason.WRITE_OUTCOME_UNKNOWN,
    )

    assert not hasattr(registry, "unquarantine")
    assert not hasattr(registry, "expire")
    with pytest.raises(WriterQuarantinedError):
        registry.release(lease, scope)


def test_lease_and_writer_run_handle_cannot_be_serialized():
    scope = _scope()
    registry = _registry_for(scope)
    lease = registry.acquire(scope)
    handle = registry.begin_run(lease, scope, _native_control())

    with pytest.raises(TypeError):
        pickle.dumps(lease)
    with pytest.raises(TypeError):
        pickle.dumps(handle)
