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
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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


def http_text(url, headers=None, timeout=25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "morewhyhan-profile-bot",
            "Accept": "application/vnd.github+json",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def http_json(url, headers=None, timeout=25):
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
            views = traffic.get("uniques") or traffic.get("count")
        except Exception:
            views = None

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

    STAGE = "wakatime:all_time"
    all_time = waka("users/current/stats/all_time")
    STAGE = "wakatime:last_7_days"
    latest = waka("users/current/stats/last_7_days")
    return {"enabled": True, "all_time": all_time, "latest": latest}


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #

def week_seconds(data):
    total = data.get("total_seconds")
    if total:
        return float(total)
    return sum(float(i.get("total_seconds") or 0) for i in (data.get("languages") or []))


def render_badges(waka_data):
    all_time = (waka_data.get("all_time") or {}).get("data") or {}
    badges = []
    total = all_time.get("human_readable_total")
    if total and float(all_time.get("total_seconds") or 0) > 0:
        badges.append(badge("Code Time", total))
    ai = find_category(all_time.get("categories"), "AI Coding")
    if ai and ai.get("text"):
        badges.append(badge("AI Code Time", ai["text"]))
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
    data = (waka_data.get("latest") or {}).get("data") or {}
    out = ["\U0001F4CA **This Week I Spent My Time On** ", "", FENCE + "text"]
    tz = data.get("timezone")
    if tz:
        out.append("\U0001F551\ufe0e Time Zone: " + str(tz))
        out.append("")

    sections = [
        ("\U0001F4AC", "Programming Languages", "languages"),
        ("\U0001F525", "Editors", "editors"),
        ("\U0001F431\u200d\U0001F4BB", "Projects", "projects"),
        ("\U0001F4BB", "Operating System", "operating_systems"),
    ]
    for icon, title, key in sections:
        items = [i for i in (data.get(key) or []) if float(i.get("percent") or 0) > 0][:5]
        out.append("%s %s: " % (icon, title))
        if not items:
            out.append("No Activity Tracked This Week")
        else:
            for item in items:
                out.append(
                    row(
                        str(item.get("name", "?")),
                        str(item.get("text", "")),
                        float(item.get("percent") or 0),
                    )
                )
        out.append("")
    out[-1] = FENCE
    return "\n".join(out)


def render_ai(waka_data):
    data = (waka_data.get("latest") or {}).get("data") or {}
    category = find_category(data.get("categories"), "AI Coding")
    sessions = int(data.get("ai_sessions") or 0)

    out = ["\U0001F916 **AI Coding This Week** ", "", FENCE + "text"]
    if not category or not sessions:
        out += ["No AI Coding Activity Tracked This Week", "", FENCE]
        return "\n".join(out)

    ai_add = int(data.get("ai_additions") or 0)
    ai_del = int(data.get("ai_deletions") or 0)
    human_add = int(data.get("human_additions") or 0)
    human_del = int(data.get("human_deletions") or 0)
    total_add = ai_add + human_add
    ai_written = (ai_add / total_add * 100) if total_add else 0.0
    total_changed = ai_add + ai_del + human_add + human_del
    manual = ((human_add + human_del) / total_changed * 100) if total_changed else 0.0

    out.append("\u23f1 AI Coding Time: %s (%s%%)" % (category.get("text"), category.get("percent")))
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
            out.append(
                row(str(model.get("name", "?")), format(lines, ",") + " lines", lines / total_lines * 100)
            )
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
    out[-1] = out[-1]
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


def build_block(gh, waka_data):
    sections = []
    latest = (waka_data.get("latest") or {}).get("data") or {}
    active = bool(waka_data.get("enabled")) and week_seconds(latest) > 0

    if waka_data.get("enabled"):
        badges = render_badges(waka_data)
        if gh.get("views") is not None:
            badges.append(badge("Profile Views", gh["views"]))
        if badges:
            sections.append("\n\n".join(badges))

    sections.append(render_github(gh, None if waka_data.get("enabled") else gh.get("views")))
    if active:
        sections.append(render_time(waka_data))
        sections.append(render_ai(waka_data))
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
