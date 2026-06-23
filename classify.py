"""
classify.py — Post-crawl email classification

Reads crawler_session.db, classifies every lead by:
  - source_domain  (where we found it — the real identity signal)
  - email_domain   (domain in the email address — infrastructure, often nic.in)
  - ministry       (human-readable name, from DOMAIN_MAP or inferred)
  - tier           (central / state / district / statutory / psu)
  - state          (populated for state + district tier entries)
  - confidence     (high = exact match in DOMAIN_MAP, low = inferred from domain string)

Writes classified_leads.csv — ready to use.

Run: python3 classify.py
     python3 classify.py --db my_other_session.db --out results.csv
"""

import argparse
import csv
import sqlite3
from urllib.parse import urlparse

import tldextract

# Use built-in snapshot only — no network fetch of public suffix list
_tld = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)

# — Master domain map ————————————————————————————————————————————————————————
# Add any domain you encounter that isn't being inferred correctly.
# source_domain (netloc) → classification dict
# Keyed WITHOUT trailing slash. www. prefix stripped before lookup.

DOMAIN_MAP = {
    # — Central Ministries ——————————————————————————————————————————————————
    "mea.gov.in": {"ministry": "Ministry of External Affairs", "tier": "central"},
    "mha.gov.in": {"ministry": "Ministry of Home Affairs", "tier": "central"},
    "mod.gov.in": {"ministry": "Ministry of Defence", "tier": "central"},
    "finance.gov.in": {"ministry": "Ministry of Finance", "tier": "central"},
    "dea.gov.in": {"ministry": "Dept of Economic Affairs", "tier": "central"},
    "cbic.gov.in": {"ministry": "Central Board of Indirect Taxes & Customs", "tier": "central"},
    "incometax.gov.in": {"ministry": "Income Tax Department", "tier": "central"},
    "mohua.gov.in": {"ministry": "Ministry of Housing & Urban Affairs", "tier": "central"},
    "commerce.gov.in": {"ministry": "Ministry of Commerce & Industry", "tier": "central"},
    "dpiit.gov.in": {"ministry": "Dept for Promotion of Industry & Internal Trade", "tier": "central"},
    "dgft.gov.in": {"ministry": "Directorate General of Foreign Trade", "tier": "central"},
    "meity.gov.in": {"ministry": "Ministry of Electronics & IT", "tier": "central"},
    "education.gov.in": {"ministry": "Ministry of Education", "tier": "central"},
    "mohfw.gov.in": {"ministry": "Ministry of Health & Family Welfare", "tier": "central"},
    "agricoop.nic.in": {"ministry": "Ministry of Agriculture & Farmers Welfare", "tier": "central"},
    "pib.gov.in": {"ministry": "Press Information Bureau", "tier": "central"},
    "dopt.gov.in": {"ministry": "Dept of Personnel & Training", "tier": "central"},
    "labour.gov.in": {"ministry": "Ministry of Labour & Employment", "tier": "central"},
    "powermin.gov.in": {"ministry": "Ministry of Power", "tier": "central"},
    "morth.gov.in": {"ministry": "Ministry of Road Transport & Highways", "tier": "central"},
    "msme.gov.in": {"ministry": "Ministry of MSME", "tier": "central"},
    "dst.gov.in": {"ministry": "Dept of Science & Technology", "tier": "central"},
    "dot.gov.in": {"ministry": "Dept of Telecommunications", "tier": "central"},
    "tribal.gov.in": {"ministry": "Ministry of Tribal Affairs", "tier": "central"},
    "minorityaffairs.gov.in": {"ministry": "Ministry of Minority Affairs", "tier": "central"},
    "socialjustice.gov.in": {"ministry": "Ministry of Social Justice & Empowerment", "tier": "central"},
    "wcd.gov.in": {"ministry": "Ministry of Women & Child Development", "tier": "central"},
    "niti.gov.in": {"ministry": "NITI Aayog", "tier": "central"},
    "tourism.gov.in": {"ministry": "Ministry of Tourism", "tier": "central"},
    "ayush.gov.in": {"ministry": "Ministry of AYUSH", "tier": "central"},
    "coal.nic.in": {"ministry": "Ministry of Coal", "tier": "central"},
    "mines.gov.in": {"ministry": "Ministry of Mines", "tier": "central"},
    "steel.gov.in": {"ministry": "Ministry of Steel", "tier": "central"},
    "jalshakti-dowr.gov.in": {"ministry": "Ministry of Jal Shakti", "tier": "central"},
    "shipmin.gov.in": {"ministry": "Ministry of Ports, Shipping & Waterways", "tier": "central"},
    "panchayat.gov.in": {"ministry": "Ministry of Panchayati Raj", "tier": "central"},
    "mib.gov.in": {"ministry": "Ministry of Information & Broadcasting", "tier": "central"},
    "moefcc.nic.in": {"ministry": "Ministry of Environment, Forest & Climate", "tier": "central"},
    "pharmaceuticals.gov.in": {"ministry": "Ministry of Chemicals & Pharmaceuticals", "tier": "central"},
    "fssai.gov.in": {"ministry": "Food Safety & Standards Authority", "tier": "central"},
    "ppac.gov.in": {"ministry": "Petroleum Planning & Analysis Cell", "tier": "central"},
    "petroleum.gov.in": {"ministry": "Ministry of Petroleum & Natural Gas", "tier": "central"},
    "cbi.gov.in": {"ministry": "Central Bureau of Investigation", "tier": "central"},
    "cvc.gov.in": {"ministry": "Central Vigilance Commission", "tier": "central"},
    "isro.gov.in": {"ministry": "Indian Space Research Organisation", "tier": "psu"},
    "drdo.gov.in": {"ministry": "Defence Research & Development Organisation", "tier": "psu"},
    "nic.gov.in": {"ministry": "National Informatics Centre", "tier": "central"},
    "digitalindia.gov.in": {"ministry": "Digital India Programme", "tier": "central"},
    "mygov.in": {"ministry": "MyGov Platform", "tier": "central"},
    "indianrailways.gov.in": {"ministry": "Indian Railways", "tier": "psu"},
    "mospi.gov.in": {"ministry": "Ministry of Statistics & Programme Implementation", "tier": "central"},
    "bis.gov.in": {"ministry": "Bureau of Indian Standards", "tier": "statutory"},
    "ddpmod.gov.in": {"ministry": "Dept of Defence Production", "tier": "central"},
    "ndma.gov.in": {"ministry": "National Disaster Management Authority", "tier": "statutory"},
    "dor.gov.in": {"ministry": "Dept of Revenue", "tier": "central"},
    "cybercrime.gov.in": {"ministry": "Cyber Crime Portal (MHA)", "tier": "central"},
    "dmeo.gov.in": {"ministry": "Development Monitoring & Evaluation Office", "tier": "central"},
    "delhipolice.gov.in": {"ministry": "Delhi Police", "tier": "central"},
    "cdsco.gov.in": {"ministry": "Central Drugs Standard Control Organisation", "tier": "central"},
    "psara.gov.in": {"ministry": "Private Security Agencies Regulation", "tier": "central"},
    # — Statutory / Constitutional Bodies ———————————————————————————————————
    "upsc.gov.in": {"ministry": "Union Public Service Commission", "tier": "statutory"},
    "ssc.nic.in": {"ministry": "Staff Selection Commission", "tier": "statutory"},
    "nhrc.nic.in": {"ministry": "National Human Rights Commission", "tier": "statutory"},
    "ncw.nic.in": {"ministry": "National Commission for Women", "tier": "statutory"},
    "uidai.gov.in": {"ministry": "UIDAI (Aadhaar)", "tier": "statutory"},
    "trai.gov.in": {"ministry": "Telecom Regulatory Authority of India", "tier": "statutory"},
    "loksabha.nic.in": {"ministry": "Lok Sabha Secretariat", "tier": "statutory"},
    "rajyasabha.nic.in": {"ministry": "Rajya Sabha Secretariat", "tier": "statutory"},
    "sansad.in": {"ministry": "Sansad (Parliament of India)", "tier": "statutory"},
    # — State Governments ————————————————————————————————————————————————————
    "maharashtra.gov.in": {"ministry": "Government of Maharashtra", "tier": "state", "state": "Maharashtra"},
    "up.gov.in": {"ministry": "Government of Uttar Pradesh", "tier": "state", "state": "Uttar Pradesh"},
    "karnataka.gov.in": {"ministry": "Government of Karnataka", "tier": "state", "state": "Karnataka"},
    "tn.gov.in": {"ministry": "Government of Tamil Nadu", "tier": "state", "state": "Tamil Nadu"},
    "ap.gov.in": {"ministry": "Government of Andhra Pradesh", "tier": "state", "state": "Andhra Pradesh"},
    "telangana.gov.in": {"ministry": "Government of Telangana", "tier": "state", "state": "Telangana"},
    "goa.gov.in": {"ministry": "Government of Goa", "tier": "state", "state": "Goa"},
    "gujaratindia.gov.in": {"ministry": "Government of Gujarat", "tier": "state", "state": "Gujarat"},
    "rajasthan.gov.in": {"ministry": "Government of Rajasthan", "tier": "state", "state": "Rajasthan"},
    "mp.gov.in": {"ministry": "Government of Madhya Pradesh", "tier": "state", "state": "Madhya Pradesh"},
    "wb.gov.in": {"ministry": "Government of West Bengal", "tier": "state", "state": "West Bengal"},
    "punjab.gov.in": {"ministry": "Government of Punjab", "tier": "state", "state": "Punjab"},
    "haryana.gov.in": {"ministry": "Government of Haryana", "tier": "state", "state": "Haryana"},
    "uk.gov.in": {"ministry": "Government of Uttarakhand", "tier": "state", "state": "Uttarakhand"},
    "jharkhand.gov.in": {"ministry": "Government of Jharkhand", "tier": "state", "state": "Jharkhand"},
    "odisha.gov.in": {"ministry": "Government of Odisha", "tier": "state", "state": "Odisha"},
    "cgstate.gov.in": {"ministry": "Government of Chhattisgarh", "tier": "state", "state": "Chhattisgarh"},
    "assam.gov.in": {"ministry": "Government of Assam", "tier": "state", "state": "Assam"},
    "kerala.gov.in": {"ministry": "Government of Kerala", "tier": "state", "state": "Kerala"},
    "bihar.gov.in": {"ministry": "Government of Bihar", "tier": "state", "state": "Bihar"},
    "himachal.nic.in": {"ministry": "Government of Himachal Pradesh", "tier": "state", "state": "Himachal Pradesh"},
    "manipur.gov.in": {"ministry": "Government of Manipur", "tier": "state", "state": "Manipur"},
    "meghalaya.gov.in": {"ministry": "Government of Meghalaya", "tier": "state", "state": "Meghalaya"},
    "mizoram.gov.in": {"ministry": "Government of Mizoram", "tier": "state", "state": "Mizoram"},
    "nagaland.gov.in": {"ministry": "Government of Nagaland", "tier": "state", "state": "Nagaland"},
    "sikkim.gov.in": {"ministry": "Government of Sikkim", "tier": "state", "state": "Sikkim"},
    "tripura.gov.in": {"ministry": "Government of Tripura", "tier": "state", "state": "Tripura"},
    "arunachal.gov.in": {"ministry": "Government of Arunachal Pradesh", "tier": "state", "state": "Arunachal Pradesh"},
    "delhi.gov.in": {"ministry": "Government of Delhi", "tier": "state", "state": "Delhi"},
    "chandigarh.gov.in": {"ministry": "Government of Chandigarh", "tier": "state", "state": "Chandigarh"},
    "puducherry.gov.in": {"ministry": "Government of Puducherry", "tier": "state", "state": "Puducherry"},
    "ladakh.gov.in": {"ministry": "Government of Ladakh", "tier": "state", "state": "Ladakh"},
}

# — State keyword → state name (for inference on unknown subdomains) —————————
STATE_KEYWORDS = {
    "maharashtra": "Maharashtra", "mumbai": "Maharashtra", "pune": "Maharashtra",
    "up": "Uttar Pradesh", "lucknow": "Uttar Pradesh",
    "karnataka": "Karnataka", "bengaluru": "Karnataka", "bangalore": "Karnataka",
    "tamilnadu": "Tamil Nadu", "tn": "Tamil Nadu", "chennai": "Tamil Nadu",
    "andhra": "Andhra Pradesh", "ap": "Andhra Pradesh",
    "telangana": "Telangana",
    "gujarat": "Gujarat", "ahmedabad": "Gujarat",
    "rajasthan": "Rajasthan", "jaipur": "Rajasthan",
    "madhyapradesh": "Madhya Pradesh", "mp": "Madhya Pradesh", "bhopal": "Madhya Pradesh",
    "westbengal": "West Bengal", "wb": "West Bengal", "kolkata": "West Bengal",
    "punjab": "Punjab", "chandigarh": "Punjab",
    "haryana": "Haryana",
    "uttarakhand": "Uttarakhand", "uk": "Uttarakhand", "dehradun": "Uttarakhand",
    "jharkhand": "Jharkhand", "ranchi": "Jharkhand",
    "odisha": "Odisha", "bhubaneswar": "Odisha",
    "chhattisgarh": "Chhattisgarh", "raipur": "Chhattisgarh",
    "assam": "Assam", "guwahati": "Assam",
    "kerala": "Kerala", "thiruvananthapuram": "Kerala",
    "bihar": "Bihar", "patna": "Bihar",
    "himachal": "Himachal Pradesh",
    "goa": "Goa",
    "delhi": "Delhi",
    "manipur": "Manipur", "imphal": "Manipur",
    "meghalaya": "Meghalaya", "shillong": "Meghalaya",
    "mizoram": "Mizoram", "aizawl": "Mizoram",
    "nagaland": "Nagaland", "kohima": "Nagaland",
    "sikkim": "Sikkim", "gangtok": "Sikkim",
    "tripura": "Tripura", "agartala": "Tripura",
    "arunachal": "Arunachal Pradesh",
    "ladakh": "Ladakh", "leh": "Ladakh",
    "puducherry": "Puducherry",
}


def _strip_www(netloc: str) -> str:
    return netloc[4:] if netloc.startswith("www.") else netloc


# All SLDs that are known ministries or states — anything else under .gov.in/.nic.in
# is treated as a district/local body by default.
_KNOWN_SLDS = {
    "mea", "mha", "mod", "finance", "dea", "cbic", "incometax", "mohua", "commerce", "dpiit",
    "dgft", "meity", "education", "mohfw", "agricoop", "pib", "dopt", "labour", "powermin",
    "morth", "msme", "dst", "dot", "tribal", "minorityaffairs", "socialjustice", "wcd",
    "niti", "tourism", "ayush", "coal", "mines", "steel", "shipmin", "panchayat", "mib",
    "moefcc", "pharmaceuticals", "fssai", "ppac", "petroleum", "cbi", "cvc", "isro", "drdo",
    "nic", "digitalindia", "mygov", "indianrailways", "upsc", "ssc", "nhrc", "ncw", "uidai",
    "trai", "loksabha", "rajyasabha", "sansad", "mospi", "india", "gov", "jalshakti-dowr",
    "bis", "ddpmod", "ndma", "dor", "cybercrime", "dmeo", "delhipolice", "cdsco", "psara",
    # state SLDs
    "maharashtra", "up", "karnataka", "tn", "ap", "telangana", "goa", "gujaratindia",
    "rajasthan", "mp", "wb", "punjab", "haryana", "uk", "jharkhand", "odisha", "cgstate",
    "assam", "kerala", "bihar", "himachal", "manipur", "meghalaya", "mizoram", "nagaland",
    "sikkim", "tripura", "arunachal", "delhi", "chandigarh", "puducherry", "ladakh",
}


def _infer_from_domain(netloc: str) -> dict:
    """
    Best-effort classification for domains NOT in DOMAIN_MAP.
    Uses the subdomain / domain string to infer tier and state.
    """
    clean = _strip_www(netloc).lower()
    extracted = _tld(clean)
    subdomain_parts = extracted.subdomain.split(".") if extracted.subdomain else []
    all_parts = subdomain_parts + [extracted.domain]

    inferred_state = None
    for part in all_parts:
        key = part.replace("-", "")
        if key in STATE_KEYWORDS:
            inferred_state = STATE_KEYWORDS[key]
            break

    district_subdomain_signals = {"collector", "dm", "dc", "district", "zilla", "sp", "sdo", "bdo"}
    has_district_subdomain = any(p in district_subdomain_signals for p in subdomain_parts)
    sld_is_unknown = extracted.domain not in _KNOWN_SLDS

    if has_district_subdomain or (sld_is_unknown and not inferred_state):
        tier = "district"
        ministry = f"District/Local Office ({netloc})"
    elif inferred_state:
        tier = "state"
        ministry = f"Government of {inferred_state} — {extracted.domain}"
    elif "nic" in all_parts:
        tier = "central"
        ministry = f"Central Govt (NIC-hosted) — {netloc}"
    else:
        tier = "central"
        ministry = f"Central Govt — {netloc}"

    result = {
        "ministry": ministry,
        "tier": tier,
        "confidence": "low",
    }
    if inferred_state:
        result["state"] = inferred_state
    return result


def classify_lead(email: str, source_url: str) -> dict:
    source_netloc = _strip_www(urlparse(source_url).netloc.lower())
    email_domain = email.split("@")[-1].lower() if "@" in email else ""

    if source_netloc in DOMAIN_MAP:
        entry = DOMAIN_MAP[source_netloc].copy()
        entry["confidence"] = "high"
    else:
        entry = _infer_from_domain(source_netloc)

    entry["source_domain"] = source_netloc
    entry["email_domain"] = email_domain
    entry["nic_email"] = email_domain in ("nic.in", "gov.in")

    return entry


def run(db_path: str, out_path: str):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT email, source_url, page_title, context_snippet, captured_at FROM leads"
    ).fetchall()
    conn.close()

    if not rows:
        print("No leads found in database.")
        return

    classified = []
    tier_counts = {}
    confidence_counts = {"high": 0, "low": 0}

    for email, source_url, page_title, context_snippet, captured_at in rows:
        meta = classify_lead(email, source_url)

        classified.append({
            "email": email,
            "ministry": meta.get("ministry", ""),
            "tier": meta.get("tier", ""),
            "state": meta.get("state", ""),
            "confidence": meta.get("confidence", ""),
            "nic_email": meta.get("nic_email", False),
            "source_domain": meta.get("source_domain", ""),
            "email_domain": meta.get("email_domain", ""),
            "source_url": source_url,
            "page_title": page_title,
            "context_snippet": context_snippet,
            "captured_at": captured_at,
        })

        tier_counts[meta.get("tier", "unknown")] = tier_counts.get(meta.get("tier"), 0) + 1
        confidence_counts[meta.get("confidence", "low")] += 1

    classified.sort(key=lambda r: (r["tier"], r["ministry"]))

    fieldnames = [
        "email", "ministry", "tier", "state", "confidence",
        "nic_email", "source_domain", "email_domain",
        "source_url", "page_title", "context_snippet", "captured_at"
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(classified)

    print(f"\n{'=' * 50}")
    print(f"  Total leads classified : {len(classified)}")
    print(f"  Output                 : {out_path}")
    print(f"\n  By tier:")
    for tier, count in sorted(tier_counts.items(), key=lambda x: -x[1]):
        print(f"    {tier:<12} {count}")
    print(f"\n  Confidence:")
    print(f"    high (exact DOMAIN_MAP match) : {confidence_counts['high']}")
    print(f"    low  (inferred from domain)   : {confidence_counts['low']}")
    print(f"\n  Tip: Low-confidence rows — verify manually or add to DOMAIN_MAP.")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify crawled leads by ministry/tier.")
    parser.add_argument("--db", default="crawler_session.db", help="SQLite DB from crawler")
    parser.add_argument("--out", default="classified_leads.csv", help="Output CSV path")
    args = parser.parse_args()
    run(args.db, args.out)
