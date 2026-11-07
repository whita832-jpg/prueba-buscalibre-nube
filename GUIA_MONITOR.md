# Monitor BuscaLibre México

Este proyecto vigila **cuatro catálogos distintos** cada hora: Alba, Alba Editorial, Acantilado y El Acantilado.

## Estado actual

- Ejecuta Python en los servidores **macOS estándar de GitHub Actions**; no necesita tu computadora encendida.
- Usa acceso directo a BuscaLibre, **sin Firecrawl ni ScrapingAnt**.
- Guarda el historial en `estado_monitor_v7.json` después de cada ejecución.
- Consulta todas las páginas de cada editorial hasta su final.
- Exige **novela + tapa dura + precio actual < $200 MXN + precio observado de $400 MXN o más durante tres fechas distintas anteriores + ficha y stock verificables**.
- No se envían alertas cuando el género o el histórico no están confirmados.
- Cuando falla la cobertura, la ejecución queda marcada como fallida y el historial de precios **no se actualiza** con datos parciales.
- En caso de fallo, puede enviar un aviso de incidencia de Telegram, como máximo uno al día, una vez configurado.

## Configurar Telegram (una sola vez)

1. Abre Telegram y busca `@BotFather`.
2. Escribe `/newbot` y sigue los pasos. Guarda el **token del bot** en un lugar privado.
3. Abre el bot que creaste y envía `/start` y después un mensaje.
4. Para averiguar el ID del chat, abre en tu propio navegador `https://api.telegram.org/botTU_TOKEN/getUpdates`, sustituyendo `TU_TOKEN`. Identifica `"chat":{"id":123...}`.
5. En GitHub, entra en **Settings → Secrets and variables → Actions → New repository secret** y crea:
   - `TELEGRAM_BOT_TOKEN`: el token de BotFather.
   - `TELEGRAM_CHAT_ID`: el número de tu chat.
6. No envíes esos datos al chat ni los incluyas en archivos del repositorio.

Después, en **Actions**, abre **Probar aviso de Telegram** y elige **Run workflow**. Deberías recibir un mensaje en Telegram confirmando la conexión.

## Consultar las ejecuciones

- **Monitor BuscaLibre — historial y alertas**: contiene la tarea manual y programada.
- El horario está expresado en UTC: `17 * * * *` significa aproximadamente el minuto 17 de cada hora.
- El reporte y los CSV se guardan durante siete días como **Artifacts** en cada ejecución.
- `estado_monitor_v7.json` contiene el histórico de precios y el último resultado; no contiene credenciales.
- Puede existir un bloqueo HTTP 403 intermitente desde GitHub. Los intentos fallidos se registran y no se presentan como revisiones completadas.

## Coste y limitaciones

El acceso a BuscaLibre es directo y gratuito mientras funcione. Los ejecutores **estándar macOS** son gratuitos en repositorios públicos de GitHub, pero GitHub puede retrasar o perder alguna tarea programada. Si la tienda bloquea los servidores en la nube, no es posible garantizar una revisión completa ni una alerta en tiempo real.

Este proyecto solamente lee páginas y comunica alertas. No compra libros ni contrata servicios.
