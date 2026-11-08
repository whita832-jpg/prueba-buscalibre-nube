# Monitor BuscaLibre México

Este proyecto vigila **cuatro catálogos distintos** cada hora: Alba, Alba Editorial, Acantilado y El Acantilado.

Cada catálogo lleva el filtro **Encuadernación → Tapa Dura** (`ff=tapa+dura`). El código comprueba que el filtro esté activado en el HTML; si no lo está, marca la revisión como incompleta. Se conserva el filtro al paginar y se comprueba el total declarado de libros para no solicitar páginas vacías superfluas.

El 7 de noviembre de 2026, BuscaLibre mostraba aproximadamente 352 + 13 + 20 + 2 = **387 libros en tapa dura**, distribuidos en **11 páginas**. Antes se revisaban **50 páginas** sin filtrar; el ahorro esperado es de unas **42 llamadas por revisión** (53 frente a 11, sin reintentos). La cifra puede cambiar según el catálogo y el número de respuestas fallidas.

**Límite:** el filtro depende de que BuscaLibre clasifique correctamente la encuadernación. Un libro etiquetado incorrectamente por la tienda podría omitirse. Además, el histórico de precios solo se forma para los libros observados en cada revisión.

## Estado actual

- Ejecuta Python en los servidores **macOS estándar de GitHub Actions**; no necesita tu computadora encendida.
- Primero usa acceso directo a BuscaLibre, **sin créditos**. Si GitHub recibe un bloqueo 403, existe un respaldo **opcional** mediante ScrapingAnt, únicamente cuando configures su token en los secretos de GitHub; sin ese token no gasta ningún crédito.
- El acceso directo volvió a su velocidad original: sin pausa obligatoria entre consultas exitosas; solo conserva reintentos breves (4 y 12 segundos) si se recibe un error. En una prueba pausada, GitHub recibió HTTP 403 incluso antes de hacer consultas sucesivas, por lo que reducir la velocidad no resolvió ese bloqueo.
- Guarda el historial en `estado_monitor_v7.json` después de cada ejecución.
- Consulta todas las páginas **filtradas por tapa dura** de cada editorial hasta su final; conserva el filtro en páginas posteriores.
- Exige **novela + tapa dura + precio actual < $200 MXN + precio observado de $400 MXN o más durante tres fechas distintas anteriores + ficha y stock verificables**.
- No se envían alertas cuando el género o el histórico no están confirmados.
- Cuando falla la cobertura, la ejecución queda marcada como fallida y el historial de precios **no se actualiza** con datos parciales.
- **Telegram solo envía avisos de ofertas verificadas**. Los errores, bloqueos 403 y revisiones incompletas se registran en GitHub Actions y nunca generan mensajes de Telegram.

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

## Respaldo opcional con ScrapingAnt (sin pagar)

Si el acceso desde GitHub da HTTP 403 con frecuencia, puedes añadir un tercer secreto:

- `SCRAPINGANT_API_TOKEN`: API Token de tu cuenta de ScrapingAnt.

El monitor intenta primero el acceso directo **en cada ejecución**, incluso si falló en la hora anterior. Si el acceso directo funciona, no consume créditos; si falla, usa ScrapingAnt para esa ejecución. Esto puede agotar los créditos gratuitos del mes: no hay compra ni suscripción automática. Si el respaldo tampoco funciona, registra el fallo y no envía alertas de ofertas basadas en datos parciales.

**Importante:** el plan gratuito de ScrapingAnt no alcanza por sí solo para revisar las 50 páginas todas las horas del mes si GitHub es bloqueado continuamente. No contrates nada sin comprobar cuánto se ha consumido.

## Consultar las ejecuciones

- **Monitor BuscaLibre — historial y alertas**: contiene la tarea manual y programada.
- El horario está expresado en UTC: `17 * * * *` significa aproximadamente el minuto 17 de cada hora.
- El reporte y los CSV se guardan durante siete días como **Artifacts** en cada ejecución.
- `estado_monitor_v7.json` contiene el histórico de precios y el último resultado; no contiene credenciales.
- Puede existir un bloqueo HTTP 403 intermitente desde GitHub. Los intentos fallidos se registran y no se presentan como revisiones completadas.

## Coste y limitaciones

El acceso directo a BuscaLibre es gratuito mientras funcione. El respaldo ScrapingAnt, si lo habilitas, consume créditos de tu cuenta gratuita hasta que se agoten. Los ejecutores **estándar macOS** son gratuitos en repositorios públicos de GitHub, pero GitHub puede retrasar o perder alguna tarea programada. Si la tienda bloquea los servidores en la nube, no es posible garantizar una revisión completa ni una alerta en tiempo real.

Este proyecto solamente lee páginas y comunica alertas. No compra libros ni contrata servicios.
