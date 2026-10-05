# Smart Audiobook

Smart Audiobook es un proyecto personal e incremental para convertir documentos en audiolibros. La versión **V0.2** analiza un archivo TXT, distingue narración y diálogo mediante reglas sencillas y genera un único WAV usando una voz para cada tipo de fragmento.

## Funcionalidades de la V0.2

- Lectura y validación de archivos `.txt` en UTF-8.
- Detección de diálogos escritos con raya larga española (`—`).
- Separación de diálogo y atribución cuando aparecen en la misma línea.
- Representación ordenada de los fragmentos como `narration` o `dialogue`.
- Visualización del análisis en JSON desde la terminal.
- Síntesis con una voz para narración y otra para todos los diálogos.
- Combinación de los fragmentos en un solo archivo WAV.
- Eliminación automática de los audios temporales.

Esta versión no identifica personajes ni utiliza inteligencia artificial.

## Requisitos

- Python 3.10 o posterior.
- Al menos dos voces disponibles en el motor de síntesis del sistema operativo.

La aplicación utiliza `pyttsx3`, por lo que no necesita claves API ni envía el texto a servicios externos. En Windows utiliza las voces SAPI instaladas en el equipo.

## Instalación

Desde la raíz del proyecto:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

## Uso

Generar el audiolibro del ejemplo:

```powershell
smart-audiobook examples/example.txt
```

El resultado se guardará en `output/example.wav`. Para elegir otro destino:

```powershell
smart-audiobook examples/example.txt --output output/mi_audiolibro.wav
```

Mostrar el análisis y generar también el audio:

```powershell
smart-audiobook examples/example.txt --show-segments
```

Mostrar únicamente el análisis, sin generar audio:

```powershell
smart-audiobook examples/example.txt --analyze-only
```

También se puede ejecutar como módulo:

```powershell
python -m smart_audiobook examples/example.txt --analyze-only
```

## Ejemplo de segmentación

Entrada:

```text
Pedro entró en la habitación.

—¿Dónde estabas? —preguntó María.

—Trabajando —respondió Pedro.

María cerró la puerta.
```

Resultado:

```json
[
  {
    "type": "narration",
    "text": "Pedro entró en la habitación."
  },
  {
    "type": "dialogue",
    "text": "¿Dónde estabas?"
  },
  {
    "type": "narration",
    "text": "preguntó María."
  },
  {
    "type": "dialogue",
    "text": "Trabajando"
  },
  {
    "type": "narration",
    "text": "respondió Pedro."
  },
  {
    "type": "narration",
    "text": "María cerró la puerta."
  }
]
```

## Cómo funciona la segmentación

Cada línea no vacía se procesa en orden:

1. Si no comienza con `—`, se clasifica como narración.
2. Si comienza con `—`, el primer fragmento se clasifica como diálogo.
3. Las rayas siguientes de esa línea alternan entre narración y diálogo.
4. Los fragmentos vacíos se descartan sin alterar el orden de los demás.

Este enfoque permite separar construcciones como `—Hola —dijo María.` sin intentar adivinar quién habla.

## Arquitectura

```text
AppAudiobook/
├── examples/
│   └── example.txt
├── output/                         # Audios finales, ignorados por Git
├── src/
│   └── smart_audiobook/
│       ├── __init__.py
│       ├── __main__.py
│       ├── audio.py                # Orquestación y combinación WAV
│       ├── cli.py                  # Interfaz de línea de comandos
│       ├── models.py               # Modelo TextSegment
│       ├── segmenter.py            # Reglas de segmentación
│       ├── text_reader.py          # Lectura y validación del TXT
│       └── tts.py                  # Síntesis con voces locales
├── tests/
│   ├── test_segmenter.py
│   └── test_text_reader.py
├── .gitignore
├── pyproject.toml
└── README.md
```

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

Las pruebas de segmentación cubren narración, diálogo aislado, diálogo con atribución, varias líneas alternadas y texto vacío.

## Limitaciones actuales

- Solo se detecta diálogo en líneas cuyo primer carácter útil es una raya larga (`—`).
- No se reconocen diálogos delimitados únicamente por comillas.
- No se identifica al personaje que habla.
- La primera voz instalada se usa para narración y la segunda para diálogo.
- Los saltos de línea vacíos no se conservan como segmentos ni como pausas explícitas.
- Los fragmentos WAV deben compartir el formato producido por el motor local.

La V0.2 no incluye LLM, servicios de IA, PDF, DOCX, frontend, base de datos, autenticación, biblioteca ni Docker.

## Credenciales

La aplicación no utiliza servicios externos ni necesita credenciales. Si una versión futura las requiere, se cargarán mediante variables de entorno; los archivos `.env` están excluidos del control de versiones.

