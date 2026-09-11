# Control de Inventario — Mercado Libre

Aplicación de escritorio (Python + PySide6) para llevar el inventario de lo que vendes en
Mercado Libre, con los datos guardados en PostgreSQL (local con Docker o en la nube con Neon).

## Qué hace (v0.1)

- Catálogo de productos: nombre, SKU, ID de publicación de ML, costo, precio y stock mínimo.
- Stock actual de cada producto, con resaltado de **sin stock** (rojo) y **stock bajo** (amarillo).
- Registro de movimientos: **entradas** (compras), **ventas**, **devoluciones** y **ajustes** por conteo físico.
- Historial de movimientos por producto o general.
- Búsqueda, filtro "sólo stock bajo" y resumen con unidades totales y valor del inventario.
- Productos inactivos: los ocultas sin perder su historial.

## Requisitos

- Python 3.11 o superior
- Una base de datos PostgreSQL: en la nube ([Neon](https://neon.com), plan gratuito) o local con Docker Desktop.

## Instalación

```powershell
# 1. Entorno de Python
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Configuración: copia la plantilla y completa los valores (ver abajo)
Copy-Item .env.example .env
```

Toda la configuración (direcciones y contraseñas de la base de datos) vive en `.env`.
Ese archivo **no se sube a git**; `.env.example` es la plantilla sin secretos.

### Opción A: base de datos en la nube (Neon)

Con la [CLI de Neon](https://www.npmjs.com/package/neon):

```powershell
npm i -g neon@latest
neon login
neon link --project-id <id-de-tu-proyecto> --branch production -y
```

`neon link` escribe `DATABASE_URL` en tu `.env`. La app acepta la URL tal como la entrega Neon
(`postgresql://…`). `neon.ts` declara la configuración del proyecto en Neon: `neon config plan`
muestra qué cambiaría y `neon deploy` lo aplica.

### Opción B: base de datos local (Docker)

1. En `.env`, define `POSTGRES_PASSWORD` y usa esa misma contraseña en `DATABASE_URL` y `TEST_DATABASE_URL`.
2. Levanta el contenedor (sólo acepta conexiones desde tu propia computadora):

   ```powershell
   docker compose up -d
   ```

El contenedor se levanta solo cada vez que abres Docker Desktop (`restart: unless-stopped`).

## Uso

Doble clic en **`iniciar.bat`**, o desde la terminal:

```powershell
.\.venv\Scripts\python.exe main.py
```

Atajos: `Ctrl+N` nuevo producto · `Ctrl+R` venta · `Ctrl+E` entrada · `Ctrl+H` historial · `Ctrl+F` buscar · `F5` actualizar.

## Respaldos

Los respaldos contienen tus datos: la carpeta `backups/` está excluida de git.

**Neon** (el plan gratuito sólo permite recuperar las últimas horas, así que conviene respaldar por tu cuenta).
Usa una imagen de PostgreSQL de la misma versión que tu servidor y la URL *sin pooler* (`DATABASE_URL_UNPOOLED` en `.env`):

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
docker run --rm -v "${PWD}\backups:/backups" postgres:18 pg_dump "<DATABASE_URL_UNPOOLED>" -Fc -f "/backups/neon_$(Get-Date -Format yyyyMMdd).dump"
```

**Docker local:**

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
docker exec inventario-db pg_dump -U inventario -Fc -f /tmp/respaldo.dump inventario
docker cp inventario-db:/tmp/respaldo.dump "backups\inventario_$(Get-Date -Format yyyyMMdd).dump"
```

Restaurar un respaldo local (reemplaza los datos actuales):

```powershell
docker cp backups\inventario_AAAAMMDD.dump inventario-db:/tmp/respaldo.dump
docker exec inventario-db pg_restore -U inventario -d inventario --clean /tmp/respaldo.dump
```

> ⚠️ `docker compose down -v` **borra** el volumen con todos los datos locales. Usa `docker compose down` (sin `-v`) para sólo detener.

## Desarrollo

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest
```

Las pruebas usan `TEST_DATABASE_URL` (la base `inventario_test` del contenedor local, que se crea sola
la primera vez) y **borran todas sus tablas** en cada corrida. Por seguridad, se niegan a correr si el
nombre de esa base no contiene `test`.

En GitHub, cada push corre el linter, las pruebas (con un PostgreSQL temporal) y una búsqueda de
secretos con [gitleaks](https://github.com/gitleaks/gitleaks) (`.github/workflows/ci.yml`).

### Estructura

```
inventario/
  config.py        lectura de .env
  db.py            conexión y creación de tablas
  models.py        tablas: products y stock_movements
  services.py      lógica de negocio (la UI sólo habla con esta capa)
  ui/              ventanas PySide6
tests/             pruebas de la lógica contra PostgreSQL
neon.ts            configuración del proyecto en Neon
```

### Modelo de datos

- **products** guarda el stock actual de cada producto.
- **stock_movements** guarda cada cambio de stock con su fecha, cantidad (con signo),
  stock resultante, precio de venta y costo unitario del momento.

El stock sólo cambia a través de movimientos, así que siempre hay un historial auditable.
Ese historial es la base para la analítica futura.

## Seguridad

- Nunca subas `.env`, `.neon` ni respaldos; `.gitignore` ya los excluye.
- Si alguna contraseña o API key llega a quedar expuesta, **cámbiala** (en Neon: *Roles → Reset password*)
  en vez de sólo borrarla del repositorio: el historial de git la conserva.

## Ideas para siguientes versiones

- **Analítica**: productos más vendidos por periodo, rotación de inventario, días de stock restantes,
  margen real por producto y sugerencias de ajuste de precio. Toda la información ya se está guardando
  en `stock_movements`.
- **Comisiones y envío de ML** por venta, para calcular la ganancia neta.
- **Integración con la API de Mercado Libre**: importar ventas automáticamente usando el ID de publicación
  y sincronizar el stock publicado.
- **Migraciones con Alembic** antes de cambiar el esquema, para no perder datos al actualizar.
- Importar/exportar productos desde Excel.
