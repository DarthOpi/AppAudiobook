"""Engine voices and user-owned character profiles in one selectable catalog."""

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
from pathlib import Path

from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import SynthesisSettings, VoiceInfo


@dataclass(frozen=True, slots=True)
class CharacterVoiceProfile:
    id: str
    name: str
    provider: str
    base_voice_id: str
    language: str | None = None
    reference_audio: str | None = None
    gender: str | None = None
    strategy: str = "dedicated"
    settings: SynthesisSettings = field(default_factory=SynthesisSettings)
    metadata: dict = field(default_factory=dict)


def load_voice_profiles(path: Path) -> tuple[CharacterVoiceProfile, ...]:
    """References are relative to this local JSON; never obtained from uploads."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or len(payload) > 1000:
            raise ValueError("Expected a list of at most 1000 profiles")
        profiles = []
        for item in payload:
            data = dict(item)
            data["settings"] = SynthesisSettings(**data.get("settings", {}))
            if data.get("reference_audio"):
                data["reference_audio"] = str((path.parent / data["reference_audio"]).resolve())
            profiles.append(CharacterVoiceProfile(**data))
        return tuple(profiles)
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise SpeechGenerationError(f"Catálogo de perfiles inválido: {path.name}") from error


class ProfiledTTSProvider:
    """Route a profile to its engine without modifying speaker or audio logic.

    Profiles do not pretend to be additional identities unless the engine uses
    reference conditioning. IDs of engine voices remain unchanged.
    """

    def __init__(self, engine, profiles: tuple[CharacterVoiceProfile, ...]):
        self.engine = engine
        self.provider_id = engine.provider_id
        self.capabilities = engine.capabilities
        self.profiles = {p.id: p for p in profiles}
        self._reference_fingerprints: dict[str, tuple[tuple[int, int], str]] = {}
        voices = {v.id for v in engine.list_voices()}
        if len(self.profiles) != len(profiles) or voices.intersection(self.profiles):
            raise SpeechGenerationError("IDs de voz/perfil duplicados.")
        for p in profiles:
            p.settings.validate()
            if p.provider != self.provider_id or p.base_voice_id not in voices or p.strategy not in {"dedicated", "generic_pool"}:
                raise SpeechGenerationError(f"Proveedor, estrategia o voz base inválidos: {p.name}")
            if p.settings.speed != 1 and not self.capabilities.supports_speed:
                raise SpeechGenerationError(f"El motor no admite velocidad en {p.name}")
            if p.settings.pitch is not None and not self.capabilities.supports_pitch:
                raise SpeechGenerationError(f"El motor no admite pitch en {p.name}")
            if p.settings.style is not None and not self.capabilities.supports_style:
                raise SpeechGenerationError(f"El motor no admite style en {p.name}")
            if p.settings.volume != 1 and not self.capabilities.supports_volume:
                raise SpeechGenerationError(f"El motor no admite volume en {p.name}")
            if p.reference_audio:
                if p.metadata.get("consent_confirmed") is not True or not self.capabilities.supports_voice_cloning:
                    raise SpeechGenerationError("Reference audio requiere motor compatible y consent_confirmed=true.")
                reference = Path(p.reference_audio)
                if not reference.is_file() or reference.suffix.casefold() != ".wav" or reference.stat().st_size > 20_000_000:
                    raise SpeechGenerationError("Reference audio debe ser un WAV local de hasta 20 MB.")

    @property
    def model_id(self) -> str:
        # Content fingerprints invalidate segment AND chapter caches on ref edits.
        data = []
        for p in self.profiles.values():
            value = asdict(p)
            if p.reference_audio:
                reference = Path(p.reference_audio)
                stat = reference.stat()
                token = (stat.st_mtime_ns, stat.st_size)
                cached = self._reference_fingerprints.get(p.reference_audio)
                if not cached or cached[0] != token:
                    cached = (token, hashlib.sha256(reference.read_bytes()).hexdigest())
                    self._reference_fingerprints[p.reference_audio] = cached
                value["reference_sha256"] = cached[1]
            data.append(value)
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:20]
        return f"{self.engine.model_id}:profiles:{digest}"

    def list_voices(self) -> list[VoiceInfo]:
        return [*self.engine.list_voices(), *(VoiceInfo(p.id, p.name, p.language, p.gender,
            p.provider, "Reference profile" if p.reference_audio else "Base voice profile (same vocal identity)",
            reference_audio=p.reference_audio, strategy=p.strategy) for p in self.profiles.values())]

    def synthesize(self, text, output_path, voice_id, settings=None):
        profile = self.profiles.get(voice_id)
        if profile is None:
            return self.engine.synthesize(text, output_path, voice_id, settings)
        supplied = settings or SynthesisSettings()
        effective = replace(profile.settings, speed=profile.settings.speed * supplied.speed,
            volume=profile.settings.volume * supplied.volume,
            pitch=supplied.pitch if supplied.pitch is not None else profile.settings.pitch,
            style=supplied.style or profile.settings.style)
        effective.validate()
        if profile.reference_audio:
            return self.engine.synthesize_reference(text, output_path, profile.base_voice_id,
                Path(profile.reference_audio), profile.language or "es", effective)
        return self.engine.synthesize(text, output_path, profile.base_voice_id, effective)
