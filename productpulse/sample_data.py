"""Deterministic synthetic feedback generator (clearly labelled demo data).

Produces realistic-looking but entirely fictional B2B SaaS feedback across 13
underlying problem themes, with paraphrased wording (so semantic and lexical
methods can be compared), seasonal shape, two deliberately emerging themes, one
declining theme, exact duplicates, short/noisy records and missing metadata.
The theme each record was generated from is written to a separate ground-truth
file used only for evaluation (docs/AI_EVALUATION.md) — never imported.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

END_DATE = datetime(2026, 9, 30, 18, 0)
WEEKS = 26


@dataclass(frozen=True)
class ThemeSpec:
    key: str
    area: str
    weight: float
    trend: str  # stable | emerging | growing | declining
    phrasings: tuple[str, ...]
    sources: tuple[str, ...] = ("Support ticket", "NPS survey", "CSM note", "G2 review", "Customer interview")
    segment_bias: str | None = None


THEMES: tuple[ThemeSpec, ...] = (
    ThemeSpec(
        "csv_export",
        "Reporting",
        1.3,
        "stable",
        (
            "CSV export times out when the report has more than a few thousand rows",
            "Exporting large reports to CSV fails with a timeout error",
            "The export button spins forever on big reports and the file never downloads",
            "We can't download our monthly report as a spreadsheet, the export just hangs",
            "Report exports over 10k rows never finish",
            "Downloading data to Excel or CSV is broken for large date ranges",
            "Export to CSV keeps failing for our finance team's quarterly report",
        ),
    ),
    ThemeSpec(
        "sso_login",
        "Authentication",
        1.0,
        "stable",
        (
            "SSO login through Okta fails intermittently and users get locked out",
            "SAML single sign-on redirects back to the login page in a loop",
            "Our employees can't sign in with Azure AD SSO since last week",
            "Single sign-on is unreliable, people have to retry login several times",
            "The SAML assertion error appears every morning when our team logs in",
            "We need SSO to work consistently before we roll out to more departments",
        ),
        segment_bias="Enterprise",
    ),
    ThemeSpec(
        "android_crash",
        "Mobile",
        0.5,
        "emerging",
        (
            "The Android app crashes immediately after the latest update",
            "Since updating the mobile app it closes as soon as I open a project",
            "App keeps crashing on my Samsung phone after version 5.2",
            "Mobile app force closes when uploading a photo on Android",
            "After the recent update the app won't even launch on Android 14",
            "Constant crashes on the Android app make it unusable in the field",
        ),
        sources=("App Store review", "Support ticket", "App Store review"),
        segment_bias="SMB",
    ),
    ThemeSpec(
        "dark_mode",
        "UI",
        0.8,
        "stable",
        (
            "Please add a dark mode, the bright white screen is hard on the eyes at night",
            "Would love a dark theme option for the web app",
            "Any plans for dark mode? Our developers keep asking for it",
            "A night mode or dark color scheme would make long sessions much easier",
            "Dark mode please! Every other tool we use has it",
        ),
        sources=("G2 review", "NPS survey", "App Store review", "Customer interview"),
    ),
    ThemeSpec(
        "slack_integration",
        "Integrations",
        0.7,
        "growing",
        (
            "We want notifications sent to Slack channels instead of email",
            "A Slack integration for task updates would save us a lot of time",
            "Please integrate with Slack so the team gets alerts where they already work",
            "Can you post status changes into our Slack workspace?",
            "Missing a native Slack app, we rely on Zapier which breaks often",
            "Being able to create tasks from Slack messages would be a game changer",
        ),
    ),
    ThemeSpec(
        "pricing_confusion",
        "Billing",
        0.8,
        "stable",
        (
            "The invoice charged us for more seats than we actually use",
            "Pricing tiers are confusing and it is unclear what is included in each plan",
            "We were surprised by an unexpected charge after upgrading",
            "Billing page doesn't explain why our monthly cost went up",
            "It's hard to understand how seat-based pricing works for guest users",
            "Our finance team cannot reconcile the invoices with our seat count",
        ),
    ),
    ThemeSpec(
        "dashboard_slow",
        "Performance",
        0.7,
        "emerging",
        (
            "Dashboards take over 30 seconds to load in the morning",
            "The main dashboard is painfully slow since the last release",
            "Loading the analytics page takes forever and often times out",
            "Charts on the dashboard render very slowly with our data volume",
            "Performance of the home dashboard has gotten much worse recently",
            "Page load times for dashboards are making the team avoid the product",
        ),
    ),
    ThemeSpec(
        "bulk_edit",
        "Workflow",
        0.8,
        "stable",
        (
            "We need a way to bulk edit tasks instead of updating them one by one",
            "Please add bulk actions so I can change the status of many items at once",
            "Editing hundreds of records individually is a huge waste of time",
            "Mass update of assignees and due dates would help a lot",
            "Multi-select and bulk delete would make cleanup much faster",
        ),
    ),
    ThemeSpec(
        "notifications_noisy",
        "Notifications",
        0.6,
        "stable",
        (
            "Way too many email notifications, my inbox is flooded",
            "I get notified about every tiny change and can't control it",
            "Notification settings are not granular enough, we just want daily digests",
            "The constant alert emails are annoying, please let us mute some of them",
            "Too much notification noise, people have started ignoring all of them",
        ),
    ),
    ThemeSpec(
        "onboarding",
        "Onboarding",
        0.6,
        "stable",
        (
            "Setting up the workspace was confusing and the documentation didn't help",
            "New users struggle to understand where to start after signing up",
            "The onboarding checklist skips important steps like inviting the team",
            "It took us two weeks to configure everything, the setup guide is unclear",
            "Getting started is hard, a guided tour or templates would help",
        ),
        sources=("Customer interview", "CSM note", "NPS survey", "Support ticket"),
    ),
    ThemeSpec(
        "support_praise",
        "Support",
        0.9,
        "stable",
        (
            "Your support team was fantastic and solved our issue within an hour",
            "Huge thanks to the customer success team, very responsive and helpful",
            "Great support experience, the agent really understood our problem",
            "Support has been excellent every time we reached out",
            "The onboarding call with your team was super helpful, thank you",
        ),
        sources=("NPS survey", "G2 review", "CSM note"),
    ),
    ThemeSpec(
        "search_broken",
        "Search",
        0.6,
        "stable",
        (
            "Search doesn't find documents even when I type the exact title",
            "The search results are irrelevant and miss obvious matches",
            "We can't filter search results by date or owner",
            "Global search is unreliable, I end up scrolling through folders instead",
            "Searching for a customer name returns nothing although the record exists",
        ),
    ),
    ThemeSpec(
        "api_rate_limit",
        "API",
        0.6,
        "declining",
        (
            "The API rate limit is too low for our nightly sync and we keep getting 429 errors",
            "Our integration hits API throttling constantly",
            "Please raise the API request limits for enterprise accounts",
            "Rate limiting on the REST API breaks our data pipeline",
            "We get too many requests errors from the API during bulk imports",
        ),
        sources=("Support ticket", "CSM note", "Customer interview"),
        segment_bias="Enterprise",
    ),
)

OPENERS = (
    "",
    "",
    "Hi team, ",
    "Honestly, ",
    "FYI: ",
    "Not sure if this is known but ",
    "Our team noticed that ",
    "Quick feedback: ",
    "As an admin, ",
    "Hello, ",
    "Me again. ",
    "Following up from our call: ",
    "One of our managers mentioned that ",
    "Small thing, but ",
)
CLOSERS = (
    "",
    " Please fix this soon.",
    " This is blocking our workflow.",
    " Would love to see this soon.",
    " Otherwise we're happy with the product.",
    " We may have to look at alternatives.",
    " It's really frustrating.",
    " Thanks!",
    " Happens to several people on our team.",
    " Let me know if you need screenshots.",
    " This came up in our weekly review.",
    " Not urgent.",
    " Any ETA?",
)
NOISE = ("ok", "n/a", "Great!", "test", "Love it", "Thanks", "meh", "good", "no comment", "fine")
SEGMENTS = (("Enterprise", 0.15), ("Mid-Market", 0.35), ("SMB", 0.5))


def _weekly_multiplier(trend: str, week: int) -> float:
    last = WEEKS - 1 - week  # 0 = most recent week
    if trend == "emerging":
        return 4.0 if last < 4 else 1.0
    if trend == "growing":
        return 1.0 + 0.06 * week
    if trend == "declining":
        return 0.25 if last < 8 else 1.3
    return 1.0


def _customers(rng: random.Random, n: int = 420) -> list[dict]:
    out = []
    for i in range(n):
        r, acc, seg = rng.random(), 0.0, "SMB"
        for name, p in SEGMENTS:
            acc += p
            if r <= acc:
                seg = name
                break
        value = {
            "Enterprise": rng.randint(60, 250) * 1000,
            "Mid-Market": rng.randint(12, 60) * 1000,
            "SMB": rng.randint(5, 100) * 100,
        }[seg]
        out.append({"id": f"CUST-{i + 1:04d}", "segment": seg, "value": value})
    return out


def generate(
    seed: int = 42, base_per_week: float = 7.5, noise_rate: float = 0.03, id_prefix: str = "FB"
) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)  # noqa: S311 — synthetic demo data, not security-sensitive
    customers = _customers(rng)
    by_seg = {s: [c for c in customers if c["segment"] == s] for s, _ in SEGMENTS}
    rows, truth = [], []
    counter = 10000

    def pick_customer(bias: str | None) -> dict:
        if bias and rng.random() < 0.7:
            return rng.choice(by_seg[bias])
        return rng.choice(customers)

    for week in range(WEEKS):
        week_start = END_DATE - timedelta(weeks=WEEKS - week)
        for spec in THEMES:
            lam = base_per_week * spec.weight * _weekly_multiplier(spec.trend, week) / 1.6
            n = sum(1 for _ in range(int(lam * 3)) if rng.random() < 1 / 3)  # ≈ Binomial(3λ, 1/3)
            for _ in range(n):
                counter += 1
                cust = pick_customer(spec.segment_bias)
                core = rng.choice(spec.phrasings)
                opener = rng.choice(OPENERS)
                body = (
                    core[0].lower() + core[1:]
                    if opener[-2:] in (", ", "t ", "s ", "n ") and core[1:2].islower()
                    else core
                )
                text = opener + body
                text = text[0].upper() + text[1:] + "." + rng.choice(CLOSERS)
                text = text.replace("..", ".").replace("?.", "?").replace("!.", "!")
                if rng.random() < 0.02:  # verbatim duplicate wording (e.g. templated survey answers)
                    text = core + "."
                created = week_start + timedelta(days=rng.random() * 7, hours=rng.randint(8, 18))
                source = rng.choice(spec.sources)
                rating = ""
                if source in ("App Store review", "G2 review"):
                    rating = str(rng.choice((4, 5)) if spec.key == "support_praise" else rng.choice((1, 2, 2, 3, 4)))
                rows.append(
                    {
                        "feedback_id": f"{id_prefix}-{counter}",
                        "feedback_text": text,
                        "created_at": "" if rng.random() < 0.01 else created.strftime("%Y-%m-%d %H:%M"),
                        "source": source,
                        "customer_id": cust["id"],
                        "customer_segment": "" if rng.random() < 0.03 else cust["segment"],
                        "product_area": "" if rng.random() < 0.05 else spec.area,
                        "rating": rating,
                        "customer_value": cust["value"],
                        "status": rng.choice(("Open", "Open", "Closed", "Pending"))
                        if source == "Support ticket"
                        else "",
                    }
                )
                truth.append({"feedback_id": f"{id_prefix}-{counter}", "theme": spec.key})
        for _ in range(sum(1 for _ in range(10) if rng.random() < noise_rate * 10 / 2)):
            counter += 1
            cust = rng.choice(customers)
            created = week_start + timedelta(days=rng.random() * 7)
            rows.append(
                {
                    "feedback_id": f"{id_prefix}-{counter}",
                    "feedback_text": rng.choice(NOISE),
                    "created_at": created.strftime("%Y-%m-%d %H:%M"),
                    "source": "NPS survey",
                    "customer_id": cust["id"],
                    "customer_segment": cust["segment"],
                    "product_area": "",
                    "rating": "",
                    "customer_value": cust["value"],
                    "status": "",
                }
            )
            truth.append({"feedback_id": f"{id_prefix}-{counter}", "theme": "noise"})
    return rows, truth


MESSY_ROWS = [
    # Non-canonical headers to demonstrate column mapping, mixed date formats, and invalid rows.
    ["Ticket #", "Comment", "Submitted", "Channel", "Account", "Plan", "Area", "Stars", "ARR"],
    [
        "T-1",
        "CSV export times out on our 20k row report",
        "2026-09-01",
        "Support ticket",
        "ACME-1",
        "Enterprise",
        "Reporting",
        "",
        "$120,000",
    ],
    ["T-2", "Please add dark mode to the web app", "09/03/2026", "G2 review", "BETA-7", "SMB", "UI", "4", "2,400"],
    [
        "T-3",
        "SSO login loops back to the sign-in page",
        "Sep 5, 2026",
        "Support ticket",
        "ACME-1",
        "Enterprise",
        "Authentication",
        "",
        "$120,000",
    ],
    ["T-4", "", "2026-09-06", "NPS survey", "GAMMA-2", "Mid-Market", "", "", ""],
    [
        "T-5",
        "Dashboard is slow to load every morning",
        "not a date",
        "Support ticket",
        "DELTA-9",
        "SMB",
        "Performance",
        "",
        "",
    ],
    ["T-6", "Mobile app crashes after the update", "2031-01-01", "App Store review", "EPS-3", "SMB", "Mobile", "1", ""],
    [
        "T-7",
        "Slack integration would be great",
        "2026-09-08",
        "CSM note",
        "ZETA-4",
        "Mid-Market",
        "Integrations",
        "five",
        "30000",
    ],
    [
        "T-8",
        '=HYPERLINK("http://example.com","click") formula-looking text is stored as plain text',
        "2026-09-09",
        "Support ticket",
        "ETA-5",
        "SMB",
        "",
        "",
        "",
    ],
    ["T-2", "Please add dark mode to the web app", "09/03/2026", "G2 review", "BETA-7", "SMB", "UI", "4", "2,400"],
    [
        "T-9",
        "Too many email notifications",
        "2026-09-10",
        "NPS survey",
        "THETA-6",
        "SMB",
        "Notifications",
        "",
        "",
        "EXTRA",
    ],
    [
        "T-10",
        "   Search   does not find   documents   by title  ",
        "2026-09-11T10:30:00Z",
        "Support ticket",
        "IOTA-8",
        "Mid-Market",
        "Search",
        "",
        "18000",
    ],
    ["T-11", "12345", "2026-09-12", "NPS survey", "KAPPA-1", "SMB", "", "", ""],
]


def write_csv(path: Path, rows: list[dict] | list[list[str]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        if fieldnames:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        else:
            csv.writer(fh).writerows(rows)


def write_all(out_dir: Path) -> dict[str, int]:
    rows, truth = generate()
    fields = list(rows[0].keys())
    write_csv(out_dir / "synthetic_feedback.csv", rows, fields)
    write_csv(out_dir / "synthetic_ground_truth.csv", truth, ["feedback_id", "theme"])
    small_rows, _ = generate(seed=7, base_per_week=1.0, noise_rate=0.02, id_prefix="SMP")
    write_csv(out_dir / "sample_feedback.csv", small_rows, fields)
    write_csv(out_dir / "messy_feedback_example.csv", MESSY_ROWS)
    return {"synthetic": len(rows), "small": len(small_rows), "messy": len(MESSY_ROWS) - 1}
