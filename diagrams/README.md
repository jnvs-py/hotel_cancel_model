# Diagramas de arquitectura — Hito 1

Tres vistas del sistema, producidas con cuatro herramientas distintas. El
contenido es identico entre herramientas (mismos nodos, mismos flujos, mismas
decisiones); lo que cambia es el formato y las capacidades del visor.

## Las tres vistas

| Vista | Que muestra |
|---|---|
| `pipeline_datos` | Del CSV crudo a la prediccion: carga, contrato tolerante, split temporal, pipeline, umbral en validacion, artefacto versionado e inferencia con contrato estricto |
| `arquitectura_ml` | Las capas del sistema: contrato de datos, entrenamiento, artefacto versionado, servicio y el CI/CD que gobierna la version |
| `interaccion_servicios` | Dev -> GitHub (CI + Release con dedup por fingerprint) -> imagen versionada -> contenedor servido -> consumidor; despliegue cloud pendiente |

## Las cuatro herramientas

| Carpeta | Formato | Como verlo / regenerar |
|---|---|---|
| `archify/` | HTML interactivo (+ JSON fuente versionado) | Abrir el `.html` en el navegador: tema claro/oscuro, zoom, busqueda, vista guiada. Regenerar: `node bin/archify.mjs deliver <tipo> <x>.json <x>.html --quality showcase` desde la skill archify |
| `drawio/` | `.drawio` (XML nativo) | Abrir en app.diagrams.net o el plugin de draw.io del editor; exportable a PNG/SVG desde ahi |
| `mermaid/` | `.mmd` (fuente Mermaid) | Renderizar con `mmdc -i <x>.mmd -o <x>.svg`, o pegar el contenido en mermaid.live / un bloque ```mermaid en GitHub |
| `d2/` | `.d2` (fuente D2) | Renderizar con `d2 <x>.d2 <x>.svg` (d2 v0.9 instalado en `~/.local/bin`) |

## Convenciones

- Etiquetas en espanol; rutas de codigo y nombres de endpoints en su forma literal.
- Trazo punteado = relacion secundaria (informacion, gobierno, version servida).
- Trazo grueso = el flujo que hay que seguir primero.
- El despliegue cloud aparece siempre como pendiente (no es un hecho todavia).
- Los `fingerprint` y `model_version` que se ven reflejan el mecanismo real
  implementado en CI (`ci.yml` + `release.yml`).

## Estado de verificacion

| Herramienta | Verificacion |
|---|---|
| mermaid | Renderiza sin errores con `mmdc` |
| d2 | Renderiza sin errores con `d2` |
| drawio | XML bien formado + legible por el tooling de draw.io |
| archify | `deliver` con 9/9 checks de composicion (showcase) y `visual-check` pass en Chromium a 1440x900 / 1600x1000 / 1920x1080 / 2048x1320 |

Versionamiento: solo archify entra al repo (los 3 `.html` entregables y los
3 `.json` fuente). La evidencia de `visual-check` (capturas PNG, HTML sellado
y reporte JSON) queda en disco local sin versionar: el repo carga liviano y
la verificacion queda documentada en esta tabla. drawio, mermaid y d2 no se
versionan: son fuentes regenerables y su sintaxis vive en esta documentacion.

Nota sobre archify: el contenido esta en espanol, pero la interfaz del visor
(botones, busqueda, leyenda interactiva) solo soporta ingles y chino; cae a
ingles por diseno de la herramienta. Los diagramas y las tarjetas son espanol.
