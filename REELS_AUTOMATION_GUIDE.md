# 📸 Guía Completa de Automatización: Instagram Reels con Cloudflare R2

Esta automatización permite publicar Reels en Instagram diariamente de forma 100% desatendida.
Los videos se almacenan en **Cloudflare R2** (sin costes de descarga/egress) y se publican a través de la **API oficial de Instagram (Meta Graph API v21.0)** mediante **GitHub Actions** o ejecución local en Python.

---

## 🏗️ ¿Cómo Funciona la Cola de Publicación?

1. En tu bucket de Cloudflare R2, creas una carpeta llamada `queue/`.
2. Subes tus videos en formato vertical 9:16 (`.mp4` o `.mov`), por ejemplo:
   - `queue/2026-09-10_montanejos.mp4`
3. *(Opcional pero muy recomendado)*: Creas un archivo de texto `.txt` con el mismo nombre exacto para definir el pie de foto (caption) y hashtags:
   - `queue/2026-09-10_montanejos.txt`
   > Si no subes archivo `.txt`, el sistema usará automáticamente el pie de foto oficial predeterminado de Valencia Tribal con enlaces y hashtags.
4. Cada día, la automatización:
   - Toma el siguiente video en orden alfabético/cronológico.
   - Lee el pie de foto del archivo `.txt`.
   - Genera una URL pública segura para que los servidores de Meta descarguen el video.
   - Envía el video a Instagram y verifica el estado de procesamiento hasta que finalice.
   - Publica el Reel en tu feed de Instagram.
   - Mueve el video y su `.txt` a la carpeta `published/` en R2 para evitar republicaciones.

---

## 1. Configuración de Cloudflare R2

### Paso 1.1: Crear el Bucket
1. Entra en tu panel de [Cloudflare](https://dash.cloudflare.com/) y en el menú lateral haz clic en **R2**.
2. Haz clic en **Create bucket**.
3. Ponle un nombre, por ejemplo: `vlc-tribe-reels` y confirma.

### Paso 1.2: Obtener las Credenciales de la API (S3 Credentials)
1. En la página principal de R2 en Cloudflare, haz clic en **Manage R2 API Tokens** (a la derecha).
2. Haz clic en **Create API Token**.
3. Configuración:
   - **Token name**: `instagram-reels-publisher`
   - **Permissions**: `Object Read & Write`
   - **Specify bucket(s)**: Selecciona el bucket creado (`vlc-tribe-reels`).
   - **TTL**: Puedes dejarlo sin expiración o renovable.
4. Guarda los datos que te mostrará Cloudflare (¡no se vuelven a mostrar!):
   - `Access Key ID` -> Variable `R2_ACCESS_KEY_ID`
   - `Secret Access Key` -> Variable `R2_SECRET_ACCESS_KEY`
   - `Endpoint`: Tu Account ID es la parte previa a `.r2.cloudflarestorage.com` -> Variable `R2_ACCOUNT_ID`

### Paso 1.3: Habilitar Acceso Público (Recomendado)
Para que Meta pueda descargar el video rápidamente:
1. Ve a tu Bucket -> pestaña **Settings**.
2. En la sección **Public Access**:
   - Puedes conectar un dominio personalizado (ej: `media.valenciatribal.com`).
   - O puedes habilitar **R2.dev subdomain** (ej: `pub-xxxxxxxxxxxxxx.r2.dev`).
3. Copia ese dominio en la variable `R2_PUBLIC_DOMAIN`.
> *Nota: Si no habilitas dominio público, el script generará automáticamente URLs prefirmadas de S3 temporales (Presigned URLs).*

---

## 2. Configuración de Instagram Graph API (Meta)

### Paso 2.1: Requisitos Previos en Instagram
1. Tu cuenta de Instagram debe ser **Profesional** (Creador o Empresa), no personal.
   *(En la app de Instagram: Configuración -> Tipo de cuenta y herramientas -> Cambiar a cuenta profesional).*
2. Tu cuenta de Instagram debe estar vinculada a una **Página de Facebook**.
   *(En Meta Business Suite o en Configuración de la Página de Facebook -> Cuentas vinculadas -> Instagram).*

### Paso 2.2: Crear una App en Meta for Developers
1. Ve a [developers.facebook.com](https://developers.facebook.com/) e inicia sesión con tu cuenta de Facebook.
2. Haz clic en **Mis apps** -> **Crear app**.
3. Selecciona tipo: **Otro** -> siguiente -> **Empresa** (Business).
4. Asigna un nombre a la app (ej: `VLC Tribe Publisher`) y crea la app.
5. En el panel de la app, busca el producto **Instagram Graph API** y haz clic en **Configurar**.

### Paso 2.3: Generar Token de Acceso con Permisos
1. En el menú superior de Meta for Developers, ve a **Herramientas** -> **Explorador de la Graph API** ([Graph API Explorer](https://developers.facebook.com/tools/explorer/)).
2. Selecciona tu App en el desplegable.
3. En **Permisos (Permissions)**, añade los siguientes 4 permisos obligatorios:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_show_list`
   - `pages_read_engagement`
4. Haz clic en **Generate Access Token**. Se abrirá una ventana de Facebook para que autorices tu Página y tu cuenta de Instagram.

### Paso 2.4: Obtener el ID de la Cuenta de Instagram (`INSTAGRAM_USER_ID`)
En el mismo Explorador de Graph API, ejecuta esta consulta:
```http
GET /me/accounts?fields=name,instagram_business_account{id,username}
```
Verás una respuesta JSON como esta:
```json
{
  "data": [
    {
      "name": "Valencia Tribal",
      "instagram_business_account": {
        "id": "17841400000000000",
        "username": "valenciatribal"
      }
    }
  ]
}
```
Copia el valor del campo `"id"` (`17841400000000000`) -> Este es tu `INSTAGRAM_USER_ID`.

### Paso 2.5: Convertir el Token a Larga Duración (60 días o Permanente)
El token del explorador dura solo 1-2 horas. Puedes extenderlo a 60 días fácilmente usando nuestra herramienta incluida:
1. En Meta for Developers -> Configuración de la App -> Básica, copia tu **Identificador de la app** (App ID) y la **Clave secreta de la app** (App Secret).
2. Ejecuta en tu terminal:
```bash
python test_reel_setup.py --get-long-lived-token --client-id "TU_APP_ID" --client-secret "TU_APP_SECRET" --token "TU_TOKEN_CORTO"
```
El script te devolverá el token extendido para pegarlo en `.env` como `INSTAGRAM_ACCESS_TOKEN`.

*(Nota: Para un token que no caduque nunca, puedes crear un "Usuario del sistema" en Meta Business Manager con acceso a la app y generar un System User Token con el permiso `instagram_content_publish`).*

---

## 3. Pruebas Locales

Una vez rellenados los campos en tu archivo local `.env`:

1. **Verificar diagnóstico de conexión:**
   ```bash
   python test_reel_setup.py --all
   ```
   Debe mostrar en verde tanto Cloudflare R2 como Instagram Graph API.

2. **Simular publicación (Dry Run):**
   ```bash
   python publish_instagram_reel.py --dry-run
   ```
   Comprueba el video en cola, la generación de URL y la previsualización del caption sin realizar ninguna publicación ni mover archivos.

3. **Publicar un Reel de prueba real:**
   ```bash
   python publish_instagram_reel.py
   ```

---

## 4. Automatización Diaria en GitHub Actions

El repositorio ya cuenta con el flujo configurado en `.github/workflows/publish_reels.yml`.

### Configurar Secretos en GitHub:
1. En tu repositorio en GitHub, ve a **Settings** -> **Secrets and variables** -> **Actions**.
2. Haz clic en **New repository secret** y añade cada uno de los siguientes con los valores de tu `.env`:
   - `R2_ACCOUNT_ID`
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`
   - `R2_BUCKET_NAME`
   - `R2_PUBLIC_DOMAIN` *(opcional)*
   - `INSTAGRAM_USER_ID`
   - `INSTAGRAM_ACCESS_TOKEN`
   - `FACEBOOK_PAGE_ID`
   - `FACEBOOK_PAGE_ACCESS_TOKEN`

### Horario de Publicación Inteligente (Transición Automática Verano / Invierno):
El sistema gestiona automáticamente el cambio de estación según la normativa oficial de la UE (Directiva 2000/84/CE):

* **Horario de Verano (CEST, hasta el último domingo de octubre) — 5 Reels diarios:**
  * 07:10 | 10:10 | 13:10 | 16:10 | 18:10
* **Horario de Invierno (CET, a partir del último domingo de octubre) — 6 Reels diarios:**
  * 07:10 | 09:10 | 12:10 | 14:10 *(hora pico almuerzo)* | 16:10 | 18:10

```yaml
schedule:
  # Disparadores UTC combinados para España (Península / Madrid) en el minuto :10:
  - cron: '10 5,6,8,11,13,14,15,16,17 * * *'
```
*(El script se ejecuta al minuto `:10` para evitar saturaciones de cola en GitHub Actions al minuto `:00`, e implementa tolerancia de desfase de hasta 75 min y un cooldown de 90 min para garantizar publicaciones puntuales sin duplicados).*


### Ejecución Manual desde GitHub:
En la pestaña **Actions** de GitHub, selecciona el flujo **Publicar Daily Reel Instagram & Facebook** y pulsa **Run workflow**. Puedes elegir activar el modo Dry Run o indicar un video específico para probarlo inmediatamente.
