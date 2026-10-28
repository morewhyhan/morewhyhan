#!/usr/bin/env python3
"""Render the GitHub profile stats block.

Data sources:
  * WakaTime public API (optional, needs WAKATIME_API_KEY)
  * GitHub public REST API / contributions page

The rendered block lives between the "waka" markers in README.md.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

LOGIN = os.environ.get("GH_LOGIN", "morewhyhan")
REPO = os.environ.get("GITHUB_REPOSITORY", LOGIN + "/" + LOGIN)
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
WAKA_KEY = os.environ.get("WAKATIME_API_KEY", "").strip()

README = "README.md"
START = "<!--START_SECTION:waka-->"
END = "<!--END_SECTION:waka-->"
ERROR_COMMENT = re.compile(r"\n?<!-- profile-update-error:.*?-->\n?", re.S)
FENCE = "```"

STAGE = "init"


def http_text(url, headers=None, timeout=30):
    last = None
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "morewhyhan-profile-bot",
                "Accept": "application/vnd.github+json",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last


def http_json(url, headers=None, timeout=30):
    return json.loads(http_text(url, headers, timeout) or "{}")


def gh_headers():
    return {"Authorization": "Bearer " + GH_TOKEN} if GH_TOKEN else {}


def bar(pct, width=25):
    filled = max(0, min(width, round(pct / 100 * width)))
    return "\u2588" * filled + "\u2591" * (width - filled)


def row(name, value, pct):
    return "%-25s%-20s%s   %05.2f %% " % (name, value, bar(pct), pct)


def human_size(kb):
    size = max(float(kb), 0.0) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "%.1f %s" % (size, unit)
        size /= 1024
    return "%.1f TB" % size


def human_time(seconds):
    seconds = int(float(seconds or 0))
    if seconds < 60:
        return "%d secs" % seconds
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return "%d hrs %d mins" % (hours, minutes)
    return "%d mins" % minutes


def badge(label, value):
    q = lambda s: urllib.parse.quote(str(s), safe="")
    return "![%s](http://img.shields.io/badge/%s-%s-blue?style=flat)" % (
        label,
        q(label),
        q(value),
    )


def waka(path):
    sep = "&" if "?" in path else "?"
    url = "https://wakatime.com/api/v1/%s%sapi_key=%s" % (
        path,
        sep,
        urllib.parse.quote(WAKA_KEY),
    )
    return http_json(url)


def find_category(categories, name):
    for cat in categories or []:
        if str(cat.get("name", "")).strip().lower() == name.lower():
            return cat
    return None


# --------------------------------------------------------------------------- #
# collectors
# --------------------------------------------------------------------------- #

def collect_github():
    global STAGE
    STAGE = "github:user"
    user = http_json("https://api.github.com/users/" + LOGIN, gh_headers())

    STAGE = "github:repos"
    repos, page = [], 1
    while page <= 3:
        batch = http_json(
            "https://api.github.com/users/%s/repos?per_page=100&page=%d&sort=pushed"
            % (LOGIN, page),
            gh_headers(),
        )
        if not isinstance(batch, list) or not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    STAGE = "github:contributions"
    year = datetime.now(timezone.utc).year
    html = http_text(
        "https://github.com/users/%s/contributions?from=%d-01-01&to=%d-12-31"
        % (LOGIN, year, year)
    )
    match = re.search(r">\s*([\d,]+)\s+contributions?\s+in", html)
    contributions = int(match.group(1).replace(",", "")) if match else 0

    STAGE = "github:private"
    private = None
    if GH_TOKEN:
        try:
            me = http_json("https://api.github.com/user", gh_headers())
            if str(me.get("login", "")).lower() == LOGIN.lower():
                private = me.get("owned_private_repos")
        except Exception:
            private = None

    STAGE = "github:views"
    views = None
    if GH_TOKEN:
        try:
            traffic = http_json(
                "https://api.github.com/repos/%s/traffic/views?per=week" % REPO,
                gh_headers(),
            )
            views = traffic.get("uniques") or traffic.get("count") or 0
        except Exception:
            views = 0

    return {
        "user": user,
        "repos": repos,
        "contributions": contributions,
        "year": year,
        "private": private,
        "views": views,
    }


def collect_wakatime():
    global STAGE
    if not WAKA_KEY:
        return {"enabled": False}

    now = datetime.now(timezone.utc)
    end = now.strftime("%Y-%m-%d")

    STAGE = "wakatime:week"
    week_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    week = waka("users/current/summaries?start=%s&end=%s" % (week_start, end))

    STAGE = "wakatime:year"
    year_start = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    year = waka("users/current/summaries?start=%s&end=%s" % (year_start, end))

    STAGE = "wakatime:stats"
    stats = {}
    try:
        stats = waka("users/current/stats/last_7_days").get("data") or {}
    except Exception:
        stats = {}

    STAGE = "wakatime:heartbeats"
    week_from = (now - timedelta(days=6)).date()
    today = now.date()
    cursor = (now - timedelta(days=90)).date()

    acc = {
        "week": {"sessions": set(), "prompts": 0, "tin": 0, "tout": 0},
        "all": {"sessions": set(), "prompts": 0, "tin": 0, "tout": 0},
    }

    while cursor <= today:
        chunk_end = min(cursor + timedelta(days=1), today)
        try:
            beats = waka(
                "users/current/heartbeats?start=%s&end=%s" % (cursor, chunk_end)
            ).get("data") or []
        except Exception:
            beats = []

        for hb in beats:
            try:
                day = datetime.fromtimestamp(float(hb.get("time")), timezone.utc).date()
            except Exception:
                day = today
            targets = [acc["all"]] if day < week_from else [acc["all"], acc["week"]]
            for bucket in targets:
                if hb.get("ai_session"):
                    bucket["sessions"].add(hb["ai_session"])
                if float(hb.get("ai_prompt_length") or 0) > 0:
                    bucket["prompts"] += 1
                bucket["tin"] += int(float(hb.get("ai_input_tokens") or 0))
                bucket["tout"] += int(float(hb.get("ai_output_tokens") or 0))

        cursor = chunk_end + timedelta(days=1)
        time.sleep(0.05)

    ai = {
        "sessions": len(acc["week"]["sessions"]),
        "prompts": acc["week"]["prompts"],
        "input_tokens": acc["week"]["tin"],
        "output_tokens": acc["week"]["tout"],
        "seconds": ai_seconds(week.get("data") or []),
        "year_seconds": ai_seconds(year.get("data") or []),
        "total": {
            "sessions": len(acc["all"]["sessions"]),
            "prompts": acc["all"]["prompts"],
            "input_tokens": acc["all"]["tin"],
            "output_tokens": acc["all"]["tout"],
        },
    }

    return {"enabled": True, "week": week, "year": year, "stats": stats, "ai": ai}


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #

def ai_seconds(days):
    total = 0.0
    for day in days:
        for cat in day.get("categories") or []:
            if str(cat.get("name", "")).strip().lower() == "ai coding":
                total += float(cat.get("total_seconds") or 0)
    return total


def aggregate(days, key, limit=5):
    totals = {}
    for day in days:
        for item in day.get(key) or []:
            name = str(item.get("name") or "Other")
            totals[name] = totals.get(name, 0.0) + float(item.get("total_seconds") or 0)
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:limit]
    return ranked


def render_badges(gh, waka_data):
    badges = []
    if waka_data.get("enabled"):
        total = (waka_data["year"].get("cumulative_total") or {}).get("text") or ""
        if total and total != "0 secs":
            badges.append(badge("Code Time", total))
        year_ai = float((waka_data.get("ai") or {}).get("year_seconds") or 0)
        if year_ai > 0:
            badges.append(badge("AI Code Time", human_time(year_ai)))
    if gh.get("views") is not None:
        badges.append(badge("Profile Views", gh["views"]))
    return badges


def render_github(gh, views):
    parts = []
    if views:
        parts += [badge("Profile Views", views), ""]

    user = gh["user"]
    storage_kb = sum(int(r.get("size") or 0) for r in gh["repos"])
    parts.append("**\U0001F431 My GitHub Data** ")
    parts.append("")
    parts.append("> \U0001F4E6 %s Used in GitHub's Storage " % human_size(storage_kb))
    parts.append(" > ")
    parts.append(
        "> \U0001F3C6 %s Contributions in the Year %d"
        % (format(gh["contributions"], ","), gh["year"])
    )
    parts.append(" > ")
    parts.append("> \U0001F4BC Opted to Hire" if user.get("hireable") else "> \U0001F6AB Not Opted to Hire")
    parts.append(" > ")
    public = int(user.get("public_repos") or 0)
    parts.append(
        "> \U0001F4DC %d Public Repositor%s "
        % (public, "y" if public == 1 else "ies")
    )
    parts.append(" > ")
    if gh.get("private") is not None:
        priv = int(gh["private"])
        parts.append(
            "> \U0001F511 %d Private Repositor%s "
            % (priv, "y" if priv == 1 else "ies")
        )
        parts.append(" > ")
    return "\n".join(parts)


def render_time(waka_data):
    week = waka_data["week"]
    days = week.get("data") or []
    cumulative = week.get("cumulative_total") or {}
    total_seconds = float(cumulative.get("seconds") or cumulative.get("total_seconds") or 0)
    if total_seconds <= 0:
        return ""

    tz = (waka_data.get("stats") or {}).get("timezone") or "UTC"

    out = ["\U0001F4CA **This Week I Spent My Time On** ", "", FENCE + "text"]
    out.append("\U0001F551\ufe0e Time Zone: " + str(tz))
    out.append("")

    sections = [
        ("\U0001F4AC", "Programming Languages", "languages"),
        ("\U0001F525", "Editors", "editors"),
        ("\U0001F431\u200d\U0001F4BB", "Projects", "projects"),
        ("\U0001F4BB", "Operating System", "operating_systems"),
    ]
    for icon, title, key in sections:
        ranked = aggregate(days, key)
        out.append("%s %s: " % (icon, title))
        if not ranked:
            out.append("No Activity Tracked This Week")
        else:
            for name, seconds in ranked:
                out.append(row(name, human_time(seconds), seconds / total_seconds * 100))
        out.append("")
    out[-1] = FENCE
    return "\n".join(out)


def render_ai(waka_data):
    data = waka_data.get("stats") or {}
    category = find_category(data.get("categories"), "AI Coding")
    sessions = int(data.get("ai_sessions") or 0)

    if sessions <= 0:
        ai = waka_data.get("ai") or {}
        if int(ai.get("sessions") or 0) <= 0:
            return ""
        cumulative = (waka_data.get("week") or {}).get("cumulative_total") or {}
        total_seconds = float(cumulative.get("seconds") or 0)
        pct = (float(ai.get("seconds") or 0) / total_seconds * 100) if total_seconds else 0.0
        out = ["\U0001F916 **AI Coding This Week** ", "", FENCE + "text"]
        out.append(
            "⏱ AI Coding Time: %s (%.2f%%)"
            % (human_time(ai.get("seconds") or 0), pct)
        )
        out.append("")
        out.append(
            "\U0001F524 %s Input Tokens, %s Output Tokens"
            % (format(int(ai.get("input_tokens") or 0), ","), format(int(ai.get("output_tokens") or 0), ","))
        )
        out.append("")
        out.append(
            "\U0001F9E0 %d AI Sessions, %d AI Prompts"
            % (int(ai.get("sessions") or 0), int(ai.get("prompts") or 0))
        )
        out.append("")
        out[-1] = FENCE
        return "\n".join(out)

    ai_add = int(data.get("ai_additions") or 0)
    ai_del = int(data.get("ai_deletions") or 0)
    human_add = int(data.get("human_additions") or 0)
    human_del = int(data.get("human_deletions") or 0)
    total_add = ai_add + human_add
    ai_written = (ai_add / total_add * 100) if total_add else 0.0
    total_changed = ai_add + ai_del + human_add + human_del
    manual = ((human_add + human_del) / total_changed * 100) if total_changed else 0.0

    out = ["\U0001F916 **AI Coding This Week** ", "", FENCE + "text"]
    out.append(
        "\u23f1 AI Coding Time: %s (%s%%)"
        % (category.get("text") if category else "0 secs", category.get("percent") if category else 0)
    )
    out.append("")
    out.append(
        "\u270d\ufe0f %s lines written by AI, %s lines written by hand (%.2f%% AI-written)"
        % (format(ai_add, ","), format(human_add, ","), ai_written)
    )
    out.append("")
    out.append(
        "\U0001F524 %s Input Tokens, %s Output Tokens"
        % (format(int(data.get("ai_input_tokens") or 0), ","), format(int(data.get("ai_output_tokens") or 0), ","))
    )
    out.append("")
    out.append("\U0001F4B5 $%.2f Estimated AI Cost This Week" % float(data.get("ai_model_total_cost") or 0))
    out.append("")
    out.append(
        "\U0001F9E0 %d AI Sessions, %d AI Prompts"
        % (sessions, int(data.get("ai_prompt_events_total") or 0))
    )
    out.append("")

    models = data.get("ai_model_breakdown") or []
    if models:
        total_lines = sum(int(m.get("lines") or 0) for m in models) or 1
        for model in models:
            lines = int(model.get("lines") or 0)
            out.append(row(str(model.get("name", "?")), format(lines, ",") + " lines", lines / total_lines * 100))
        out.append("")

    prompt_len = float(data.get("ai_prompt_length_avg") or 0)
    per_session = float(data.get("ai_prompt_events_avg_per_session") or 0)
    reliance = (
        "\U0001F916 AI-Driven" if ai_written >= 66
        else "\u2696\ufe0f Balanced with AI" if ai_written >= 33
        else "\U0001F9D1\u200d\U0001F4BB Mostly Hands-On"
    )
    verbosity = "\U0001F4DA Verbose Prompter" if prompt_len >= 500 else "\u2702\ufe0f Concise Prompter"
    rhythm = "\U0001F501 Iterative Prompter" if per_session >= 3 else "\U0001F3AF One-Shot Prompter"
    review = "\U0001F680 High AI Trust" if manual <= 5 else "\U0001F50D Hands-On Reviewer"

    out.append("\U0001F50E AI Coding Insights:")
    out.append("%s \u2014 %.2f%% of written lines came from AI" % (reliance, ai_written))
    out.append("%s \u2014 average %s characters per prompt" % (verbosity, format(round(prompt_len), ",")))
    out.append("%s \u2014 average %.0f prompts per session" % (rhythm, per_session))
    out.append("%s \u2014 %.2f%% of changed lines were hand-edited" % (review, manual))
    out.append("")
    out[-1] = FENCE
    return "\n".join(out)


def render_languages(gh):
    repos = [r for r in gh["repos"] if not r.get("fork")]
    counts = {}
    for repo in repos:
        lang = repo.get("language") or ""
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    total = sum(counts.values()) or 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    top = ranked[0][0] if ranked else "Markdown"
    out = ["**I Mostly Code in %s** " % top, "", FENCE + "text"]
    for name, count in ranked:
        out.append(row(name, "%d repo%s" % (count, "" if count == 1 else "s"), count / total * 100))
    out.append(FENCE)
    return "\n".join(out)


def render_ai_total(waka_data):
    ai = waka_data.get("ai") or {}
    total = ai.get("total") or {}
    if int(total.get("sessions") or 0) <= 0:
        return ""

    out = ["\U0001F3C6 **AI Coding All Time** ", "", FENCE + "text"]
    out.append("\u23f1 %s" % human_time(ai.get("year_seconds") or 0))
    out.append("")
    out.append(
        "\U0001F524 %s Input Tokens, %s Output Tokens"
        % (
            format(int(total.get("input_tokens") or 0), ","),
            format(int(total.get("output_tokens") or 0), ","),
        )
    )
    out.append("")
    out.append(
        "\U0001F9E0 %d AI Sessions, %d AI Prompts"
        % (int(total.get("sessions") or 0), int(total.get("prompts") or 0))
    )
    out.append("")
    out[-1] = FENCE
    return "\n".join(out)


def build_block(gh, waka_data):
    sections = []

    if waka_data.get("enabled"):
        time_block = render_time(waka_data)
        ai_block = render_ai(waka_data)
    else:
        time_block = ai_block = ""

    badges = render_badges(gh, waka_data)
    if badges:
        sections.append("\n\n".join(badges))

    sections.append(render_github(gh, None if waka_data.get("enabled") else gh.get("views")))
    if time_block:
        sections.append(time_block)
    if ai_block:
        sections.append(ai_block)
    total_block = render_ai_total(waka_data) if waka_data.get("enabled") else ""
    if total_block:
        sections.append(total_block)
    sections.append(render_languages(gh))

    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S")
    sections.append("\n\n\n Last Updated on %s UTC" % now)
    return "\n\n".join(sections).strip()


def sanitize(message):
    text = str(message)
    if WAKA_KEY:
        text = text.replace(WAKA_KEY, "***")
    if GH_TOKEN:
        text = text.replace(GH_TOKEN, "***")
    return text.replace("--", "").replace("\n", " ")[:300]


def error_comment(exc):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "<!-- profile-update-error: %s | %s | %s: %s -->" % (
        stamp,
        STAGE,
        type(exc).__name__,
        sanitize(exc),
    )


def main():
    with open(README, encoding="utf-8") as fh:
        readme = fh.read()

    problems = []
    block = None
    try:
        gh = collect_github()
    except Exception as exc:
        problems.append(error_comment(exc))
        print("github collection failed at %s: %s" % (STAGE, exc), flush=True)
        gh = None

    if gh is not None:
        waka_data = {"enabled": False}
        if WAKA_KEY:
            try:
                waka_data = collect_wakatime()
            except Exception as exc:
                problems.append(error_comment(exc))
                print("wakatime collection failed at %s: %s" % (STAGE, exc), flush=True)
        block = build_block(gh, waka_data)
        print("rendered block, wakatime=%s" % waka_data.get("enabled"), flush=True)

    updated = ERROR_COMMENT.sub("\n", readme)
    if block is not None:
        body = START + "\n" + block + "\n" + END
        pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
        updated = pattern.sub(lambda _: body, updated, count=1)

    if problems:
        updated = updated.rstrip("\n") + "\n\n" + "\n".join(problems) + "\n"

    if updated != readme:
        with open(README, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(updated)
        print("README.md updated")
    else:
        print("README.md unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
