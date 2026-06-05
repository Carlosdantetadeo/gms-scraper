import streamlit as st
import json
import time
import os
import requests
import pandas as pd
import gspread
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import zipfile
import shutil
import tempfile
import fitz  # PyMuPDF
import re
from apify_client import ApifyClient
import io
import numpy as np
import scipy.ndimage as ndi
from urllib.parse import urljoin
from PIL import Image
from collections import Counter

try:
    from rembg import remove as _rembg_remove, new_session as _rembg_new_session
    REMBG_AVAILABLE = True
except Exception:
    REMBG_AVAILABLE = False
    _rembg_remove = None
    _rembg_new_session = None

# Cargar variables de entorno desde .env.local si existe
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="GMS Scraper Intelligence", layout="wide", page_icon="🕵️‍♂️")

# --- MÓDULOS DEL PROYECTO ---
from core.ui_styles import apply_custom_styles
from core.utils import get_driver, extract_json_ld, parse_product_schema, connect_gspread

# --- ESTILOS PREMIUM (CSS) ---
apply_custom_styles()

# --- CONSTANTES ---
_PRODUCT_CODE_COMMON_WORDS = {
    'PRECIO', 'SUGERIDO', 'LIFESTYLE', 'ATHLETIC', 'RUNNING',
    'WALKING', 'TRAINING', 'MAYOR', 'EDICION', 'MODELO'
}


# --- FUNCIONES AUXILIARES PARA DETECCIÓN DE CÓDIGOS DE PRODUCTO ---
def is_product_code(word):
    """Valida si una palabra es un código de producto (ej: NACALIF0231A0203 o 25503-M1)."""
    if len(word) < 5 or len(word) > 20:
        return False
    alpha_count = sum(ch.isalpha() for ch in word)
    digit_count = sum(ch.isdigit() for ch in word)
    if alpha_count < 1 or digit_count < 1:
        return False
    if word.upper() in _PRODUCT_CODE_COMMON_WORDS:
        return False
    return True

def code_score(code):
    """Puntaje: códigos de 10-16 chars (típicos) tienen mayor prioridad."""
    length = len(code)
    if 10 <= length <= 16:
        return 100 + length
    elif 7 <= length <= 20:
        return 50 + length
    return length


# --- FUNCIONES AUXILIARES PDF (módulo-nivel para evitar redefinición en cada run) ---
def find_variant_codes(text):
    """Busca códigos de variante tipo 25515-M1, 25503-M12, etc."""
    if not text:
        return []
    codes = re.findall(r'\b(\d{4,6}[\-]?[A-Z]+\d{1,3})\b', text.upper())
    if codes:
        return codes
    candidates = []
    words = re.split(r'[\s,;|]+', text.upper())
    for word in words:
        clean_word = re.sub(r'^[^A-Z0-9]+|[^A-Z0-9]+$', '', word)
        if is_product_code(clean_word):
            candidates.append(clean_word)
    return candidates


def extract_sizes_from_text(text):
    """Extrae tallas de texto. Busca secuencias de números 33-48."""
    if not text:
        return ""
    clean = re.sub(r'[\n\r]+', ' ', text).strip()
    nums = re.findall(r'\b([3][3-9]|[4][0-8])\b', clean)
    if len(nums) >= 2:
        return " ".join(nums)
    return ""


def chunk_text(text: str, chunk_size: int = 350, overlap: int = 60) -> list:
    """Divide texto en chunks con overlap para preservar contexto entre fragmentos."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + chunk_size]))
        i += chunk_size - overlap
    return [c for c in chunks if c.strip()]


def cosine_similarity_search(query_emb: np.ndarray, corpus_embs: np.ndarray, top_k: int = 5):
    """Retorna índices y scores de los top_k chunks más similares a la query."""
    sims = corpus_embs @ query_emb / (
        np.linalg.norm(corpus_embs, axis=1) * np.linalg.norm(query_emb) + 1e-9
    )
    top_idx = np.argsort(sims)[::-1][:top_k]
    return [(int(i), float(sims[i])) for i in top_idx]


def extract_page_prices_and_model(all_text_blocks, page_height, page_obj, ocr_reader=None):
    """Extrae Modelo, Precio por Mayor y Precio Sugerido de una página PDF.
    Usa ocr_reader (EasyOCR) como fallback si los textos no tienen precios."""
    precio_mayor = ""
    precio_sugerido = ""
    modelo = ""

    # PASO 1: Buscar en TODOS los bloques de texto de la página
    for tb in all_text_blocks:
        text_upper = tb['text'].upper()

        if not modelo:
            modelo_match = re.search(r'MODELO\s*[:\s]*(\d{4,6})', text_upper)
            if modelo_match:
                modelo = modelo_match.group(1)

        if not precio_mayor and 'MAYOR' in text_upper:
            price_match = re.search(r'(?:S/|S\.|S\s*/)\s*\.?\s*(\d+(?:[.,]\d+)?)', tb['text'])
            if price_match:
                precio_mayor = f"S/ {price_match.group(1)}"
            else:
                nums = re.findall(r'\b(\d{2,4})\b', tb['text'])
                for n in nums:
                    if 10 <= int(n) <= 9999:
                        precio_mayor = f"S/ {n}"
                        break

        if not precio_sugerido and 'SUGERIDO' in text_upper:
            price_match = re.search(r'(?:S/|S\.|S\s*/)\s*\.?\s*(\d+(?:[.,]\d+)?)', tb['text'])
            if price_match:
                precio_sugerido = f"S/ {price_match.group(1)}"
            else:
                nums = re.findall(r'\b(\d{2,4})\b', tb['text'])
                for n in nums:
                    if 10 <= int(n) <= 9999:
                        precio_sugerido = f"S/ {n}"
                        break

    # PASO 2: Si no encontró modelo, extraerlo del primer código detectado
    if not modelo:
        for tb in all_text_blocks:
            if tb['codes']:
                m = re.match(r'(\d{4,6})', tb['codes'][0])
                if m:
                    modelo = m.group(1)
                    break

    # PASO 3: Fallback — buscar precios con S/ en la zona inferior (65%+)
    if not precio_mayor and not precio_sugerido:
        bottom_threshold = page_height * 0.65
        bottom_prices = []
        for tb in all_text_blocks:
            if tb['bbox'].y0 < bottom_threshold:
                continue
            prices_found = re.findall(r'(?:S/|S\.)\s*\.?\s*(\d+(?:[.,]\d+)?)', tb['text'])
            bottom_prices.extend(prices_found)
        if len(bottom_prices) >= 2:
            nums_sorted = sorted(set([int(p.replace(',', '.').split('.')[0]) for p in bottom_prices]))
            precio_mayor = f"S/ {nums_sorted[0]}"
            precio_sugerido = f"S/ {nums_sorted[-1]}"
        elif len(bottom_prices) == 1:
            precio_sugerido = f"S/ {bottom_prices[0]}"

    # PASO 4: OCR fallback — renderiza zona inferior y la lee con EasyOCR
    if not precio_mayor and not precio_sugerido and ocr_reader is not None:
        try:
            bottom_clip = fitz.Rect(0, page_height * 0.80, page_obj.rect.width, page_height)
            ocr_pix = page_obj.get_pixmap(matrix=fitz.Matrix(3, 3), clip=bottom_clip)
            ocr_img = Image.open(io.BytesIO(ocr_pix.tobytes("png"))).convert("RGB")

            ocr_results = ocr_reader.readtext(np.array(ocr_img))
            ocr_full = " ".join([t for (_, t, _) in ocr_results])
            ocr_upper = ocr_full.upper()

            if not modelo:
                m_match = re.search(r'MODELO\s*[:\s]*(\d{4,6})', ocr_upper)
                if m_match:
                    modelo = m_match.group(1)

            if 'MAYOR' in ocr_upper:
                pm = re.search(r'MAYOR[^\d]*(?:S/?\.?\s*)?(\d+)', ocr_upper)
                if pm:
                    precio_mayor = f"S/ {pm.group(1)}"

            if 'SUGERIDO' in ocr_upper:
                ps = re.search(r'SUGERIDO[^\d]*(?:S/?\.?\s*)?(\d+)', ocr_upper)
                if ps:
                    precio_sugerido = f"S/ {ps.group(1)}"

            if not precio_mayor and not precio_sugerido:
                ocr_prices = re.findall(r'S/?\.?\s*(\d+)', ocr_full)
                if len(ocr_prices) >= 2:
                    sorted_p = sorted(set([int(p) for p in ocr_prices if 10 <= int(p) <= 9999]))
                    if sorted_p:
                        precio_mayor = f"S/ {sorted_p[0]}"
                        precio_sugerido = f"S/ {sorted_p[-1]}"
        except Exception:
            pass

    return modelo, precio_mayor, precio_sugerido


# --- MODELOS PESADOS CACHEADOS (se cargan una sola vez por sesión de Streamlit) ---
@st.cache_resource(show_spinner="Cargando modelo OCR es/en (primera vez)...")
def _get_ocr_reader_es_en():
    try:
        import easyocr
        return easyocr.Reader(['es', 'en'], gpu=False, verbose=False)
    except Exception:
        return None

@st.cache_resource(show_spinner="Cargando modelo OCR en (primera vez)...")
def _get_ocr_reader_en():
    try:
        import easyocr
        return easyocr.Reader(['en'], gpu=False, verbose=False)
    except Exception:
        return None

@st.cache_resource(show_spinner="Cargando modelo de embeddings multilingüe (primera vez ~380 MB)...")
def _get_embedding_model():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    except Exception:
        return None


@st.cache_resource(show_spinner="Cargando modelo IA de fondos (primera vez)...")
def _get_rembg_session():
    if not REMBG_AVAILABLE:
        return None
    try:
        return _rembg_new_session("isnet-general-use")
    except Exception:
        return None


# --- SIDEBAR: NAV & CONFIG ---
st.sidebar.title("🎛️ Panel de Control")

st.sidebar.subheader("📍 Selección de Herramienta")
mode = st.sidebar.radio(
    "Elige el proceso a ejecutar:",
    [
        "1. 🗺️ MAPEO (Solo Jerarquía)",
        "2. 📸 SCRAPER (Imágenes + Datos)",
        "3. 📄 PDF SCRAPER (Imágenes + Precios)",
        "4. 🌐 WEB INTEL (Apify Powered)",
        "5. 🪄 AI BACKGROUND REMOVER",
        "6. 📺 YOUTUBE EXTRACTOR",
        "7. 🧠 VIDEO RAG"
    ],
    index=0
)
st.sidebar.markdown("---")

st.sidebar.header("⚙️ Configuración Cloud")
uploaded_file = st.sidebar.file_uploader("Sube tu 'credentials.json'", type="json")

sheet_name = "Productos Scrapeados"
json_content = None

local_creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
if uploaded_file is None and os.path.exists(local_creds_path):
    try:
        with open(local_creds_path, "r") as f:
            json_content = json.load(f)
        st.sidebar.success("✅ 'credentials.json' detectado automáticamente.")
    except Exception as e:
        st.sidebar.error(f"Error leyendo archivo local: {e}")
elif uploaded_file is not None:
    try:
        json_content = json.load(uploaded_file)
        st.sidebar.success("Credenciales cargadas.")
    except Exception as e:
        st.sidebar.error(f"Error procesando JSON subido: {e}")

if json_content:
    try:
        client_email = json_content.get("client_email", "Desconocido")
        st.sidebar.info("🔑 **Service Account Email:**")
        st.sidebar.code(client_email, language="text")
        st.sidebar.warning(f"⚠️ Asegúrate de compartir tu Google Sheet '{sheet_name}' con este email.")

        if st.sidebar.button("Conectar a Google Sheets"):
            with st.spinner("Conectando..."):
                conn, error = connect_gspread(json_content, sheet_name)
                if conn:
                    st.session_state['sheet_connection'] = conn
                    st.sidebar.success(f"¡Conectado a '{sheet_name}'!")
                    expected_headers = ["Nombre", "Precio", "Categoría", "Nombre Archivo Local", "Link Original"]
                    try:
                        headers = conn.row_values(1)
                        if headers != expected_headers:
                            st.sidebar.info("Actualizando cabeceras...")
                            conn.insert_row(expected_headers, 1)
                    except Exception:
                        pass
                else:
                    if error == "SpreadsheetNotFound":
                        st.sidebar.error(f"❌ No se encontró la hoja '{sheet_name}'.")
                        st.sidebar.markdown(f"""
                        **Solución:**
                        1. Crea una Hoja de Cálculo en Google Drive.
                        2. Llámanla exactamente: `{sheet_name}`
                        3. Comparte con: `{client_email}`
                        """)
                    else:
                        st.sidebar.error(f"Error de conexión: {error}")

        if 'sheet_connection' in st.session_state:
            st.sidebar.success("🟢 Google Sheets conectado")
    except Exception as e:
        st.sidebar.error(f"Error procesando JSON: {e}")


# --- MODO 1: MAPEO DE JERARQUÍA ---
if mode == "1. 🗺️ MAPEO (Solo Jerarquía)":
    st.subheader("1. 🗺️ Mapeo de Jerarquía (Solo Estructura)")
    st.info("Genera un mapa visual de categorías y subcategorías. No descarga productos.")

    home_url = st.text_input("URL Principal (Home)", placeholder="https://tienda.com")
    request_delay_map = st.slider(
        "Delay entre solicitudes (seg)", min_value=0.5, max_value=5.0, value=1.0, step=0.5,
        help="Pausa entre cada URL escaneada. Aumentar si el sitio bloquea bots."
    )
    st.warning("⚠️ El análisis completo recorrerá todos los enlaces del menú principal. Puede tardar varios minutos en tiendas grandes.")

    if st.button("🔍 Analizar Estructura Completa"):
        if not home_url:
            st.error("Ingresa una URL.")
        else:
            try:
                with st.spinner(f"Escaneando estructura global de {home_url}..."):
                    hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    r = requests.get(home_url, headers=hdrs, timeout=10)
                    soup = BeautifulSoup(r.content, 'html.parser')

                    navs = soup.find_all('nav')
                    if not navs:
                        st.warning("No se detectaron etiquetas <nav>. Buscando enlaces en listas generales...")
                        lists = soup.find_all('ul')
                    else:
                        lists = []
                        for nav in navs:
                            lists.extend(nav.find_all('ul'))

                    seen_links = set()
                    potential_links = []
                    for ul in lists:
                        for li in ul.find_all('li'):
                            a = li.find('a')
                            if a and a.get('href') and a.get_text(strip=True):
                                full_link = urljoin(home_url, a.get('href'))
                                if full_link.startswith(home_url) and full_link not in seen_links:
                                    parent = li.find_parent('ul').find_parent('li')
                                    parent_text = parent.find('a').get_text(strip=True) if (parent and parent.find('a')) else "Root"
                                    potential_links.append({
                                        "Nivel 1": parent_text,
                                        "Nivel 2": a.get_text(strip=True),
                                        "URL": full_link
                                    })
                                    seen_links.add(full_link)

                    st.info(f"Se detectaron {len(potential_links)} categorías. Iniciando escaneo profundo...")
                    progress_bar = st.progress(0)
                    architecture_data = []

                    for idx, cat in enumerate(potential_links):
                        try:
                            r_cat = requests.get(cat["URL"], headers=hdrs, timeout=5)
                            soup_cat = BeautifulSoup(r_cat.content, 'html.parser')
                            h1 = soup_cat.find('h1')
                            h1_text = h1.get_text(strip=True) if h1 else ""
                            h2s = [h.get_text(strip=True) for h in soup_cat.find_all('h2')[:3]]
                            row = cat.copy()
                            row["Título Sección"] = f"H1: {h1_text} | H2: {', '.join(h2s)}"
                            architecture_data.append(row)
                        except Exception:
                            row = cat.copy()
                            row["Título Sección"] = "Error al acceder"
                            architecture_data.append(row)

                        time.sleep(request_delay_map)
                        progress_bar.progress((idx + 1) / len(potential_links))

                    if architecture_data:
                        df_arch = pd.DataFrame(architecture_data)
                        st.success("Análisis completado.")
                        st.dataframe(df_arch)
                        st.session_state['modo1_csv'] = df_arch.to_csv(index=False).encode('utf-8')
                    else:
                        st.error("No se encontraron enlaces válidos.")
            except Exception as e:
                st.error(f"Error analizando estructura: {e}")

    if 'modo1_csv' in st.session_state:
        st.download_button(
            "💾 Descargar Estructura (CSV)",
            st.session_state['modo1_csv'],
            "estructura_arquitectura.csv",
            "text/csv"
        )


# --- MODO 2: SCRAPER POR RANGOS ---
elif mode == "2. 📸 SCRAPER (Imágenes + Datos)":
    st.subheader("2. 📸 Scraper de Productos (Full Data)")
    st.info("Descarga Imágenes y Datos (Excel) simultáneamente recorriendo páginas.")

    col1, col2 = st.columns([3, 1])
    with col1:
        base_url = st.text_input("URL Base (ej: https://tienda.com/zapatos?page=)", placeholder="https://tienda.com/zapatos?page=")
    with col2:
        st.write("Rango de Páginas")
        c_start, c_end = st.columns(2)
        start_page = c_start.number_input("Desde", min_value=1, value=1)
        end_page = c_end.number_input("Hasta", min_value=1, value=5)

    request_delay_scraper = st.slider(
        "Delay entre páginas (seg)", min_value=1, max_value=15, value=3,
        help="Tiempo de espera tras cargar cada página. Aumentar en sitios lentos o con anti-bot."
    )

    start_bulk = st.button("🚀 Iniciar Ciclo de Scraping", type="primary")

    if start_bulk:
        if not json_content:
            st.error("⚠️ Faltan las credenciales. Sube 'credentials.json'.")
        elif not base_url:
            st.error("⚠️ Falta la URL Base.")
        else:
            # Reusar conexión existente en sesión o crear una nueva
            sheet_connection = st.session_state.get('sheet_connection')
            if not sheet_connection:
                sheet_connection, conn_error = connect_gspread(json_content, sheet_name)
                if not sheet_connection:
                    st.error(f"Error conectando a Sheets: {conn_error}")
                    st.stop()
                st.session_state['sheet_connection'] = sheet_connection

            driver = get_driver()
            if not driver:
                st.stop()

            temp_dir = tempfile.mkdtemp()
            all_products_collected = []
            total_pages = end_page - start_page + 1
            progress_bar = st.progress(0)
            status_text = st.empty()

            try:
                for idx, current_page in enumerate(range(start_page, end_page + 1)):
                    target_url = f"{base_url}{current_page}"
                    status_text.write(f"⏳ Procesando página {current_page}/{end_page}: `{target_url}`")

                    try:
                        driver.get(target_url)
                        time.sleep(request_delay_scraper)
                        html = driver.page_source

                        page_products = []

                        schemas = extract_json_ld(html)
                        for s in schemas:
                            p = parse_product_schema(s, target_url)
                            if p:
                                page_products.append(p)

                        if not page_products:
                            soup = BeautifulSoup(html, 'html.parser')
                            potential_products = soup.select(
                                '.product, .product-item, .product-card, .item, li.item, .card, div[data-product-id]'
                            )
                            for p_el in potential_products:
                                item = {}
                                name_el = p_el.select_one('.product-name, .name, .title, .product-title, h3, h2, h4, a.title')
                                if not name_el:
                                    continue
                                item['Nombre'] = name_el.get_text(strip=True)

                                url_el = p_el.select_one('a')
                                item['URL'] = urljoin(base_url, url_el.get('href')) if (url_el and url_el.get('href')) else target_url

                                price_el = p_el.select_one('.price, .amount, .special-price, span[class*="price"], [data-price]')
                                if price_el:
                                    item['Precio'] = price_el.get_text(strip=True)
                                else:
                                    raw_text = p_el.get_text(separator=' ', strip=True)
                                    match = re.search(r'(?:S/|\$|USD|S\.)\s*\d+(?:[.,]\d+)?', raw_text)
                                    item['Precio'] = match.group(0) if match else 'Consultar'
                                item['Categoría'] = 'General'

                                sku_el = p_el.select_one('.sku, [data-sku], [data-product-sku], .product-code, [data-id]')
                                if sku_el:
                                    item['Codigo'] = (
                                        sku_el.get('data-sku') or sku_el.get('data-product-sku')
                                        or sku_el.get('data-id') or sku_el.get_text(strip=True)
                                    )
                                else:
                                    item['Codigo'] = ''

                                img_el = p_el.select_one('img')
                                item['Imagen URL'] = (img_el.get('data-src') or img_el.get('src') or '') if img_el else ''

                                page_products.append(item)

                        if page_products:
                            for p in page_products:
                                filename = ""
                                img_url = p.get('Imagen URL')
                                codigo_val = p.get('Codigo', '')
                                if img_url:
                                    try:
                                        img_url = urljoin(target_url, img_url)
                                        r_img = requests.get(img_url, stream=True, timeout=5)
                                        if r_img.status_code == 200:
                                            base_name = "".join(
                                                c for c in str(codigo_val) if c.isalnum() or c in '-_'
                                            ).strip() if codigo_val else ""
                                            if not base_name:
                                                clean_name = "".join(
                                                    c for c in p.get('Nombre', 'img') if c.isalnum() or c == ' '
                                                ).strip()
                                                base_name = f"{current_page}_{clean_name[:40]}"
                                            filename = f"{base_name}.jpg"
                                            with open(os.path.join(temp_dir, filename), 'wb') as f:
                                                r_img.raw.decode_content = True
                                                shutil.copyfileobj(r_img.raw, f)
                                    except Exception:
                                        pass

                                row = [str(x) if x else "" for x in [
                                    p.get('Nombre'), p.get('Precio'),
                                    p.get('Categoría'), filename, p.get('URL')
                                ]]
                                sheet_connection.append_row(row)

                            all_products_collected.extend(page_products)
                            st.toast(f"Página {current_page}: {len(page_products)} items.")
                        else:
                            st.warning(f"Página {current_page} sin datos.")

                    except Exception as e:
                        st.error(f"Error en página {current_page}: {e}")

                    progress_bar.progress((idx + 1) / total_pages)

            finally:
                driver.quit()
                status_text.write("✅ Ciclo finalizado.")

                if all_products_collected:
                    st.success(f"Total: {len(all_products_collected)} productos.")
                    zip_path_tmp = tempfile.mktemp(suffix=".zip")
                    with zipfile.ZipFile(zip_path_tmp, 'w') as zipf:
                        for root, dirs, files in os.walk(temp_dir):
                            for file in files:
                                zipf.write(os.path.join(root, file), file)
                    st.session_state['modo2_zip'] = zip_path_tmp

                shutil.rmtree(temp_dir, ignore_errors=True)

    if 'modo2_zip' in st.session_state and os.path.exists(st.session_state['modo2_zip']):
        with open(st.session_state['modo2_zip'], "rb") as f:
            st.download_button("📦 Descargar ZIP Imágenes", f, "imagenes_master_tool.zip", "application/zip")


# --- MODO 3: PDF SCRAPER ---
elif mode == "3. 📄 PDF SCRAPER (Imágenes + Precios)":
    st.subheader("3. 📄 Scraper de PDF (Imágenes y Precios)")
    st.info("Sube un catálogo en PDF para extraer imágenes y detectar precios automáticamente.")

    pdf_file = st.file_uploader("Sube tu archivo PDF", type="pdf")
    clean_pdf_bg = st.checkbox("🪄 Limpiar fondo con IA (Extraer solo la zapatilla en fondo blanco)")

    if pdf_file:
        if st.button("🔍 Analizar PDF"):
            with st.spinner("Procesando PDF..."):
                pdf_ai_session = None
                if clean_pdf_bg:
                    pdf_ai_session = _get_rembg_session()
                    if pdf_ai_session is None:
                        st.warning("No se pudo cargar el modelo de IA. Las imágenes se exportarán sin remover el fondo.")

                # OCR cacheado: se carga una sola vez en la sesión de Streamlit
                ocr_reader_pdf = _get_ocr_reader_es_en()

                t_path = os.path.join(tempfile.gettempdir(), f"temp_upload_{int(time.time())}.pdf")
                with open(t_path, "wb") as f:
                    f.write(pdf_file.getbuffer())

                temp_dir_img = tempfile.mkdtemp()

                try:
                    product_data = []
                    mapping_data = []
                    img_count = 0
                    last_modelo = ""
                    last_precio_mayor = ""
                    last_precio_sugerido = ""

                    with fitz.open(t_path) as doc:
                        seen_xrefs = set()
                        for i in range(len(doc)):
                            page = doc[i]
                            page_num = i + 1
                            page_height = page.rect.height
                            page_width = page.rect.width

                            text_blocks = page.get_text("blocks")
                            parsed_texts = []
                            for b in text_blocks:
                                if b[6] == 0:
                                    text = b[4].strip()
                                    if not text:
                                        continue
                                    parsed_texts.append({
                                        'bbox': fitz.Rect(b[:4]),
                                        'text': text,
                                        'codes': find_variant_codes(text),
                                        'sizes': extract_sizes_from_text(text)
                                    })

                            page_modelo, page_precio_mayor, page_precio_sugerido = extract_page_prices_and_model(
                                parsed_texts, page_height, page, ocr_reader=ocr_reader_pdf
                            )

                            if page_precio_mayor or page_precio_sugerido:
                                last_modelo = page_modelo or last_modelo
                                last_precio_mayor = page_precio_mayor
                                last_precio_sugerido = page_precio_sugerido
                            else:
                                page_modelo = page_modelo or last_modelo
                                page_precio_mayor = last_precio_mayor
                                page_precio_sugerido = last_precio_sugerido

                            if not page_modelo and last_modelo:
                                page_modelo = last_modelo

                            product_data.append({
                                "Página": page_num,
                                "Modelo": page_modelo,
                                "Precio Mayor": page_precio_mayor,
                                "Precio Sugerido": page_precio_sugerido
                            })

                            image_info_list = page.get_image_info(xrefs=True)
                            valid_images_info = [
                                img for img in image_info_list
                                if (img['bbox'][2] - img['bbox'][0]) * (img['bbox'][3] - img['bbox'][1]) >= 10000
                                and (img['bbox'][2] - img['bbox'][0]) * (img['bbox'][3] - img['bbox'][1]) < page_width * page_height * 0.85
                            ]

                            for img_raw in doc.get_page_images(i):
                                xref = img_raw[0]
                                smask_xref = img_raw[1]

                                if xref in seen_xrefs:
                                    continue

                                img_bbox = next(
                                    (fitz.Rect(info['bbox']) for info in valid_images_info if info['xref'] == xref),
                                    None
                                )
                                if not img_bbox:
                                    continue
                                seen_xrefs.add(xref)

                                img_center_x = (img_bbox.x0 + img_bbox.x1) / 2
                                img_width = img_bbox.x1 - img_bbox.x0
                                max_dx = max(img_width * 0.8, 120)
                                code_candidates = []
                                size_candidates = []

                                for pt in parsed_texts:
                                    pt_center_x = (pt['bbox'].x0 + pt['bbox'].x1) / 2
                                    dx = abs(img_center_x - pt_center_x)
                                    dy = pt['bbox'].y0 - img_bbox.y1
                                    if dx < max_dx and -30 < dy < 250:
                                        if pt['codes']:
                                            code_candidates.append((dy, pt['codes'][0], pt))
                                        if pt['sizes']:
                                            size_candidates.append((dy, pt['sizes']))

                                closest_code = ""
                                closest_size = ""
                                if code_candidates:
                                    code_candidates.sort(key=lambda x: abs(x[0]))
                                    closest_code = code_candidates[0][1]
                                    code_block = code_candidates[0][2]
                                    if code_block['sizes']:
                                        closest_size = code_block['sizes']
                                    elif size_candidates:
                                        size_candidates.sort(key=lambda x: abs(x[0]))
                                        closest_size = size_candidates[0][1]
                                elif size_candidates:
                                    size_candidates.sort(key=lambda x: abs(x[0]))
                                    closest_size = size_candidates[0][1]

                                pix = fitz.Pixmap(doc, xref)
                                if pix.n - pix.alpha > 3:
                                    pix = fitz.Pixmap(fitz.csRGB, pix)

                                try:
                                    img_base = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
                                except Exception:
                                    continue

                                if smask_xref > 0:
                                    try:
                                        mask_pix = fitz.Pixmap(doc, smask_xref)
                                        mask_img = Image.open(io.BytesIO(mask_pix.tobytes("png"))).convert("L")
                                        if img_base.size != mask_img.size:
                                            mask_img = mask_img.resize(img_base.size)
                                        img_base.putalpha(mask_img)
                                    except Exception:
                                        pass

                                bg_canvas = Image.new("RGBA", img_base.size, (255, 255, 255, 255))
                                composite_img = Image.alpha_composite(bg_canvas, img_base).convert("RGB")

                                code_label = re.sub(r'[^\w\-]', '', closest_code)
                                filename_parts = [f"pag{page_num}"]
                                if code_label:
                                    filename_parts.append(code_label)
                                if closest_size:
                                    size_clean = re.sub(r'[^\d\-]', '', re.sub(r'[\s\n\r]+', '-', closest_size).strip('-'))
                                    if size_clean:
                                        filename_parts.append(f"T{size_clean}")

                                img_filename = "_".join(filename_parts) + ".jpg"
                                save_path = os.path.join(temp_dir_img, img_filename)

                                if clean_pdf_bg and pdf_ai_session:
                                    try:
                                        ai_input = composite_img.copy()
                                        if ai_input.width > 2048 or ai_input.height > 2048:
                                            ai_input.thumbnail((2048, 2048), Image.LANCZOS)
                                        mask_img_ai = _rembg_remove(
                                            ai_input, session=pdf_ai_session,
                                            post_process_mask=False, alpha_matting=False, only_mask=True
                                        )
                                        if mask_img_ai.size != composite_img.size:
                                            mask_img_ai = mask_img_ai.resize(composite_img.size, Image.LANCZOS)
                                        mask_arr = np.array(mask_img_ai)
                                        mask_bool = mask_arr > 10
                                        labeled, num_features = ndi.label(mask_bool)
                                        if num_features > 1:
                                            comp_sizes = ndi.sum(mask_bool, labeled, range(num_features + 1))
                                            comp_sizes[0] = 0
                                            valid_mask = (labeled == np.argmax(comp_sizes))
                                            mask_arr = np.where(valid_mask, mask_arr, 0)
                                        clean_mask_ai = Image.fromarray(mask_arr, mode='L')
                                        final_rgba = composite_img.convert("RGBA")
                                        final_rgba.putalpha(clean_mask_ai)
                                        final_white_bg = Image.new("RGBA", final_rgba.size, (255, 255, 255, 255))
                                        Image.alpha_composite(final_white_bg, final_rgba).convert("RGB").save(
                                            save_path, "JPEG", quality=100, subsampling=0
                                        )
                                    except Exception as e:
                                        st.warning(f"Aviso AI: {str(e)}")
                                        composite_img.save(save_path, "JPEG", quality=100)
                                else:
                                    composite_img.save(save_path, "JPEG", quality=100)

                                mapping_data.append({
                                    "Nombre Archivo": img_filename,
                                    "Página": page_num,
                                    "Modelo": page_modelo,
                                    "Código": closest_code,
                                    "Tallas": closest_size,
                                    "Precio Mayor": page_precio_mayor,
                                    "Precio Sugerido": page_precio_sugerido
                                })
                                img_count += 1

                    if mapping_data:
                        pd.DataFrame(mapping_data).to_csv(
                            os.path.join(temp_dir_img, "asociacion_productos.csv"), index=False
                        )

                    st.success(f"Detección finalizada: {img_count} imágenes y {len(product_data)} páginas procesadas.")

                    if mapping_data:
                        st.write("---")
                        st.subheader("📊 Detalle por Zapatilla")
                        st.dataframe(pd.DataFrame(mapping_data), use_container_width=True)

                    if product_data:
                        st.write("---")
                        st.subheader("💰 Precios por Página")
                        st.dataframe(pd.DataFrame(product_data), use_container_width=True)

                    if img_count > 0:
                        zip_path_pdf = tempfile.mktemp(suffix=".zip")
                        with zipfile.ZipFile(zip_path_pdf, 'w') as zipf:
                            for root, dirs, files in os.walk(temp_dir_img):
                                for file in files:
                                    zipf.write(os.path.join(root, file), file)
                        st.session_state['modo3_zip'] = zip_path_pdf

                except Exception as e:
                    st.error(f"Error procesando PDF: {e}")
                finally:
                    if os.path.exists(t_path):
                        os.unlink(t_path)
                    shutil.rmtree(temp_dir_img, ignore_errors=True)

    if 'modo3_zip' in st.session_state and os.path.exists(st.session_state['modo3_zip']):
        with open(st.session_state['modo3_zip'], "rb") as f:
            st.download_button("📦 Descargar Imágenes Extraídas", f, "imagenes_pdf.zip", "application/zip")


# --- MODO 4: WEB INTEL (APIFY) ---
elif mode == "4. 🌐 WEB INTEL (Apify Powered)":
    st.subheader("4. 🌐 Scraper Inteligente via Apify")
    st.info("Utiliza herramientas de Apify para scrapear sitios complejos (Instagram, Google Maps, etc.)")

    api_token = st.text_input(
        "Apify API Token", type="password",
        value=os.environ.get("APIFY_TOKEN", ""),
        help="Consigue uno en apify.com · También puedes definirlo en .env.local"
    )

    actor_id = st.selectbox("Selecciona el Actor", [
        "apify/web-scraper",
        "apify/instagram-scraper",
        "compass/crawler-google-places",
        "apify/google-search-scraper",
        "clockworks/tiktok-scraper"
    ])

    start_urls = st.text_area("URLs de inicio (una por línea)", placeholder="https://tienda.com/productos")
    max_items = st.number_input(
        "Máximo de resultados", min_value=1, max_value=5000, value=50,
        help="Cantidad máxima de ítems a extraer. Más ítems = más tiempo y costo en Apify."
    )

    if st.button("🚀 Iniciar Trabajo en Apify"):
        if not api_token:
            st.error("⚠️ Falta el API Token de Apify.")
        elif not start_urls:
            st.error("⚠️ Ingresa al menos una URL.")
        else:
            with st.spinner(f"Iniciando {actor_id} en la infraestructura de Apify..."):
                try:
                    client = ApifyClient(api_token)
                    run_input = {
                        "startUrls": [{"url": u.strip()} for u in start_urls.split("\n") if u.strip()],
                        "maxItems": int(max_items)
                    }
                    if actor_id == "apify/web-scraper":
                        run_input["pageFunction"] = (
                            "async function pageFunction(context) "
                            "{ return { url: context.request.url, title: await context.page.title() }; }"
                        )

                    run = client.actor(actor_id).call(run_input=run_input)
                    st.success(f"Trabajo finalizado! ID: {run['id']}")

                    results = list(client.dataset(run["defaultDatasetId"]).iterate_items())
                    if results:
                        st.write(f"Se encontraron {len(results)} registros.")
                        df_apify = pd.DataFrame(results)
                        st.dataframe(df_apify)
                        st.session_state['modo4_csv'] = df_apify.to_csv(index=False).encode('utf-8')
                    else:
                        st.warning("No se encontraron registros en el dataset.")

                except Exception as e:
                    st.error(f"Error ejecutando Apify: {str(e)}")

    if 'modo4_csv' in st.session_state:
        st.download_button("📊 Descargar Resultados (CSV)", st.session_state['modo4_csv'], "apify_results.csv", "text/csv")


# --- MODO 5: AI BACKGROUND REMOVER ---
elif mode == "5. 🪄 AI BACKGROUND REMOVER":
    st.subheader("5. 🪄 Removedor de Fondo en Bloque (AI)")
    st.info("Sube múltiples imágenes. La IA eliminará el fondo y descargará las imágenes con fondo blanco o transparente.")

    if not REMBG_AVAILABLE:
        st.error("⚠️ La librería 'rembg' no está disponible. Revisa los logs de instalación.")
    else:
        bg_col1, bg_col2 = st.columns([2, 1])
        with bg_col1:
            uploaded_images = st.file_uploader(
                "Sube imágenes (JPG, PNG, WEBP)",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True
            )
        with bg_col2:
            st.write("Configuración de Salida")
            output_format = st.radio("Fondo de las imágenes:", [
                "Blanco Puro (JPG)",
                "Transparente (PNG)",
                "Solo Zapatilla + Mismo Fondo + Nombrar con Código (JPG)"
            ])
            keep_largest = st.checkbox(
                "🧹 Filtrar logos/sellos", value=True,
                help="Conserva únicamente el objeto más grande (la zapatilla principal)."
            )

        if uploaded_images:
            if st.button("🪄 Iniciar Remoción en Bloque"):
                temp_dir_bg = tempfile.mkdtemp()
                progress_bar = st.progress(0)
                status_text = st.empty()

                # Modelos cacheados: se cargan una sola vez por sesión
                general_session = _get_rembg_session()
                reader = None
                if output_format == "Solo Zapatilla + Mismo Fondo + Nombrar con Código (JPG)":
                    reader = _get_ocr_reader_en()
                    if reader is None:
                        st.warning("OCR no disponible. Las imágenes se nombrarán con el nombre de archivo original.")

                processed_count = 0
                for idx, img_file in enumerate(uploaded_images):
                    status_text.write(f"Procesando: {img_file.name} ({idx+1}/{len(uploaded_images)})...")
                    try:
                        input_img = Image.open(img_file).convert("RGBA")
                        mask_img = _rembg_remove(
                            input_img,
                            session=general_session,
                            post_process_mask=False,
                            alpha_matting=False,
                            only_mask=True
                        )

                        mask_arr = np.array(mask_img)
                        if keep_largest:
                            try:
                                mask_bool = mask_arr > 10
                                labeled, num_features = ndi.label(mask_bool)
                                if num_features > 1:
                                    sizes = ndi.sum(mask_bool, labeled, range(num_features + 1))
                                    sizes[0] = 0
                                    valid_mask = (labeled == np.argmax(sizes))
                                    mask_arr = np.where(valid_mask, mask_arr, 0)
                            except Exception as e:
                                st.warning(f"Error evaluando sellos extra: {e}")

                        input_img.putalpha(Image.fromarray(mask_arr, mode='L'))
                        output_img = input_img
                        base_name = os.path.splitext(img_file.name)[0]

                        if output_format == "Blanco Puro (JPG)":
                            white_bg = Image.new("RGBA", output_img.size, (255, 255, 255, 255))
                            final_img = Image.alpha_composite(white_bg, output_img).convert("RGB")
                            final_img.save(os.path.join(temp_dir_bg, f"{base_name}_blanco.jpg"), "JPEG", quality=100, subsampling=0)

                        elif output_format == "Transparente (PNG)":
                            output_img.save(os.path.join(temp_dir_bg, f"{base_name}_transparente.png"), "PNG")

                        else:
                            detected_code = base_name
                            if reader is not None:
                                try:
                                    img_cv = np.array(input_img.convert('RGB'))
                                    results = reader.readtext(img_cv)
                                    all_candidate_codes = []
                                    ocr_box_texts = []

                                    for (_, text, _) in results:
                                        ocr_box_texts.append(text)
                                        for word in re.split(r'[\s,;|.\-]+', text.upper()):
                                            clean_word = re.sub(r'[^A-Z0-9]', '', word)
                                            if is_product_code(clean_word):
                                                all_candidate_codes.append(clean_word)

                                    for k in range(len(ocr_box_texts) - 1):
                                        combined = (
                                            re.sub(r'[^A-Z0-9]', '', ocr_box_texts[k].upper())
                                            + re.sub(r'[^A-Z0-9]', '', ocr_box_texts[k + 1].upper())
                                        )
                                        if is_product_code(combined):
                                            all_candidate_codes.append(combined)

                                    if all_candidate_codes:
                                        all_candidate_codes = list(set(all_candidate_codes))
                                        all_candidate_codes.sort(key=code_score, reverse=True)
                                        detected_code = all_candidate_codes[0]
                                except Exception as e:
                                    st.warning(f"Aviso OCR: {e}")

                            try:
                                sample_points = [
                                    (0.50, 0.05), (0.50, 0.95), (0.30, 0.10),
                                    (0.70, 0.10), (0.50, 0.50), (0.20, 0.50), (0.80, 0.50),
                                ]
                                sampled_colors = []
                                for px_frac, py_frac in sample_points:
                                    sx = min(int(input_img.width * px_frac), input_img.width - 1)
                                    sy = min(int(input_img.height * py_frac), input_img.height - 1)
                                    c = input_img.getpixel((sx, sy))
                                    sampled_colors.append((c[0] // 10 * 10, c[1] // 10 * 10, c[2] // 10 * 10, 255))
                                bg_color = Counter(sampled_colors).most_common(1)[0][0]
                            except Exception:
                                bg_color = (235, 235, 235, 255)

                            original_bg = Image.new("RGBA", output_img.size, bg_color)
                            final_img = Image.alpha_composite(original_bg, output_img).convert("RGB")
                            final_img.save(os.path.join(temp_dir_bg, f"{detected_code}.jpg"), "JPEG", quality=100, subsampling=0)

                        processed_count += 1

                    except Exception as e:
                        st.error(f"Error procesando {img_file.name}: {str(e)}")

                    progress_bar.progress((idx + 1) / len(uploaded_images))

                status_text.write("✅ Proceso completado.")

                if processed_count > 0:
                    zip_path_bg = tempfile.mktemp(suffix=".zip")
                    with zipfile.ZipFile(zip_path_bg, 'w') as zipf:
                        for root, dirs, files in os.walk(temp_dir_bg):
                            for file in files:
                                zipf.write(os.path.join(root, file), file)
                    st.session_state['modo5_zip'] = zip_path_bg

                shutil.rmtree(temp_dir_bg, ignore_errors=True)

    if 'modo5_zip' in st.session_state and os.path.exists(st.session_state['modo5_zip']):
        with open(st.session_state['modo5_zip'], "rb") as f:
            st.download_button(
                "📦 Descargar Imágenes sin Fondo (ZIP)",
                f,
                "imagenes_sin_fondo.zip",
                "application/zip",
                type="primary"
            )


# --- MODO 7: VIDEO RAG ---
elif mode == "7. 🧠 VIDEO RAG":
    st.subheader("7. 🧠 RAG — Interroga cualquier video de YouTube")
    st.info(
        "Carga la transcripción de un video y hazle preguntas. "
        "La IA encuentra los fragmentos exactos donde se habla del tema que buscas."
    )

    # ── Paso 1: Cargar transcripción ──────────────────────────────────────────
    tab_yt, tab_txt = st.tabs(["🔗 Desde YouTube (subtítulos)", "📄 Subir .txt (Whisper)"])

    with tab_yt:
        st.caption("Usa los subtítulos oficiales del video. Rápido, sin descarga de audio.")
        yt_url_rag = st.text_input("URL del video", key="rag_url_input",
                                    placeholder="https://www.youtube.com/watch?v=...")
        lang_rag = st.selectbox("Idioma del video", ["auto", "es", "en"], key="rag_lang")

        if st.button("📥 Obtener Transcripción", key="rag_fetch"):
            if not yt_url_rag:
                st.error("Ingresa una URL.")
            else:
                vid_match = re.search(r'(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})', yt_url_rag)
                if not vid_match:
                    st.error("URL de YouTube no válida.")
                else:
                    with st.spinner("Obteniendo transcripción..."):
                        try:
                            from youtube_transcript_api import (
                                YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
                            )
                            vid_id = vid_match.group(1)
                            if lang_rag == "auto":
                                try:
                                    segs = YouTubeTranscriptApi.get_transcript(vid_id, languages=["es", "en"])
                                except Exception:
                                    segs = YouTubeTranscriptApi.get_transcript(vid_id)
                            else:
                                segs = YouTubeTranscriptApi.get_transcript(vid_id, languages=[lang_rag])

                            # Construir texto con timestamps para referencia
                            lines = []
                            for seg in segs:
                                m, s = int(seg["start"] // 60), int(seg["start"] % 60)
                                lines.append(f"[{m:02d}:{s:02d}] {seg['text']}")
                            transcript_raw = "\n".join(lines)

                            # Obtener título
                            try:
                                import yt_dlp
                                with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                                    info = ydl.extract_info(yt_url_rag, download=False)
                                    rag_title = info.get("title", "Video sin título")
                            except Exception:
                                rag_title = f"Video {vid_id}"

                            st.session_state["rag_transcript"] = transcript_raw
                            st.session_state["rag_title"] = rag_title
                            st.session_state.pop("rag_chunks", None)
                            st.session_state.pop("rag_embeddings", None)
                            st.session_state.pop("rag_history", None)
                            st.success(f"Transcripción cargada: {len(segs)} segmentos.")

                        except TranscriptsDisabled:
                            st.error("Este video no tiene subtítulos disponibles.")
                            st.info("Usa `youtube_transcriber.py` para transcribir con Whisper y sube el .txt en la pestaña de al lado.")
                        except NoTranscriptFound:
                            st.warning("No hay subtítulos en ese idioma. Prueba con 'auto'.")
                        except ImportError:
                            st.error("Instala 'youtube-transcript-api': añádelo a requirements.txt.")
                        except Exception as e:
                            st.error(f"Error: {e}")

    with tab_txt:
        st.caption("Si el video no tiene subtítulos, usa `youtube_transcriber.py` y sube el .txt aquí.")
        uploaded_txt = st.file_uploader("Sube la transcripción (.txt)", type=["txt"], key="rag_txt_upload")
        txt_title = st.text_input("Nombre del video (opcional)", placeholder="Clase de Python — Semana 3")

        if st.button("📥 Cargar Transcripción", key="rag_load_txt"):
            if not uploaded_txt:
                st.error("Sube un archivo .txt primero.")
            else:
                transcript_raw = uploaded_txt.read().decode("utf-8", errors="ignore")
                st.session_state["rag_transcript"] = transcript_raw
                st.session_state["rag_title"] = txt_title or uploaded_txt.name.replace(".txt", "")
                st.session_state.pop("rag_chunks", None)
                st.session_state.pop("rag_embeddings", None)
                st.session_state.pop("rag_history", None)
                st.success(f"Archivo cargado: {len(transcript_raw.split())} palabras.")

    # ── Paso 2: Indexar ───────────────────────────────────────────────────────
    if "rag_transcript" in st.session_state:
        st.markdown("---")
        title_display = st.session_state["rag_title"]
        word_count = len(st.session_state["rag_transcript"].split())
        st.success(f"**{title_display}** — {word_count:,} palabras cargadas")

        if "rag_embeddings" not in st.session_state:
            chunk_size = st.slider("Tamaño de fragmento (palabras)", 150, 600, 350, 50,
                                    help="Fragmentos más pequeños = respuestas más precisas. Más grandes = más contexto.")
            if st.button("🧮 Indexar para RAG", type="primary"):
                emb_model = _get_embedding_model()
                if emb_model is None:
                    st.error("Modelo de embeddings no disponible. Instala 'sentence-transformers'.")
                else:
                    with st.spinner("Dividiendo texto y generando embeddings..."):
                        chunks = chunk_text(st.session_state["rag_transcript"], chunk_size=chunk_size)
                        progress = st.progress(0)
                        batch_size = 32
                        all_embs = []
                        for i in range(0, len(chunks), batch_size):
                            batch = chunks[i:i + batch_size]
                            embs = emb_model.encode(batch, show_progress_bar=False)
                            all_embs.append(embs)
                            progress.progress(min((i + batch_size) / len(chunks), 1.0))

                        st.session_state["rag_chunks"] = chunks
                        st.session_state["rag_embeddings"] = np.vstack(all_embs)
                        st.session_state["rag_history"] = []
                        st.success(f"✅ {len(chunks)} fragmentos indexados. ¡Listo para preguntas!")
                        st.rerun()
        else:
            n_chunks = len(st.session_state["rag_chunks"])
            st.info(f"🟢 {n_chunks} fragmentos indexados — puedes hacer preguntas.")
            if st.button("🔄 Re-indexar con otro tamaño", key="rag_reindex"):
                st.session_state.pop("rag_embeddings", None)
                st.session_state.pop("rag_chunks", None)
                st.session_state.pop("rag_history", None)
                st.rerun()

    # ── Paso 3: Chat ──────────────────────────────────────────────────────────
    if "rag_embeddings" in st.session_state:
        st.markdown("---")
        st.subheader("💬 Pregunta sobre el video")

        top_k = st.slider("Fragmentos a mostrar por pregunta", 1, 6, 3, key="rag_topk")

        # Renderizar historial de chat
        history = st.session_state.get("rag_history", [])
        for turn in history:
            with st.chat_message("user"):
                st.write(turn["question"])
            with st.chat_message("assistant"):
                st.write(f"**Fragmentos más relevantes de:** *{st.session_state['rag_title']}*")
                for rank, (chunk_text_result, score) in enumerate(turn["results"], 1):
                    relevance = "🟢" if score > 0.55 else "🟡" if score > 0.35 else "🔴"
                    with st.expander(f"{relevance} Fragmento {rank} — relevancia {score:.0%}", expanded=(rank == 1)):
                        st.write(chunk_text_result)

        # Input de nueva pregunta
        question = st.chat_input("Escribe tu pregunta aquí...")
        if question:
            emb_model = _get_embedding_model()
            if emb_model is None:
                st.error("Modelo de embeddings no disponible.")
            else:
                q_emb = emb_model.encode([question], show_progress_bar=False)[0]
                top_results = cosine_similarity_search(
                    q_emb,
                    st.session_state["rag_embeddings"],
                    top_k=top_k
                )
                results_text = [
                    (st.session_state["rag_chunks"][idx], score)
                    for idx, score in top_results
                ]
                st.session_state["rag_history"].append({
                    "question": question,
                    "results": results_text
                })
                st.rerun()

        # Botón para limpiar historial
        if history:
            if st.button("🗑️ Limpiar conversación"):
                st.session_state["rag_history"] = []
                st.rerun()
