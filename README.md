# Telnyx TTS Latency Test

Benchmark multiple Telnyx TTS voices and generate a shareable PDF report.

This repo follows the same basic shape as the STT latency harness, but it uses TTS-specific metrics:

- `first_audio_ms`: time from sending text to receiving the first audio bytes
- `final_audio_ms`: time from sending text to the final observed audio chunk
- `audio_duration_ms`: estimated generated audio duration for `linear16`
- `rtf`: final audio latency divided by generated audio duration

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELNYX_API_KEY="YOUR_API_KEY"
```

## Run the benchmark

```bash
python3 run.py --runs 3 --json-out reports/tts-benchmark-results.json
```

Sample size is calculated as:

```text
voices × prompts × runs
```

The JSON output records `voice_count`, `prompt_count`, `runs_per_prompt`, `sample_size`, `concurrency`, and `region`.

Default voices:

- `Telnyx.NaturalHD.astra`
- `aws.Polly.Generative.Lucia`
- `azure.en-US-AvaMultilingualNeural`

The default output format is `mp3` because it works across the default multi-provider voice set. Use `--audio-format linear16` when benchmarking voices that support raw PCM and when you want `audio_duration_ms` and `rtf`.

Ultra voices are REST-only, so the runner automatically benchmarks `Telnyx.Ultra.*` voices through the REST endpoint while keeping WebSocket for the other providers.

Run a custom set of voices:

```bash
python3 run.py \
  --voice Telnyx.NaturalHD.astra \
  --voice aws.Polly.Generative.Lucia \
  --voice azure.en-US-AvaMultilingualNeural \
  --runs 5 \
  --concurrency 1 \
  --region "us-east client" \
  --audio-format mp3 \
  --json-out reports/custom-results.json
```

Use a custom prompt:

```bash
python3 run.py --text "Thanks for calling. How can I help?" --runs 5
```

Use a prompt file:

```bash
python3 run.py --prompt-file samples/prompts.json --runs 5
```

## Generate the report

```bash
python3 generate_report.py reports/tts-benchmark-results.json
```

This writes:

- `reports/tts-benchmark-report.html`
- `reports/tts-benchmark-report.pdf`

PDF export uses Microsoft Edge or Google Chrome in headless mode.

## Method

For each voice, prompt, and run:

1. Open a WebSocket to `wss://api.telnyx.com/v2/text-to-speech/speech`.
2. Send the required init frame: `{"text":" "}`.
3. Start the timer immediately before sending the prompt frame with `flush: true`.
4. Send `{"text":""}` to flush and close the sequence.
5. Measure time to first audio bytes and final audio bytes.
6. Estimate generated audio duration when output is `linear16`.

Keep prompt text, output format, sample rate, voice settings, client region, concurrency, endpoint, and credentials fixed when comparing runs.

## Useful flags

```bash
python3 run.py --help
python3 generate_report.py --help
```

## Notes

Telnyx Ultra is REST-only today, so it is not included in the default WebSocket benchmark. BYOK voices can require provider credentials and may fail unless your account is configured for that provider.
