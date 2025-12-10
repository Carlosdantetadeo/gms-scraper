# GMS Universal Scraper

Esta es una aplicación de Streamlit para extraer productos de e-commerce automáticamente y subirlos a Google Sheets.

## Requisitos Previos

1.  **Python 3.8+** instalado.
2.  **Google Chrome** instalado.
3.  Un proyecto en Google Cloud con la API de **Google Sheets** y **Google Drive** habilitada.
4.  Un archivo **`credentials.json`** de tu cuenta de servicio de Google.

## Instalación

1.  Abre una terminal en esta carpeta.
2.  Instala las dependencias:
    ```bash
    pip install -r requirements.txt
    ```

## Uso

1.  Ejecuta la aplicación:
    ```bash
    streamlit run main.py
    ```
2.  Se abrirá una pestaña en tu navegador.
3.  En la barra lateral, sube tu archivo `credentials.json` y dale click a **Conectar**.
4.  Ingresa la **URL de la categoría** que quieres scrapear (ej. `https://tienda.com/zapatillas`).
5.  Click en **Iniciar Scraping**.
6.  Observa cómo se llena tu Google Sheet y descarga el ZIP con las imágenes al final.

## Notas

-   La aplicación busca datos estructurados **Schema.org (JSON-LD)**. Funciona mejor en sitios modernos (Shopify, WooCommerce, Magento, etc.).
-   Las imágenes se descargan localmente de forma temporal.
