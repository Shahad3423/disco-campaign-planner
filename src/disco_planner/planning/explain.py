"""Plain-English wording for host scores and reasons: what an advertiser reads in the app and the config.

The numbers and the internal detail strings stay in the score components (the logic and the score breakdown use them);
this module only translates them into sentences.
"""
from __future__ import annotations

import re

NAMES = {"category_fit": "Product match", "price_fit": "Affordability", "audience_fit": "Audience match",
         "persona_fit": "Shopper-type match", "notes_fit": "Host notes", "scale": "Host size", "seasonality": "Gifting season"}


JARGON = [(r"\bhigh-AOV\b", "high-spending"), (r"\blow-AOV\b", "low-spending"), (r"\bAOVs?\b", "average order value"),
          (r"\bDTC\b", "direct-to-consumer")]


def plain_text(text: str) -> str:
    """Free text written by the LLM (host-note reads, conflict evidence) can echo catalog jargon; translate the usual terms."""
    for pattern, words in JARGON:
        text = re.sub(pattern, words, text, flags=re.IGNORECASE)
    return text


def tags(text: str) -> str:
    words = [plain_text(t.strip().replace("wellness_dtc", "wellness DTC").replace("_", " ")) for t in text.split(",") if t.strip()]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _strength(value: float | None) -> str:
    if value is None:
        return "a match"
    return "a direct match" if value >= 0.7 else "a partial match" if value >= 0.4 else "only a loose match"


def match(detail: str, value: float | None = None, recommended: bool = False) -> str:
    """category_fit detail -> 'it sells pet food and pet supplies (a direct match)'. The wording follows the value, so a
    weak link is never called a direct match; a recommended host is never told it is 'not your kind of product'."""
    from disco_planner.data import catalog  # local: keep this module importable without loading the catalog

    d = re.sub(r" \(multiplier[^)]*\)$", "", detail)
    if d.startswith("audience-led"):
        m = re.search(r"(\d+)% of this host's shoppers are (?:in your stated audience|your likely buyers)", d)
        return (f"it sells something different, but {m[1]}% of its shoppers are in the audience you stated"
                if m else "it sells something different, but its shoppers are the people you target")
    if d.startswith("no category link"):
        return "it doesn't sell anything related to your product"
    m = re.match(r"matches (.*) \((.*)\)$", d)
    if not m:
        return d
    found = [t.strip() for t in m[1].split(",")]
    categories = {p.category for p in catalog.publishers()}
    generic = set(catalog.config("scoring")["generic_tags"])
    specific = [t for t in found if t not in categories and t not in generic]
    broad = [t for t in found if t in categories]
    traits = [t for t in found if t in generic]
    kind = m[2]
    if kind == "specific" and specific:
        return f"it sells {tags(', '.join(specific))} ({_strength(value)})"
    if kind.startswith("category") and broad:
        return (f"it is in the same broad category ({tags(', '.join(broad))})" if recommended
                else f"it shares only the broad category ({tags(', '.join(broad))}), not your kind of product")
    if traits:
        return f"the only link is a shared trait ({tags(', '.join(traits))}), not the product itself"
    return f"it matches {tags(m[1])}"


def price(detail: str) -> str:
    parts = []
    for p in re.sub(r" \(multiplier[^)]*\)$", "", detail).split("; "):
        if m := re.match(r"price \$(\S+) vs host AOV \$(\S+) \(affordable\)", p):
            parts.append(f"your price (${m[1]}) is within what its shoppers usually spend (${m[2]})")
        elif m := re.match(r"price \$(\S+) vs host AOV \$(\S+) \(([\d.]+)x the typical order\)", p):
            parts.append(f"your price (${m[1]}) is {m[3]}x what its shoppers usually spend (${m[2]})")
        elif m := re.match(r"host income (\S+) below (\S+) needed for (\S+)", p):
            parts.append(f"its shoppers' income ({m[1]}) is below what a {m[3]} product needs ({m[2]})")
        elif m := re.match(r"host income (\S+) suits (\S+)", p):
            parts.append(f"its shoppers' income ({m[1]}) suits a {m[2]} product")
        else:
            parts.append(p.replace(" (left out)", ""))
    return "; ".join(parts)


def persona(detail: str) -> str:
    m = re.match(r"reaches (.*) best \(host fit ([\d.]+)\)", detail)
    return f"its shoppers look most like {m[1]} ({float(m[2]):.0%} match)" if m else detail.replace(" (left out)", "")


def audience(detail: str) -> str:
    d = detail.replace("female", "women").replace(" male", " men").replace("gender not stated", "no gender stated")
    d = re.sub(r"age (\S+) vs target (\S+)", r"its shoppers are \1, you target \2", d)
    return d.replace(" (left out)", "")


def component(c) -> str:
    """One score component (name, detail, value) as a plain sentence fragment, without a final full stop."""
    if c.name == "category_fit":
        text = match(c.detail, c.value)
    else:
        fn = {"price_fit": price, "persona_fit": persona, "audience_fit": audience}.get(c.name)
        text = fn(c.detail) if fn else plain_text(c.detail.replace(" (left out)", ""))
    return text.rstrip(". ")


def flag(f: str) -> str:
    if f.startswith("competitor_conflict"):
        m = re.search(r"host also sells (.+?) \(", f)
        return f"it also sells {tags(m[1]) if m else 'a similar product'}, so it may need to opt in to show a competing brand"
    if f.startswith("weak_network_fit"):
        return "no host both sells this kind of product and has shoppers at this price, so treat it as a small test"
    if f.startswith("audience_note_conflict"):
        return "the host's notes suggest its shoppers don't fit your audience (" + plain_text(f.split(": ", 1)[-1]) + ")"
    return f.replace("_", " ")


BADGES = {"competitor_conflict": "needs host approval", "weak_network_fit": "small test", "audience_note_conflict": "audience conflict"}


def badge(f: str) -> str:
    return next((v for k, v in BADGES.items() if f.startswith(k)), f.split(":")[0].replace("_", " "))


def breakdown(profile, pub, components, relevance_source: str, audience: dict) -> list[str]:
    """The facts behind a host's verdict, one line each: what it sells against your product, the audience check with its
    numbers and its bar, shopper types, price and host notes. A host with the wrong category but the right audience can
    still qualify (D26); this says how close it came."""
    from disco_planner.data import catalog

    traits = set(catalog.config("scoring")["generic_tags"])

    def described(tag_list: list[str]) -> str:
        sells = [t for t in tag_list if t not in traits]
        known = [t for t in tag_list if t in traits]
        text = tags(", ".join(sells)) if sells else "nothing specific"
        return text + (f" (known for: {tags(', '.join(known))})" if known else "")

    yours = [t.tag for t in sorted(profile.relevant_tags, key=lambda t: -t.weight)]
    common = [t for t in yours if t in pub.tags]
    lines = [f"Sells: {described(sorted(pub.tags))}.",
             f"Your product: {described(yours[:6])}." if yours else "Your product: no tags were found.",
             f"In common: {tags(', '.join(common))}." if common else "In common: nothing."]
    density, minimum = audience["density"], audience["minimum"]
    if density is None:
        lines.append("Audience: you didn't state one, so this host can only qualify through what it sells.")
    elif audience["needs_age"] and not audience["age_stated"]:
        lines.append(f"Audience: {density:.0%} of its shoppers match the gender you stated, but one trait matches about half of "
                     "all shoppers, so it can't qualify a host alone. Stating an age range would let the audience count.")
    else:
        text = f"Audience: {density:.0%} of its shoppers are in the audience you stated ({audience_words(audience['detail'])})."
        if relevance_source == "audience":
            text += " That is enough to recommend it on audience alone, even though it sells something different."
        elif not common:
            text += f" Recommending a host on audience alone needs at least {minimum:.0%}, so it falls short."
        lines.append(text)
    by_name = {c.name: c for c in components}
    for name in ("persona_fit", "price_fit", "notes_fit"):
        c = by_name.get(name)
        if c is not None and c.value is not None and not (name == "notes_fit" and c.detail == "no read on notes"):
            lines.append(f"{NAMES[name]}: {component(c)}.")
    return lines


def audience_words(detail: str) -> str:
    """audience_density detail -> '62% women x 75% of ages 30-55 inside 25-45'."""
    d = detail.replace("female", "women").replace(" male", " men").replace(" x ", " and ")
    return re.sub(r"(\d+)% of ages (\S+) inside (\S+)", r"\1% of its ages \2 fall in your \3", d)

