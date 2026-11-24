"""Record a live application form, so its test fixture is the tenant's markup.

ADR 5: tests built from HTML we wrote pass while the live page fails. Every
selector in the fillers was found by opening the real form once; this keeps the
page that proved it, as a fixture, instead of a memory of it.

The recording is a *replayable* copy, not a byte-for-byte archive: scripts and
remote stylesheets are removed so the fixture opens from disk with no network,
and stays small enough to read. Everything that identifies a control — tag,
type, name, id, aria-label, label text — is kept, because that is what the
filler will be written against. The inventory written next to it is the shortcut:
it lists what the page actually contains, so a selector is looked up rather than
guessed.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

#: Text on the control that reveals the form on a posting page.
APPLY_WORDS = re.compile(
    r"^(apply( now| for (this )?(job|position|role))?|start (your )?application"
    r"|i'?m interested|begin application|apply online)\s*$",
    re.I,
)

INVENTORY_JS = """
() => {
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 || box.height > 0;
  };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return (el.labels[0].innerText || '').trim();
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const id = el.getAttribute('id');
    if (id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (explicit) return (explicit.innerText || '').trim();
    }
    const wrap = el.closest('label');
    if (wrap) return (wrap.innerText || '').trim();
    return '';
  };
  const out = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    if (!visible(el)) continue;
    const row = {
      tag: el.tagName.toLowerCase(),
      type,
      id: el.getAttribute('id') || '',
      name: el.getAttribute('name') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      placeholder: el.getAttribute('placeholder') || '',
      label: labelOf(el).slice(0, 120),
      required: !!el.required,
    };
    if (el.tagName.toLowerCase() === 'select') {
      row.options = Array.from(el.options).slice(0, 12).map((o) => (o.text || '').trim());
      row.option_count = el.options.length;
    }
    out.push(row);
  }
  return out;
}
"""

BUTTONS_JS = """
() => Array.from(document.querySelectorAll('a, button, [role="button"]'))
  .filter((el) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  })
  .map((el) => ({
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || '').trim().slice(0, 60),
    href: el.getAttribute('href') || '',
    id: el.getAttribute('id') || '',
  }))
  .filter((row) => row.text)
  .slice(0, 60);
"""


def slug_for(url: str, ats: str = "") -> str:
    """A stable fixture name from a posting URL: ``neudata-916b4157``.

    The org and the posting id are what tell two forms at the same ATS apart;
    the path is dropped because it is mostly the job title.
    """
    parts = [part for part in re.sub(r"^https?://", "", url).split("/") if part]
    host = parts[0] if parts else "page"
    rest = [part for part in parts[1:] if part not in ("j", "application", "apply")]
    stem = "-".join([host.split(".")[0]] + rest[-2:]) if rest else host.split(".")[0]
    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    if ats and not stem.startswith(ats):
        stem = f"{ats}-{stem}"
    return stem[:80] or "page"


def strip_for_replay(html: str) -> str:
    """Remove what makes a recorded page need the network or a server.

    Scripts are the point: they would re-render the form from an API call and
    could replace the markup the fixture exists to preserve. Remote stylesheets
    and iframes go with them. Inline ``style`` attributes and ``<style>`` blocks
    stay, because some forms are laid out with them and a hidden container is
    part of the behaviour being recorded.
    """
    html = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", html)
    html = re.sub(r"(?is)<script\b[^>]*/>", "", html)
    html = re.sub(r"(?is)<noscript\b[^>]*>.*?</noscript>", "", html)
    html = re.sub(r"(?is)<iframe\b[^>]*>.*?</iframe>", "", html)
    html = re.sub(r"(?is)<link\b[^>]*rel=[\"']?(?:preload|prefetch|modulepreload)[^>]*>", "", html)
    return html


def annotate(html: str, *, url: str, ats: str, captured: str) -> str:
    """Say where the fixture came from, in the fixture itself.

    Immediately after the doctype: before it would put the browser in quirks
    mode, and a ``<head>`` search is unreliable because some pages carry a long
    style attribute on ``<html>`` first.
    """
    note = (
        f"<!-- recorded from {url} ({ats or 'unknown ats'}) on {captured};"
        " scripts and remote assets removed for offline replay -->\n"
    )
    match = re.match(r"(?is)\s*<!doctype[^>]*>", html)
    if match:
        return html[: match.end()] + "\n" + note + html[match.end():]
    return note + html


def capture(
    url: str,
    *,
    ats: str = "",
    out_dir: Path,
    headless: bool = True,
    timeout_ms: int = 45_000,
    wait_ms: int = 2_500,
    wait_for_controls: float = 15,
    click: str = "",
) -> dict:
    """Open ``url``, reveal the form if it is behind an Apply control, save it.

    Returns the metadata written next to the fixture. The metadata is the point
    of the exercise: it records which controls the live page really had, so a
    filler is written against evidence and a missing selector is a fact about
    the page rather than a guess about it.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - environment dependent
        raise RuntimeError("playwright is not installed")

    ats = ats or _ats_from(url)
    captured = datetime.now().astimezone().isoformat(timespec="seconds")
    out_dir = Path(out_dir)
    slug = slug_for(url, ats)

    notes: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1100},
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(wait_ms)

        # A posting page often shows the form only after an Apply control. One
        # click, the obvious one, and only if no form is showing already.
        if not _controls(page):
            _dismiss_cookies(page)
            clicked = _click_apply(page, click)
            if clicked:
                notes.append(f"clicked {clicked}")
                page.wait_for_timeout(wait_ms)
            else:
                notes.append("no apply control found; recorded the posting page")
        else:
            notes.append("form was already on the page")

        if not _controls(page):
            _await_controls(page, seconds=wait_for_controls)

        controls = _controls(page)
        buttons = _buttons(page)
        final_url = page.url
        html = annotate(strip_for_replay(page.content()), url=final_url, ats=ats, captured=captured)
        # A page with no controls is evidence of a wall, not a fixture: it goes
        # somewhere a test will never replay it from.
        if not controls:
            out_dir = out_dir / "_failed"
            notes.append("no form controls rendered; kept as evidence, not a fixture")
        out_dir.mkdir(parents=True, exist_ok=True)
        html_path = out_dir / f"{slug}.html"
        meta_path = out_dir / f"{slug}.json"
        html_path.write_text(html, encoding="utf-8")
        try:
            page.screenshot(path=str(out_dir / f"{slug}.png"), full_page=True)
        except Exception:  # noqa: BLE001 - a screenshot is a convenience
            pass
        context.close()
        browser.close()

    meta = {
        "ats": ats,
        "url": url,
        "final_url": final_url,
        "captured": captured,
        "slug": slug,
        "fixture": html_path.name,
        "path": str(html_path),
        "control_count": len(controls),
        "controls": controls,
        "buttons": buttons,
        "notes": notes,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def detect_ats(url: str) -> str:
    """Which board a URL belongs to, via the fillers' own host table."""
    from .fill import detect_ats as _detect

    return _detect(url)


def _ats_from(url: str) -> str:
    return detect_ats(url)


def _controls(page) -> list[dict]:
    try:
        return page.evaluate(INVENTORY_JS)
    except Exception:  # noqa: BLE001
        return []


def _await_controls(page, *, seconds: float) -> None:
    """Give a single-page app time to render its form, without hanging on one."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _controls(page):
            return
        page.wait_for_timeout(500)


def _buttons(page) -> list[dict]:
    try:
        return page.evaluate(BUTTONS_JS)
    except Exception:  # noqa: BLE001
        return []


def _click_apply(page, wanted: str = "") -> str:
    """Press the control that reveals the form. Returns what was clicked.

    A click that an overlay swallows is common on these pages, so the control's
    own ``href`` is the fallback: following the link is the same journey without
    fighting the cookie banner.
    """
    for frame in page.frames:
        try:
            candidates = frame.query_selector_all("a, button, [role='button']")
        except Exception:  # noqa: BLE001
            continue
        for element in candidates:
            try:
                text = (element.inner_text() or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if not text:
                continue
            matches = (wanted and wanted.lower() in text.lower()) or APPLY_WORDS.match(text)
            if not matches:
                continue
            try:
                element.click(timeout=3_000)
                return text
            except Exception:  # noqa: BLE001
                href = ""
                try:
                    href = element.get_attribute("href") or ""
                except Exception:  # noqa: BLE001
                    href = ""
                if href:
                    try:
                        page.goto(
                            page.evaluate("(href) => new URL(href, location.href).href", href),
                            wait_until="domcontentloaded",
                            timeout=45_000,
                        )
                        return f"{text} (followed {href})"
                    except Exception:  # noqa: BLE001
                        continue
    return ""


def _dismiss_cookies(page) -> None:
    """Accept a cookie banner when one is in the way. Nothing else is pressed."""
    for label in ("Accept all", "Accept cookies", "Accept", "I agree", "Got it"):
        try:
            button = page.query_selector(
                f"button:has-text('{label}'), [role='button']:has-text('{label}')"
            )
        except Exception:  # noqa: BLE001
            button = None
        if button is None:
            continue
        try:
            button.click(timeout=2_000)
            page.wait_for_timeout(500)
            return
        except Exception:  # noqa: BLE001
            continue


def inventory_line(control: dict) -> str:
    """One control, in the shape a selector is written from."""
    bits = [control.get("tag", "?")]
    if control.get("type"):
        bits.append(f"type={control['type']}")
    if control.get("id"):
        bits.append(f"id={control['id']}")
    if control.get("name"):
        bits.append(f"name={control['name']}")
    if control.get("label"):
        bits.append(f'label="{control["label"]}"')
    if control.get("required"):
        bits.append("required")
    if control.get("option_count"):
        bits.append(f"options={control['option_count']}")
    return "  ".join(bits)
