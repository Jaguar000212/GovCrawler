"""URL helpers shared by both tiers (cloud coordination + agent crawler)."""


def strip_www(netloc: str) -> str:
    """Drop a leading `www.` so `www.example.gov.in` and `example.gov.in`
    collapse to the same root for dedup / seed-domain matching."""
    return netloc.removeprefix("www.")
