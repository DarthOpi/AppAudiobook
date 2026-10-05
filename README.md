# Smart Audiobook

Smart Audiobook es un proyecto personal e incremental para convertir documentos en audiolibros. La versión **V0.1** tiene un alcance intencionadamente pequeño: lee un archivo TXT y genera un archivo WAV mediante síntesis de voz local.

## Alcance de la V0.1

- Lectura de archivos `.txt` en UTF-8.
- Validación del archivo de entrada y de su contenido.
- Conversión del texto a voz con el motor instalado en el sistema operativo.
- Generación de un archivo `.wav`.
- Interfaz de línea de comandos.

Esta versión no incluye detección de personajes, inteligencia artificial, PDF, DOCX, interfaz web, base de datos ni autenticación.

## Requisitos

- Python 3.10 o posterior.
- Un motor de síntesis de voz disponible en el sistema operativo.

La aplicación utiliza `pyttsx3`, por lo que no necesita claves API ni envía el texto a un servicio externo. En Windows utiliza las voces SAPI instaladas en el equipo.

## Instalación

Desde la raíz del proyecto, crea y activa un entorno virtual:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

## Uso

Para convertir el texto incluido como ejemplo:

```powershell
smart-audiobook examples/example.txt
```

El audio se guardará en `output/example.wav`. También puedes elegir otro destino:

```powershell
smart-audiobook examples/example.txt --output output/mi_audiolibro.wav
```

Como alternativa al comando instalado:

```powershell
python -m smart_audiobook examples/example.txt
```

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

## Estructura

```text
AppAudiobook/
├── examples/                  # Textos de entrada de ejemplo
├── output/                    # Audios generados, ignorados por Git
├── src/
│   └── smart_audiobook/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py             # Interfaz de línea de comandos
│       ├── text_reader.py     # Lectura y validación del TXT
│       └── tts.py             # Conversión de texto a audio
├── tests/                     # Pruebas automatizadas
├── .gitignore
├── pyproject.toml
└── README.md
```

## Credenciales

La V0.1 no utiliza servicios externos ni necesita credenciales. Si una versión futura las requiere, se cargarán mediante variables de entorno; los archivos `.env` están excluidos del control de versiones.

