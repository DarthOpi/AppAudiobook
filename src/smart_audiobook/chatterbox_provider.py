"""Optional local multilingual conditioning adapter; never downloads weights."""

from pathlib import Path
from threading import RLock

from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import ProviderCapabilities, SynthesisSettings, VoiceInfo, _validate_wav_path, _validate_generated_file


class ChatterboxTTSProvider:
    provider_id = "chatterbox"
    capabilities = ProviderCapabilities(supports_volume=True, supports_style=True,
        supports_voice_cloning=True, supports_gpu=True)

    def __init__(self, model_directory: Path, device="cpu", *, t3_model="v2", loader=None, writer=None):
        if device not in {"cpu", "cuda", "auto"} or t3_model not in {"v2", "v3"}:
            raise SpeechGenerationError("Chatterbox: device cpu/cuda/auto y modelo v2/v3.")
        self.directory = model_directory.resolve()
        if not self.directory.is_dir():
            raise SpeechGenerationError("Configura CHATTERBOX_MODEL_DIR con pesos locales. No se descargan automáticamente.")
        self.device = device
        self.t3_model = t3_model
        self.model_id = f"chatterbox-multilingual-{t3_model}:{self.directory.as_posix()}"
        self._loader, self._writer = loader, writer
        self._model = None
        self._default_conditionals = None
        self._lock = RLock()

    def list_voices(self):
        # Listing the catalog does not import torch or load a heavyweight model.
        return [VoiceInfo("chatterbox:default", "Chatterbox base · Spanish", "es", provider=self.provider_id,
            description="Identidad base del modelo; añade referencias propias para otras identidades.")]

    def synthesize(self, text, output_path, voice_id, settings=None):
        return self.synthesize_reference(text, output_path, voice_id, None, "es", settings)

    def synthesize_reference(self, text, output_path, voice_id, reference, language="es", settings=None):
        _validate_wav_path(output_path)
        settings = settings or SynthesisSettings()
        settings.validate()
        if voice_id != "chatterbox:default" or settings.speed != 1 or settings.pitch is not None or settings.style not in {None, "neutral", "expressive"}:
            raise SpeechGenerationError("Chatterbox: voz/ajustes no compatibles (sin speed/pitch; style neutral/expressive).")
        if reference and (not reference.is_file() or reference.suffix.casefold() != ".wav"):
            raise SpeechGenerationError("Referencia WAV local no disponible.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:  # Conditioning modifies the model: serialize preview and TTS.
            try:
                if self._model is None:
                    if self._loader:
                        self._model = self._loader(self.directory, self.device)
                    else:
                        import torch
                        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
                        device = "cuda" if self.device == "auto" and torch.cuda.is_available() else "cpu" if self.device == "auto" else self.device
                        options = {"t3_model": "v3"} if self.t3_model == "v3" else {}
                        self._model = ChatterboxMultilingualTTS.from_local(self.directory, device=device, **options)
                    self._default_conditionals = self._model.conds
                self._model.conds = self._default_conditionals
                audio = self._model.generate(text, language_id=language.replace("_", "-").split("-")[0],
                    audio_prompt_path=str(reference) if reference else None,
                    exaggeration=.8 if settings.style == "expressive" else .5)
                if self._writer:
                    self._writer(output_path, audio, self._model.sr)
                else:
                    import torchaudio
                    torchaudio.save(str(output_path), (audio * settings.volume).clamp(-1, 1).cpu(), self._model.sr)
            except ImportError as error:
                raise SpeechGenerationError("Instala el extra reference-tts en un entorno compatible (recomendado Python 3.11).") from error
            except Exception as error:
                raise SpeechGenerationError(f"Chatterbox local falló: {type(error).__name__}. Comprueba pesos, referencia y dispositivo.") from error
        _validate_generated_file(output_path)
        return output_path
