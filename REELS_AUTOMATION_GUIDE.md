# 📸 Guía Completa de Automatización: Instagram Reels, Facebook & YouTube Shorts con Cloudflare R2

Esta automatización permite publicar Reels en Instagram, Facebook Pages y YouTube Shorts diariamente de forma 100% desatendida.
Los videos se almacenan en **Cloudflare R2** (sin costes de descarga/egress) y se publican a través de las APIs oficiales (**Meta Graph API v21.0** y **YouTube Data API v3**) mediante **GitHub Actions** o ejecución local en Python.

---

## 🏗️ ¿Cómo Funciona la Cola de Publicación?

1. En tu bucket de Cloudflare R2, creas una carpeta llamada `queue/` (o subes los videos a la raíz).
2. Subes tus videos en formato vertical 9:16 (`.mp4` o `.mov`), por ejemplo:
   - `queue/fiesta-barco-01-dj-barco-grande.mp4`
3. **Definición de Textos y Descripciones:**
   - **Opción A (Catálogo Centralizado Multiplataforma - Recomendado):** Subes un archivo `textos-reels.json` con claves correspondientes al nombre de cada video y sus campos específicos:
     - `instagram`: Copy con hashtags y llamadas a acción para Reels.
     - `facebook`: Copy adaptado con enlace web directo de reserva.
     - `youtube_titulo`: Título conciso optimizado (añade `#Shorts` automáticamente).
     - `youtube_descripcion`: Descripción con enlaces y detalles del plan.
     - `texto_en_pantalla` / `tiktok`: Para subtítulos o TikTok.
   - **Opción B (Archivo Individual .txt):** Creas un `.txt` con el mismo nombre del video (`fiesta-barco-01-dj-barco-grande.txt`).
   > *Nota importante: El texto genérico predeterminado antiguo de excursiones ha sido desactivado permanentemente. Si un video no tiene texto definido en `textos-reels.json` ni en `.txt`, el sistema lo omitirá automáticamente para garantizar que nunca se publique sin su copy específico.*
4. Cada día, la automatización:
   - Toma el siguiente video en orden alfabético/cronológico que tenga texto asignado para cada plataforma.
   - Resuelve el título y copy personalizado correspondiente (`textos-reels.json` o `.txt`).
   - Genera una URL pública segura para que los servidores de Meta descarguen el video, y realiza subida directa mediante streaming/chunking a YouTube Shorts.
   - Publica en **Instagram** (5 reels/día en verano, 6 en invierno).
   - Publica en **Facebook** en las **2 horas pico de mayor audiencia** (13:20/14:20 y 18:20).
   - Publica en **YouTube Shorts** en las **2 horas pico de mayor audiencia** (13:20/14:20 y 18:20) para maximizar la retención y cuidar la cuota de la API de Google.
   - Gestiona el estado de publicación con un archivo automático `.status.json` en R2.
   - **Regla de archivado:** Solo cuando un video se ha publicado en **todas las plataformas configuradas (Instagram, Facebook y YouTube)**, se mueve automáticamente junto con su `.status.json` a `published/` (el archivo central `textos-reels.json` permanece intacto).

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
2. Haz clic en **New repository secret** y añade cada uno de los siguientes con los valores correspondientes:
   - `R2_ACCOUNT_ID`
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`
   - `R2_BUCKET_NAME`
   - `R2_PUBLIC_DOMAIN` *(opcional)*
   - `INSTAGRAM_USER_ID`
   - `INSTAGRAM_ACCESS_TOKEN`
   - `FACEBOOK_PAGE_ID`
   - `FACEBOOK_PAGE_ACCESS_TOKEN`
   - `YOUTUBE_CLIENT_ID`
   - `YOUTUBE_CLIENT_SECRET`
   - `YOUTUBE_REFRESH_TOKEN`

### Horario de Publicación Inteligente (Transición Automática Verano / Invierno):
El sistema gestiona automáticamente el cambio de estación según la normativa oficial de la UE (Directiva 2000/84/CE):

* **Instagram (Frecuencia Completa):**
  * **Verano (CEST, UTC+2) — 5 Reels diarios:** 07:20 | 10:20 | 13:20 | 16:20 | 18:20
  * **Invierno (CET, UTC+1) — 6 Reels diarios:** 07:20 | 09:20 | 12:20 | 14:20 | 16:20 | 18:20

* **Facebook & YouTube Shorts (2 Videos diarios en Horas Pico de Máxima Audiencia):**
  * **Verano (CEST, UTC+2):** 
    * `13:20` *(Almuerzo / pausa de mediodía)*
    * `18:20` *(Tarde / salida del trabajo y tiempo libre)*
  * **Invierno (CET, UTC+1):** 
    * `14:20` *(Almuerzo / hora pico en España)*
    * `18:20` *(Tarde / salida del trabajo y tiempo libre)*

```yaml
schedule:
  # Disparadores UTC combinados para España (Península / Madrid) en el minuto :20:
  - cron: '20 5,6,8,11,13,14,15,16,17 * * *'
```
*(El script se ejecuta al minuto `:20` para evitar saturaciones de cola en GitHub Actions. En las franjas pico publica en todas las plataformas activas; en las restantes, solo en Instagram. Ningún video se traslada a `published/` hasta que todas las redes lo hayan emitido).*


### Ejecución Manual desde GitHub:
En la pestaña **Actions** de GitHub, selecciona el flujo **Publicar Daily Reel Instagram, Facebook & YouTube Shorts** y pulsa **Run workflow**. Puedes elegir activar el modo Dry Run o indicar un video específico para probarlo inmediatamente.
