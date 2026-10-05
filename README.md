# Smart Audiobook

Smart Audiobook es un proyecto incremental para convertir novelas y novelas web en audiolibros. La **V0.5** incorpora una interfaz web con FastAPI sin abandonar la CLI: ambas utilizan el mismo servicio de aplicación y el mismo pipeline de documentos, personajes, voces y TTS.

## Qué incluye V0.5

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
- Página de resultado con capítulos, personajes, voces y estadísticas.
- Reproducción HTML5 y descarga controlada de cada WAV.
- Logging básico y eliminación inmediata de los uploads temporales.

No incluye OCR, EPUB, descarga de novelas web, frontend separado, base de datos, autenticación, Docker, edición manual ni procesamiento asíncrono avanzado.

## Flujo

```text
CLI ─────────┐
             ↓
      Application Service
             ↑
Web UI ──────┘
             ↓
     Document Pipeline
             ↓
    Speaker Detection
             ↓
            TTS
             ↓
WAV por capítulo + WAV completo + metadata.json
```

El formato se detecta por la extensión. Desde `Document` en adelante, el pipeline es el mismo para todos los orígenes.

## Requisitos

- Python 3.10 o posterior.
- Una o más voces instaladas en el sistema operativo.
- Opcional: `GEMINI_API_KEY` para resolver diálogos ambiguos.

La síntesis usa `pyttsx3` y se ejecuta localmente. Gemini solo recibe contexto limitado de diálogos que las reglas no pueden resolver.

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

## Web interface

Arranca el servidor desde la raíz del proyecto:

```powershell
uvicorn smart_audiobook.web:app --reload
```

Después abre [http://127.0.0.1:8000](http://127.0.0.1:8000).

Flujo básico:

1. Selecciona un TXT, PDF o DOCX de hasta 20 MiB.
2. Pulsa **Generar audiolibro**.
3. La web valida el archivo y llama al mismo `AudiobookApplicationService` que utiliza la CLI.
4. La página de resultado muestra documento, capítulos, personajes, voces, segmentos y palabras aproximadas.
5. Utiliza los reproductores HTML5 o los botones de descarga para cada capítulo y el audiolibro completo.

Los uploads se guardan en una carpeta temporal y se eliminan al terminar, incluso si el procesamiento falla. Los resultados necesarios se conservan bajo:

```text
output/web/<job_id>/<nombre_libro>/
```

Los endpoints reciben un identificador UUID y un nombre previamente registrado. No aceptan rutas del sistema proporcionadas por el navegador ni permiten descargar archivos fuera del resultado correspondiente.

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
  "source": "C:\\libros\\mi_libro.docx",
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

## Arquitectura

```text
src/smart_audiobook/
├── application.py            # Servicio compartido por CLI y web
├── audio.py                  # Síntesis ordenada y combinación WAV
├── book_processor.py         # Orquestación de libro, capítulos y metadatos
├── chapter_detection.py      # Heurísticas y estilos DOCX
├── characters.py             # Registro canónico de personajes
├── cli.py                    # Interfaz de línea de comandos
├── document_loaders.py       # Adaptadores TXT, PDF y DOCX
├── gemini_resolver.py        # Adaptador aislado de Gemini
├── models.py                 # Document, Chapter y modelos de diálogo
├── output_files.py           # Nombres seguros y metadata.json
├── segmenter.py              # Narración frente a diálogo
├── speaker_identification.py # Reglas, fallback y caché
├── speaker_resolvers.py      # Contrato y reglas deterministas
├── text_normalizer.py        # Normalización explícita
├── tts.py                    # Motor de voz local
├── voice_assignment.py       # Personaje → voz
├── web.py                    # Rutas FastAPI y registro temporal de resultados
├── web_security.py           # Uploads, firmas y rutas permitidas
├── templates/                # Páginas Jinja2
└── static/                   # CSS responsive
```

`AudiobookApplicationService` es el punto de entrada compartido. Los cargadores dependen de `DocumentLoader`, y la integración LLM depende de `SpeakerResolver`. Las rutas HTTP no implementan reglas de negocio: validan el borde web, llaman al servicio y convierten el resultado en una vista.

## Tests

```powershell
python -m pip install -e ".[web,test]"
python -m unittest discover -s tests -v
```

La suite cubre el pipeline anterior y la web: página principal, uploads TXT/PDF/DOCX, extensión, contenido real, archivo vacío, límite de tamaño, sanitización, descarga, path traversal y errores del pipeline. Gemini y el TTS se simulan cuando corresponde; las pruebas automatizadas no consumen API ni generan voz real.

## Limitaciones conocidas

- No hay OCR para PDF escaneados.
- La extracción PDF depende de cómo esté construido el documento y puede alterar el orden de maquetaciones complejas.
- Las tablas e imágenes DOCX no se narran.
- Las heurísticas pueden detectar de más o de menos en novelas con encabezados no convencionales.
- Solo se detectan diálogos con raya larga; las comillas aún no se tratan como diálogo.
- Las reglas de hablante cubren un conjunto limitado de verbos de habla en español.
- Si faltan voces, se reutilizan de forma cíclica.
- La generación web es síncrona; un documento grande mantiene abierta la petición hasta terminar.
- Los identificadores de resultados viven en memoria y se pierden al reiniciar el servidor, aunque los WAV permanezcan en `output/web/`.
- No existe limpieza programada de audiolibros ya generados.
- No hay autenticación ni separación entre usuarios; V0.5 está pensada para uso local.
- La salida de audio de V0.5 es WAV, no MP3.
