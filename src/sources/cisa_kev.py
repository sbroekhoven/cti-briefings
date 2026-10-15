"""CISA Known Exploited Vulnerabilities catalog (JSON, no key). Every entry is actively exploited."""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import http
from ..models import IntelItem
from ..util import parse_date, utcnow
from .base import Source, register

URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


@register("cisa_kev")
class CisaKevSource(Source):
    def fetch(self, since: datetime) -> list[IntelItem]:
        data = http.get(self.options.get("url", URL)).json()
        bonus = float(self.options.get("bonus", 25))
        now = utcnow()
        items = []
        for v in data.get("vulnerabilities", []):
            added = parse_date(v.get("dateAdded"))
            if added is not None:
                # dateAdded has no time; treat it as end of that day (never in the future)
                added = min(added + timedelta(hours=23, minutes=59), now)
            ransomware = v.get("knownRansomwareCampaignUse", "Unknown")
            summary = (
                f"{v.get('shortDescription', '')} Required action: {v.get('requiredAction', '')} "
                f"Due: {v.get('dueDate', 'n/a')}. Known ransomware use: {ransomware}."
            )
            items.append(self.item(
                title=f"{v['cveID']}: {v.get('vendorProject', '')} {v.get('product', '')} {v.get('vulnerabilityName', '')}".strip(),
                link=f"https://nvd.nist.gov/vuln/detail/{v['cveID']}",
                summary=summary,
                published=added,
                bonus=bonus + (10 if ransomware == "Known" else 0),
                details={"cve": v["cveID"], "product": f"{v.get('vendorProject', '')} {v.get('product', '')}".strip(),
                         "description": v.get("vulnerabilityName", ""), "exploited": True,
                         "ransomware": ransomware == "Known", "due": v.get("dueDate")},
            ))
        return items
