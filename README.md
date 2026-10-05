# Smart Audiobook

Smart Audiobook es un proyecto personal e incremental para convertir documentos en audiolibros. La versión **V0.3** analiza un TXT, separa narración y diálogo, identifica al personaje que habla y genera un único WAV asignando voces automáticamente.

## Funcionalidades de la V0.3

- Lectura y validación de archivos `.txt` en UTF-8.
- Segmentación de narración y diálogos escritos con raya larga (`—`).
- Identificación de hablantes mediante reglas deterministas.
- Resolución opcional de casos ambiguos mediante Google Gemini.
- Ventana limitada de contexto alrededor de cada diálogo.
- Respuestas LLM estructuradas y validadas.
- Caché en memoria para consultas idénticas durante una ejecución.
- Normalización de nombres sin perder su primera grafía visible.
- Lista de personajes detectados en la salida de análisis.
- Asignación automática de una voz por personaje cuando hay voces suficientes.
- Combinación ordenada de los fragmentos en un único WAV.
- Eliminación automática de audios temporales.

## Flujo

```text
TXT
 ↓
segmentación de narración y diálogo
 ↓
reglas de identificación del hablante
 ↓ solo si no hay resultado fiable
Gemini opcional con contexto cercano
 ↓
segmentos con speaker + lista de personajes
 ↓
asignación automática de voces
 ↓
TTS local + combinación WAV
 ↓
audiolibro final
```

## Requisitos

- Python 3.10 o posterior.
- Al menos una voz disponible en el motor de síntesis del sistema operativo.
- Opcional: una clave de Gemini API para resolver diálogos ambiguos.

La síntesis utiliza `pyttsx3`, por lo que el texto destinado al audio se procesa localmente. Solo el contexto de los diálogos ambiguos se envía a Gemini cuando la integración está instalada y existe `GEMINI_API_KEY`.

## Instalación

### Uso local sin LLM

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

### Uso con Gemini

Instala el extra opcional:

```powershell
python -m pip install -e ".[llm]"
```

Copia el archivo de ejemplo:

```powershell
Copy-Item .env.example .env
```

Edita `.env` y añade tu clave:

```dotenv
GEMINI_API_KEY=tu_clave
```

`.env` está ignorado por Git. Nunca subas una clave real al repositorio.

La integración utiliza `gemini-3.1-flash-lite`, un modelo Flash-Lite orientado a tareas sencillas y de alto volumen. Google ofrece un nivel gratuito sujeto a sus límites y condiciones actuales. Consulta la [documentación de precios de Gemini](https://ai.google.dev/gemini-api/docs/pricing) antes de utilizarlo.

## Uso

Generar el audiolibro:

```powershell
smart-audiobook examples/example.txt
```

Si existe una clave configurada, Gemini solo se consulta para los diálogos que las reglas no puedan resolver.

Ejecutar explícitamente sin Gemini:

```powershell
smart-audiobook examples/example.txt --no-llm
```

Mostrar el análisis y generar audio:

```powershell
smart-audiobook examples/example.txt --show-segments
```

Mostrar únicamente el análisis:

```powershell
smart-audiobook examples/example.txt --analyze-only
```

Elegir otro archivo de salida:

```powershell
smart-audiobook examples/example.txt --output output/mi_audiolibro.wav
```

También se puede ejecutar como módulo:

```powershell
python -m smart_audiobook examples/example.txt --analyze-only
```

## Ejemplo

Texto:

```text
—¿Dónde estabas? —preguntó María.
```

Segmentos:

```json
[
  {
    "type": "dialogue",
    "speaker": "María",
    "text": "¿Dónde estabas?"
  },
  {
    "type": "narration",
    "speaker": "Narrator",
    "text": "preguntó María."
  }
]
```

## Identificación de personajes

### Resolución mediante reglas

Las reglas examinan la atribución narrativa inmediatamente posterior al diálogo. Reconocen verbos frecuentes como `dijo`, `respondió`, `preguntó`, `gritó`, `susurró`, `exclamó`, `contestó`, `añadió`, `replicó` y `murmuró` cuando van seguidos de un nombre propio.

Esta estrategia es rápida, determinista y no consume API.

### Resolución mediante Gemini

Si las reglas no encuentran una atribución fiable, el adaptador de Gemini recibe:

- el diálogo actual;
- hasta dos segmentos anteriores;
- hasta dos segmentos posteriores;
- los personajes conocidos hasta ese momento.

Gemini debe devolver un objeto con `speaker` y `confidence`. La aplicación valida el JSON, el nombre y que la confianza esté entre 0 y 1. Ante una respuesta inválida, un error de red o falta de clave, el personaje queda como `Unknown` y la ejecución continúa.

El resto de la aplicación depende únicamente del contrato `SpeakerResolver`; el SDK de Google está encapsulado en `GeminiSpeakerResolver`, por lo que otro proveedor puede añadirse sin cambiar la lógica de identificación.

## Consistencia de nombres

Los nombres se comparan ignorando mayúsculas, espacios redundantes y tildes. Por ejemplo, `María`, `MARÍA` y `Maria` comparten la misma identidad. La primera grafía encontrada se conserva para mostrarla al usuario.

`Unknown` no se incorpora a la lista de personajes detectados.

## Asignación de voces

`voice_assignment.py` asigna voces por orden de primera aparición:

```text
Narrator → primera voz
primer personaje → segunda voz
segundo personaje → tercera voz
```

Si hay más personajes que voces instaladas, las voces se reutilizan de forma cíclica. La asignación está separada del TTS para permitir selección manual en una versión futura.

## Arquitectura

```text
AppAudiobook/
├── examples/
│   └── example.txt
├── output/                              # Audios finales, ignorados por Git
├── src/smart_audiobook/
│   ├── audio.py                         # Orquestación y combinación WAV
│   ├── characters.py                    # Normalización y registro de nombres
│   ├── cli.py                           # Interfaz de línea de comandos
│   ├── gemini_resolver.py               # Adaptador aislado de Gemini API
│   ├── models.py                        # Modelos del dominio
│   ├── segmenter.py                     # Narración frente a diálogo
│   ├── speaker_identification.py        # Reglas, fallback y caché
│   ├── speaker_resolvers.py             # Contrato y reglas deterministas
│   ├── text_reader.py                   # Lectura y validación del TXT
│   ├── tts.py                           # Síntesis con voces locales
│   └── voice_assignment.py              # Personaje → voz
├── tests/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

Las llamadas a Gemini están simuladas en los tests; la suite nunca depende de una API externa ni consume cuota.

## Limitaciones conocidas

- Las reglas solo reconocen un conjunto pequeño de verbos de habla en español.
- Las atribuciones indirectas o alejadas suelen necesitar Gemini.
- Gemini puede inferir un personaje incorrecto incluso con una respuesta válida.
- La ventana de contexto es deliberadamente pequeña y no sirve para libros enormes.
- La caché solo dura durante la ejecución actual.
- No se detectan diálogos delimitados únicamente por comillas.
- No existe edición manual de personajes ni selección manual de voces.
- El nivel gratuito de Gemini tiene cuotas y disponibilidad definidas por Google.

La V0.3 no incluye PDF, DOCX, frontend, biblioteca, usuarios, autenticación, base de datos, Docker ni procesamiento paralelo avanzado.

