import streamlit as st

def apply_custom_styles():
    st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;700&display=swap');

    /* Variables and Globals */
    :root {
        --bg-color: #0b0f19;
        --card-bg: rgba(18, 24, 43, 0.6);
        --primary-accent: #00f2fe;
        --secondary-accent: #4facfe;
        --text-main: #f8fafc;
        --text-muted: #94a3b8;
        --border-glass: rgba(255, 255, 255, 0.08);
    }

    /* Main App Background - Premium Dark */
    .stApp, .main {
        font-family: 'Outfit', sans-serif !important;
        background-color: var(--bg-color) !important;
        background-image: 
            radial-gradient(circle at 15% 50%, rgba(79, 172, 254, 0.15), transparent 25%),
            radial-gradient(circle at 85% 30%, rgba(0, 242, 254, 0.15), transparent 25%);
        color: var(--text-main) !important;
    }

    /* Base Typography overrides for Streamlit elements */
    p, h1, h2, h3, h4, h5, h6, label, li {
        font-family: 'Outfit', sans-serif !important;
        color: var(--text-main) !important;
    }

    /* Text Inputs & UI elements */
    .stTextInput>div>div>input, .stNumberInput>div>div>input, .stTextArea>div>div>textarea {
        background-color: rgba(15, 23, 42, 0.8) !important;
        border: 1px solid var(--border-glass) !important;
        color: white !important;
        border-radius: 8px;
        box-shadow: inset 0 2px 4px rgba(0,0,0,0.2) !important;
        transition: border 0.3s ease;
    }
    
    .stTextInput>div>div>input:focus, .stNumberInput>div>div>input:focus {
        border-color: var(--secondary-accent) !important;
        box-shadow: 0 0 0 1px var(--secondary-accent) !important;
    }

    /* Sidebar - Deep Glassmorphism */
    [data-testid="stSidebar"] {
        background: rgba(15, 23, 42, 0.7) !important;
        backdrop-filter: blur(20px) saturate(180%);
        -webkit-backdrop-filter: blur(20px) saturate(180%);
        border-right: 1px solid var(--border-glass);
    }
    
    [data-testid="stSidebarNav"] {
        background-image: none !important;
    }

    /* Uploaders */
    [data-testid="stFileUploadDropzone"] {
        background-color: rgba(30, 41, 59, 0.5) !important;
        border: 2px dashed rgba(79, 172, 254, 0.5) !important;
        border-radius: 15px;
        transition: all 0.3s ease;
    }
    [data-testid="stFileUploadDropzone"]:hover {
        background-color: rgba(30, 41, 59, 0.8) !important;
        border-color: var(--primary-accent) !important;
    }

    /* Hero Section */
    .hero-container {
        position: relative;
        overflow: hidden;
        background: linear-gradient(135deg, rgba(15,23,42,0.8) 0%, rgba(30,41,59,0.9) 100%);
        padding: 3.5rem 2rem;
        border-radius: 24px;
        text-align: center;
        margin-bottom: 2.5rem;
        border: 1px solid rgba(255,255,255,0.05);
        box-shadow: 0 20px 40px rgba(0,0,0,0.3);
    }

    .hero-container::before {
        content: '';
        position: absolute;
        top: -50%; left: -50%;
        width: 200%; height: 200%;
        background: radial-gradient(circle, rgba(79,172,254,0.15) 0%, transparent 50%);
        animation: rotate 20s linear infinite;
        z-index: 0;
        pointer-events: none;
    }

    @keyframes rotate {
        100% { transform: rotate(360deg); }
    }

    .hero-title {
        position: relative;
        z-index: 1;
        font-size: 3.8rem;
        font-weight: 700;
        margin-bottom: 0.5rem;
        background: linear-gradient(to right, var(--primary-accent), var(--secondary-accent));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        letter-spacing: -1px;
    }

    .hero-subtitle {
        position: relative;
        z-index: 1;
        font-size: 1.25rem;
        color: var(--text-muted);
        font-weight: 300;
        letter-spacing: 0.5px;
    }

    /* Buttons */
    .stButton>button {
        background: linear-gradient(135deg, var(--secondary-accent) 0%, var(--primary-accent) 100%) !important;
        color: #0f172a !important;
        border: none !important;
        padding: 0.75rem 2rem !important;
        border-radius: 12px !important;
        font-weight: 700 !important;
        font-size: 1rem !important;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
        box-shadow: 0 10px 20px -10px var(--secondary-accent) !important;
    }

    .stButton>button:hover {
        transform: translateY(-3px) scale(1.02);
        box-shadow: 0 15px 25px -10px var(--primary-accent) !important;
        filter: brightness(1.2);
    }

    /* Checkboxes and Radios */
    .stRadio>div, .stCheckbox>label {
        background: rgba(30, 41, 59, 0.4);
        padding: 0.5rem 1rem;
        border-radius: 8px;
        border: 1px solid rgba(255,255,255,0.05);
    }

    /* Cards */
    .info-card {
        background: var(--card-bg);
        backdrop-filter: blur(12px);
        padding: 1.8rem;
        border-radius: 16px;
        border: 1px solid var(--border-glass);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
        margin-bottom: 1.5rem;
        color: var(--text-muted);
        line-height: 1.6;
        transition: transform 0.3s ease;
    }
    
    .info-card:hover {
        transform: translateY(-5px);
        border-color: rgba(79, 172, 254, 0.3);
    }

    .info-card b {
        color: var(--primary-accent);
    }

    /* DataFrames / Tables */
    [data-testid="stDataFrame"] {
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid var(--border-glass);
        background: rgba(15, 23, 42, 0.8) !important;
    }

    /* Expander */
    .streamlit-expanderHeader {
        background-color: var(--card-bg) !important;
        border-radius: 12px !important;
        border: 1px solid var(--border-glass) !important;
        color: white !important;
    }

    /* Animations for smooth entry */
    @keyframes fadeSlideUp {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }
    .stMarkdown, section[data-testid="stSidebar"] {
        animation: fadeSlideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1);
    }
</style>
""", unsafe_allow_html=True)

    # Header Section
    st.markdown("""
<div class="hero-container">
    <div class="hero-title">GMS INTELLIGENCE</div>
    <div class="hero-subtitle">Next-Gen Visual Scraper & Market Data Engine</div>
</div>
""", unsafe_allow_html=True)

    with st.expander("ℹ️ Guía Rápida de Uso"):
        st.markdown("""
        <div class="info-card">
            Explore sitios web, extraiga datos estructurados mediante <b>Schema.org (JSON-LD)</b> y procese catálogos en PDF en segundos.
            <br><br>
            🚀 <b>Optimizado con IA</b> para analítica de precios, visión computacional y monitoreo de competidores.
        </div>
        """, unsafe_allow_html=True)
