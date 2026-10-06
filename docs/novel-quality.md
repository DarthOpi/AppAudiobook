# Corrección de calidad de novelas — V0.9

Esta corrección no avanza a V0.10. No introduce cuentas, base de datos, cloud,
OCR, scraping ni distribución. El repositorio utilizado es `D:\AppAudiobook`.
El proyecto original de la novela y su `.env` no se modificaron.

## Diagnóstico reproducido

Fuente local: PDF de 461 páginas proporcionado por el usuario. Solo se extrajeron
pequeñas muestras para diagnóstico/aceptación; no se analizaron sus 461 páginas.
Las primeras páginas contienen portada e índice; se eligieron páginas narrativas.

La pérdida ocurre en dos fases anteriores al LLM:

1. El loader creaba un bloque por línea visual. El constructor de capítulos
   separaba esos bloques como párrafos, rompiendo texto y contexto.
2. El segmentador solo reconocía líneas que comenzaban con raya. Los pensamientos
   entre comillas simples quedaban como narración. La atribución anterior con
   una acción entre nombre y verbo tampoco encajaba en las reglas.

El registro no recibía nombres porque no existían resoluciones válidas que registrar.
La clave Gemini sí estaba configurada; tener la clave no prueba que se realicen llamadas.
Ahora se cuentan las invocaciones reales al SDK y los fallos, independientemente de
las peticiones al resolver y de las resoluciones recuperadas desde caché.

## Comparación verificable

Misma muestra de páginas 9–10, extracción física frente al pipeline corregido.
Prueba Gemini limitada explícitamente a **3 llamadas**:

| Métrica | Antes | Después |
| --- | ---: | ---: |
| Narración | 38 | 20 |
| Diálogos | 4 | 4 |
| Pensamientos internos | 0 | 3 |
| Personajes confirmados, sin Narrator | 0 | 2 |
| Diálogos resueltos | 0 | 2 |

Personajes: **Sunny, Solvane**. Reglas: 2 diálogos. Gemini: 2 pensamientos
de Sunny con confianza 0.95; devolvió también Unknown para un diálogo ambiguo.
Quedan 3 segmentos Unknown (2 diálogos, 1 pensamiento); el límite de llamadas
dejó casos sin consultar. Ningún diálogo/pensamiento pertenece a Narrator.
La narración física se reduce por reflow y por separar los pensamientos, no por
descartar texto. Se reconstruyeron 27 párrafos lógicos en esas dos páginas.

La muestra más amplia de páginas 8–12 produjo 83 narraciones, 6 diálogos, 13
pensamientos y los mismos dos personajes. Confirmó 4 llamadas reales, pero una
terminó en **HTTP 504** y no hubo resoluciones LLM válidas en esa ejecución.
El error fue visible y no se almacenó como respuesta reutilizable en caché.
La segunda comprobación de páginas 9–10 confirmó funcionamiento real del proveedor.
Las respuestas de un LLM pueden variar; estos números son observaciones de la prueba,
no expectativas rígidas de los tests.

## Cambios de dominio y flujo

```text
PDF → extracción → limpieza de bordes → PdfTextReconstructor
    → párrafos/bloques → capítulos → segmentación
    → reglas de atribución / pensamiento / autoidentificación explícita
    → continuidad (solo diálogos) → candidatos → LLM si falta evidencia
    → confidence policy → CharacterRegistry / candidatos pendientes → revisión
```

- `narration`: Narrator.
- `dialogue`: voz hablada, raya o comillas dobles/angulares.
- `internal_thought`: pensamiento, comillas simples rectas/tipográficas.
- `system_message`: tipo reservado, sin clasificación automática.
- Contexto compacto persistido y defaults vacíos para estado antiguo.
- Pensamientos no usan alternancia conversacional ni atribuciones de habla.
- Nombres nuevos inferidos son provisionales; una atribución nominal inequívoca
  o autoidentificación explícita puede validar el nombre. No se fusionan automáticamente
  entidades indirectas o nombres anteriores.
- Thresholds, revisión, aliases temporales y overrides manuales permanecen vigentes.
- La clave de caché incluye tipo, ventana efectiva, candidatos, modelo y revisión
  del prompt `novel-segments-v1`; no reutiliza el prompt antiguo.
- Métricas de estado y contadores de llamadas se guardan en el análisis JSON.
  Las resoluciones describen los segmentos actuales; las llamadas son acumulativas.
- El contador de diálogos del perfil no cuenta pensamientos como habla. La actividad
  del personaje sí puede actualizarse por un pensamiento resuelto.

## Voces: resultado y condición pendiente

El catálogo real examinado devuelve Piper en CPU, idioma `es_ES`, tres archivos
ONNX y cuatro identidades: Carlfm, MLS 9972 y dos speakers de Sharvard.
No era un límite artificial del selector: eran todas las voces instaladas.
Piper no admite embeddings/referencias ni style/pitch en este adaptador. Un perfil
de velocidad/volumen no crea una voz distinta. Este catálogo no basta como motor
principal para un reparto amplio y expresivo.

Se añadió **soporte opcional**, no una sustitución automática:

- `VoiceInfo` continúa representando la voz real del motor.
- `CharacterVoiceProfile` describe voz base, ID, nombre, idioma, proveedor,
  ajustes, reference audio autorizado, metadata y estrategia dedicada/pool.
- `ProfiledTTSProvider` expone todas las voces reales junto a perfiles válidos y
  dirige síntesis/previews al motor. Conserva IDs y decisiones manuales.
- La caché incorpora perfiles y fingerprints de referencias; cambiar una referencia
  invalida audio y fingerprints de capítulo de forma conservadora.
- El adaptador Chatterbox Multilingual admite español, CPU/CUDA y reference
  conditioning, carga perezosa y bloqueo durante cambios de conditioning.
- No se descargaron modelos ni grabaciones. No se crearon ni clonaron voces reales.
- Chatterbox requiere pesos locales, entorno compatible y referencias propias,
  sintéticas o autorizadas. La recomendación del proveedor es Python 3.11;
  el entorno actual de Piper usa Python 3.14. La compatibilidad, velocidad y calidad
  acústica de Chatterbox en este Windows **no están verificadas**.
- Por tanto, sin esa configuración siguen existiendo **cuatro identidades Piper**.
  La ampliación real y expresiva del reparto queda pendiente de aportar modelos y
  referencias autorizadas y realizar una escucha de aceptación.

Fuentes de evaluación: [README oficial de Chatterbox](https://github.com/resemble-ai/chatterbox),
[adaptador multilingüe oficial](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py).
No se eligió Turbo/Nano porque el catálogo oficial los describe como modelos ingleses.
El SDK/modelo local escogido permanece desacoplado de la lógica de personajes.

## Archivos creados

Rutas relativas a `D:\AppAudiobook`, como aparecen en GitHub Desktop:

| Archivo | Finalidad |
| --- | --- |
| `src/smart_audiobook/pdf_reconstruction.py` | Reflow conservador de párrafos PDF. |
| `src/smart_audiobook/voice_catalog.py` | Perfiles y composición con catálogo real. |
| `src/smart_audiobook/chatterbox_provider.py` | Adaptador local opcional con reference conditioning. |
| `examples/diagnose_novel.py` | Extracción, párrafos, tipos y voces sin llamadas externas. |
| `examples/verify_novel_sample.py` | Aceptación limitada de PDF, Gemini opt-in. |
| `examples/voice_profiles.example.json` | Pool de ajustes sobre una voz Piper, sin fingir otra identidad. |
| `examples/reference_profiles.example.json` | Plantilla de referencias; consentimiento desactivado por defecto. |
| `tests/fixtures/novel_regression.txt` | Ficción original pequeña, sin copiar una novela. |
| `tests/test_novel_quality.py` | Regresiones offline de narrativa, Gemini, catálogo, perfiles y UI. |
| `docs/novel-quality.md` | Este informe. |

## Archivos modificados

| Grupo | Archivos |
| --- | --- |
| Configuración y documentación | `.env.example`, `.gitignore`, `README.md`, `pyproject.toml` |
| Importación y estimaciones | `src/smart_audiobook/document_loaders.py`, `src/smart_audiobook/chapter_detection.py`, `src/smart_audiobook/book_manifest.py` |
| Dominio y resolución | `src/smart_audiobook/models.py`, `src/smart_audiobook/segmenter.py`, `src/smart_audiobook/speaker_resolvers.py`, `src/smart_audiobook/speaker_candidates.py`, `src/smart_audiobook/speaker_identification.py`, `src/smart_audiobook/characters.py` |
| Gemini | `src/smart_audiobook/gemini_provider.py`, `src/smart_audiobook/gemini_resolver.py`, `src/smart_audiobook/llm_resolver.py` |
| Aplicación, estado y métricas | `src/smart_audiobook/application.py`, `src/smart_audiobook/book_processor.py`, `src/smart_audiobook/book_workflow.py`, `src/smart_audiobook/review_store.py`, `src/smart_audiobook/review_service.py` |
| Voces y CLI | `src/smart_audiobook/tts_config.py`, `src/smart_audiobook/voice_assignment.py`, `src/smart_audiobook/cli.py` |
| Web | `src/smart_audiobook/web.py`, `src/smart_audiobook/templates/review.html`, `src/smart_audiobook/templates/book.html` |

## Cómo probar

1. Reinicia el servidor para cargar el código nuevo.
2. Importa el fixture original TXT, analiza un capítulo, abre revisión y comprueba
   Daniel, Elena, pensamientos y Unknown. Escucha previews Piper si deseas.
3. Para un PDF **importado previamente**, importa un proyecto nuevo y analiza solo
   un capítulo/rango pequeño: reanalizar Unknown no cambia la extracción ni
   segmentación antigua. No se descartaron ni migraron silenciosamente correcciones.
4. Ejecuta la suite offline y la aceptación limitada desde la raíz:

```powershell
python -m pytest -q
python examples/verify_novel_sample.py "work\86024bb183ae4830a8e628c26cdfba79\source\shadow_slave_vol_04.pdf" --start-page 9 --pages 2 --llm --max-llm-calls 3
```

La prueba requiere `GEMINI_API_KEY` local y envía contexto compacto a Google; no
modifica el libro, no crea audio, no imprime la clave y no envía las 461 páginas.
Para probar sin API, omite `--llm`. La suite normal mockea Gemini y TTS pesado.
La guía de configuración opcional de Chatterbox y perfiles está en README.

Resultado final de la suite: **217 passed, 1 skipped, 12 subtests passed**.
Los 46 casos nuevos (incluidos parámetros) se suman a las 171 pruebas anteriores;
la integración optativa de Piper continúa omitida en la ejecución normal. También
pasó `git diff --check`. `.env`, modelos, perfiles locales y referencias en `voices/`
continúan ignorados por Git. No se creó ningún commit.

## Límites deliberados

No se reconstruye con precisión geométrica toda maquetación PDF; puntuación y
líneas cortas pueden dejar límites ambiguos. No se cruzan capítulos con contexto
futuro, ni se convierte una revelación en una fusión automática. Las citas literales
no habladas pueden confundirse con diálogo. Unknown y los candidatos pendientes
requieren revisión. No se añadió un reparto ficticio de voces: hacen falta
identidades realmente distintas y una comprobación acústica del motor alternativo.
