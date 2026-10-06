# Smart Audiobook

Smart Audiobook es un proyecto incremental para convertir novelas y novelas web en audiolibros. La **V0.8** añade memoria de personajes por libro, aliases temporales, resolución contextual y revisión por prioridad sobre el TTS local reanudable de V0.7.

## Qué incluye V0.8

- Carga automática de `.txt`, `.pdf` y `.docx` mediante una interfaz común.
- Modelo interno independiente del formato de origen.
- Extracción de texto nativo en PDF; los PDF escaneados requieren OCR y se rechazan con un mensaje claro.
- Lectura de párrafos y estilos de encabezado en DOCX.
- Normalización conservadora que mantiene párrafos y diálogos con raya (`—`).
- Detección de capítulos orientada a novelas y novelas web.
- Pipeline anterior completo: segmentación, reglas, Gemini opcional, personajes, voces y TTS local.
- Un WAV por capítulo, un WAV completo y `metadata.json`.
- Nombres de archivo portables y seguros.
- Interfaz web responsive con templates HTML y CSS propio.
- Upload validado de TXT, PDF y DOCX con límite de 20 MiB.
- Estado temporal por UUID persistido en JSON, sin base de datos.
- Página de revisión con speakers editables, filtros y paginación.
- Creación, renombrado y fusión normalizada de personajes.
- Catálogo de voces desacoplado mediante `TTSProvider`.
- Selección manual y preview cacheado de voces.
- Generación final desde el análisis revisado, sin repetir Gemini.
- Página de resultado con capítulos, personajes, voces y estadísticas.
- Reproducción HTML5 y descarga controlada de cada WAV.
- Logging básico y eliminación inmediata de los uploads temporales.
- Piper local opcional, con catálogo multi-modelo y soporte multi-speaker.
- Caché persistente por contenido, voz, proveedor, modelo y ajustes.
- Chunking por frases, pausas configurables y concatenación WAV por streaming.
- Estado por capítulo, reanudación tras fallos y progreso web mediante polling.

No incluye OCR, EPUB, descarga de novelas web, frontend separado, base de datos, autenticación, Docker, biblioteca permanente ni procesamiento asíncrono avanzado.

## Flujo

```text
Upload
  ↓
Document Pipeline
  ↓
Speaker Analysis
  ↓
work/<processing_id>/analysis.json
  ↓
Review UI
  ├── Edit speakers
  ├── Manage characters
  └── Select and preview voices
  ↓
Final TTS generation
  ↓
WAV por capítulo + WAV completo + metadata.json
```

El formato se detecta por la extensión. Desde `Document` en adelante, el pipeline es el mismo para todos los orígenes.

## Requisitos

- Python 3.10 o posterior.
- Para Piper: uno o más modelos de voz compatibles bajo `voices/`.
- Opcional: `GEMINI_API_KEY` para resolver diálogos ambiguos.

Piper es el proveedor recomendado para novelas. El proveedor `system` de V0.6, basado en `pyttsx3`, sigue disponible como compatibilidad. Gemini solo recibe contexto limitado de diálogos que las reglas no pueden resolver.

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Para instalar la interfaz web:

```powershell
python -m pip install -e ".[web]"
```

Para instalar Piper junto con la web:

```powershell
python -m pip install -e ".[web,local-tts]"
```

Para desarrollo y tests web:

```powershell
python -m pip install -e ".[web,test]"
```

Para habilitar Gemini:

```powershell
python -m pip install -e ".[llm]"
Copy-Item .env.example .env
```

Después, añade la clave únicamente a `.env`:

```dotenv
GEMINI_API_KEY=tu_clave
```

`.env` está ignorado por Git. Si la variable no existe, Smart Audiobook sigue funcionando con reglas locales y marca los diálogos ambiguos como `Unknown`.

La integración utiliza el SDK oficial `google-genai` y el modelo estable
`gemini-3.5-flash-lite`, elegido por su baja latencia y coste. Gemini es solo
el fallback: primero se aplican las reglas locales y no se le envían los
diálogos que ya tienen una resolución fiable.

## Web interface

Arranca el servidor desde la raíz del proyecto:

```powershell
uvicorn smart_audiobook.web:app --reload
```

Después abre [http://127.0.0.1:8000](http://127.0.0.1:8000).

Flujo básico:

1. Selecciona un TXT, PDF o DOCX de hasta 20 MiB.
2. Pulsa **Analizar documento**.
3. Revisa speakers, resuelve los `Unknown`, crea o combina personajes y selecciona voces.
4. Usa **Preview** para escuchar una muestra corta de cada voz.
5. Pulsa **Generate audiobook** cuando el reparto esté listo.
6. Reproduce o descarga cada capítulo y el audiolibro completo.

El upload inicial se valida en una carpeta temporal. Después se crea un espacio de trabajo controlado, identificado exclusivamente mediante UUID:

```text
work/<processing_id>/
├── source/<documento_sanitizado>
├── analysis.json
├── previews/<hash_de_voz>.wav
└── audio/<nombre_libro>/
    ├── 01_capitulo.wav
    ├── full_audiobook.wav
    └── metadata.json
```

`analysis.json` contiene el análisis editable, los identificadores de segmento, confianza, método de resolución, catálogo de voces y asignaciones. Recargar o filtrar la página reutiliza este estado: no vuelve a extraer el documento ni a ejecutar Gemini. Los endpoints validan el UUID y nunca utilizan nombres proporcionados por el navegador como rutas internas.

## V0.6 – Character & Voice Review

### Corregir speakers

Cada diálogo muestra su ID, capítulo, confianza y método de detección. Selecciona otro personaje o `New character…` y pulsa **Guardar cambios visibles**. Los diálogos `Unknown` aparecen destacados y el filtro **Unknown only** permite revisarlos rápidamente.

La lista se pagina de 50 en 50 y puede filtrarse por personaje o capítulo. Los filtros no repiten el análisis.

### Gestionar personajes

- **Renombrar** actualiza todos los segmentos asociados.
- Si el nombre nuevo ya existe según su forma normalizada —ignorando mayúsculas, acentos y espacios triviales— ambos personajes se combinan.
- **Fusionar personajes** mueve todos los segmentos del origen al destino y conserva una asignación de voz válida.
- Los personajes creados desde un diálogo quedan disponibles para el resto del documento.

### Seleccionar y previsualizar voces

La barra lateral muestra las voces que ofrece el proveedor TTS local, incluyendo únicamente la metadata realmente disponible. Cada personaje puede elegir una voz distinta. **Preview** genera una frase corta en WAV y la conserva en caché por `processing_id` y voz.

La interfaz depende de `TTSProvider`, no de un SDK concreto. V0.7 ofrece `PiperTTSProvider` y conserva `SystemTTSProvider`; un proveedor nuevo puede incorporarse sin cambiar la revisión ni el procesador del libro.

### Generar el audio definitivo

Antes de generar se comprueba que existen voces, que `Narrator` tiene una y que todos los speakers utilizados tienen una selección válida. Los `Unknown` producen una advertencia y confirmación, pero no bloquean obligatoriamente la generación. El TTS recibe directamente el análisis revisado y no vuelve a invocar reglas ni Gemini.

## Uso

Los tres formatos utilizan el mismo comando:

```powershell
smart-audiobook novela.txt
smart-audiobook novela.pdf
smart-audiobook novela.docx
```

También puede ejecutarse como módulo:

```powershell
python -m smart_audiobook novela.txt
```

Opciones útiles:

```powershell
# Analizar capítulos y personajes sin generar audio
smart-audiobook novela.docx --analyze-only

# Mostrar segmentos y generar audio
smart-audiobook novela.pdf --show-segments

# No utilizar Gemini aunque exista una clave
smart-audiobook novela.txt --no-llm

# Elegir la carpeta raíz de salida
smart-audiobook novela.txt --output mis_audiolibros

# Compatibilidad V0.1–V0.3: elegir directamente el WAV completo
smart-audiobook novela.txt --output output/mi_audiolibro.wav
```

## Resultado

Para un libro llamado `mi_libro`, el resultado es:

```text
output/
└── mi_libro/
    ├── 01_prologo.wav
    ├── 02_capitulo_1.wav
    ├── 03_capitulo_2.wav
    ├── full_audiobook.wav
    └── metadata.json
```

Se conserva WAV porque es la salida nativa del motor TTS local y no requiere instalar FFmpeg. `metadata.json` contiene título, formato, capítulos, personajes, voces, fecha, origen y nombres de salida; nunca incluye claves API.

Ejemplo abreviado:

```json
{
  "title": "mi_libro",
  "format": "docx",
  "chapter_count": 3,
  "detected_characters": ["Narrator", "María"],
  "voice_assignments": {
    "Narrator": "identificador-voz-1",
    "María": "identificador-voz-2"
  },
  "generated_at": "2026-10-05T18:00:00+00:00",
  "source": "mi_libro.docx",
  "outputs": {
    "chapters": ["01_prologo.wav", "02_capitulo_1.wav"],
    "full_audiobook": "full_audiobook.wav"
  }
}
```

## Detección de capítulos

En DOCX se priorizan los estilos `Heading 1` / `Título 1` y niveles equivalentes. En TXT y PDF se reconocen, entre otros:

- `Capítulo 1`, `Capítulo IV`, `Chapter One`;
- `Prólogo` y `Epílogo`;
- `1. Introducción` y `3 - Una promesa`;
- `Episodio 42`, `Parte 2`, `Volume 3`;
- encabezados Markdown como `# Chapter 12`;
- títulos breves en mayúsculas.

Si no aparece ninguna división fiable, se crea un único capítulo llamado `Full document`. Las heurísticas son intencionadamente simples: pueden necesitar ajustes para obras con convenciones poco habituales.

## Tratamiento por formato

### TXT

Se espera UTF-8. Se conservan saltos de párrafo y líneas de diálogo.

### PDF

Se usa extracción nativa con `pypdf`. Se eliminan números de página evidentes y encabezados o pies repetidos. V0.4 no aplica OCR; un PDF formado solo por imágenes produce un error informativo.

> **PDF escaneado o basado en imágenes: no soportado todavía.** OCR queda fuera de V0.4 y podrá añadirse en una versión futura si se considera necesario.

### DOCX

Se usa `python-docx`. Se leen párrafos, título del documento y estilos de encabezado. En V0.4 se ignoran imágenes, tablas, notas al pie avanzadas y otros elementos no narrativos.

## Probar con los ejemplos locales

El repositorio incluye fixtures pequeños sin descargas externas:

```powershell
smart-audiobook tests/fixtures/sample_book.txt --analyze-only --no-llm
smart-audiobook tests/fixtures/sample_book.pdf --analyze-only --no-llm
smart-audiobook tests/fixtures/sample_book.docx --analyze-only --no-llm
```

Para realizar una generación completa:

```powershell
smart-audiobook tests/fixtures/sample_book.txt --no-llm
```

Los binarios PDF y DOCX se pueden reconstruir con `tests/fixtures/build_fixtures.py`; ese script de desarrollo usa `reportlab`, que no es una dependencia de ejecución de Smart Audiobook.

## Character Intelligence

Cada libro mantiene un `CharacterRegistry` dentro de `analysis.json`. Sus perfiles tienen ID estable, nombre canónico, aliases con `known_from_chapter`, importancia, atributos opcionales, estadísticas, actividad reciente, voz y procedencia manual. El registro centraliza búsqueda, normalización, fusión, estadísticas y sugerencias. Género y edad permanecen desconocidos si no existe información; V0.8 no los deduce.

Ejemplo conceptual:

```json
{
  "id": "char_000002",
  "canonical_name": "Sunny",
  "aliases": [{"name": "Sunless", "known_from_chapter": 20, "source": "manual"}],
  "importance": "major",
  "gender": null,
  "dialogue_count": 35,
  "voice_strategy": "dedicated"
}
```

`Sunless` identifica a Sunny desde el capítulo 20. Al reanalizar el capítulo 5, ese alias no está disponible. Los perfiles descubiertos en capítulos futuros tampoco entran en los candidatos. El contexto posterior se limita al mismo capítulo y no reutiliza etiquetas de speaker resueltas en el futuro. Las descripciones enriquecidas registran hasta qué capítulo se conocían y no forman parte del prompt de atribución.

La importancia usa heurísticas transparentes: `major` a partir de 30 diálogos, o 10 diálogos repartidos en al menos 5 capítulos; `supporting` desde 5 diálogos, 3 capítulos o 20 menciones; `minor` con alguna aparición; `unknown` cuando falta evidencia o el perfil es provisional. Los recuentos se reconstruyen sin duplicarlos en cada guardado. La actividad conserva las últimas 64 observaciones por personaje; la selección usa una ventana reciente de 12 segmentos por defecto.

Resolución:

```text
Document → Segmentation → Character Registry → Candidate Generator
                                                  ↓
                      Explicit/Syntactic Rules → Conversation Resolver
                                                  ↓
                                Candidate scoring / exact known-name label
                                                  ↓
                                         LLM if still ambiguous
                                                  ↓
                                     Confidence Policy → Review → Speaker

Character Registry
├── Profiles and stable IDs
├── Temporal aliases and knowledge
├── Statistics and importance
├── Recent activity
└── Voice assignments and manual provenance
```

Los candidatos se puntúan por menciones cercanas, aliases, speakers anteriores y actividad; se envían como máximo 6. Una mención aislada no basta para asignar speaker automáticamente. La alternancia A-B-A solo se acepta en un intercambio corto entre dos participantes, sin señales de cambio de escena. Una atribución explícita o etiqueta de personaje evita la consulta a Gemini.

Umbrales en `.env`:

```dotenv
SPEAKER_ACCEPT_CONFIDENCE=0.85
SPEAKER_REVIEW_CONFIDENCE=0.60
SPEAKER_CANDIDATE_LIMIT=6
CHARACTER_ACTIVE_WINDOW=12
SPEAKER_PROMPT_VERSION=v2
```

Con confianza >= 0.85 se acepta la resolución; entre 0.60 y 0.85 se acepta y marca revisión; por debajo queda `Unknown`. Un nuevo nombre sugerido por Gemini exige evidencia textual, permanece `pending` y no recibe diálogos ni voz definitiva hasta confirmarlo. La respuesta JSON se valida y no puede elegir un personaje fuera de los candidatos sin declarar que es nuevo.

La caché LLM vive en el proyecto. Su clave incluye diálogo, contexto compacto, candidatos, actividad, capítulo, modelo/proveedor y versión de prompt. Los hits y las decisiones locales incrementan `llm_calls_saved`. Las solicitudes siguen siendo individuales: en V0.8 se prioriza la continuidad secuencial y una validación simple sobre batching, que podría propagar errores entre diálogos.

En la web, `Character Registry` muestra aliases, importancia, estadísticas, voz y primeras/últimas apariciones. Añade o elimina aliases indicando desde cuándo se conocen. Para mover un nombre detectado a alias, fusiona el personaje de origen en el destino y elige el capítulo de conocimiento. El destino conserva su voz manual; si no la tiene, se conserva una voz manual del origen. Renombrar cambia el nombre canónico y conserva el anterior como alias. Las sugerencias de duplicados por similitud/títulos o evidencia de «conocido como» requieren confirmación; no relacionamos nombres diferentes sin evidencia.

`Review Issues` filtra Unknown, baja confianza, candidatos nuevos y duplicados. La lista prioriza los casos dudosos. Reanalizar permite seleccionar segmentos o actuar sobre Unknown/baja confianza: toda resolución `manual` se conserva incluso si se selecciona expresamente. Las fusiones confirmadas son correcciones explícitas del usuario y actualizan los diálogos. Los hashes TTS cambian cuando cambian las voces usadas, sin eliminar audios reutilizables.

Las voces se asignan de forma determinista, reservando voces disponibles para narrador y personajes major/supporting; los menores comparten el resto del catálogo. Solo se utiliza metadata real de género cuando existe una coincidencia; si falta, la selección es neutral. Una elección manual nunca se sobrescribe. No se deducen edades ni estilos vocales.

`Enriquecer con Gemini` es una acción explícita y opcional: utiliza hasta ocho fragmentos de evidencia anteriores al capítulo indicado, requiere al menos tres, y propone una descripción y rasgos. No ejecuta llamadas continuas, crea aliases automáticamente ni usa conocimiento externo del libro.

Migración: los estados anteriores sin `schema_version` se cargan como versión 1; se reconstruyen perfiles/estadísticas y se preservan voces existentes como manuales por precaución. El siguiente guardado escribe `schema_version: 2` atómicamente. No se reanaliza el documento ni se pierden salidas existentes. Las versiones futuras desconocidas se rechazan con un error controlado.

Prueba desde CLI:

```powershell
smart-audiobook examples/character_intelligence.txt --analyze-only --show-characters --character-stats --no-llm
python examples/verify_character_intelligence.py
smart-audiobook examples/character_intelligence.txt --project-id UUID --reanalyze-unresolved --no-llm
```

La tercera orden abre un proyecto existente de `work/`; sustituye UUID por su identificador. El archivo posicional se conserva por compatibilidad y no se vuelve a cargar al abrir un proyecto. Quita `--no-llm` para habilitar Gemini si existe una clave. La prueba reproducible usa respuestas LLM de fixture, sin red ni generación de audio. En la web, sube `examples/character_intelligence.txt`, confirma los candidatos, revisa Sunny/Sunless y Sir Gilead/Gilead y comprueba los aliases al reanalizar. `metadata.json` incluye perfiles y métricas de resolución, revisión, importancia y llamadas ahorradas.

Limitaciones: heurísticas orientadas a diálogos españoles con raya, estadísticas de menciones solo para nombres ya conocidos, detección de aliases limitada a patrones explícitos y gestión manual, ventana por capítulo, sin batching ni recuperación externa de conocimiento. El registro es específico del libro y se persiste en JSON; no hay base de datos global.

## Local TTS

V0.7 usa [Piper](https://github.com/OHF-Voice/piper1-gpl) como motor neuronal local recomendado: funciona sin API externa, dispone de modelos en español, produce WAV PCM y puede ejecutarse en CPU o GPU. Piper se carga de forma diferida; instalar o ejecutar los tests básicos no descarga modelos ni carga redes neuronales.

Instala el extra y descarga voces desde el catálogo oficial de Piper:

```powershell
python -m pip install -e ".[web,local-tts]"
python -m piper.download_voices --data-dir voices es_ES-sharvard-medium
```

El descargador oficial guarda el `.onnx` y su `.onnx.json`; ambos son necesarios. Puedes repetir el comando para añadir modelos españoles. Smart Audiobook descubre todos los modelos de `voices/`, incluidos sus speakers internos. Revisa la ficha y licencia de cada voz antes de usarla o distribuir audio. El repositorio no incluye modelos, voces clonadas ni audio de terceros.

Configuración en `.env`:

```dotenv
TTS_PROVIDER=piper
PIPER_VOICES_DIR=voices
PIPER_DEVICE=auto
TTS_CHUNK_MAX_CHARS=400
TTS_SEGMENT_PAUSE_MS=180
TTS_PARAGRAPH_PAUSE_MS=320
TTS_SPEAKER_CHANGE_PAUSE_MS=260
TTS_CHAPTER_PAUSE_MS=700
```

`PIPER_DEVICE=auto` usa CUDA si ONNX Runtime anuncia `CUDAExecutionProvider`; de lo contrario usa CPU e informa de que una novela larga puede tardar. Para forzar CPU usa `cpu`. Para GPU instala `onnxruntime-gpu` compatible con tu CUDA y usa `cuda`; si CUDA no está disponible se muestra un error, sin cambiar silenciosamente de proveedor. `TTS_PROVIDER=system` recupera el motor `pyttsx3` de V0.6.

El catálogo formaliza id, nombre visible, idioma, proveedor, género, descripción/estilo y audio de referencia cuando existen. No inventa campos ausentes. `strategy` admite `dedicated` y `generic_pool`; en V0.7 la elección es manual y las asignaciones personaje → voz se conservan en `analysis.json`. Velocidad y volumen están preparados; pitch, estilo y clonación solo se habilitan si un proveedor declara esas capacidades.

## Long audiobook generation

Cada segmento se divide respetando párrafos, frases y palabras. El formato interno es WAV PCM y la concatenación escribe bloques pequeños, por lo que no carga una novela completa en RAM y no requiere FFmpeg.

Antes de sintetizar se calcula un SHA-256 con texto, voz, proveedor, modelo y ajustes. Un hit reutiliza el WAV; cualquier cambio relevante crea otra clave. La web guarda la caché en `work/<id>/cache/audio/`. `generation_state.json` registra capítulos `running`, `completed`, `failed` o `cancelled`. Al repetir la ejecución, los capítulos completos con la misma huella se reutilizan y un capítulo parcial aprovecha sus segmentos cacheados.

La reanudación está activa por defecto:

```powershell
smart-audiobook novela.txt --tts-provider piper
smart-audiobook novela.txt --tts-provider piper --force-regenerate
smart-audiobook novela.txt --tts-provider piper --no-resume
```

`--force-regenerate` ignora caché y capítulos completados. La web muestra progreso por polling y permite solicitar cancelación entre fragmentos. Lo ya cacheado se conserva.

## Arquitectura

```text
src/smart_audiobook/
├── application.py            # Servicio compartido por CLI y web
├── audio.py                  # Síntesis ordenada y combinación WAV
├── audio_cache.py            # Caché SHA-256 persistente y atómica
├── book_processor.py         # Orquestación de libro, capítulos y metadatos
├── chapter_detection.py      # Heurísticas y estilos DOCX
├── characters.py             # Registro canónico de personajes
├── cli.py                    # Interfaz de línea de comandos
├── document_loaders.py       # Adaptadores TXT, PDF y DOCX
├── gemini_provider.py        # Adaptador del SDK oficial de Gemini
├── gemini_resolver.py        # Configuración por entorno y compatibilidad
├── llm_providers.py          # Contrato independiente del proveedor LLM
├── llm_resolver.py           # Prompt y validación del dominio
├── models.py                 # Document, Chapter y modelos de diálogo
├── output_files.py           # Nombres seguros y metadata.json
├── piper_provider.py         # Adaptador neuronal local opcional
├── review_service.py         # Edición consistente y generación revisada
├── review_store.py           # Persistencia JSON temporal por UUID
├── segmenter.py              # Narración frente a diálogo
├── speaker_identification.py # Reglas, fallback y caché
├── speaker_resolvers.py      # Contrato y reglas deterministas
├── text_normalizer.py        # Normalización explícita
├── text_chunking.py          # Fragmentación respetuosa con frases
├── tts.py                    # Motor de voz local
├── tts_config.py             # Configuración y factoría de proveedores
├── tts_providers.py          # Contrato, capacidades y motor del sistema
├── voice_assignment.py       # Personaje → voz
├── web.py                    # Rutas FastAPI del flujo análisis/revisión/generación
├── web_security.py           # Uploads, firmas y rutas permitidas
├── templates/                # Páginas Jinja2
└── static/                   # CSS responsive
```

`AudiobookApplicationService` continúa siendo el punto de entrada compartido. La CLI conserva `process()` para el flujo automático; la web usa `analyze()` y solo llama a `generate()` después de la revisión. `ReviewService` aplica las ediciones y `ReviewProjectStore` persiste el estado. Las rutas HTTP validan el borde web y no contienen reglas de personajes, voces ni TTS.

```text
                    TTSProvider
                        |
          +-------------+-------------+
          |                           |
       PiperTTS                  SystemTTS
          |
       Modelos
          |
      Audio Cache
          |
      Segment WAV
          |
     Chapter Builder
          |
       Book Builder

Document → Analysis → Review → Voice Assignment → TTS Queue
                                                        ↓
                                                  Cache lookup
                                          HIT → reuse | MISS → synthesize
                                                        ↓
                                      Chapter WAV → Full audiobook WAV
```

```text
SpeakerIdentificationService
          ↓
      LLMResolver
          ↓
      LLMProvider
          ↓
    GeminiProvider → google-genai
```

### Comprobar el fallback de Gemini

El repositorio incluye `examples/gemini_ambiguous.txt`, con un diálogo que las
reglas no pueden atribuir de forma determinista. Ejecútalo con:

```powershell
smart-audiobook examples/gemini_ambiguous.txt --analyze-only
```

Con `GEMINI_API_KEY` en `.env`, los casos ambiguos se consultan a Gemini. Para
comparar el comportamiento estrictamente local, repite el análisis con
`--no-llm`; esos casos se mantienen como `Unknown`.

## Tests

```powershell
python -m pip install -e ".[web,test,llm]"
python -m unittest discover -s tests -v
```

La suite cubre el pipeline anterior y la web, además del contrato TTS, catálogo Piper, CPU fallback, capabilities, claves e invalidación de caché, hits/misses, chunking, fallos, reanudación y capítulos idempotentes. Gemini y Piper se simulan cuando corresponde; las pruebas normales no consumen API, descargan modelos ni cargan redes neuronales. La integración real Piper es opt-in con `RUN_PIPER_INTEGRATION=1`.

## Limitaciones conocidas

- No hay OCR para PDF escaneados.
- La extracción PDF depende de cómo esté construido el documento y puede alterar el orden de maquetaciones complejas.
- Las tablas e imágenes DOCX no se narran.
- Las heurísticas pueden detectar de más o de menos en novelas con encabezados no convencionales.
- Solo se detectan diálogos con raya larga; las comillas aún no se tratan como diálogo.
- Las reglas de hablante cubren un conjunto limitado de verbos de habla en español.
- Si faltan voces, se reutilizan de forma cíclica.
- La generación web usa una tarea local y polling; no es una cola distribuida. Tras reiniciar la aplicación hay que iniciar de nuevo la acción, que continúa desde caché/estado.
- El estado de revisión es temporal pero sobrevive a reinicios mientras permanezca su directorio bajo `work/`.
- No existe todavía limpieza automática de espacios de trabajo antiguos.
- No hay autenticación ni separación entre usuarios; V0.7 está pensada para uso local.
- La salida de audio de V0.7 es WAV, no MP3.
- La variedad y expresividad dependen del modelo Piper elegido; V0.7 no infiere edad, género ni estilos interpretativos.
