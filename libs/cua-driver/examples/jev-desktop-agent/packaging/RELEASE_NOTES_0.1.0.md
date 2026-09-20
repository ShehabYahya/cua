# Porter v0.1.0

Porter is a native resident desktop assistant built on Cua Driver and Jev.

This first preview includes:

- Aurora Dark native PySide6/Qt Quick interface.
- Full main window plus a translucent compact command bar.
- Resident background runtime with tray/status presence.
- Single-instance app activation so repeated launches restore the resident Porter process.
- Hands-free microphone mode with local speech detection and automatic endpointing.
- Direct Jev action selection over bounded locally compiled candidates.
- Local multi-step operation bundles for common desktop/browser flows.
- Secure provider credentials through the OS keyring.
- OpenRouter / TypeSafe provider and model settings.
- Strict or permissive visual-click binding.
- XDG GlobalShortcuts portal integration for the quick bar.
- Persistent voice, control, personalization, and appearance settings.
- First-run model/Cua onboarding.
- Cua Driver health diagnostics, doctor, guided installation, and updater controls.
- Native Porter update checking against `porter-v*` GitHub releases.
- Installable Debian package with launcher, icon, AppStream metadata, autostart support, and locked dependencies.

## Host requirements

Porter runs on the host desktop and uses Cua Driver for desktop authority. GNOME Wayland users should use a current Cua Driver and the supported window-geometry helper described in the repository documentation.

## Privacy boundary

Passive microphone monitoring uses local speech detection. Silent room audio is discarded locally; detected utterances are sent to the configured speech-to-text provider only when transcription is required.
