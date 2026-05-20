#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


TELNYX_GREEN = "#00E5A8"
CREAM = "#F7F3E8"
INK = "#090B0B"
MUTED = "#9AA29F"
PANEL = "#121615"


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f} ms"


def fmt_num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def logo_svg() -> str:
    logo_path = Path(__file__).parent / "assets" / "telnyx-logo-cream.svg"
    if logo_path.exists():
        return logo_path.read_text()
    return "<span>Telnyx</span>"


def metric_rows(summary: dict) -> str:
    rows = []
    for voice, item in sorted(summary.items(), key=lambda row: row[1].get("first_audio_p50_ms") or 999999):
        rows.append(
            f"""
            <tr>
              <td><span class="voice-dot"></span>{voice}</td>
              <td>{fmt_ms(item.get("first_audio_p50_ms"))}</td>
              <td>{fmt_ms(item.get("first_audio_p95_ms"))}</td>
              <td>{fmt_ms(item.get("final_audio_p50_ms"))}</td>
              <td>{fmt_num(item.get("rtf_p50"))}</td>
              <td>{item.get("successes", 0)}/{item.get("runs", 0)}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def bar_chart(summary: dict, key: str, label: str) -> str:
    rows = [(voice, item.get(key)) for voice, item in summary.items() if item.get(key) is not None]
    if not rows:
        return '<div class="empty">No successful runs for this metric.</div>'
    max_value = max(value for _, value in rows) or 1
    html_rows = []
    for voice, value in sorted(rows, key=lambda row: row[1]):
        width = max(4, value / max_value * 100)
        html_rows.append(
            f"""
            <div class="bar-row">
              <div class="bar-label">{voice}</div>
              <div class="bar-track"><div class="bar-fill" style="width:{width:.2f}%"></div></div>
              <div class="bar-value">{fmt_ms(value)}</div>
            </div>
            """
        )
    return f'<div class="chart-title">{label}</div><div class="bar-chart">{"".join(html_rows)}</div>'


def insight_cards(summary: dict) -> str:
    usable = [(voice, row) for voice, row in summary.items() if row.get("first_audio_p50_ms") is not None]
    if not usable:
        return '<div class="insight">No successful runs were recorded.</div>'
    fastest_first = min(usable, key=lambda row: row[1]["first_audio_p50_ms"])
    fastest_final = min(usable, key=lambda row: row[1].get("final_audio_p50_ms") or 999999)
    best_rtf = min(
        [row for row in usable if row[1].get("rtf_p50") is not None],
        key=lambda row: row[1]["rtf_p50"],
        default=None,
    )
    cards = [
        ("First audio", fastest_first[0], fmt_ms(fastest_first[1].get("first_audio_p50_ms"))),
        ("Final audio", fastest_final[0], fmt_ms(fastest_final[1].get("final_audio_p50_ms"))),
    ]
    if best_rtf:
        cards.append(("Real-time factor", best_rtf[0], fmt_num(best_rtf[1].get("rtf_p50"))))
    return "\n".join(
        f"""
        <div class="insight">
          <div class="insight-label">{label}</div>
          <div class="insight-value">{value}</div>
          <div class="insight-detail">{voice}</div>
        </div>
        """
        for label, voice, value in cards
    )


def render_html(data: dict) -> str:
    metadata = data.get("metadata", {})
    summary = data.get("summary", {})
    prompt_count = len(metadata.get("prompts", []))
    voice_count = len(metadata.get("voices", []))
    total_runs = sum(row.get("runs", 0) for row in summary.values())
    metric_defs = metadata.get("metric_definitions", {})

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telnyx TTS latency benchmark</title>
<style>
  @page {{ size: 11in 8.5in; margin: 0; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: {INK};
    color: {CREAM};
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  .page {{
    width: 11in;
    height: 8.5in;
    padding: 0.44in 0.52in;
    page-break-after: always;
    background:
      linear-gradient(180deg, rgba(0,229,168,0.08), rgba(0,0,0,0) 36%),
      {INK};
    position: relative;
    overflow: hidden;
  }}
  .page::after {{
    content: "";
    position: absolute;
    left: 0.52in;
    right: 0.52in;
    bottom: 0.28in;
    height: 1px;
    background: rgba(247,243,232,0.16);
  }}
  .brand {{ width: 1.85in; height: 0.36in; }}
  .brand svg {{ display: block; width: 100%; height: auto; }}
  .hero {{
    display: grid;
    grid-template-columns: 1.05fr 0.95fr;
    gap: 0.46in;
    align-items: center;
    height: 6.7in;
  }}
  h1 {{
    font-size: 56px;
    line-height: 0.95;
    margin: 0.45in 0 0.2in;
    letter-spacing: 0;
    max-width: 6.2in;
  }}
  h2 {{
    font-size: 33px;
    line-height: 1.04;
    margin: 0 0 0.25in;
    letter-spacing: 0;
  }}
  p {{
    color: rgba(247,243,232,0.78);
    font-size: 16px;
    line-height: 1.45;
    margin: 0 0 0.16in;
  }}
  .kicker {{
    color: {TELNYX_GREEN};
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 800;
  }}
  .hero-copy {{ max-width: 5.9in; }}
  .hero-stats {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0.15in;
  }}
  .stat, .insight {{
    background: rgba(247,243,232,0.055);
    border: 1px solid rgba(247,243,232,0.13);
    border-radius: 8px;
    padding: 0.22in;
  }}
  .stat-number {{
    font-size: 36px;
    font-weight: 760;
    color: {CREAM};
  }}
  .stat-label, .insight-label {{
    color: {MUTED};
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.06in;
  }}
  .grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.3in;
    margin-top: 0.22in;
  }}
  .panel {{
    background: {PANEL};
    border: 1px solid rgba(247,243,232,0.12);
    border-radius: 8px;
    padding: 0.24in;
  }}
  .panel.large {{ min-height: 5.6in; }}
  .chart-title {{
    font-size: 14px;
    font-weight: 760;
    margin-bottom: 0.18in;
  }}
  .bar-row {{
    display: grid;
    grid-template-columns: 2.35in 1fr 0.75in;
    align-items: center;
    gap: 0.13in;
    margin-bottom: 0.16in;
  }}
  .bar-label {{
    color: rgba(247,243,232,0.88);
    font-size: 13px;
    overflow-wrap: anywhere;
  }}
  .bar-track {{
    height: 0.17in;
    background: rgba(247,243,232,0.09);
    border-radius: 999px;
    overflow: hidden;
  }}
  .bar-fill {{
    height: 100%;
    background: {TELNYX_GREEN};
    border-radius: 999px;
  }}
  .bar-value {{ font-size: 13px; color: {CREAM}; text-align: right; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 0.1in;
    font-size: 13px;
  }}
  th {{
    text-align: left;
    color: {MUTED};
    font-weight: 700;
    padding: 0.12in 0.08in;
    border-bottom: 1px solid rgba(247,243,232,0.16);
  }}
  td {{
    padding: 0.13in 0.08in;
    border-bottom: 1px solid rgba(247,243,232,0.08);
    color: rgba(247,243,232,0.9);
  }}
  .voice-dot {{
    display: inline-block;
    width: 8px;
    height: 8px;
    background: {TELNYX_GREEN};
    border-radius: 50%;
    margin-right: 8px;
  }}
  .insights {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.16in;
    margin: 0.18in 0 0.24in;
  }}
  .insight-value {{
    font-size: 28px;
    font-weight: 780;
  }}
  .insight-detail {{
    color: rgba(247,243,232,0.72);
    font-size: 13px;
    margin-top: 0.06in;
    overflow-wrap: anywhere;
  }}
  .definition {{
    border-left: 3px solid {TELNYX_GREEN};
    padding-left: 0.15in;
    margin-bottom: 0.17in;
  }}
  .definition b {{
    display: block;
    font-size: 16px;
    margin-bottom: 0.04in;
  }}
  .definition span {{
    color: rgba(247,243,232,0.73);
    font-size: 14px;
    line-height: 1.36;
  }}
  .footer {{
    position: absolute;
    left: 0.52in;
    bottom: 0.42in;
    color: rgba(247,243,232,0.45);
    font-size: 11px;
  }}
</style>
</head>
<body>
  <section class="page">
    <div class="brand">{logo_svg()}</div>
    <div class="hero">
      <div class="hero-copy">
        <div class="kicker">TTS latency benchmark</div>
        <h1>TTS latency by voice</h1>
        <p>We benchmarked Telnyx TTS WebSocket voices by sending the same prompt set through each voice and timing when audio first arrives, when the final audio chunk arrives, and how fast synthesis runs versus generated audio duration.</p>
        <p>Use this report to compare voice options for live assistants, outbound calls, and latency-sensitive voice workflows.</p>
      </div>
      <div class="hero-stats">
        <div class="stat"><div class="stat-label">Voices</div><div class="stat-number">{voice_count}</div></div>
        <div class="stat"><div class="stat-label">Prompts</div><div class="stat-number">{prompt_count}</div></div>
        <div class="stat"><div class="stat-label">Runs</div><div class="stat-number">{total_runs}</div></div>
        <div class="stat"><div class="stat-label">Format</div><div class="stat-number">{metadata.get("audio_format", "n/a")}</div></div>
      </div>
    </div>
    <div class="footer">{metadata.get("created_at", "")}</div>
  </section>

  <section class="page">
    <h2>Benchmark results</h2>
    <p>Sorted by median first audio latency. Lower values are better.</p>
    <div class="insights">{insight_cards(summary)}</div>
    <div class="panel large">
      <table>
        <thead>
          <tr>
            <th>Voice</th>
            <th>First audio p50</th>
            <th>First audio p95</th>
            <th>Final audio p50</th>
            <th>RTF p50</th>
            <th>Success</th>
          </tr>
        </thead>
        <tbody>{metric_rows(summary)}</tbody>
      </table>
    </div>
  </section>

  <section class="page">
    <h2>First and final audio latency</h2>
    <p>First audio maps to perceived response delay. Final audio maps to complete synthesis time.</p>
    <div class="grid-2">
      <div class="panel large">{bar_chart(summary, "first_audio_p50_ms", "First audio p50")}</div>
      <div class="panel large">{bar_chart(summary, "final_audio_p50_ms", "Final audio p50")}</div>
    </div>
  </section>

  <section class="page">
    <h2>How to read this</h2>
    <div class="grid-2">
      <div class="panel large">
        <div class="definition"><b>First audio</b><span>{metric_defs.get("first_audio_ms", "")}</span></div>
        <div class="definition"><b>Final audio</b><span>{metric_defs.get("final_audio_ms", "")}</span></div>
        <div class="definition"><b>Generated audio duration</b><span>{metric_defs.get("audio_duration_ms", "")}</span></div>
        <div class="definition"><b>Real-time factor</b><span>{metric_defs.get("rtf", "")}</span></div>
      </div>
      <div class="panel large">
        <div class="kicker">Use case implications</div>
        <p>For live voice agents, first audio is usually the most visible TTS metric because it controls how quickly the assistant starts speaking.</p>
        <p>Final audio and real-time factor matter when the response is long, when audio must be generated before playback, or when you need predictable batch generation.</p>
        <p>For apples-to-apples runs, keep prompt text, output format, sample rate, voice settings, region, and provider credentials fixed.</p>
      </div>
    </div>
  </section>
</body>
</html>
"""


def find_browser() -> str | None:
    candidates = [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("chromium"),
        shutil.which("google-chrome"),
        shutil.which("microsoft-edge"),
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).exists()), None)


def export_pdf(html_path: Path, pdf_path: Path) -> None:
    browser = find_browser()
    if not browser:
        raise RuntimeError("No Chromium browser found. HTML report was still written.")
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            f"--print-to-pdf={pdf_path}",
            f"file://{html_path.resolve()}",
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Telnyx-branded TTS benchmark report.")
    parser.add_argument("results_json")
    parser.add_argument("--out-dir", default="reports")
    parser.add_argument("--name", default="tts-benchmark-report")
    parser.add_argument("--html-only", action="store_true")
    args = parser.parse_args()

    data = json.loads(Path(args.results_json).read_text())
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{args.name}.html"
    pdf_path = out_dir / f"{args.name}.pdf"
    html_path.write_text(render_html(data))
    print(f"Wrote {html_path}")
    if not args.html_only:
        export_pdf(html_path, pdf_path)
        print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    main()
