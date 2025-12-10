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

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="E-Com Intelligence Tool", layout="wide", page_icon="🧠")

# --- ESTILOS CUSTOM (CSS) ---
st.markdown("""
<style>
    .main-header {font-size: 2.5rem; color: #4B0082; text-align: center; margin-bottom: 1rem;}
    .sub-header {font-size: 1.2rem; color: #555;}
    .stButton>button {width: 100%; border-radius: 5px; font-weight: bold;}
    .reportview-container {background: #f0f2f6;}
    div.stButton > button:first-child {background-color: #4B0082; color: white;}
    div.stButton > button:hover {border-color: #4B0082; color: #4B0082;}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">🧠 Herramienta de Scraper & Intelligence E-commerce</div>', unsafe_allow_html=True)
st.markdown('<div style="text-align: center; margin-bottom: 2rem;">Análisis de Arquitectura y Extracción Masiva de Datos</div>', unsafe_allow_html=True)
st.markdown("""
Esta herramienta scrapea productos de una URL dada detectando **JSON-LD (Schema.org)** automáticamente.
Sube tu archivo `credentials.json`, conecta tu Google Sheet y descarga las imágenes.
""")

# --- FUNCIONES DE UTILIDAD ---

def get_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        st.error(f"Error al iniciar Chrome Driver: {e}")
        return None

def extract_json_ld(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    schemas = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                schemas.extend(data)
            else:
                schemas.append(data)
        except:
            continue
    return schemas

def parse_product_schema(schema, url):
    """Intenta extraer datos de un esquema de tipo Product."""
    item = {}
    
    # Verificar si es Product
    msg_type = schema.get('@type')
    if isinstance(msg_type, list):
        if 'Product' not in msg_type:
            return None
    elif msg_type != 'Product':
        return None
        
    # Nombre
    item['Nombre'] = schema.get('name')
    
    # URL
    item['URL'] = schema.get('url', url) # Fallback a la url actual si no está en el schema
    if item['URL'] and not item['URL'].startswith('http'):
         # Intentar arreglar URL relativa (muy básico)
         from urllib.parse import urljoin
         item['URL'] = urljoin(url, item['URL'])

    # Categoría
    item['Categoría'] = schema.get('category', 'Desconocido')
    
    # Imagen
    image = schema.get('image')
    if isinstance(image, list):
        item['Imagen URL'] = image[0] if image else None
    elif isinstance(image, dict):
        item['Imagen URL'] = image.get('url')
    else:
        item['Imagen URL'] = image
        
    # Precio (Offers)
    offers = schema.get('offers')
    if isinstance(offers, list):
        offer = offers[0]
    else:
        offer = offers
        
    if offer:
        item['Precio'] = offer.get('price')
        item['Moneda'] = offer.get('priceCurrency')
    else:
        item['Precio'] = 'N/A'
        item['Moneda'] = ''
        
    return item

def connect_gspread(json_content, sheet_name):
    try:
        # Usamos el método moderno de gspread que maneja scopes automáticamente
        client = gspread.service_account_from_dict(json_content)
        sheet = client.open(sheet_name).sheet1
        return sheet, None
    except gspread.SpreadsheetNotFound:
        return None, "SpreadsheetNotFound"
    except Exception as e:
        return None, str(e)

# --- SIDEBAR: NAV & CONFIG ---
st.sidebar.title("🎛️ Panel de Control")

# 1. SELECTOR DE MODO (Prioridad UX)
st.sidebar.subheader("📍 Selección de Herramienta")
mode = st.sidebar.radio(
    "Elige el proceso a ejecutar:", 
    ["1. 🗺️ MAPEO (Solo Jerarquía)", "2. 📸 SCRAPER (Imágenes + Datos)"],
    index=0
)
st.sidebar.markdown("---")

# 2. CONFIGURACIÓN (Credenciales)
st.sidebar.header("⚙️ Configuración Cloud")
uploaded_file = st.sidebar.file_uploader("Sube tu 'credentials.json'", type="json")

sheet_connection = None
sheet_name = "Productos Scrapeados" # Hardcoded como pide el prompt
json_content = None

# Intentar cargar localmente si existe y no se ha subido nada
local_creds_path = "credentials.json"
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
        # MOSTRAR EMAIL DE SERVICIO
        client_email = json_content.get("client_email", "Desconocido")
        st.sidebar.info(f"🔑 **Service Account Email:**")
        st.sidebar.code(client_email, language="text")
        st.sidebar.warning(f"⚠️ Asegúrate de compartir tu Google Sheet '{sheet_name}' con este email.")

        if st.sidebar.button("Conectar a Google Sheets"):
            with st.spinner("Conectando..."):
                sheet_connection, error = connect_gspread(json_content, sheet_name)
                
                if sheet_connection:
                    st.sidebar.success(f"¡Conectado a '{sheet_name}'!")
                    # Check headers
                    expected_headers = ["Nombre", "Precio", "Categoría", "Nombre Archivo Local", "Link Original"]
                    try:
                        headers = sheet_connection.row_values(1)
                        if headers != expected_headers:
                            st.sidebar.info("Actualizando cabeceras...")
                            sheet_connection.insert_row(expected_headers, 1)
                    except:
                        pass
                else:
                    if error == "SpreadsheetNotFound":
                        st.sidebar.error(f"❌ No se encontró la hoja '{sheet_name}'.")
                        st.sidebar.markdown(f"""
                        **Solución:**
                        1. Crea una Hoja de Cálculo en Google Drive.
                        2. Llámanla exactamente: `{sheet_name}`
                        3. Dale click a "Compartir" y pega el email de arriba: `{client_email}`
                        """)
                    else:
                        st.sidebar.error(f"Error de conexión: {error}")
    except Exception as e:
        st.sidebar.error(f"Error procesando JSON: {e}")



# --- MODO 1: MAPEO DE JERARQUÍA ---
if mode == "1. 🗺️ MAPEO (Solo Jerarquía)":
    st.subheader("1. 🗺️ Mapeo de Jerarquía (Solo Estructura)")
    st.info("Genera un mapa visual de categorías y subcategorías. No descarga productos.")
    
    home_url = st.text_input("URL Principal (Home)", placeholder="https://tienda.com")
    st.warning("⚠️ El análisis completo recorrerá todos los enlaces del menú principal. Puede tardar varios minutos en tiendas grandes.")
    
    if st.button("🔍 Analizar Estructura Completa"):
        if not home_url:
            st.error("Ingresa una URL.")
        else:
            try:
                with st.spinner(f"Escaneando estructura global de {home_url}..."):
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    r = requests.get(home_url, headers=headers, timeout=10)
                    soup = BeautifulSoup(r.content, 'html.parser')
                    
                    navs = soup.find_all('nav')
                    potential_links = []
                    
                    if not navs:
                        st.warning("No se detectaron etiquetas <nav> estándar. Buscando enlaces en listas generales...")
                        lists = soup.find_all('ul')
                    else:
                        lists = []
                        for nav in navs:
                            lists.extend(nav.find_all('ul'))

                    # Recolectar TODOS los enlaces del árbol de navegación
                    seen_links = set()
                    for ul in lists:
                        for li in ul.find_all('li'):
                            a = li.find('a')
                            if a and a.get('href') and a.get_text(strip=True):
                                link_text = a.get_text(strip=True)
                                link_href = a.get('href')
                                from urllib.parse import urljoin
                                full_link = urljoin(home_url, link_href)
                                
                                if full_link.startswith(home_url) and full_link not in seen_links:
                                    # Padres
                                    parent = li.find_parent('ul').find_parent('li')
                                    parent_text = parent.find('a').get_text(strip=True) if (parent and parent.find('a')) else "Root"
                                    
                                    potential_links.append({
                                        "Nivel 1": parent_text,
                                        "Nivel 2": link_text,
                                        "URL": full_link
                                    })
                                    seen_links.add(full_link)
                    
                    # Profundizar (Visitar TODOS los links detectados)
                    total_links = len(potential_links)
                    st.info(f"Se detectaron {total_links} categorías/secciones. Iniciando escaneo profundo...")
                    
                    progress_bar = st.progress(0)
                    architecture_data = []
                    
                    st.write(f"Profundizando en {len(potential_links)} enlaces detectados...")
                    
                    for idx, cat in enumerate(potential_links):
                        try:
                            r_cat = requests.get(cat["URL"], headers=headers, timeout=5)
                            soup_cat = BeautifulSoup(r_cat.content, 'html.parser')
                            
                            # Extraer titulos
                            h1 = soup_cat.find('h1')
                            h1_text = h1.get_text(strip=True) if h1 else ""
                            
                            h2s = [h.get_text(strip=True) for h in soup_cat.find_all('h2')[:3]] # Top 3 H2
                            section_titles = f"H1: {h1_text} | H2: {', '.join(h2s)}"
                            
                            row = cat.copy()
                            row["Título Sección"] = section_titles
                            architecture_data.append(row)
                            
                        except:
                            row = cat.copy()
                            row["Título Sección"] = "Error al acceder"
                            architecture_data.append(row)
                            
                        progress_bar.progress((idx + 1) / len(potential_links))
                    
                    if architecture_data:
                        df_arch = pd.DataFrame(architecture_data)
                        st.success("Análisis completado.")
                        st.dataframe(df_arch)
                        
                        csv = df_arch.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            "💾 Descargar Estructura (CSV)",
                            csv,
                            "estructura_arquitectura.csv",
                            "text/csv"
                        )
                    else:
                        st.error("No se encontraron enlaces válidos.")
            except Exception as e:
                st.error(f"Error analizando estructura: {e}")

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

    start_bulk = st.button("🚀 Iniciar Ciclo de Scraping", type="primary")
    
    if start_bulk:
        if not json_content:
            st.error("⚠️ Faltan las credenciales. Sube 'credentials.json'.")
        elif not base_url:
            st.error("⚠️ Falta la URL Base.")
        else:
            sheet_connection, error = connect_gspread(json_content, sheet_name)
            if not sheet_connection:
                st.error(f"Error conectando a Sheets: {error}")
                st.stop()
                
            driver = get_driver()
            if not driver:
                st.stop()
            
            temp_dir = tempfile.mkdtemp()
            st.toast(f"Carpeta temporal: {temp_dir}")
            
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
                        time.sleep(3) 
                        html = driver.page_source
                        
                        # Estrategia Híbrida (Schema + Fallback)
                        page_products = []
                        
                        # 1. Schema
                        schemas = extract_json_ld(html)
                        for s in schemas:
                            p = parse_product_schema(s, target_url)
                            if p: page_products.append(p)
                                
                        # 2. Fallback
                        if not page_products:
                            soup = BeautifulSoup(html, 'html.parser')
                            potential_products = soup.select('.product, .product-item, .product-card, .item, li.item, .card, div[data-product-id]')
                            for p_el in potential_products:
                                item = {}
                                name_el = p_el.select_one('.product-name, .name, .title, .product-title, h3, h2, h4, a.title')
                                if name_el: item['Nombre'] = name_el.get_text(strip=True)
                                else: continue
                                
                                url_el = p_el.select_one('a')
                                if url_el and url_el.get('href'):
                                    item['URL'] = url_el.get('href')
                                    from urllib.parse import urljoin
                                    item['URL'] = urljoin(base_url, item['URL'])
                                else: item['URL'] = target_url
                                
                                price_el = p_el.select_one('.price, .amount, .special-price, span[class*="price"]')
                                item['Precio'] = price_el.get_text(strip=True) if price_el else 'Consultar'
                                item['Categoría'] = 'General'
                                
                                img_el = p_el.select_one('img')
                                if img_el:
                                    src = img_el.get('data-src') or img_el.get('src')
                                    item['Imagen URL'] = src
                                else: item['Imagen URL'] = ''
                                
                                page_products.append(item)

                        # Subir y Descargar
                        if page_products:
                            for p in page_products:
                                # Columnas: [Nombre, Precio, Categoría, Nombre Archivo Local, Link Original]
                                filename = ""
                                img_url = p.get('Imagen URL')
                                if img_url:
                                    try:
                                        from urllib.parse import urljoin
                                        img_url = urljoin(target_url, img_url)
                                        r_img = requests.get(img_url, stream=True, timeout=5)
                                        if r_img.status_code == 200:
                                            clean_name = "".join([c for c in p.get('Nombre', 'img') if c.isalpha() or c.isdigit() or c==' ']).strip()
                                            filename = f"{current_page}_{clean_name[:40]}.jpg"
                                            with open(os.path.join(temp_dir, filename), 'wb') as f:
                                                r_img.raw.decode_content = True
                                                shutil.copyfileobj(r_img.raw, f)
                                    except:
                                        pass
                                
                                row = [
                                    p.get('Nombre'),
                                    p.get('Precio'),
                                    p.get('Categoría'),
                                    filename,       # Nombre Archivo Local
                                    p.get('URL')    # Link Original
                                ]
                                row = [str(x) if x else "" for x in row]
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
                    
                    with open(zip_path_tmp, "rb") as f:
                        st.download_button("📦 Descargar ZIP Imágenes", f, "imagenes_master_tool.zip", "application/zip")
