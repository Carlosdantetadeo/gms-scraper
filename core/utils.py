import streamlit as st
import json
import gspread
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import re

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
    
    # Código / SKU
    item['Codigo'] = schema.get('sku') or schema.get('mpn') or ''
    
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
