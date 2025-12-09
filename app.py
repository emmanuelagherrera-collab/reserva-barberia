import streamlit as st
import pandas as pd
import mercadopago
import json
import base64
import urllib.parse
from datetime import datetime, timedelta, time
from google.oauth2 import service_account
from googleapiclient.discovery import build
import pytz
import re

# 1. CONFIGURACIÓN
st.set_page_config(page_title="Reserva Estilo", page_icon="💈", layout="wide")

# ==========================================
# 🛑 EL PORTERO (SALA DE LLEGADA)
# ==========================================
# Si el usuario llega con CUALQUIER parámetro en la URL, lo atrapamos aquí.
# No recargamos automáticamente. Le mostramos una pantalla de resultado.

qp = st.query_params

if "status" in qp or "external_reference" in qp or "collection_status" in qp:
    status = qp.get("status")
    
    # PANTALLA DE RESULTADO
    st.title("Proceso Finalizado 🏁")
    
    if status == "approved":
        # ... Lógica de éxito (reproducir tickets, guardar en calendario, etc) ...
        # (Aquí iría tu lógica de agendar_evento si el pago fue exitoso)
        st.success("✅ El pago fue aprobado.")
        st.info("Revisa tu correo para los detalles de la reserva.")
        
    else:
        # CASO: VOLVER AL SITIO / FALLO
        st.warning("⚠️ Volviste sin completar el pago o hubo un error.")
        st.write("No te preocupes, no se ha realizado ningún cobro.")

    # EL BOTÓN SALVAVIDAS
    # Este botón es el que limpia la URL manualmente. Es infalible.
    if st.button("🏠 Volver al Inicio (Limpiar)"):
        st.query_params.clear() # Borra la basura de la URL
        st.rerun()              # Recarga la app limpia
    
    st.stop() # 🛑 DETENEMOS TODO AQUÍ. El usuario no ve nada más hasta que presione el botón.

# ==========================================
# AQUI EMPIEZA TU APP NORMAL (Limpia)
# ==========================================
# Si el código pasa del st.stop() de arriba, es porque la URL está limpia.

# TUS VARIABLES
CALENDAR_ID = "emmanuelagherrera@gmail.com"
CREDENTIALS_FILE = 'credentials.json'
URL_SHEETS = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSQsZwUWKZAbBMSbJoOAoZOS6ZqbBoFEYAoSOHBvV7amaOPPkXxEYnTnHAelBa-g_EzFibe6jDyvMuc/pub?output=csv"
MP_ACCESS_TOKEN = "APP_USR-3110718966988352-120714-d3a0dd0e9831c38237e3450cea4fc5ef-3044196256"
UBICACION_LAT_LON = pd.DataFrame({'lat': [-33.5226], 'lon': [-70.5986]}) 
LINK_WHATSAPP = "https://wa.me/56912345678" 
ZONA_HORARIA = pytz.timezone('America/Santiago')

# TUS FUNCIONES NECESARIAS
def empaquetar_datos(datos):
    return base64.urlsafe_b64encode(json.dumps(datos).encode()).decode()

@st.cache_data(ttl=60)
def cargar_servicios():
    try:
        df = pd.read_csv(URL_SHEETS)
        df.columns = df.columns.str.lower().str.strip()
        servicios = {}
        for _, row in df.iterrows():
            servicios[row['servicio']] = {
                "duracion": int(row['duracion_min']), 
                "precio_total": int(row['precio']),
                "abono": int(row['abono']),
                "pendiente": int(row['precio']) - int(row['abono']),
                "descripcion": row.get('descripcion', "Servicio profesional.")
            }
        return servicios
    except: return {}

def conectar_calendario():
    try:
        if "google_credentials" in st.secrets:
            creds = service_account.Credentials.from_service_account_info(
                dict(st.secrets["google_credentials"]), scopes=['https://www.googleapis.com/auth/calendar'])
        else:
            creds = service_account.Credentials.from_service_account_file(
                CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/calendar'])
        return build('calendar', 'v3', credentials=creds)
    except: return None

def obtener_bloques(fecha, duracion):
    service = conectar_calendario()
    if not service: return []
    ini = ZONA_HORARIA.localize(datetime.combine(fecha, time.min)).astimezone(pytz.UTC).isoformat()
    fin = ZONA_HORARIA.localize(datetime.combine(fecha, time.max)).astimezone(pytz.UTC).isoformat()
    events = service.events().list(calendarId=CALENDAR_ID, timeMin=ini, timeMax=fin, singleEvents=True).execute().get('items', [])
    act = ZONA_HORARIA.localize(datetime.combine(fecha, time(10, 0)))
    fin_jornada = ZONA_HORARIA.localize(datetime.combine(fecha, time(20, 0)))
    bloques = []
    while act + timedelta(minutes=duracion) <= fin_jornada:
        cand_fin = act + timedelta(minutes=duracion)
        choque = False
        for ev in events:
            if not ev.get('start'): continue
            ev_ini = datetime.fromisoformat(ev['start']['dateTime']).astimezone(ZONA_HORARIA)
            ev_fin = datetime.fromisoformat(ev['end']['dateTime']).astimezone(ZONA_HORARIA)
            if (act < ev_fin) and (cand_fin > ev_ini): choque = True; break
        if not choque: bloques.append(act.strftime("%H:%M"))
        act += timedelta(minutes=30)
    return bloques

def generar_link_pago(datos):
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
    url_base = "https://reserva-barberia-9jzeauyq6n2eaosbgz6xec.streamlit.app/"
    
    # PREFERENCIA
    pref = {
        "items": [{"title": f"Reserva: {datos['servicio']}", "quantity": 1, "unit_price": float(datos['abono']), "currency_id": "CLP"}],
        "payer": {"email": datos['email'] if "@" in datos['email'] else "cliente@test.com"},
        "external_reference": empaquetar_datos(datos),
        "back_urls": {"success": url_base, "failure": url_base, "pending": url_base},
        "auto_return": "approved"
    }
    res = sdk.preference().create(pref)
    return res["response"]["init_point"] if res["status"] in [200, 201] else None

def validar_datos(n, e, t):
    return True, "" # Simplificado para la prueba

# INTERFAZ NORMAL
if 'step' not in st.session_state: st.session_state.step = 1
if 'datos_servicio' not in st.session_state: st.session_state.datos_servicio = {}

def reset(): st.session_state.step = 1

st.markdown("<style>.stButton button {width:100%; font-weight:bold;}</style>", unsafe_allow_html=True)

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3504/3504100.png", width=60)
    st.subheader("Barbería Pro")
    if st.session_state.step > 1:
        if st.button("⬅️ Volver"): reset(); st.rerun()

st.title("💈 Reserva tu Turno")
servicios = cargar_servicios()

if st.session_state.step == 1:
    if not servicios: st.warning("Cargando servicios...")
    for nom, info in servicios.items():
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"### {nom}\n⏱️ {info['duracion']} min • Reserva: **${info['abono']:,}**")
            c2.markdown(f"Total: ${info['precio_total']:,}")
            if c2.button("Reservar", key=nom):
                st.session_state.servicio_seleccionado = nom
                st.session_state.datos_servicio = info
                st.session_state.step = 2
                st.rerun()

elif st.session_state.step == 2:
    svc = st.session_state.datos_servicio
    st.info(f"Reservando: **{st.session_state.servicio_seleccionado}**")
    c1, c2 = st.columns(2)
    with c1:
        fecha = st.date_input("Fecha", min_value=datetime.now(ZONA_HORARIA).date(), max_value=datetime.now(ZONA_HORARIA).date()+timedelta(days=30))
        bloques = obtener_bloques(fecha, svc['duracion'])
        hora = st.selectbox("Hora", bloques) if bloques else None
        if not bloques: st.warning("Sin horas disponibles.")
    with c2:
        with st.form("pago"):
            n, e, t = st.text_input("Nombre"), st.text_input("Email"), st.text_input("Teléfono")
            if st.form_submit_button("💳 Pagar Abono", type="primary"):
                if n and e and t and hora:
                    datos = {"fecha":str(fecha),"hora":hora,"cliente":n,"email":e,"tel":t,"servicio":st.session_state.servicio_seleccionado,"abono":svc['abono'],"precio_total":svc['precio_total'],"pendiente":svc['pendiente'],"duracion":svc['duracion']}
                    link = generar_link_pago(datos)
                    if link: st.link_button("👉 Ir a MercadoPago", link, type="primary")
                else: st.error("Faltan datos")
