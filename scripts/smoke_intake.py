"""Run intake on every example advertiser and print gate + top hosts (dev smoke test)."""
import re
import sys
import time
from datetime import date

from disco_planner.data.catalog import DATA_DIR
from disco_planner.llm.provider import default_provider
from disco_planner.planning.intake import parse_brief
from disco_planner.planning.publisher_scoring import rank_publishers

text = (DATA_DIR / "example_advertisers.txt").read_text()
briefs = re.findall(r"^(\d+)\.\s+(.+)$", text, re.MULTILINE)
only = set(sys.argv[1:])
provider = default_provider()
for num, brief in briefs:
    if only and num not in only:
        continue
    t = time.time()
    p = parse_brief(brief, provider, date(2026, 10, 30))
    ranked = rank_publishers(p, date(2026, 10, 30))
    rec = [f"{s.publisher_name}({s.score:.2f}{'!' if s.flags else ''})" for s in ranked if s.recommended]
    print(f"#{num} [{p.gate} {time.time()-t:.1f}s] tier={p.price_tier} ${p.price_point_usd} "
          f"tags={[f'{x.tag}:{x.weight}' for x in p.relevant_tags]} compete={p.competing_tags}")
    print(f"    -> {rec}")
    print(f"    conflicts={[(c.persona_id, c.conflict) for c in p.persona_conflicts]}  Q={p.clarifying_questions[:2]}")
