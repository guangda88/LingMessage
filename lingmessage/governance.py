"""灵信治理引擎 — proposal / vote / decision 流程"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lingmessage.mailbox import Mailbox
from lingmessage.types import (
    Channel,
    LingIdentity,
    Message,
    MessageType,
    SourceType,
    ThreadHeader,
)

logger = logging.getLogger(__name__)


class VoteValue(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


class ProposalStatus(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


@dataclass
class VoteTally:
    approve: int = 0
    reject: int = 0
    abstain: int = 0
    voters: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approve": self.approve,
            "reject": self.reject,
            "abstain": self.abstain,
            "voters": self.voters,
        }


@dataclass
class ProposalResult:
    thread_id: str
    proposal_id: str
    status: ProposalStatus
    tally: VoteTally
    decision_message_id: str = ""


def propose(
    mailbox: Mailbox,
    *,
    proposer: LingIdentity,
    recipients: tuple[LingIdentity, ...],
    channel: Channel,
    topic: str,
    body: str,
    quorum: int | None = None,
    deadline_hours: int | None = None,
) -> tuple[ThreadHeader, Message]:
    """Open a governance proposal thread.

    The proposal message uses message_type=PROPOSAL. Metadata carries
    governance parameters (quorum, deadline) so they are persisted with
    the message.
    """
    meta: dict[str, str] = {
        "governance": "true",
    }
    if quorum is not None:
        meta["quorum"] = str(quorum)
    if deadline_hours is not None:
        meta["deadline_hours"] = str(deadline_hours)

    header, msg = mailbox.open_thread(
        sender=proposer,
        recipients=recipients,
        channel=channel,
        topic=topic,
        subject=f"Proposal: {topic}",
        body=body,
        message_type=MessageType.PROPOSAL,
        metadata=meta,
        source_type=SourceType.INFERRED,
    )
    logger.info("Proposal opened: thread=%s proposer=%s topic=%s", header.thread_id, proposer.value, topic)
    return header, msg


def cast_vote(
    mailbox: Mailbox,
    *,
    thread_id: str,
    voter: LingIdentity,
    vote: VoteValue,
    reason: str = "",
) -> Message:
    """Cast a vote on a proposal thread.

    The vote message uses message_type=VOTE with metadata carrying
    the vote value.
    """
    header = mailbox.load_thread_header(thread_id)
    if header is None:
        raise ValueError(f"Thread {thread_id} not found")

    msg = mailbox.reply(
        thread_id=thread_id,
        sender=voter,
        recipient=LingIdentity.ALL,
        subject=f"Vote: {vote.value}",
        body=reason or f"{voter.value} votes {vote.value}",
        message_type=MessageType.VOTE,
        metadata={"vote": vote.value},
        source_type=SourceType.INFERRED,
    )
    logger.info("Vote cast: thread=%s voter=%s vote=%s", thread_id, voter.value, vote.value)
    return msg


def tally_votes(
    mailbox: Mailbox,
    thread_id: str,
) -> VoteTally:
    """Count all votes on a proposal thread."""
    messages = mailbox.load_thread_messages(thread_id)
    tally = VoteTally()
    for m in messages:
        if m.message_type == MessageType.VOTE:
            vote_meta = dict(m.metadata)
            vote_val = vote_meta.get("vote", "")
            try:
                vv = VoteValue(vote_val)
            except ValueError:
                continue
            sender_val = m.sender.value
            if sender_val in tally.voters:
                old = tally.voters[sender_val]
                if old == VoteValue.APPROVE.value:
                    tally.approve -= 1
                elif old == VoteValue.REJECT.value:
                    tally.reject -= 1
                elif old == VoteValue.ABSTAIN.value:
                    tally.abstain -= 1
            tally.voters[sender_val] = vv.value
            if vv == VoteValue.APPROVE:
                tally.approve += 1
            elif vv == VoteValue.REJECT:
                tally.reject += 1
            elif vv == VoteValue.ABSTAIN:
                tally.abstain += 1
    return tally


def resolve(
    mailbox: Mailbox,
    *,
    thread_id: str,
    resolver: LingIdentity,
    auto: bool = False,
) -> ProposalResult:
    """Resolve a proposal by tallying votes and posting a decision.

    Resolution rules:
    - If quorum is set in proposal metadata, require at least that many
      non-abstain votes.
    - Simple majority of non-abstain votes wins.
    - Tie or no quorum -> REJECTED.
    - ``auto=True`` means only resolve if quorum met and majority clear.

    Returns a ProposalResult with the outcome.
    """
    header = mailbox.load_thread_header(thread_id)
    if header is None:
        raise ValueError(f"Thread {thread_id} not found")

    messages = mailbox.load_thread_messages(thread_id)
    proposal_msg = None
    proposal_meta: dict[str, str] = {}
    for m in messages:
        if m.message_type == MessageType.PROPOSAL:
            proposal_msg = m
            proposal_meta = dict(m.metadata)
            break

    if proposal_msg is None:
        raise ValueError(f"No proposal message found in thread {thread_id}")

    tally = tally_votes(mailbox, thread_id)
    quorum = int(proposal_meta.get("quorum", "0"))
    non_abstain = tally.approve + tally.reject

    if quorum > 0 and non_abstain < quorum:
        if auto:
            return ProposalResult(
                thread_id=thread_id,
                proposal_id=proposal_msg.message_id,
                status=ProposalStatus.OPEN,
                tally=tally,
            )
        status = ProposalStatus.REJECTED
        reason = f"Quorum not met ({non_abstain}/{quorum})"
    elif tally.approve > tally.reject:
        status = ProposalStatus.ACCEPTED
        reason = f"Passed ({tally.approve}/{tally.reject}, {tally.abstain} abstain)"
    else:
        status = ProposalStatus.REJECTED
        reason = f"Rejected ({tally.approve}/{tally.reject}, {tally.abstain} abstain)"

    decision_msg = mailbox.reply(
        thread_id=thread_id,
        sender=resolver,
        recipient=LingIdentity.ALL,
        subject=f"Decision: {status.value}",
        body=f"Proposal {status.value}.\n\n{reason}\n\nTally: approve={tally.approve} reject={tally.reject} abstain={tally.abstain}",
        message_type=MessageType.DECISION,
        metadata={
            "proposal_status": status.value,
            "tally": json.dumps(tally.to_dict()),
        },
        source_type=SourceType.INFERRED,
    )

    logger.info("Proposal resolved: thread=%s status=%s reason=%s", thread_id, status.value, reason)

    return ProposalResult(
        thread_id=thread_id,
        proposal_id=proposal_msg.message_id,
        status=status,
        tally=tally,
        decision_message_id=decision_msg.message_id,
    )
