"""Conservative normalization. Unknowns stay unknown; evidence stays attached."""
import hashlib
import re
from datetime import date, datetime
from urllib.parse import urlparse, urlunparse


def text(value):
    return "" if value is None or str(value).strip() in ("Not stated", "None stated", "None") else str(value).strip()


def stable_id(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:20]


def canonical_url(url):
    p = urlparse(url)
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", p.query, ""))


def safe_url(value):
    return value if value and urlparse(value).scheme in ("http", "https") and urlparse(value).netloc else None


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def category(value):
    v = value.lower()
    if any(w in v for w in ('software','developer','cyber','information technology','data engineer','data scientist','devops','product design',' ux ','ui/ux')):
        return 'technology'
    if any(w in v for w in ('physician','nurse','medical','healthcare','clinical','therapist','dental','pharmac','patient')):
        return 'healthcare'
    if any(w in v for w in ('teacher','education','school principal','instructor','professor','tutor')):
        return 'education'
    if any(w in v for w in ("wealth", "financial planning")):
        return "wealth"
    if any(w in v for w in ("insurance", "risk", "actuarial")):
        return "insurance"
    if any(w in v for w in ('bank','credit analyst','lending','loan officer')):
        return "banking"
    if any(w in v for w in ('finance','financial','budget','treasury','capital markets','investment analyst')) and not any(w in v for w in ('account','audit','tax')):
        return "corporate_finance"
    if any(w in v for w in ('account','audit','payroll','bookkeep','tax','cpa')):
        return 'accounting'
    if any(w in v for w in ('engineer','mechanical','electrical','architect')):
        return 'engineering'
    if any(w in v for w in ('legal','attorney','paralegal','compliance')):
        return 'legal'
    if any(w in v for w in ('construction','electrician','plumber','technician','maintenance','carpenter','automotive','mechanic')):
        return 'construction'
    if any(w in v for w in ('sales','marketing','business development','recruit','talent','brand','communications')):
        return 'sales_marketing'
    if any(w in v for w in ('driver','warehouse','logistics','transport','operations','supply chain','fleet','inventory','procurement')):
        return 'operations'
    if any(w in v for w in ('administrat','assistant','customer','human resources','coordinator','reception','office manager')):
        return 'administration'
    if any(w in v for w in ('police','correction','public safety','officer','inspector','firefighter','case manager')):
        return 'public_service'
    if any(w in v for w in ('retail','restaurant','hospitality','cook','cashier','barista')):
        return 'hospitality'
    if any(w in v for w in ('scientist','research','laboratory','environment','wildlife','biolog','chemist')):
        return 'science'
    return 'other'


def classifications(title, body, employment="", arrangement="", career=""):
    combined = f"{title}\n{body}"
    type_value = "internship" if re.search(r"\bintern(ship)?\b", title + " " + employment, re.I) else "program" if re.search(r"\bprogram\b", title, re.I) else "job"
    # Employment and arrangement are deliberately not inferred from one another.
    commitment = "part_time" if re.search(r"\bpart[ -]time\b", employment + " " + body, re.I) else "full_time" if re.search(r"\bfull[ -]time\b", employment + " " + body, re.I) else "unknown"
    arrangement_value = "unknown"
    if re.search(r"\bhybrid\b|some flexibility for remote work", arrangement + " " + body, re.I):
        arrangement_value = "hybrid"
    elif re.search(r"\b(on[ -]site|in[ -]person)\b", arrangement + " " + body, re.I):
        arrangement_value = "on_site"
    elif re.search(r"\bremote\b", arrangement, re.I):
        arrangement_value = "remote"
    stage = {"Early Career": "early", "Mid Career": "mid", "Senior": "senior"}.get(career, "unknown")
    if not career:
        stage = "senior" if re.search(r"\b(senior|manager|supervisor|licensed CPA)\b", title, re.I) else "early" if type_value == "internship" else "unknown"
    return {"opportunity_type": type_value, "commitment": commitment, "arrangement": arrangement_value, "career_stage": stage}


def schedule_evidence(body, rules):
    evidence = []
    # Work with individual clauses so an unrelated benefit cannot negate a valid schedule statement.
    clauses = re.split(r"[\n.!?;]+", body)
    for rule in rules:
        pattern = rule["pattern"].replace(r"\m", r"\b").replace(r"\M", r"\b")
        exclusion = (rule.get("exclusion_pattern") or "").replace(r"\m", r"\b").replace(r"\M", r"\b")
        for clause in clauses:
            if re.search(pattern, clause, re.I) and not (exclusion and re.search(exclusion, clause, re.I)):
                evidence.append({"id": rule["id"], "label": rule["label"], "evidence": clause.strip()[:450]})
                break
    return evidence
