"""Exact Decimal reward allocation for chore claims."""

from decimal import Decimal


CENT = Decimal("0.01")


def split_reward(amount, claimant_count):
    """Split ``amount`` into exact cent shares, favoring earlier claimants."""
    if claimant_count < 1:
        return []
    if amount is None:
        return [None] * claimant_count

    cents = int(Decimal(str(amount)).quantize(CENT) / CENT)
    base, remainder = divmod(cents, claimant_count)
    return [
        Decimal(base + (1 if index < remainder else 0)) * CENT
        for index in range(claimant_count)
    ]


def allocate_reward_shares(chore):
    """Store the approval-time split on the chore's ordered claim rows."""
    claims = list(chore.claims.all())
    if not claims:
        return []
    shares = split_reward(chore.reward_amount, len(claims))
    for claim, share in zip(claims, shares):
        claim.reward_share = share
    type(claims[0]).objects.bulk_update(claims, ["reward_share"])
    return shares
