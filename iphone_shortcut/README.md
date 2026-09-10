# Zaki iPhone Shortcut

Per spec §3.2, this is a **native Apple Shortcut, no custom code on-device**
— built by hand in the Shortcuts app. I can't generate or install a
`.shortcut` file for you (there's no way for me to verify a hand-crafted
binary plist actually imports and runs on a real device), so this is a
precise build guide instead, plus the backend contract it talks to.

## API Contract: `POST /api/assistant/voice`

One call does the whole round-trip — transcribe (if audio) → reason →
synthesize — so the Shortcut itself stays to ~4 actions.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `text` | string | one of `text`/`audio` | Already-dictated text (iOS's own Dictate Text action) |
| `audio` | file | one of `text`/`audio` | Raw recording (m4a/wav/etc.) — transcribed server-side via OpenAI |
| `session_id` | string | no, default `"default"` | Use a fixed value like `iphone` to keep a separate conversation thread from the Windows client |
| `voice` | string | no | Overrides the default TTS voice (`ar-EG-ShakirNeural`) for this call |

Header: `X-API-Key: <your ZAKI_API_KEY>`

**Response**

- Body: `audio/mpeg` — the spoken reply, ready to play directly
- Headers:
  - `X-Zaki-Reply-B64` — the reply text, **base64-encoded** (HTTP headers aren't UTF-8-safe, so raw Arabic can't go in a header directly)
  - `X-Zaki-Route` — `gemini` or `claude`
  - `X-Zaki-Model` — exact model ID used

Verified live 2026-09-08: the text-dictation path (200, real audio + correctly round-tripping Arabic reply text) works end-to-end. The audio-upload path is wired identically but needs `OPENAI_API_KEY` set on the backend to actually transcribe — not yet configured, so not yet live-verified; the graceful "not configured" error path is confirmed instead.

## Before you build the Shortcut: reachability

Your iPhone can't reach `127.0.0.1:8000` — that's *the PC's own loopback
address*, not visible to any other device. Pick one:

- **Quick local test (same Wi-Fi)**: run the backend bound to your LAN, e.g.
  `uvicorn zaki.main:app --host 0.0.0.0 --port 8000`, allow port 8000 through
  Windows Firewall, then use your PC's local IP in the Shortcut
  (`http://192.168.x.x:8000` — find it via `ipconfig`). Only works while
  both devices are on the same network and the PC is running the server.
- **Real deployment**: per the spec, the backend is meant to run on Vercel
  eventually — that hasn't happened yet in this build (still local-only).
  Once deployed, swap in the real HTTPS URL and the Shortcut works from
  anywhere, no LAN dependency. Worth doing as a follow-up beyond Step 6.

## Building the Shortcut

Open the **Shortcuts** app → **+** (new shortcut) → rename it "Zaki".

1. **Dictate Text**
   Add the action. Set language to Arabic if prompted (or leave
   auto-detect). This gives you a `Dictated Text` variable for later steps.

2. **Get Contents of URL**
   - URL: `http://<your-backend>/api/assistant/voice`
   - Method: **POST**
   - Headers: add `X-API-Key` → your `ZAKI_API_KEY` value
   - Request Body: **Form**
     - `text` → the `Dictated Text` variable from step 1
     - `session_id` → `iphone` (literal text)

   This action's own output/result *is* the audio data (the response
   body) — keep that result for step 4.

3. **Get Details of Web Response** (sometimes shown as part of "Get
   Contents of URL"'s advanced options in newer iOS versions — if you
   don't find it as a separate action, look under the Web category)
   - Get: **Headers**
   - Then **Get Dictionary Value** for key `X-Zaki-Reply-B64`
   - Then **Base64 Decode** (Shortcuts' text/encoding actions — search
     "base64" in the action library if the exact name differs on your iOS
     version) to get the plain reply text back, if you want to show it.

4. **Play Sound**
   - Input: the result from step 2 (the `Get Contents of URL` output)

   Optional: add **Show Notification** with the decoded text from step 3
   for a visual glance alongside the spoken reply.

## Attaching it for quick access

Per spec: tap-to-trigger, no wake word (not feasible for third-party apps
on iOS without Siri as the trigger layer). Either:
- Long-press the Shortcut → **Add to Home Screen**, or
- iPhone 15 Pro/16/17 with an Action Button: **Settings → Action Button →
  Shortcut →** select "Zaki"

## Known limitations (this step)

- Local-only backend means the Shortcut only works on the same Wi-Fi as
  the PC running it, until real deployment happens (see reachability
  section above).
- Audio-upload path (recording instead of dictating) needs
  `OPENAI_API_KEY` set on the backend — not yet configured/verified live.
  Dictation-based text input needs no such key and is the recommended
  default (it's also simpler to build).
- Exact Shortcuts action names/UI can shift slightly between iOS versions
  — if a step doesn't match exactly, search the action library for the
  closest equivalent (e.g. "base64", "dictionary value", "web response").
