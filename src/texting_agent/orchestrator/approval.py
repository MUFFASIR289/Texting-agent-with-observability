"""The approval binding `[FR-42]`, `[FR-42a]`, `[FR-45]`, `[SEC-10]`, `[VR-10]`.

An approver signs off on a *specific campaign*: this copy, this offer, to these
people. So the hash covers all three. Hashing only the content would let the
audience be re-scored between approval and send, and the campaign an approver
saw would not be the campaign that went out `[EC-28]`.

The audience is frozen into `campaign_targets` **before** hashing, and nothing
may add a row afterwards. Send-time gates may skip a recipient - unsubscribed,
no consent, over the frequency cap - and skipping does not change the hash,
because the frozen list is unchanged `[EC-27]`.
"""

import hashlib
import json

from texting_agent.database.repositories.campaign_repo import CampaignRepository


def content_hash(repo: CampaignRepository, campaign_id: str) -> str:
    """SHA-256 over canonical JSON of variants, offers and the frozen audience.

    Canonical means sorted keys and a fixed field order, so the same campaign
    hashes the same way twice. Anything read here must be stable: a timestamp or
    a row id would make every recomputation differ and the check meaningless.
    """
    segments = [
        {
            "name": row["name"],
            "priority": row["priority"],
            "playbook_id": row["playbook_id"],
            "offer": json.loads(row["offer_json"] or "{}"),
            "channels": row["channels"],
            "predicate": json.loads(row["predicate_json"] or "{}"),
        }
        for row in repo.list_segments(campaign_id)
    ]
    variants = [
        {
            "segment_name": row["segment_name"],
            "channel": row["channel"],
            "label": row["label"],
            "subject_template": row["subject_template"],
            "body_template": row["body_template"],
            "cta_text": row["cta_text"],
            "cta_url_key": row["cta_url_key"],
        }
        for row in repo.list_variants(campaign_id)
    ]
    audience = sorted(row["customer_id"] for row in repo.list_targets(campaign_id))

    canonical = json.dumps(
        {"segments": segments, "variants": variants, "audience": audience},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
