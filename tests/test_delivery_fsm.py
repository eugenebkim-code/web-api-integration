import pytest

from delivery_fsm import (
    is_valid_transition,
    is_final,
    DELIVERY_NEW,
    DELIVERY_IN_PROGRESS,
    DELIVERED,
    CANCELLED,
)

def test_new_to_in_progress_allowed():
    assert is_valid_transition(DELIVERY_NEW, DELIVERY_IN_PROGRESS) is True

def test_in_progress_to_delivered_allowed():
    assert is_valid_transition(DELIVERY_IN_PROGRESS, DELIVERED) is True

def test_in_progress_to_cancelled_allowed():
    assert is_valid_transition(DELIVERY_IN_PROGRESS, CANCELLED) is True

def test_regression_not_allowed():
    # нельзя вернуться назад
    assert is_valid_transition(DELIVERED, DELIVERY_IN_PROGRESS) is False
    assert is_valid_transition(CANCELLED, DELIVERY_IN_PROGRESS) is False

def test_final_states_are_immutable():
    assert is_final(DELIVERED) is True
    assert is_final(CANCELLED) is True
    assert is_final(DELIVERY_NEW) is False
    assert is_final(DELIVERY_IN_PROGRESS) is False