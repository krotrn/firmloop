# FirmLoop

**An agentic AI system that programs an ESP32 from plain-English (or spoken) requests, tests its own code, and only flashes the board when the tests pass.**

You say: *"Turn on the fan when it's above 30 degrees and show the temperature on the screen."*
FirmLoop:

1. Turns the request into **acceptance scenarios** ("temp 30.1 → fan ON", "temp 30.0 → fan OFF") and shows you what it understood.
2. After you confirm, **generates tests** from those scenarios (plus boundary cases and every older test in the session).
3. Has an AI model **write the firmware logic** on top of a small, pre-tested helper library.
4. **Compiles and runs the logic on your PC** in a locked-down sandbox with a fake clock and fake sensors. If tests fail, the agent reads the failures and fixes the code (max 5 attempts).
5. Compiles for the ESP32, **flashes the real board once**, runs a hardware self-check and a short on-board test.
6. Saves the result as a **version** (instant rollback), and reads a summary aloud.

Follow-up requests (*"also beep when motion is detected"*) **modify the current program**; all older tests must keep passing.

> Single-board, single-user, runs locally.

---

## Features

- Natural-language → structured, confirmable spec → generated tests → AI-written code → self-correcting loop
- Native (PC) test execution in milliseconds, sandboxed with Docker + seccomp
- Real ESP32 flashing + hardware self-check + on-board verification
- **Virtual device panel**: the full system works with no hardware attached
- Version history, spec diffs, code diffs, one-click rollback
- Swappable AI models: Gemini (free tier), Ollama (local, offline), Anthropic Claude
- Voice input (browser speech recognition) and spoken status updates
- Plug-in hardware registry: add a sensor by adding one folder
- Benchmark suite with hidden reference tests (measures understanding and implementation separately)

---

## Requirements

| What | Version / note |
|---|---|
| Linux (developed on Arch) | Windows/macOS not supported for USB flashing |
| Docker Engine + Docker Compose v2 | `docker compose version` must work |
| Git | any recent |
| An AI provider | Gemini API key (free tier) **or** Ollama **or** Anthropic API key |
| *(optional)* NVIDIA GPU + `nvidia-container-toolkit` | only to run Ollama inside Docker with GPU |
| *(optional)* ESP32 DevKitC (WROOM-32) + parts | see [Hardware](#hardware). Not needed to start: use virtual mode |

Your user must be able to access serial ports for real-hardware mode:

```bash
sudo usermod -aG uucp "$USER"     # Arch (on Debian/Ubuntu the group is "dialout")
# log out and back in
```

---

## Setup

```bash
git clone https://github.com/krotrn/firmloop.git firmloop
cd firmloop
cp .env.example .env
# edit .env: set LLM_PROVIDER and the matching key / URL (see .env.example comments)

make build        # builds all Docker images (first build downloads the ESP32 toolchain, ~1 GB)
make doctor       # checks Docker, images, AI provider, toolchains, and (if plugged in) the board
```

`make doctor` prints one line per check with `OK` / `WARN` / `FAIL` and how to fix each failure.

### Using a local model (Ollama)

**Recommended:** run Ollama inside Docker with the GPU (needs `nvidia-container-toolkit`):

```bash
make up-ollama
docker compose exec ollama ollama pull qwen2.5-coder:7b
# in .env:
#   LLM_PROVIDER=ollama
#   OLLAMA_BASE_URL=http://ollama:11434
```

Alternative: Ollama installed on the host. By default it only listens on 127.0.0.1, which containers cannot reach. Make it listen on the Docker bridge address (never `0.0.0.0`):

```bash
ip addr show docker0                       # usually 172.17.0.1
sudo systemctl edit ollama                 # add:  [Service]
                                           #       Environment="OLLAMA_HOST=172.17.0.1:11434"
sudo systemctl restart ollama
ollama pull qwen2.5-coder:7b
# in .env:
#   LLM_PROVIDER=ollama
#   OLLAMA_BASE_URL=http://host.docker.internal:11434
```

Local models are slow on a 4 GB GPU; `.env` sets `OLLAMA_TIMEOUT_S=300` and `OLLAMA_NUM_CTX=8192` for this.

---

## Running

```bash
make up           # start everything (device mode from .env, default virtual)
make logs         # follow logs of all services
make down         # stop
```

Open **http://localhost:3000**.

For development with hot reload:

```bash
make dev          # runner, builder, device + backend (auto-reload) in Docker; frontend `pnpm dev` on the host
```

Needs Node 24 and pnpm on the host. Backend source changes reload the backend container; frontend changes hot-reload in the browser. Ctrl-C stops the frontend; `make down` stops the containers and `make up` goes back to the normal images.

1. Click **New session**.
2. Type or speak a request (mic button, Chrome/Edge only).
3. Check **"Here's what I understood"**, edit if needed, click **Confirm**.
4. Watch the loop: tests → code → native test → (fix) → build → flash → verify.
5. Play with the board, or with the sliders on the virtual panel.
6. Send follow-ups. Use the **Versions** tab to compare or roll back.

Device mode is chosen in **Settings** (`real` / `virtual`) or by `DEVICE_MODE` in `.env`. In `real` mode with no board plugged in, runs stop with "No board connected — plug it in or switch to virtual mode". There is no silent fallback to virtual.

You can keep several sessions. The board always runs the **last deployed** version from any session; the header shows which one.

---

## Testing

```bash
make test              # all tests: backend, services, firmware native harness, frontend
make test-backend      # pytest for backend
make test-firmware     # helper-library + harness self-tests in the runner image
make test-frontend     # vitest
make lint              # ruff + pyright + eslint + tsc
make bench PROVIDER=gemini     # run the benchmark suite against a provider
```

---

## Hardware

Total ≈ ₹1,800 - 2,500. Wiring is fixed and defined in `hardware/*/manifest.yaml`.

| Part | ESP32 pin | Channel id(s) |
|---|---|---|
| ESP32 DevKitC V4 (WROOM-32, auto-reset, CP2102 or CH340) | - | - |
| DHT22 temperature + humidity | GPIO 4 | `temp`, `humidity` |
| LDR + 10 kΩ divider | GPIO 34 (ADC1) | `light` |
| HC-SR501 PIR motion | GPIO 27 | `motion` |
| Push button (to GND, internal pull-up) | GPIO 19 | `button` |
| 1-channel 5 V relay module (opto-isolated) + small 5 V fan on separate supply | GPIO 26 | `fan` |
| Active buzzer | GPIO 25 | `buzzer` |
| LED + 220 Ω | GPIO 18 | `led` |
| SSD1306 0.96" OLED (I²C) | SDA 21, SCL 22 | `display` |

Safety: **never** switch mains voltage with the relay. Fan uses its own 5 V supply; ESP32 pins never power a motor.

Relay note: the ESP32 drives 3.3 V logic. Many cheap 5 V active-low relay modules do not switch **off** reliably from 3.3 V. Buy a module sold as "3.3 V trigger compatible" or one with a high/low trigger jumper, and test it in TASKS 7.0. Fallback if it misbehaves: a logic-level MOSFET module (e.g. AO3400/IRLZ44N based) to switch the 5 V fan. Same pin and channel, only the manifest's `active_low` changes.

---

## Folder structure

```
firmloop/
├── README.md               ← you are here
├── .env.example            ← all environment variables, documented
├── Makefile                ← every command you need
├── compose.yml
├── backend/                ← FastAPI app: agent loop, API, DB (Python)
├── runner/                 ← sandbox service: native compile + run tests
├── builder/                ← ESP32 compile service (arduino-cli)
├── device/                 ← USB serial service: flash, telemetry, injection
├── firmware/
│   ├── lib/FirmLoop/       ← helper library used by all generated code
│   ├── native/             ← PC fake of the library + test harness
│   └── template/           ← sketch skeleton that wraps generated logic
├── hardware/               ← one folder per component (manifest + driver + AI notes)
├── benchmark/tasks/        ← benchmark tasks with hidden reference tests
├── frontend/               ← Next.js dashboard
└── shared/                 ← generated JSON Schemas shared by backend + frontend
```

---
