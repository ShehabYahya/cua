# Porter v0.1.1

Porter is a native resident desktop assistant built on Cua Driver and Jev.

This release adds spoken replies:

- Porter can speak each completed reply through a local OpenAI-compatible
  `/audio/speech` endpoint instead of only showing text.
- Speech falls back to `spd-say`, `espeak-ng`, or `espeak` when the local
  endpoint or a WAV player is unavailable, so a missing service never breaks a
  command.
- Hands-free microphone capture pauses while Porter is speaking, so replies are
  never transcribed back as new commands.
- Voice & Audio gains a "Speak Porter replies" switch plus model, voice, and
  speech-language fields, persisted with the other Porter settings.

Everything from v0.1.0 remains: Aurora Dark native PySide6/Qt Quick interface,
resident runtime with tray presence, single-instance activation, hands-free
voice input, bounded Jev candidate selection, local operation bundles, secure
provider credentials, Cua Driver diagnostics and guided maintenance, native
`porter-v*` update checks, and the installable Debian package.

## Host requirements

Porter runs on the host desktop and uses Cua Driver for desktop authority.
GNOME Wayland users should use a current Cua Driver and the supported
window-geometry helper described in the repository documentation.

Spoken replies additionally expect a local speech service reachable at
`http://127.0.0.1:9393/v1` (OpenAI-compatible `POST /audio/speech`) and a WAV
player such as `aplay` or `pw-play`. Without them Porter falls back to the
system speech binaries.

## Privacy boundary

Passive microphone monitoring uses local speech detection. Silent room audio is
discarded locally; detected utterances are sent to the configured speech-to-text
provider only when transcription is required. Reply audio is synthesized by the
configured local endpoint and is not sent to a remote provider by Porter.
