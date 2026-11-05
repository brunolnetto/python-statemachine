"""Regression tests for hierarchy-aware native guard queries."""

import pytest
from statemachine.exceptions import InvalidDefinition
from statemachine.spec_parser import UnsupportedExpression
from statemachine.spec_parser import operator_mapping
from statemachine.spec_parser import parse_boolean_expr
from statemachine.spec_parser import parse_expr
from statemachine.states import States

from statemachine import State
from statemachine import StateChart


def evaluate_guard(machine, expression: str) -> bool:
    compiled = parse_boolean_expr(
        expression,
        variable_hook=lambda name: lambda *args, **kwargs: getattr(kwargs["machine"], name),
        operator_mapping=operator_mapping,
    )
    return bool(compiled(machine=machine))


class Release(StateChart):
    class pipeline(State.Parallel):
        class build(State.Compound):
            running = State(name="Build running", initial=True, value="build.running")
            passed = State(name="Build passed", final=True, value="build.passed")
            cached = State(name="Build cached", final=True, value="build.cached")
            finish_build = running.to(passed)
            restore_build = running.to(cached)

        class tests(State.Compound):
            running = State(name="Tests running", initial=True, value="tests.running")
            passed = State(name="Tests passed", final=True, value="tests.passed")
            finish_tests = running.to(passed)

        class review(State.Compound):
            waiting = State(initial=True, value="review.waiting")
            approved = State(final=True, value="review.approved")
            skipped = State(final=True, value="review.skipped")
            approve = waiting.to(approved)
            skip = waiting.to(skipped)

    released = State(final=True)
    manual_audit = State(final=True)

    pipeline.to(
        manual_audit,
        cond="child('pipeline.review') == 'skipped' and is_done('pipeline.build')",
    )
    pipeline.to(
        released,
        cond="is_done('pipeline.build') and is_done('pipeline.tests') "
        "and child('pipeline.review') == 'approved'",
    )


class ParallelOnly(StateChart):
    class pipeline(State.Parallel):
        class build(State.Compound):
            running = State(name="Build running", initial=True, value="parallel.build.running")
            passed = State(name="Build passed", final=True, value="parallel.build.passed")
            finish_build = running.to(passed)

        class tests(State.Compound):
            running = State(name="Tests running", initial=True, value="parallel.tests.running")
            passed = State(name="Tests passed", final=True, value="parallel.tests.passed")
            finish_tests = running.to(passed)


class AsyncParallelOnly(StateChart):
    class pipeline(State.Parallel):
        class build(State.Compound):
            running = State(name="Async build running", initial=True, value="async.build.running")
            passed = State(name="Async build passed", final=True, value="async.build.passed")
            finish_build = running.to(passed, after="after_finish_build")

        class tests(State.Compound):
            running = State(name="Async tests running", initial=True, value="async.tests.running")
            passed = State(name="Async tests passed", final=True, value="async.tests.passed")
            finish_tests = running.to(passed)

    async def after_finish_build(self):
        return None


class AtomicFinal(StateChart):
    active = State(initial=True)
    finished = State(final=True)
    finish = active.to(finished)


def _build_dotted_local_id_fixture():
    # Keep the State objects outside the StateChart class body so the metaclass
    # registers them only through the explicit States collection.
    literal_dot = State(initial=True, value="literal-dot")
    completed = State(final=True, value="literal-complete")
    states = States({"literal.dot": literal_dot, "completed": completed})
    finish = literal_dot.to(completed)
    return states, finish


_DOTTED_STATES, _DOTTED_FINISH = _build_dotted_local_id_fixture()


class DottedLocalID(StateChart):
    # Explicit States keys may contain dots; In("literal.dot") must stay a local ID.
    # The transition is needed because every non-final state must have an outgoing edge.
    states = _DOTTED_STATES
    finish_literal = _DOTTED_FINISH


def test_unique_local_ids_and_qualified_paths():
    machine = Release()
    assert evaluate_guard(machine, "child('pipeline.build') == 'running'")
    assert evaluate_guard(machine, "child('pipeline.tests') == 'running'")

    machine.send("finish_tests")

    assert not evaluate_guard(machine, "is_done('pipeline.build')")
    assert evaluate_guard(machine, "is_done('pipeline.tests')")
    assert evaluate_guard(machine, "child('pipeline.build') == 'running'")
    assert evaluate_guard(machine, "child('pipeline.tests') == 'passed'")


def test_multiple_final_outcomes_share_structural_completion():
    machine = Release()
    machine.send("restore_build")
    assert evaluate_guard(machine, "is_done('pipeline.build')")
    assert evaluate_guard(machine, "child('pipeline.build') == 'cached'")


def test_parallel_completion_requires_every_direct_region():
    machine = ParallelOnly()
    assert not evaluate_guard(machine, "is_done('pipeline')")
    machine.send("finish_build")
    assert not evaluate_guard(machine, "is_done('pipeline')")
    machine.send("finish_tests")
    assert evaluate_guard(machine, "is_done('pipeline')")


def test_ambiguous_bare_ids_are_rejected_for_new_queries():
    machine = Release()
    with pytest.raises(InvalidDefinition, match="Ambiguous state ID 'passed'"):
        evaluate_guard(machine, "is_done('passed')")
    with pytest.raises(InvalidDefinition, match="Ambiguous state ID 'running'"):
        evaluate_guard(machine, "child('running') == 'x'")


def test_native_in_retains_legacy_bare_id_membership():
    machine = Release()
    assert not evaluate_guard(machine, "In('passed')")
    machine.send("finish_tests")
    assert evaluate_guard(machine, "In('passed')")
    assert evaluate_guard(machine, "In('pipeline', 'build')")


def test_native_in_supports_exact_qualified_membership():
    machine = Release()
    machine.send("finish_tests")
    assert evaluate_guard(machine, "In(path('pipeline.tests.passed'))")
    assert not evaluate_guard(machine, "In(path('pipeline.build.passed'))")
    assert evaluate_guard(
        machine,
        "In('pipeline', path('pipeline.tests.passed'))",
    )


def test_native_in_preserves_dotted_local_ids():
    machine = DottedLocalID()
    assert evaluate_guard(machine, "In('literal.dot')")
    assert evaluate_guard(machine, r"In(path(r'literal\.dot'))")
    with pytest.raises(InvalidDefinition, match="Unknown qualified state path"):
        evaluate_guard(machine, "In(path('literal.dot'))")


def test_child_is_defined_only_for_compound_states():
    machine = Release()
    with pytest.raises(InvalidDefinition, match="requires a compound state"):
        evaluate_guard(machine, "child('pipeline') == 'build'")
    with pytest.raises(InvalidDefinition, match="requires a compound state"):
        evaluate_guard(machine, "child('released') == 'x'")


def test_child_returns_none_for_inactive_compound():
    machine = Release()
    machine.send("finish_build")
    machine.send("finish_tests")
    machine.send("approve")
    assert set(machine.configuration_values) == {"released"}
    # Query the inactive compound without making parallel states pretend to have a child.
    assert evaluate_guard(machine, "not child('pipeline.review')")


def test_async_engine_uses_the_same_state_query_semantics():
    import asyncio

    async def exercise():
        machine = AsyncParallelOnly()
        await machine.activate_initial_state()
        assert not evaluate_guard(machine, "is_done('pipeline')")
        await machine.send("finish_build")
        assert evaluate_guard(machine, "is_done('pipeline.build')")
        assert not evaluate_guard(machine, "is_done('pipeline')")
        await machine.send("finish_tests")
        assert evaluate_guard(machine, "is_done('pipeline')")

    asyncio.run(exercise())


def test_is_done_uses_engine_atomic_state_semantics():
    machine = AtomicFinal()
    machine.send("finish")
    assert "finished" in machine.configuration_values
    assert not evaluate_guard(machine, "is_done('finished')")
    assert machine._engine.is_in_final_state(machine.__class__.states_map["finished"]) is False


def test_escaped_path_segments_are_parsed():
    from statemachine.state_queries import _split_path

    assert _split_path(r"pipeline.stage\.blue") == ("pipeline", "stage.blue")
    assert _split_path(r"pipeline.stage\\blue") == ("pipeline", "stage\\blue")


def test_unknown_and_malformed_references_are_explicit():
    machine = Release()
    with pytest.raises(InvalidDefinition, match="Unknown state path"):
        evaluate_guard(machine, "is_done('pipeline.missing')")
    with pytest.raises(InvalidDefinition, match="Empty path segment"):
        evaluate_guard(machine, "is_done('pipeline..build')")
    with pytest.raises(UnsupportedExpression, match="Keyword arguments"):
        parse_boolean_expr(
            "is_done(state_ref='pipeline.build')",
            variable_hook=lambda name: lambda *args, **kwargs: None,
            operator_mapping=operator_mapping,
        )


def test_guard_only_functions_are_not_exposed_to_restricted_datamodel():
    with pytest.raises(UnsupportedExpression, match="Unsupported function: is_done"):
        parse_expr("is_done('pipeline.build')", variable_hook=lambda name: None)
    with pytest.raises(UnsupportedExpression, match="Unsupported function: path"):
        parse_expr("In(path('pipeline.build'))", variable_hook=lambda name: None)


@pytest.mark.parametrize(
    "expression",
    [
        "In(path())",
        "In(path('a', 'b'))",
        "In(path(state_ref))",
        "In(path('a', key='b'))",
        "In(path(*('a',)))",
        "is_done(path('a'))",
        "child(path('a'))",
    ],
)
def test_path_wrapper_rejects_invalid_call_shapes(expression):
    with pytest.raises(UnsupportedExpression):
        parse_boolean_expr(
            expression,
            variable_hook=lambda name: None,
            operator_mapping=operator_mapping,
        )
