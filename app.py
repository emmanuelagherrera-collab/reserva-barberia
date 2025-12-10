import streamlit as st
import pandas as pd
import mercadopago
import uuid
from datetime import datetime, timedelta, time
from google.oauth2 import service_account
from googleapiclient.discovery import build
import pytz
import re
import json
import base64
import urllib.parse
import time as time_lib

# ==========================================
# 🔧 ZONA DE CONFIGURACIÓN
# ==========================================
CALENDAR_ID = "emmanuelagherrera@gmail.com"
MP_ACCESS_TOKEN = "APP_USR-3110718966988352-120714-d3a0dd0e9831c38237e3450cea4fc5ef-3044196256"

URL_SHEETS = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSQsZwUWKZAbBMSbJoOAoZOS6ZqbBoFEYAoSOHBvV7amaOPPkXxEYnTnHAelBa-g_EzFibe6jDyvMuc/pub?output=csv"
UBICACION_LAT_LON = pd.DataFrame({'lat': [-33.5226], 'lon': [-70.5986]})
LINK_WHATSAPP = "https://wa.me/56912345678"
ZONA_HORARIA = pytz.timezone('America/Santiago')

st.set_page_config(page_title="Reserva Estilo", page_icon="💈", layout="wide")

# 3. Carga Segura de Credenciales Google
try:
    if "google_credentials" in st.secrets:
        creds_dict = dict(st.secrets["google_credentials"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    else:
        st.error("⚠️ Falta la sección [google_credentials] en secrets.toml")
        st.stop()
except Exception as e:
    st.error(f"Error cargando secretos: {e}")
    st.stop()

# ==========================================
# 🎨 ESTILOS CSS
# ==========================================
st.markdown("""
<style>
    .stButton button { width: 100%; border-radius: 8px; font-weight: bold; }
    div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"] { background-color: transparent; }
    .price-abono { font-size: 1.4rem; font-weight: 800; color: #2e7d32; text-align: right; }
    .price-total { font-size: 0.9rem; color: #757575; text-align: right; text-decoration: none; }
    .badge-pago { background-color: #e8f5e9; color: #2e7d32; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8rem;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stDeployButton {display:none;}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 🧠 GESTIÓN DE ESTADO
# ==========================================
if 'step' not in st.session_state: st.session_state.step = 1
if 'servicio_seleccionado' not in st.session_state: st.session_state.servicio_seleccionado = None
if 'datos_servicio' not in st.session_state: st.session_state.datos_servicio = {}

def resetear_proceso():
    st.session_state.step = 1
    st.session_state.servicio_seleccionado = None
    st.session_state.datos_servicio = {}

# ==========================================
# 🧠 BACKEND Y UTILIDADES
# ==========================================
def empaquetar_datos(datos):
    json_str = json.dumps(datos)
    return base64.urlsafe_b64encode(json_str.encode()).decode()

def desempaquetar_datos(token):
    try:
        json_str = base64.urlsafe_b64decode(token.encode()).decode()
        return json.loads(json_str)
    except: return None

def generar_link_ws_dinamico(telefono_local, nombre, fecha_hora, servicio):
    mensaje = f"Hola 👋, soy {nombre}. Tengo una reserva el {fecha_hora} para {servicio}. Necesito modificarla."
    mensaje_encoded = urllib.parse.quote(mensaje)
    return f"https://wa.me/{telefono_local}?text={mensaje_encoded}"

@st.cache_data(ttl=60)
def cargar_servicios():
    try:
        df = pd.read_csv(URL_SHEETS)
        df.columns = df.columns.str.lower().str.strip()
        
        servicios = {}
        for _, row in df.iterrows():
            desc = row['descripcion'] if 'descripcion' in row else "Servicio profesional."
            precio_total = int(row['precio'])
            abono = int(row['abono']) if 'abono' in row and pd.notna(row['abono']) else precio_total
            
            servicios[row['servicio']] = {
                "duracion": int(row['duracion_min']), 
                "precio_total": precio_total,
                "abono": abono,
                "pendiente": precio_total - abono,
                "descripcion": desc
            }
        return servicios
    except Exception as e:
        st.error(f"Error leyendo Excel: {e}")
        return {}

def conectar_calendario():
    try:
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/calendar']
        )
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        print(f"Error conectando al calendario: {e}")
        return None

# ==========================================
# 🚦 GESTIÓN DE BLOQUEOS (SEMÁFORO)
# ==========================================
def reservar_cupo_temporal(datos_cita):
    """Crea un evento PROVISORIO (Gris) para bloquear el horario en TU calendario."""
    service = conectar_calendario()
    if not service: 
        st.error("Error: No se pudo conectar
