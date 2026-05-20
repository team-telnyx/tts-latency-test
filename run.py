#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import websockets


DEFAULT_ENDPOINT = "wss://api.telnyx.com/v2/text-to-speech/speech"
DEFAULT_REST_ENDPOINT = "https://api.telnyx.com/v2/text-to-speech/speech"
DEFAULT_VOICES = [
    "Telnyx.NaturalHD.astra",
    "aws.Polly.Generative.Lucia",
    "azure.en-US-AvaMultilingualNeural",
]
DEFAULT_AUDIO_FORMAT = "mp3"
DEFAULT_SAMPLE_RATE = 24000


@dataclass
class RunResult:
    voice: str
    prompt_index: int
    text: str
    run_index: int
    interface: str
    audio_format: str
    sample_rate: int
    success: bool
    first_audio_ms: float | None = None
    final_audio_ms: float | None = None
    audio_duration_ms: float | None = None
    rtf: float | None = None
    audio_chunks: int = 0
    audio_bytes: int = 0
    final_seen: bool = False
    error: str | None = None


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def load_prompts(args: argparse.Namespace) -> list[str]:
    prompts: list[str] = []
    if args.text:
        prompts.extend(args.text)
    if args.prompt_file:
        data = json.loads(Path(args.prompt_file).read_text())
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            raise ValueError("Prompt file must contain a JSON list of strings.")
        prompts.extend(data)
    if not prompts:
        prompts = json.loads(Path("samples/prompts.json").read_text())
    return [prompt.strip() for prompt in prompts if prompt.strip()]


def make_url(args: argparse.Namespace, voice: str) -> str:
    params: dict[str, Any] = {"voice": voice}
    if args.audio_format:
        params["audio_format"] = args.audio_format
    if args.sample_rate:
        params["sample_rate"] = str(args.sample_rate)
    return f"{args.endpoint}?{urlencode(params)}"


def compute_audio_duration_ms(audio_bytes: int, audio_format: str, sample_rate: int) -> float | None:
    if audio_format != "linear16" or audio_bytes <= 0:
        return None
    return (audio_bytes / 2) / sample_rate * 1000


def interface_for_voice(voice: str) -> str:
    if voice.startswith("Telnyx.Ultra."):
        return "rest"
    return "websocket"


def run_once_rest(
    args: argparse.Namespace,
    api_key: str,
    voice: str,
    text: str,
    prompt_index: int,
    run_index: int,
) -> RunResult:
    result = RunResult(
        voice=voice,
        prompt_index=prompt_index,
        text=text,
        run_index=run_index,
        interface="rest",
        audio_format=args.audio_format,
        sample_rate=args.sample_rate,
        success=False,
    )
    payload = {
        "text": text,
        "voice": voice,
        "output_type": "binary_output",
    }
    if args.sample_rate:
        payload["sampling_rate"] = args.sample_rate

    request = urllib.request.Request(
        args.rest_endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        start = time.perf_counter()
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                elapsed_ms = (time.perf_counter() - start) * 1000
                if result.first_audio_ms is None:
                    result.first_audio_ms = elapsed_ms
                result.final_audio_ms = elapsed_ms
                result.audio_chunks += 1
                result.audio_bytes += len(chunk)
        result.audio_duration_ms = compute_audio_duration_ms(
            result.audio_bytes,
            args.audio_format,
            args.sample_rate,
        )
        if result.final_audio_ms is not None and result.audio_duration_ms:
            result.rtf = result.final_audio_ms / result.audio_duration_ms
        result.final_seen = True
        result.success = result.first_audio_ms is not None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        result.error = f"HTTPError {exc.code}: {body}"
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"

    return result


async def run_once_websocket(
    args: argparse.Namespace,
    api_key: str,
    voice: str,
    text: str,
    prompt_index: int,
    run_index: int,
) -> RunResult:
    result = RunResult(
        voice=voice,
        prompt_index=prompt_index,
        text=text,
        run_index=run_index,
        interface="websocket",
        audio_format=args.audio_format,
        sample_rate=args.sample_rate,
        success=False,
    )

    headers = {"Authorization": f"Bearer {api_key}"}
    url = make_url(args, voice)

    try:
        async with websockets.connect(
            url,
            additional_headers=headers,
            open_timeout=args.timeout,
            close_timeout=args.timeout,
            max_size=None,
        ) as websocket:
            await websocket.send(json.dumps({"text": " "}))
            if args.init_delay_ms:
                await asyncio.sleep(args.init_delay_ms / 1000)

            start = time.perf_counter()
            await websocket.send(json.dumps({"text": text, "flush": True}))
            await websocket.send(json.dumps({"text": ""}))

            while True:
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=args.timeout)
                except asyncio.TimeoutError:
                    result.error = f"Timed out after {args.timeout}s waiting for audio."
                    break
                except websockets.ConnectionClosed:
                    break

                elapsed_ms = (time.perf_counter() - start) * 1000
                if isinstance(raw_message, bytes):
                    audio = raw_message
                    message = {"audio": None, "isFinal": False}
                else:
                    message = json.loads(raw_message)
                    audio_value = message.get("audio")
                    audio = base64.b64decode(audio_value) if audio_value else b""

                if args.show_stream:
                    print(json.dumps(message, ensure_ascii=False))

                if message.get("error"):
                    result.error = str(message["error"])
                    break

                if audio:
                    if result.first_audio_ms is None:
                        result.first_audio_ms = elapsed_ms
                    result.final_audio_ms = elapsed_ms
                    result.audio_chunks += 1
                    result.audio_bytes += len(audio)

                if message.get("isFinal") is True:
                    result.final_seen = True
                    break

            result.audio_duration_ms = compute_audio_duration_ms(
                result.audio_bytes,
                args.audio_format,
                args.sample_rate,
            )
            if result.final_audio_ms is not None and result.audio_duration_ms:
                result.rtf = result.final_audio_ms / result.audio_duration_ms
            result.success = result.first_audio_ms is not None and result.error is None
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"

    return result


async def run_once(
    args: argparse.Namespace,
    api_key: str,
    voice: str,
    text: str,
    prompt_index: int,
    run_index: int,
) -> RunResult:
    if interface_for_voice(voice) == "rest":
        return await asyncio.to_thread(run_once_rest, args, api_key, voice, text, prompt_index, run_index)
    return await run_once_websocket(args, api_key, voice, text, prompt_index, run_index)


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    api_key = args.api_key or os.getenv("TELNYX_API_KEY")
    if not api_key:
        raise RuntimeError("Set TELNYX_API_KEY or pass --api-key.")

    prompts = load_prompts(args)
    results: list[RunResult] = []

    for voice in args.voice:
        for prompt_index, text in enumerate(prompts, start=1):
            for run_index in range(1, args.runs + 1):
                print(f"Running {voice} prompt={prompt_index} run={run_index}")
                results.append(await run_once(args, api_key, voice, text, prompt_index, run_index))

    summary: dict[str, Any] = {}
    for voice in args.voice:
        voice_results = [item for item in results if item.voice == voice]
        ok = [item for item in voice_results if item.success]
        first_audio = [item.first_audio_ms for item in ok if item.first_audio_ms is not None]
        final_audio = [item.final_audio_ms for item in ok if item.final_audio_ms is not None]
        rtf_values = [item.rtf for item in ok if item.rtf is not None]
        summary[voice] = {
            "interface": interface_for_voice(voice),
            "runs": len(voice_results),
            "successes": len(ok),
            "errors": len(voice_results) - len(ok),
            "first_audio_p50_ms": rounded(percentile(first_audio, 50)),
            "first_audio_p95_ms": rounded(percentile(first_audio, 95)),
            "final_audio_p50_ms": rounded(percentile(final_audio, 50)),
            "final_audio_p95_ms": rounded(percentile(final_audio, 95)),
            "rtf_p50": rounded(percentile(rtf_values, 50)),
            "audio_chunks_median": rounded(statistics.median([item.audio_chunks for item in ok])) if ok else None,
        }

    return {
        "metadata": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "endpoint": args.endpoint,
            "rest_endpoint": args.rest_endpoint,
            "audio_format": args.audio_format,
            "sample_rate": args.sample_rate,
            "runs_per_prompt": args.runs,
            "prompts": prompts,
            "voices": args.voice,
            "metric_definitions": {
                "first_audio_ms": "Time from sending the benchmark text frame to receiving the first audio bytes.",
                "final_audio_ms": "Time from sending the benchmark text frame or REST request to the last observed audio bytes.",
                "audio_duration_ms": "Estimated generated audio duration for linear16 output.",
                "rtf": "Final audio latency divided by generated audio duration. Lower is better.",
            },
        },
        "summary": summary,
        "results": [asdict(item) for item in results],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Telnyx TTS latency.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--rest-endpoint", default=DEFAULT_REST_ENDPOINT)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--voice", action="append", default=[])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--text", action="append")
    parser.add_argument("--prompt-file", default=None)
    parser.add_argument("--audio-format", default=DEFAULT_AUDIO_FORMAT)
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--init-delay-ms", type=int, default=50)
    parser.add_argument("--json-out", default="reports/tts-benchmark-results.json")
    parser.add_argument("--json", action="store_true", help="Print full JSON results to stdout.")
    parser.add_argument("--show-stream", action="store_true", help="Print raw server JSON messages.")
    args = parser.parse_args()
    if not args.voice:
        args.voice = DEFAULT_VOICES
    return args


def main() -> None:
    args = parse_args()
    output = asyncio.run(run_benchmark(args))
    output_path = Path(args.json_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2))

    print("\nSummary")
    for voice, row in output["summary"].items():
        print(
            f"{voice}: first audio p50={row['first_audio_p50_ms']} ms, "
            f"first audio p95={row['first_audio_p95_ms']} ms, "
            f"final audio p50={row['final_audio_p50_ms']} ms, "
            f"rtf p50={row['rtf_p50']}, success={row['successes']}/{row['runs']}"
        )
    print(f"\nWrote {output_path}")
    if args.json:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
