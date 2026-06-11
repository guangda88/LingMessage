from __future__ import annotations

from pathlib import Path

import pytest

from lingmessage.governance import (
    ProposalStatus,
    VoteValue,
    cast_vote,
    propose,
    resolve,
    tally_votes,
)
from lingmessage.mailbox import Mailbox
from lingmessage.types import Channel, LingIdentity


@pytest.fixture
def mailbox(tmp_path: Path) -> Mailbox:
    return Mailbox(root=tmp_path / "mb")


class TestPropose:
    def test_creates_proposal_thread(self, mailbox: Mailbox) -> None:
        header, msg = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE, LingIdentity.LINGZHI),
            channel=Channel.GOVERNANCE,
            topic="Adopt new logging standard",
            body="We should standardize on structured logging across all projects.",
        )
        assert header.topic == "Adopt new logging standard"
        assert msg.message_type.value == "proposal"
        assert "governance" in dict(msg.metadata)

    def test_proposal_with_quorum(self, mailbox: Mailbox) -> None:
        header, msg = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.GOVERNANCE,
            topic="With quorum",
            body="body",
            quorum=3,
        )
        meta = dict(msg.metadata)
        assert meta["quorum"] == "3"

    def test_proposal_with_deadline(self, mailbox: Mailbox) -> None:
        _, msg = propose(
            mailbox,
            proposer=LingIdentity.LINGYI,
            recipients=(LingIdentity.LINGFLOW,),
            channel=Channel.GOVERNANCE,
            topic="Deadline test",
            body="body",
            deadline_hours=48,
        )
        meta = dict(msg.metadata)
        assert meta["deadline_hours"] == "48"


class TestCastVote:
    def test_cast_approve(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        msg = cast_vote(
            mailbox,
            thread_id=header.thread_id,
            voter=LingIdentity.LINGCLAUDE,
            vote=VoteValue.APPROVE,
            reason="I agree",
        )
        assert msg.message_type.value == "vote"
        meta = dict(msg.metadata)
        assert meta["vote"] == "approve"

    def test_cast_reject(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        msg = cast_vote(
            mailbox,
            thread_id=header.thread_id,
            voter=LingIdentity.LINGCLAUDE,
            vote=VoteValue.REJECT,
        )
        assert msg.message_type.value == "vote"

    def test_vote_on_nonexistent_thread_raises(self, mailbox: Mailbox) -> None:
        with pytest.raises(ValueError, match="not found"):
            cast_vote(
                mailbox,
                thread_id="nonexistent",
                voter=LingIdentity.LINGCLAUDE,
                vote=VoteValue.APPROVE,
            )


class TestTallyVotes:
    def test_empty_tally(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        tally = tally_votes(mailbox, header.thread_id)
        assert tally.approve == 0
        assert tally.reject == 0
        assert tally.abstain == 0

    def test_count_votes(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE, LingIdentity.LINGZHI),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.APPROVE)
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGZHI, vote=VoteValue.REJECT)
        tally = tally_votes(mailbox, header.thread_id)
        assert tally.approve == 1
        assert tally.reject == 1
        assert len(tally.voters) == 2

    def test_vote_override(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.APPROVE)
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.REJECT)
        tally = tally_votes(mailbox, header.thread_id)
        assert tally.approve == 0
        assert tally.reject == 1
        assert tally.voters["lingclaude"] == "reject"


class TestResolve:
    def test_resolve_accepted(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE, LingIdentity.LINGZHI),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.APPROVE)
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGZHI, vote=VoteValue.APPROVE)

        result = resolve(mailbox, thread_id=header.thread_id, resolver=LingIdentity.LINGMESSAGE)
        assert result.status == ProposalStatus.ACCEPTED
        assert result.tally.approve == 2
        assert result.decision_message_id

        msgs = mailbox.load_thread_messages(header.thread_id)
        decision_msgs = [m for m in msgs if m.message_type.value == "decision"]
        assert len(decision_msgs) == 1
        assert "accepted" in decision_msgs[0].subject.lower()

    def test_resolve_rejected(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE, LingIdentity.LINGZHI),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.REJECT)
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGZHI, vote=VoteValue.APPROVE)
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGYI, vote=VoteValue.REJECT)

        result = resolve(mailbox, thread_id=header.thread_id, resolver=LingIdentity.LINGMESSAGE)
        assert result.status == ProposalStatus.REJECTED
        assert result.tally.reject == 2

    def test_resolve_tie_is_rejected(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.APPROVE)
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGFLOW, vote=VoteValue.REJECT)

        result = resolve(mailbox, thread_id=header.thread_id, resolver=LingIdentity.LINGMESSAGE)
        assert result.status == ProposalStatus.REJECTED

    def test_resolve_quorum_not_met_manual(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE, LingIdentity.LINGZHI, LingIdentity.LINGYI),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
            quorum=3,
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.APPROVE)

        result = resolve(mailbox, thread_id=header.thread_id, resolver=LingIdentity.LINGMESSAGE)
        assert result.status == ProposalStatus.REJECTED

    def test_resolve_quorum_met(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE, LingIdentity.LINGZHI),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
            quorum=2,
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.APPROVE)
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGZHI, vote=VoteValue.APPROVE)

        result = resolve(mailbox, thread_id=header.thread_id, resolver=LingIdentity.LINGMESSAGE)
        assert result.status == ProposalStatus.ACCEPTED

    def test_resolve_auto_quorum_not_met_stays_open(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE, LingIdentity.LINGZHI),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
            quorum=2,
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.APPROVE)

        result = resolve(mailbox, thread_id=header.thread_id, resolver=LingIdentity.LINGMESSAGE, auto=True)
        assert result.status == ProposalStatus.OPEN
        assert result.decision_message_id == ""

    def test_resolve_no_proposal_raises(self, mailbox: Mailbox) -> None:
        mailbox.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.GOVERNANCE,
            topic="not a proposal",
            subject="hello",
            body="body",
        )
        threads = mailbox.list_threads()
        with pytest.raises(ValueError, match="No proposal message"):
            resolve(mailbox, thread_id=threads[0].thread_id, resolver=LingIdentity.LINGMESSAGE)

    def test_decision_message_contains_tally(self, mailbox: Mailbox) -> None:
        header, _ = propose(
            mailbox,
            proposer=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.GOVERNANCE,
            topic="test",
            body="body",
        )
        cast_vote(mailbox, thread_id=header.thread_id, voter=LingIdentity.LINGCLAUDE, vote=VoteValue.APPROVE)

        result = resolve(mailbox, thread_id=header.thread_id, resolver=LingIdentity.LINGMESSAGE)
        msgs = mailbox.load_thread_messages(header.thread_id)
        decision = [m for m in msgs if m.message_type.value == "decision"][0]
        meta = dict(decision.metadata)
        assert "tally" in meta
        tally_data = __import__("json").loads(meta["tally"])
        assert tally_data["approve"] == 1
