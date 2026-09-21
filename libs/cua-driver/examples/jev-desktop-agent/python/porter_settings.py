from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot

from jev_adapter import OPENROUTER_MODEL
from global_shortcuts import GlobalShortcutConfig
from openrouter_client import DEFAULT_REASONING_MODEL, DEFAULT_STT_MODEL
from porter_voice import HandsFreeVoiceConfig
from runtime import PorterRuntimeConfig


@dataclass(frozen=True)
class PorterAppSettings:
    provider: str = "auto"
    jev_model: str = OPENROUTER_MODEL
    vision_enabled: bool = True
    vision_model: str = DEFAULT_REASONING_MODEL
    writer_model: str = DEFAULT_REASONING_MODEL
    stt_model: str = DEFAULT_STT_MODEL
    voice_language: str = ""
    voice_silence: float = 0.55
    hands_free: bool = True
    microphone_device: int | None = None
    download_root: str = ""
    confirm_actions: bool = False
    allow_foreground: bool = True
    visual_click_mode: str = "strict"
    start_at_login: bool = False
    global_shortcut_enabled: bool = True
    global_shortcut_trigger: str = "CTRL+ALT+space"
    preferred_name: str = ""
    accent_color: str = "#49A7FF"
    compact_idle_opacity: float = 0.26
    compact_hover_opacity: float = 0.72
    animations_enabled: bool = True
    onboarding_complete: bool = False
    max_steps: int = 30
    max_candidates: int = 32

    def runtime_config(self) -> PorterRuntimeConfig:
        return PorterRuntimeConfig(
            provider=self.provider,
            jev_model=self.jev_model.strip() or None,
            vision_enabled=self.vision_enabled,
            vision_model=self.vision_model.strip() or DEFAULT_REASONING_MODEL,
            writer_model=self.writer_model.strip() or DEFAULT_REASONING_MODEL,
            max_steps=max(1, int(self.max_steps)),
            max_candidates=max(4, min(32, int(self.max_candidates))),
            download_root=self.download_root.strip() or None,
            enforce_policy=self.confirm_actions,
            allow_foreground=self.allow_foreground,
            visual_click_mode=self.visual_click_mode,
        )

    def voice_config(self) -> HandsFreeVoiceConfig:
        return HandsFreeVoiceConfig(
            enabled=self.hands_free,
            stt_model=self.stt_model.strip() or DEFAULT_STT_MODEL,
            language=self.voice_language.strip() or None,
            microphone_device=self.microphone_device,
            silence_seconds=max(0.1, min(10.0, float(self.voice_silence))),
        )

    def shortcut_config(self) -> GlobalShortcutConfig:
        return GlobalShortcutConfig(
            enabled=self.global_shortcut_enabled,
            preferred_trigger=(
                self.global_shortcut_trigger.strip()
                or "CTRL+ALT+space"
            ),
        )


class PorterSettingsStore:
    """Persistent non-secret application settings backed by QSettings."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings()

    @staticmethod
    def _bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if value in {None, "", -1, "-1"}:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def load(self) -> PorterAppSettings:
        s = self.settings
        visual = str(s.value("computer/visual_click_mode", "strict"))
        if visual not in {"strict", "permissive"}:
            visual = "strict"
        provider = str(s.value("models/provider", "auto"))
        if provider not in {"auto", "openrouter", "typesafe"}:
            provider = "auto"

        return PorterAppSettings(
            provider=provider,
            jev_model=str(s.value("models/jev_model", OPENROUTER_MODEL)),
            vision_enabled=self._bool(
                s.value("models/vision_enabled", True),
                True,
            ),
            vision_model=str(
                s.value("models/vision_model", DEFAULT_REASONING_MODEL)
            ),
            writer_model=str(
                s.value("models/writer_model", DEFAULT_REASONING_MODEL)
            ),
            stt_model=str(s.value("voice/stt_model", DEFAULT_STT_MODEL)),
            voice_language=str(s.value("voice/language", "")),
            voice_silence=float(s.value("voice/silence", 0.55)),
            hands_free=self._bool(s.value("voice/hands_free", True), True),
            microphone_device=self._int_or_none(
                s.value("voice/microphone_device", None)
            ),
            download_root=str(s.value("computer/download_root", "")),
            confirm_actions=self._bool(
                s.value("computer/confirm_actions", False),
                False,
            ),
            allow_foreground=self._bool(
                s.value("computer/allow_foreground", True),
                True,
            ),
            visual_click_mode=visual,
            start_at_login=self._bool(
                s.value("general/start_at_login", False),
                False,
            ),
            global_shortcut_enabled=self._bool(
                s.value("shortcuts/enabled", True),
                True,
            ),
            global_shortcut_trigger=str(
                s.value("shortcuts/preferred_trigger", "CTRL+ALT+space")
            ),
            preferred_name=str(
                s.value("personalization/preferred_name", "")
            ),
            accent_color=str(
                s.value("appearance/accent_color", "#49A7FF")
            ),
            compact_idle_opacity=float(
                s.value("appearance/compact_idle_opacity", 0.26)
            ),
            compact_hover_opacity=float(
                s.value("appearance/compact_hover_opacity", 0.72)
            ),
            animations_enabled=self._bool(
                s.value("appearance/animations_enabled", True),
                True,
            ),
            onboarding_complete=self._bool(
                s.value("general/onboarding_complete", False),
                False,
            ),
            max_steps=int(s.value("advanced/max_steps", 30)),
            max_candidates=int(s.value("advanced/max_candidates", 32)),
        )

    def save(self, value: PorterAppSettings) -> None:
        s = self.settings
        s.setValue("models/provider", value.provider)
        s.setValue("models/jev_model", value.jev_model)
        s.setValue("models/vision_enabled", value.vision_enabled)
        s.setValue("models/vision_model", value.vision_model)
        s.setValue("models/writer_model", value.writer_model)
        s.setValue("voice/stt_model", value.stt_model)
        s.setValue("voice/language", value.voice_language)
        s.setValue("voice/silence", value.voice_silence)
        s.setValue("voice/hands_free", value.hands_free)
        s.setValue(
            "voice/microphone_device",
            -1 if value.microphone_device is None else value.microphone_device,
        )
        s.setValue("computer/download_root", value.download_root)
        s.setValue("computer/confirm_actions", value.confirm_actions)
        s.setValue("computer/allow_foreground", value.allow_foreground)
        s.setValue("computer/visual_click_mode", value.visual_click_mode)
        s.setValue("general/start_at_login", value.start_at_login)
        s.setValue("shortcuts/enabled", value.global_shortcut_enabled)
        s.setValue(
            "shortcuts/preferred_trigger",
            value.global_shortcut_trigger,
        )
        s.setValue(
            "personalization/preferred_name",
            value.preferred_name,
        )
        s.setValue("appearance/accent_color", value.accent_color)
        s.setValue(
            "appearance/compact_idle_opacity",
            value.compact_idle_opacity,
        )
        s.setValue(
            "appearance/compact_hover_opacity",
            value.compact_hover_opacity,
        )
        s.setValue(
            "appearance/animations_enabled",
            value.animations_enabled,
        )
        s.setValue(
            "general/onboarding_complete",
            value.onboarding_complete,
        )
        s.setValue("advanced/max_steps", value.max_steps)
        s.setValue("advanced/max_candidates", value.max_candidates)
        s.sync()


class SecretStore:
    """Porter credentials stored through the OS keyring, never QSettings."""

    SERVICE = "Porter"
    OPENROUTER = "OPENROUTER_API_KEY"
    TYPESAFE = "TYPESAFE_API_KEY"

    def __init__(self, backend=None) -> None:
        self._backend = backend

    def _module(self):
        if self._backend is not None:
            return self._backend
        import keyring

        return keyring

    def get(self, name: str) -> str | None:
        try:
            value = self._module().get_password(self.SERVICE, name)
        except Exception:
            return None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def set(self, name: str, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("credential must not be empty")
        self._module().set_password(self.SERVICE, name, cleaned)

    def delete(self, name: str) -> None:
        try:
            self._module().delete_password(self.SERVICE, name)
        except Exception:
            pass

    def is_set(self, name: str) -> bool:
        return bool(os.getenv(name, "").strip() or self.get(name))

    def apply_to_environment(self, *, overwrite: bool = False) -> None:
        for name in (self.OPENROUTER, self.TYPESAFE):
            if not overwrite and os.getenv(name, "").strip():
                continue
            value = self.get(name)
            if value:
                os.environ[name] = value


class AutostartManager:
    """XDG desktop-session autostart for the native Porter app."""

    def __init__(
        self,
        command: list[str],
        *,
        path: Path | None = None,
    ) -> None:
        self.command = tuple(command)
        self.path = path or (
            Path.home() / ".config" / "autostart" / "porter.desktop"
        )

    @staticmethod
    def _exec_line(command: tuple[str, ...]) -> str:
        # Desktop Entry Exec parsing is not shell parsing. Quote every argument
        # using the double-quote/backslash rules from the desktop-entry spec.
        def quote(value: str) -> str:
            escaped = (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("$", "\\$")
                .replace("`", "\\`")
            )
            return '"' + escaped + '"'

        return " ".join(quote(part) for part in command)

    def enabled(self) -> bool:
        return self.path.is_file()

    def set_enabled(self, enabled: bool) -> None:
        if not enabled:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Version=1.0",
                    "Name=Porter",
                    "Comment=Resident desktop intelligence",
                    f"Exec={self._exec_line(self.command)}",
                    "Terminal=false",
                    "X-GNOME-Autostart-enabled=true",
                    "",
                ]
            ),
            encoding="utf-8",
        )


class PorterSettingsModel(QObject):
    settingsChanged = Signal()
    credentialsChanged = Signal()
    dirtyChanged = Signal()
    applyStatusChanged = Signal()

    def __init__(
        self,
        worker,
        *,
        store: PorterSettingsStore,
        secrets: SecretStore,
        autostart: AutostartManager,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._store = store
        self._secrets = secrets
        self._autostart = autostart
        self._saved = store.load()
        self._draft = self._saved
        self._credentials_changed = False
        self._dirty = False
        self._apply_status = ""
        self._pending_apply = False

        worker.reconfigured.connect(self._on_reconfigured)
        worker.reconfigureFailed.connect(self._on_reconfigure_failed)

    def _change(self, **values: Any) -> None:
        updated = replace(self._draft, **values)
        if updated == self._draft:
            return
        self._draft = updated
        self.settingsChanged.emit()
        dirty = updated != self._saved or self._credentials_changed
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirtyChanged.emit()

    def _set_apply_status(self, value: str) -> None:
        if value == self._apply_status:
            return
        self._apply_status = value
        self.applyStatusChanged.emit()

    @Property(str, notify=settingsChanged)
    def provider(self) -> str:
        return self._draft.provider

    @Property(str, notify=settingsChanged)
    def jevModel(self) -> str:
        return self._draft.jev_model

    @Property(bool, notify=settingsChanged)
    def visionEnabled(self) -> bool:
        return self._draft.vision_enabled

    @Property(str, notify=settingsChanged)
    def visionModel(self) -> str:
        return self._draft.vision_model

    @Property(str, notify=settingsChanged)
    def writerModel(self) -> str:
        return self._draft.writer_model

    @Property(str, notify=settingsChanged)
    def sttModel(self) -> str:
        return self._draft.stt_model

    @Property(str, notify=settingsChanged)
    def voiceLanguage(self) -> str:
        return self._draft.voice_language

    @Property(float, notify=settingsChanged)
    def voiceSilence(self) -> float:
        return self._draft.voice_silence

    @Property(bool, notify=settingsChanged)
    def handsFree(self) -> bool:
        return self._draft.hands_free

    @Property(str, notify=settingsChanged)
    def downloadRoot(self) -> str:
        return self._draft.download_root

    @Property(bool, notify=settingsChanged)
    def confirmActions(self) -> bool:
        return self._draft.confirm_actions

    @Property(bool, notify=settingsChanged)
    def allowForeground(self) -> bool:
        return self._draft.allow_foreground

    @Property(str, notify=settingsChanged)
    def visualClickMode(self) -> str:
        return self._draft.visual_click_mode

    @Property(bool, notify=settingsChanged)
    def startAtLogin(self) -> bool:
        return self._draft.start_at_login

    @Property(bool, notify=settingsChanged)
    def globalShortcutEnabled(self) -> bool:
        return self._draft.global_shortcut_enabled

    @Property(str, notify=settingsChanged)
    def globalShortcutTrigger(self) -> str:
        return self._draft.global_shortcut_trigger

    @Property(str, notify=settingsChanged)
    def preferredName(self) -> str:
        return self._draft.preferred_name

    @Property(str, notify=settingsChanged)
    def accentColor(self) -> str:
        return self._draft.accent_color

    @Property(float, notify=settingsChanged)
    def compactIdleOpacity(self) -> float:
        return self._draft.compact_idle_opacity

    @Property(float, notify=settingsChanged)
    def compactHoverOpacity(self) -> float:
        return self._draft.compact_hover_opacity

    @Property(bool, notify=settingsChanged)
    def animationsEnabled(self) -> bool:
        return self._draft.animations_enabled

    @Property(int, notify=settingsChanged)
    def maxSteps(self) -> int:
        return self._draft.max_steps

    @Property(int, notify=settingsChanged)
    def maxCandidates(self) -> int:
        return self._draft.max_candidates

    @Property(bool, notify=settingsChanged)
    def onboardingComplete(self) -> bool:
        return self._saved.onboarding_complete

    @Property(bool, notify=settingsChanged)
    def needsOnboarding(self) -> bool:
        # First-run completion is a UI preference, not a live credential probe.
        # Once the user finishes setup, keep it finished across launches even if
        # a provider is temporarily unavailable or the keyring is locked.
        return not self._saved.onboarding_complete

    @Property(bool, notify=credentialsChanged)
    def openRouterConfigured(self) -> bool:
        return self._secrets.is_set(SecretStore.OPENROUTER)

    @Property(bool, notify=credentialsChanged)
    def typeSafeConfigured(self) -> bool:
        return self._secrets.is_set(SecretStore.TYPESAFE)

    @Property(bool, notify=dirtyChanged)
    def dirty(self) -> bool:
        return self._dirty

    @Property(str, notify=applyStatusChanged)
    def applyStatus(self) -> str:
        return self._apply_status

    @Slot(str)
    def setProvider(self, value: str) -> None:
        if value in {"auto", "openrouter", "typesafe"}:
            self._change(provider=value)

    @Slot(str)
    def setJevModel(self, value: str) -> None:
        self._change(jev_model=value)

    @Slot(bool)
    def setVisionEnabled(self, value: bool) -> None:
        self._change(vision_enabled=bool(value))

    @Slot(str)
    def setVisionModel(self, value: str) -> None:
        self._change(vision_model=value)

    @Slot(str)
    def setWriterModel(self, value: str) -> None:
        self._change(writer_model=value)

    @Slot(str)
    def setSttModel(self, value: str) -> None:
        self._change(stt_model=value)

    @Slot(str)
    def setVoiceLanguage(self, value: str) -> None:
        self._change(voice_language=value)

    @Slot(float)
    def setVoiceSilence(self, value: float) -> None:
        self._change(voice_silence=max(0.1, min(10.0, float(value))))

    @Slot(bool)
    def setHandsFree(self, value: bool) -> None:
        self._change(hands_free=bool(value))

    @Slot(str)
    def setDownloadRoot(self, value: str) -> None:
        self._change(download_root=value)

    @Slot(bool)
    def setConfirmActions(self, value: bool) -> None:
        self._change(confirm_actions=bool(value))

    @Slot(bool)
    def setAllowForeground(self, value: bool) -> None:
        self._change(allow_foreground=bool(value))

    @Slot(str)
    def setVisualClickMode(self, value: str) -> None:
        if value in {"strict", "permissive"}:
            self._change(visual_click_mode=value)

    @Slot(bool)
    def setStartAtLogin(self, value: bool) -> None:
        self._change(start_at_login=bool(value))

    @Slot(bool)
    def setGlobalShortcutEnabled(self, value: bool) -> None:
        self._change(global_shortcut_enabled=bool(value))

    @Slot(str)
    def setGlobalShortcutTrigger(self, value: str) -> None:
        cleaned = value.strip()
        if cleaned:
            self._change(global_shortcut_trigger=cleaned)

    @Slot(str)
    def setPreferredName(self, value: str) -> None:
        self._change(preferred_name=value.strip()[:80])

    @Slot(str)
    def setAccentColor(self, value: str) -> None:
        cleaned = value.strip()
        if (
            len(cleaned) == 7
            and cleaned.startswith("#")
            and all(
                char in "0123456789abcdefABCDEF"
                for char in cleaned[1:]
            )
        ):
            self._change(accent_color=cleaned.upper())

    @Slot(float)
    def setCompactIdleOpacity(self, value: float) -> None:
        self._change(
            compact_idle_opacity=max(0.08, min(0.60, float(value)))
        )

    @Slot(float)
    def setCompactHoverOpacity(self, value: float) -> None:
        self._change(
            compact_hover_opacity=max(0.40, min(0.98, float(value)))
        )

    @Slot(bool)
    def setAnimationsEnabled(self, value: bool) -> None:
        self._change(animations_enabled=bool(value))

    @Slot(bool)
    def setOnboardingComplete(self, value: bool) -> None:
        value = bool(value)
        self._draft = replace(
            self._draft,
            onboarding_complete=value,
        )
        try:
            self._store.settings.setValue(
                "general/onboarding_complete",
                value,
            )
            self._store.settings.sync()
        except Exception as error:
            self._set_apply_status(
                f"Could not save setup state: {error}"
            )
            self.settingsChanged.emit()
            return

        self._saved = replace(
            self._saved,
            onboarding_complete=value,
        )
        dirty = (
            self._draft != self._saved
            or self._credentials_changed
        )
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirtyChanged.emit()
        self.settingsChanged.emit()

    @Slot(int)
    def setMaxSteps(self, value: int) -> None:
        self._change(max_steps=max(1, min(200, int(value))))

    @Slot(int)
    def setMaxCandidates(self, value: int) -> None:
        self._change(max_candidates=max(4, min(32, int(value))))

    def _save_secret(self, name: str, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            self._set_apply_status("Enter a non-empty API key.")
            return
        try:
            self._secrets.set(name, cleaned)
        except Exception as error:
            self._set_apply_status(f"Could not store credential: {error}")
            return
        os.environ[name] = cleaned
        self._credentials_changed = True
        if not self._dirty:
            self._dirty = True
            self.dirtyChanged.emit()
        self.credentialsChanged.emit()
        self.settingsChanged.emit()
        self._set_apply_status(
            "Credential stored securely. Apply to restart the backend with it."
        )

    @Slot(str)
    def saveOpenRouterKey(self, value: str) -> None:
        self._save_secret(SecretStore.OPENROUTER, value)

    @Slot(str)
    def saveTypeSafeKey(self, value: str) -> None:
        self._save_secret(SecretStore.TYPESAFE, value)

    @Slot()
    def clearOpenRouterKey(self) -> None:
        self._secrets.delete(SecretStore.OPENROUTER)
        os.environ.pop(SecretStore.OPENROUTER, None)
        self._credentials_changed = True
        if not self._dirty:
            self._dirty = True
            self.dirtyChanged.emit()
        self.credentialsChanged.emit()
        self.settingsChanged.emit()
        self._set_apply_status(
            "OpenRouter credential cleared. Apply to restart the backend."
        )

    @Slot()
    def clearTypeSafeKey(self) -> None:
        self._secrets.delete(SecretStore.TYPESAFE)
        os.environ.pop(SecretStore.TYPESAFE, None)
        self._credentials_changed = True
        if not self._dirty:
            self._dirty = True
            self.dirtyChanged.emit()
        self.credentialsChanged.emit()
        self.settingsChanged.emit()
        self._set_apply_status(
            "TypeSafe credential cleared. Apply to restart the backend."
        )

    @Slot()
    def revert(self) -> None:
        self._draft = self._saved
        dirty = self._credentials_changed
        changed = dirty != self._dirty
        self._dirty = dirty
        self.settingsChanged.emit()
        if changed:
            self.dirtyChanged.emit()
        self._set_apply_status(
            "Non-secret changes reverted."
            if self._credentials_changed
            else "Changes reverted."
        )

    def _persist_current(self, success_message: str) -> bool:
        try:
            self._store.save(self._draft)
            self._autostart.set_enabled(self._draft.start_at_login)
        except Exception as error:
            self._set_apply_status(f"Could not save settings: {error}")
            return False
        self._saved = self._draft
        self._credentials_changed = False
        self.settingsChanged.emit()
        if self._dirty:
            self._dirty = False
            self.dirtyChanged.emit()
        self._set_apply_status(success_message)
        return True

    @Slot()
    def apply(self) -> None:
        if self._pending_apply:
            return

        backend_changed = (
            self._credentials_changed
            or self._draft.runtime_config() != self._saved.runtime_config()
            or self._draft.voice_config() != self._saved.voice_config()
            or self._draft.shortcut_config() != self._saved.shortcut_config()
        )
        if not backend_changed:
            self._persist_current("Settings saved.")
            return

        self._set_apply_status("Applying settings…")
        # Set this before scheduling. The worker can complete quickly enough to
        # emit reconfigured before reconfigure() returns.
        self._pending_apply = True
        future = self._worker.reconfigure(
            self._draft.runtime_config(),
            self._draft.voice_config(),
            self._draft.shortcut_config(),
        )
        if future is None:
            self._pending_apply = False
            self._set_apply_status("Porter runtime is not ready.")
            return

    @Slot()
    def _on_reconfigured(self) -> None:
        if not self._pending_apply:
            return
        self._pending_apply = False
        if not self._persist_current("Settings applied."):
            self._set_apply_status(
                "Runtime updated, but settings could not be saved."
            )

    @Slot(str)
    def _on_reconfigure_failed(self, message: str) -> None:
        if not self._pending_apply:
            return
        self._pending_apply = False
        self._set_apply_status(f"Could not apply settings: {message}")
