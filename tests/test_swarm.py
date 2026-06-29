"""Unit tests for core/agent_swarm.py — swarm protocol and orchestration."""

from __future__ import annotations


def test_swarm_message_repr():
    from core.agent_swarm import SwarmMessage, Intent
    msg = SwarmMessage(sender="alice", recipient="bob", intent=Intent.ASK, content="hi")
    r = repr(msg)
    assert "alice" in r
    assert "bob" in r
    assert "ask" in r


def test_swarm_message_defaults():
    from core.agent_swarm import SwarmMessage
    msg = SwarmMessage()
    assert msg.id  # auto-generated
    assert msg.intent.value == "info"
    assert msg.content == ""


def test_conversation_channel_send():
    from core.agent_swarm import ConversationChannel, SwarmMessage, Intent
    channel = ConversationChannel()
    msg = SwarmMessage(sender="test", intent=Intent.INFO, content="hello")
    channel.send(msg)
    # If we got here without exception, it works
    messages = channel._messages
    assert len(messages) >= 1
    assert messages[-1].content == "hello"


def test_intent_values():
    from core.agent_swarm import Intent
    assert Intent.ASK == "ask"
    assert Intent.ANSWER == "answer"
    assert Intent.DELEGATE == "delegate"
    assert Intent.TASK_DONE == "task_done"
