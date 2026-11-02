# Woffu Helper — automatización de fichajes y servidor MCP

[English](README.md) · [Guía completa de la CLI](docs/USAGE.md) · [Configuración MCP](docs/MCP.md)

**Completa días sin fichajes en Woffu, corrige salidas olvidadas y automatiza las entradas, salidas y pausas para comer.**

Woffu Helper es una herramienta de código abierto en Python con comandos de terminal y un servidor MCP local opcional para asistentes de IA. Cada persona utiliza su propia instalación y cuenta de Woffu. Es un proyecto independiente, sin afiliación con Woffu, que utiliza la API interna de su aplicación web.

## Qué puedes hacer

| Necesidad | Herramienta |
|---|---|
| Completar registros horarios de días vacíos | `woffu-backfill fill` |
| Añadir un fichaje de salida olvidado | `woffu-backfill close` |
| Fichar automáticamente según tu horario | `woffu-autosign`, ejecutado con un programador de tareas |
| Recuperar incidencias al día siguiente | Recuperación configurable de días vacíos y salidas pendientes |
| Revisar y completar fichajes desde una IA | `woffu-mcp`, conectado a un cliente compatible |

Las reparaciones conservan los fichajes existentes y omiten festivos, ausencias aprobadas o pendientes, registros aceptados o bloqueados y cambios pendientes. El descanso para comer tiene un mínimo predeterminado de **30 minutos**, configurable. Registra únicamente horas realmente trabajadas y respeta el procedimiento de tu empresa.

## Instalación

Necesitas Python **3.9 o posterior** para la CLI y **3.10 o posterior** para MCP. En Linux/macOS:

```bash
git clone https://github.com/jamessi1989/woffu-helper.git ~/woffu-helper
cd ~/woffu-helper
python3 -m venv .venv
.venv/bin/python -m pip install .
source .venv/bin/activate
```

Para añadir MCP, instala el extra con `.venv/bin/python -m pip install '.[mcp]'`. En Windows utiliza la carpeta `Scripts` del entorno virtual.

## Conectar tu cuenta

Inicia sesión en la web de Woffu de tu empresa. Abre las herramientas de desarrollo del navegador → **Network/Red**, recarga la página y copia el JWT de la cabecera `authorization` de una petición.

```bash
mkdir -p ~/.config/woffu
cp -n env.example ~/.config/woffu/env
chmod 600 ~/.config/woffu/env
$EDITOR ~/.config/woffu/env
```

Guarda `WOFFU_DOMAIN=tuempresa.woffu.com` y `WOFFU_TOKEN=tu_token` en ese archivo. No compartas el token en chats, incidencias ni commits. Cuando caduque, tendrás que renovarlo desde Woffu.

## Previsualizar antes de escribir

Cambia las fechas y horas del ejemplo por las que realmente trabajaste:

```bash
# Completar días vacíos: ocho horas de trabajo y 30 minutos de comida.
woffu-backfill fill --from 2026-10-26 --to 2026-10-30 \
  --slots 09:00-13:00,13:30-17:30 --variance 0 --dry-run

# Añadir solo la salida final que falta.
woffu-backfill close --from 2026-10-26 --to 2026-10-30 \
  --hours 8 --variance 0 --dry-run
```

`--dry-run` no guarda cambios. Quítalo para enviar la propuesta; la CLI pedirá confirmación. `--variance 0` evita variaciones aleatorias entre la previsualización y la ejecución. La empresa puede tener que aprobar los cambios enviados.

## Utilizarlo con un asistente de IA

El servidor MCP es un pequeño programa que tu cliente de IA inicia **en tu propio ordenador**. Se conecta a tu cuenta de Woffu. No necesitas alquilar un servidor ni conectarte al ordenador de otra persona.

Para una instalación nueva, copia `config.example.json` a `~/.config/woffu/config.json` y adapta tu horario. El ejemplo usa 09:00–13:00 y 13:30–17:30, sin tiempo extra ni variación, y con recuperación automática desactivada. Conserva tu configuración si ya utilizabas la herramienta.

Después de [conectar el cliente MCP](docs/MCP.md), puedes pedir:

> Revisa la semana pasada en Woffu y dime qué días están vacíos o tienen una salida pendiente.

> El viernes trabajé de 09:00 a 13:00 y de 13:30 a 17:30. Muéstrame cómo quedaría ese día antes de guardarlo.

El modo predeterminado permite consultar y previsualizar. `--allow-writes` añade la herramienta para aplicar una propuesta concreta. Se comprueba de nuevo que el registro siga siendo editable y no haya cambiado. Las propuestas caducan a los 15 minutos y solo se pueden utilizar una vez.

La integración local requiere un cliente que pueda ejecutar MCP mediante stdio. Esta versión no publica un plugin alojado para ChatGPT web; encontrar el repositorio en una búsqueda no conecta tu cuenta automáticamente. Consulta la [guía MCP](docs/MCP.md) para los clientes y formatos documentados.

## Automatización y recuperación

`woffu-autosign` necesita que el ordenador esté encendido, conectado y ejecutando el programador de tareas. Admite horarios por día de la semana, pausas por fecha y recuperación `off`, `fill`, `close` o `both`.

La recuperación automática puede completar el día laborable elegible más reciente si está vacío y cerrar salidas pendientes dentro del periodo configurado. No reconstruye pausas o fichajes intermedios que falten en un día ya cerrado. Usa la zona horaria local del ordenador y no admite turnos que crucen la medianoche.

Consulta la [referencia de configuración, systemd y cron](docs/USAGE.md). Las instalaciones anteriores mantienen sus valores predeterminados; añadir MCP no modifica el programador existente.

## Desarrollo y licencia

[Contribuciones y pruebas](CONTRIBUTING.md) · [Cambios por versión](CHANGELOG.md) · [Seguridad](SECURITY.md) · [Licencia MIT](LICENSE)
