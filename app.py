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

# ==========================================
# 🔧 ZONA DE CONFIGURACIÓN
# ==========================================
CALENDAR_ID = "emmanuelagherrera@gmail.com"
CREDENTIALS_FILE = 'credentials.json'
URL_SHEETS = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSQsZwUWKZAbBMSbJoOAoZOS6ZqbBoFEYAoSOHBvV7amaOPPkXxEYnTnHAelBa-g_EzFibe6jDyvMuc/pub?output=csv"
MP_ACCESS_TOKEN = "APP_USR-3110718966988352-120714-d3a0dd0e9831c38237e3450cea4fc5ef-3044196256"
UBICACION_LAT_LON = pd.DataFrame({'lat': [-33.5226], 'lon': [-70.5986]}) 
LINK_WHATSAPP = "https://wa.me/56912345678" 
ZONA_HORARIA = pytz.timezone('America/Santiago')

st.set_page_config(page_title="Reserva Estilo", page_icon="💈", layout="wide")

# ==========================================
# 🧠 FUNCIONES BACKEND
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

def obtener_bloques_disponibles(fecha, duracion):
    service = conectar_calendario()
    if not service: return []
    inicio_utc = ZONA_HORARIA.localize(datetime.combine(fecha, time.min)).astimezone(pytz.UTC).isoformat()
    fin_utc = ZONA_HORARIA.localize(datetime.combine(fecha, time.max)).astimezone(pytz.UTC).isoformat()
    events = service.events().list(calendarId=CALENDAR_ID, timeMin=inicio_utc, timeMax=fin_utc, singleEvents=True).execute().get('items', [])
    
    hora_act = ZONA_HORARIA.localize(datetime.combine(fecha, time(10, 0))) 
    hora_fin = ZONA_HORARIA.localize(datetime.combine(fecha, time(20, 0))) 
    bloques = []
    
    while hora_act + timedelta(minutes=duracion) <= hora_fin:
        fin_cand = hora_act + timedelta(minutes=duracion)
        choque = False
        for ev in events:
            start = ev['start'].get('dateTime')
            end = ev['end'].get('dateTime')
            if not start: continue
            ev_start = datetime.fromisoformat(start).astimezone(ZONA_HORARIA)
            ev_end = datetime.fromisoformat(end).astimezone(ZONA_HORARIA)
            if (hora_act < ev_end) and (fin_cand > ev_start):
                choque = True; break
        if not choque: bloques.append(hora_act.strftime("%H:%M"))
        hora_act += timedelta(minutes=30)
    return bloques

def agendar_evento_confirmado(datos_cita, id_pago):
    service = conectar_calendario()
    fecha = datetime.strptime(datos_cita['fecha'], "%Y-%m-%d").date()
    h, m = map(int, datos_cita['hora'].split(":"))
    dt_ini = ZONA_HORARIA.localize(datetime.combine(fecha, time(h, m)))
    dt_fin = dt_ini + timedelta(minutes=datos_cita['duracion'])
    
    link_cambio = generar_link_ws_dinamico(LINK_WHATSAPP.replace("https://wa.me/", ""), datos_cita['cliente'], f"{datos_cita['fecha']} {datos_cita['hora']}", datos_cita['servicio'])
    evento = {
        'summary': f"✅ {datos_cita['cliente']} - {datos_cita['servicio']}",
        'description': f"Abono Web: ${datos_cita['abono']:,} (ID: {id_pago})\nPENDIENTE: ${datos_cita['pendiente']:,}\nTel: {datos_cita['tel']}\nCambios: {link_cambio}",
        'attendees': [{'email': datos_cita['email']}], 
        'start': {'dateTime': dt_ini.isoformat()}, 'end': {'dateTime': dt_fin.isoformat()},
        'colorId': '11'
    }
    try: 
        service.events().insert(calendarId=CALENDAR_ID, body=evento, sendUpdates='all').execute()
        return True
    except: return False

def generar_link_pago(datos):
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
    ref = empaquetar_datos(datos)
    # ⚠️ URL FIX: Asegúrate que esta sea tu URL base limpia
    url_base = "https://reserva-barberia-9jzeauyq6n2eaosbgz6xec.streamlit.app/" 
    
    pref = {
        "items": [{"title": f"Reserva: {datos['servicio']}", "quantity": 1, "unit_price": float(datos['abono']), "currency_id": "CLP"}],
        "payer": {"email": datos['email'] if "@" in datos['email'] else "cliente@test.com"},
        "external_reference": ref,
        "back_urls": {"success": url_base, "failure": url_base, "pending": url_base},
        "auto_return": "approved"
    }
    res = sdk.preference().create(pref)
    return res["response"]["init_point"] if res["status"] in [200, 201] else None

# ==========================================
# 🔄 LÓGICA DE RETORNO (PRIORITARIA)
# ==========================================
# Gestiona el regreso de MercadoPago antes de pintar nada
qp = st.query_params

if "external_reference" in qp:
    status = str(qp.get("status"))
    pid = qp.get("payment_id")
    ref_data = desempaquetar_datos(qp.get("external_reference"))

    # CASO 1: ÉXITO (status=approved)
    if status == "approved" and ref_data:
        if agendar_evento_confirmado(ref_data, pid):
            st.balloons()
            st.success("✅ ¡Reserva Exitosa!")
            st.markdown(f"**Cliente:** {ref_data['cliente']} | **Fecha:** {ref_data['fecha']} {ref_data['hora']}")
            st.info(f"Te enviamos un correo a {ref_data['email']}")
            if st.button("Hacer otra reserva"):
                st.query_params.clear()
                st.rerun()
            st.stop()
        else:
            st.error("Error agendando en Google Calendar (El pago sí se recibió).")
            st.stop()

    # CASO 2: "VOLVER AL SITIO", CANCELADO O NULL
    # Si el status es 'null' (texto), failure, o rejected, restauramos la sesión.
    elif status in ["null", "failure", "rejected"] or pid == "null":
        if ref_data:
            # Restauramos los datos en session_state para que el usuario no pierda todo
            st.session_state.step = 2
            st.session_state.servicio_seleccionado = ref_data['servicio']
            st.session_state.datos_servicio = {
                'servicio': ref_data['servicio'], 'precio_total': ref_data['precio_total'],
                'abono': ref_data['abono'], 'pendiente': ref_data['pendiente'],
                'duracion': ref_data['duracion'], 'descripcion': "Servicio recuperado"
            }
            # Opcional: Recuperar inputs si quieres (requiere lógica extra en widgets), 
            # pero con esto ya vuelves a la pantalla de pago.
            
            # Limpiamos la URL para quitar ?status=null... y recargamos
            st.query_params.clear()
            st.rerun()

# ==========================================
# 🖥️ INTERFAZ DE USUARIO
# ==========================================
if 'step' not in st.session_state: st.session_state.step = 1

with st.sidebar:
    st.subheader("Barbería Pro")
    if st.session_state.step > 1:
        if st.button("⬅️ Volver al Inicio"):
            st.session_state.clear()
            st.rerun()

servicios_db = cargar_servicios()

# PANTALLA 1: SELECCIÓN
if st.session_state.step == 1:
    st.title("💈 Selecciona tu Servicio")
    for nombre, info in servicios_db.items():
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"### {nombre}\n⏱️ {info['duracion']} min")
            c2.markdown(f"**Total: ${info['precio_total']:,}**")
            if c2.button(f"Reservar (${info['abono']:,})", key=nombre):
                st.session_state.servicio_seleccionado = nombre
                st.session_state.datos_servicio = info
                st.session_state.step = 2
                st.rerun()

# PANTALLA 2: FECHA Y PAGO
elif st.session_state.step == 2:
    svc = st.session_state.datos_servicio
    st.title(f"📅 Agendando: {st.session_state.servicio_seleccionado}")
    
    col_a, col_b = st.columns(2)
    
    with col_a:
        hoy = datetime.now(ZONA_HORARIA).date()
        fecha = st.date_input("Fecha", min_value=hoy, max_value=hoy+timedelta(days=30))
        bloques = obtener_bloques_disponibles(fecha, svc['duracion'])
        hora = st.selectbox("Hora Disponibles", bloques) if bloques else None
        if not bloques: st.warning("No hay horas para esta fecha.")

    with col_b:
        with st.form("confirmar"):
            nombre = st.text_input("Tu Nombre")
            email = st.text_input("Email")
            tel = st.text_input("Teléfono")
            
            if st.form_submit_button("💳 Ir a Pagar", type="primary"):
                if nombre and email and tel and hora:
                    datos = {
                        "fecha": str(fecha), "hora": hora, "cliente": nombre, "email": email, "tel": tel,
                        "servicio": svc['servicio'], "precio_total": svc['precio_total'],
                        "abono": svc['abono'], "pendiente": svc['pendiente'], "duracion": svc['duracion']
                    }
                    link = generar_link_pago(datos)
                    if link:
                        st.link_button("👉 Click para Pagar en MercadoPago", link, type="primary")
                else:
                    st.error("Faltan datos.")
