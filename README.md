# Guía de instalación y de uso de ArchiHUB

## Detalles del aplicativo

El sistema ArchiHUB se compone de dos partes principales: un backend y un frontend. El backend, siendo una API, permite que el frontend sea un componente intercambiable. Actualmente, hay una versión del frontend que ejecuta todas las tareas relacionadas con el procesamiento y la gestión del archivo. No obstante, esta configuración no restringe el uso del sistema, ya que se puede desarrollar una interfaz adaptada a las necesidades específicas de cada usuario, dependiendo de sus requerimientos.

En cuanto al backend, opera utilizando una base de datos MongoDB en conjunto con ElasticSearch para indexar el contenido y realizar búsquedas avanzadas. La API se encuentra desarrollada en Python, utilizando FastAPI como framework, y emplea un gestor de tareas basado en Celery.

El aplicativo se sirve con **uvicorn** desde el punto de entrada ASGI `main:app`, y el código vive en el paquete `archihub/`. Las tareas en segundo plano las ejecuta un worker de **Celery** sobre `archihub.worker.celery_app`. API y worker son dos procesos distintos: sin el worker no corren el procesamiento de archivos, la indexación ni las acciones masivas de los plugins.

---

## Modo producción

**En producción no se instala nada desde este repositorio.** Todo el despliegue —imágenes, variables de entorno, volúmenes de datos, red entre servicios y actualizaciones— lo gestiona el repositorio [getting-started](https://github.com/Archihub-App/getting-started), que es el kit de despliegue oficial.

Allí encontrarás:

- [`local-machine/archihub/docker-compose.yml`](https://github.com/Archihub-App/getting-started/blob/main/local-machine/archihub/docker-compose.yml): el stack completo en una sola máquina (backend, worker de Celery, frontend, MongoDB, ElasticSearch, Qdrant y Redis).
- `local-machine/install.sh`: la instalación local automatizada.
- `network/`: los archivos de _compose_ para repartir el stack entre varias máquinas (`appMachine/`, `mongoCluster/`), que es lo recomendable para entornos colaborativos o de producción real.
- Los directorios de datos montados en los contenedores (`original/`, `webfiles/`, `userfiles/`, `temporal/`).

Sigue los pasos de la [documentación oficial](https://archihub-app.github.io/archihub.github.io/), sección **Empieza ahora**. Este repositorio solo aporta el `Dockerfile` que construye la imagen del backend; los parámetros del despliegue se configuran en `getting-started`.

Dos advertencias que suelen costar tiempo:

- El archivo `.env` de _compose_ sirve para sustituir `${VARIABLE}` **dentro** del `docker-compose.yml`; no inyecta nada por sí solo en el contenedor. Una variable nueva debe aparecer además en el bloque `x-archihub_env_variables` (el worker lo hereda) y en `.env.bak`, porque el worker construye la misma configuración que la API y falla igual si le falta.
- `JWT_SECRET_KEY` y `FERNET_KEY` son obligatorias y **no tienen valor por defecto**: el proceso se niega a arrancar sin ellas. Es deliberado; una clave por defecto en el código sería una clave compartida por todos los despliegues que nadie eligió.

---

## Modo desarrollo

Estas instrucciones son para trabajar sobre el código, no para desplegar.

### 1. Requisitos

**Python 3.11 o 3.12** (`requires-python = ">=3.11,<3.13"` en `pyproject.toml`).

**Paquetes del sistema.** Varias dependencias son extensiones en C o envuelven binarios externos, así que hay que instalarlos antes que el entorno de Python. En Debian/Ubuntu:

```bash
sudo apt-get update && sudo apt-get install -y \
    ffmpeg libsndfile1 poppler-utils libvips-dev libreoffice \
    libimage-exiftool-perl libmagic1 \
    libldap2-dev libsasl2-dev \
    gcc g++ ninja-build git
```

`libldap2-dev`/`libsasl2-dev` son necesarios para compilar `python-ldap` (autenticación contra directorio); `libmagic1` para detectar tipos de contenido; el resto para el procesamiento de medios y documentos. Es la misma lista que instala el `Dockerfile`.

**Servicios externos.** El backend espera encontrar:

| Servicio | Para qué | Puerto por defecto |
|---|---|---|
| MongoDB | base de datos principal | 27017 |
| Redis | broker de Celery y caché | 6379 |
| ElasticSearch | índice de búsqueda | 9200 |
| Qdrant | búsqueda vectorial (opcional) | 6333 |

Lo más cómodo es levantarlos con los contenedores del kit de despliegue y correr solo el backend en local.

### 2. Instalar las dependencias

Con [uv](https://docs.astral.sh/uv/) (recomendado, `uv.lock` está versionado):

```bash
uv sync --extra dev
```

O con pip, en un entorno virtual propio:

```bash
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

El extra `dev` añade `pytest` y `pytest-asyncio`. `httpx` **no** está ahí: es una dependencia de tiempo de ejecución, porque todas las llamadas a modelos de lenguaje pasan por ella.

**Los plugins declaran sus propias dependencias.** Cada plugin instalado en `archihub/plugins/<slug>/` puede traer su `requirements.txt` (paquetes de Python), su `packages.txt` (paquetes del sistema, un nombre de apt por línea) y su `.env`. Para instalarlas todas de una vez:

```bash
sudo PLUGIN_CONSTRAINTS=/tmp/constraints.txt ./scripts/install_plugin_deps.sh archihub/plugins
```

Necesita permisos de root porque `packages.txt` se instala con `apt-get`. El archivo de restricciones existe para que un plugin pueda **añadir** paquetes pero nunca mover una versión que el backend ya fijó.

### 3. Configurar el entorno

Crea un `.env` en la raíz del repositorio (está en `.gitignore`, nunca se versiona):

```bash
# Obligatorias, sin valor por defecto
JWT_SECRET_KEY=<una cadena larga y aleatoria>
FERNET_KEY=<una clave Fernet válida>

# Modo de ejecución
ENVIRONMENT=DEV
BACKEND_PORT=5000

# MongoDB
MONGO_IP_SERVER=localhost
MONGO_PORT=27017
MONGO_INITDB_ROOT_USERNAME=admin
MONGO_INITDB_ROOT_PASSWORD=<contraseña>
MONGO_DATABASE=archihub-dev

# Redis / Celery
CELERY_BROKER_URL=redis://localhost

# ElasticSearch
ELASTIC_DOMAIN=http://localhost
ELASTIC_PORT=9200
ELASTIC_USER=elastic
ELASTIC_PASSWORD=<contraseña>
ELASTIC_INDEX_PREFIX=archihub-dev

# Rutas de almacenamiento
ORIGINAL_FILES_PATH=/ruta/a/original
WEB_FILES_PATH=/ruta/a/webfiles
USER_FILES_PATH=/ruta/a/userfiles
TEMPORAL_FILES_PATH=/ruta/a/temporal

# Origen del frontend (si se omite, CORS queda abierto)
URL_FRONTEND=http://localhost:3000
```

`archihub/core/settings.py` es la lista completa y comentada de variables, con sus valores por defecto. Todo lo que el backend lee vive ahí; lo que lee un plugin va en el `.env` del propio plugin y se lee con `archihub/plugins/framework/config.py`.

Genera una clave Fernet con:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4. Compilar las traducciones

`gettext` solo lee los catálogos compilados, así que editar un `.po` no cambia nada hasta que se ejecuta:

```bash
./compile_translations.sh
```

Compila el catálogo de la aplicación y el de cada plugin que traiga traducciones.

### 5. Levantar el servidor

```bash
PYTHONPATH=. python -m uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

`PYTHONPATH=.` es necesario para cualquier cosa que se ejecute como script (uvicorn, celery, `tools/*.py`); sin él `archihub` no es importable y el error es un `ModuleNotFoundError` que no dice mucho más.

`--reload` recarga al guardar y es solo para desarrollo. En producción `start.sh` corre uvicorn con `--workers` y sin recarga, supervisándolo para poder reiniciarlo con SIGHUP (así funciona el botón «Reiniciar backend» de la pantalla de administración).

Comprobaciones rápidas, sin autenticación:

```bash
curl http://localhost:5000/health/live
curl http://localhost:5000/health/ready
```

### 6. Levantar el worker

En otra terminal, con el mismo `.env`:

```bash
PYTHONPATH=. CELERY_WORKER=1 \
    python -m celery --app archihub.worker.celery_app worker --loglevel INFO
```

Y, si necesitas las tareas programadas (el plugin `scheduleSystemTasks`):

```bash
PYTHONPATH=. CELERY_WORKER=1 \
    python -m celery --app archihub.worker.celery_app beat \
    --loglevel INFO --schedule /tmp/celerybeat-schedule
```

**Lee las dos líneas del banner de arranque del worker:**

- `transport:` tiene que ser la URL **redis://**. Si dice `amqp://`, el broker no se configuró y las tareas se envían a un RabbitMQ que este despliegue no tiene: la API sigue respondiendo con normalidad y los trabajos simplemente se pierden.
- `[tasks]` lista las tareas del núcleo más una por cada tarea de cada plugin **activo**. El conjunto de plugins activos se lee de la base de datos, así que compáralo con lo que el log de arranque dice haber montado, no con un número fijo.

Si vas a correr un worker de pruebas contra un Redis compartido, aíslalo en otra base de datos —`CELERY_BROKER_URL='redis://localhost/1'`—: dos workers sobre la misma cola compiten por los mismos mensajes y no hay forma de saber cuál ejecutó qué.

### 7. Índices de MongoDB

El backend los crea al arrancar (`AUTO_CREATE_INDEXES=true` por defecto). Si prefieres gestionarlos como una migración, ponlo en `false` y ejecuta:

```bash
PYTHONPATH=. python tools/create_indexes.py              # crear (idempotente)
PYTHONPATH=. python tools/create_indexes.py --dry-run    # ver qué crearía
PYTHONPATH=. python tools/create_indexes.py --stats      # tamaños y uso
```

### 8. Las pruebas

La suite no necesita infraestructura: Mongo, Redis, ElasticSearch y la caché están simulados o desactivados.

```bash
pytest -q                                  # el backend y todos los plugins instalados
pytest -q tests                            # solo el backend
pytest -q archihub/plugins/<slug>/tests    # solo un plugin
```

`tests/` contiene solo las pruebas del backend. Las de cada plugin van en su propia carpeta, `archihub/plugins/<slug>/tests/`, para que viajen con el plugin; `pytest` las recoge de ahí, y `conftest.py`, en la raíz, prepara el entorno para ambas. Esas carpetas no se copian en la imagen. Como los nombres de los archivos de prueba no pueden repetirse entre carpetas, conviene incluir el nombre del plugin (`test_plugin_<slug>.py`).

Si instalaste con pip sin el extra `dev`, instala `pytest` y `pytest-asyncio` antes.

### 9. Desarrollar un plugin

Un plugin es un directorio que se copia en `archihub/plugins/<slug>/` y que expone una función `build()`. Todo lo que hay en esa carpeta está en `.gitignore` salvo los plugins que vienen con el backend, así que un plugin instalado no se versiona por accidente.

Para saber cuáles están instalados, cuáles están activos y si el conjunto activo es montable:

```bash
PYTHONPATH=. python -c "from archihub.plugins.framework.discovery import \
    get_active_plugin_slugs, list_installed_plugins, assert_active_plugins_are_mountable; \
    print('activos:', get_active_plugin_slugs()); \
    print('instalados:', list_installed_plugins()); \
    assert_active_plugins_are_mountable(); print('el guard pasa')"
```

Qué plugins están activos decide qué rutas se montan y qué tareas de Celery se registran, y ambas cosas se leen **una sola vez, al arrancar**: activar un plugin no cambia nada observable hasta que todos los procesos se reinician.

---

## Documentación de la Api de la herramienta. *Para desarrolladores*

Como se mencionó anteriormente, como usuario puedes desarrollar una interfaz completamente personalizada. Para facilitar esta tarea, hemos puesto a disposición de los usuarios de la herramienta la documentación de la Api de ArchiHUB. Para acceder a ella, el aplicativo se debe [haber iniciado](https://archihub-app.github.io/archihub.github.io/es/install_local/#arrancar-el-aplicativo).

Con el aplicativo andando, debes acceder al endpoint de Swagger en la URL [http://localhost:{FASTAPI_RUN_PORT}/apidocs/](http://localhost:11000/apidocs/), donde *FASTAPI_RUN_PORT* es el número de puerto configurado en las varibles de entorno y que por defecto es 11000. La especificación en crudo está disponible en `/openapi.json` (y en `/apispec_1.json`, que se mantiene por compatibilidad con los enlaces existentes).

En un entorno de desarrollo levantado según las instrucciones anteriores, la documentación queda en [http://localhost:5000/apidocs/](http://localhost:5000/apidocs/).

## Guías de uso del aplicativo

En cuanto al uso del aplicativo, te recomendamos revisar las [guías en video](https://www.youtube.com/watch?v=XrH0VRjUpys&list=PLzh6tCpowSeuJ7QOqjVL_lM5ASIcBdQXu) para el uso de ArchiHUB.

## Citación

Peña, N. (2023-2025). ArchiHUB: Digital public infrastructure for community archives (Version X.X.X) [Computer software]. https://github.com/Archihub-App 

## Documentación del proyecto con DeepWiki

Además de la [documentación oficial](https://archihub-app.github.io/archihub.github.io/), Se ha puesto a disposición de los usuarios una documentación generada automáticamente del proyecto usando DeepWiki. Esta documentación se encuentra disponible aquí:

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Archihub-App/archihub-backend)
