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

# ==========================================
# ⚙️ CONFIGURACIÓN DE VARIABLES
# ==========================================
st.set_page_config(page_title="Reserva Tu Hora", page_icon="tj", layout="wide")

# 1. DATOS FIJOS (Estos los dejamos aquí porque no están en tus secrets actuales)
# -------------------------------------------------------------------------
CALENDAR_TARGET_EMAIL = "emmanuelagherrera@gmail.com" 
MP_ACCESS_TOKEN = "APP_USR-3110718966988352-120714-d3a0dd0e9831c38237e3450cea4fc5ef-3044196256"
# -------------------------------------------------------------------------

SHEET_MENU_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSQsZwUWKZAbBMSbJoOAoZOS6ZqbBoFEYAoSOHBvV7amaOPPkXxEYnTnHAelBa-g_EzFibe6jDyvMuc/pub?output=csv"
LOCAL_COORDS = pd.DataFrame({'lat': [-33.5226], 'lon': [-70.5986]}) 
WHATSAPP_LINK_BASE = "https://wa.me/56912345678" 
TIMEZONE = pytz.timezone('America/Santiago')

# 2. CARGA DE SECRETOS (Solo Credenciales de Google)
# -------------------------------------------------------------------------
try:
    # Leemos la sección [google_credentials] tal como la tienes en secrets.toml
    if "google_credentials" in st.secrets:
        GOOGLE_JSON_CREDS = dict(st.secrets["google_credentials"])

        # Corrección de saltos de línea para la llave privada
        if "private_key" in GOOGLE_JSON_CREDS:
            GOOGLE_JSON_CREDS["private_key"] = GOOGLE_JSON_CREDS["private_key"].replace("\\n", "\n")
    else:
        st.error("⚠️ Falta la sección [google_credentials] en secrets.toml")
        st.stop()

except Exception as e:
    st.error(f"❌ Error leyendo secretos: {e}")
    st.stop()

# ==========================================
# 🎨 ESTILOS VISUALES (CSS)
# ==========================================
st.markdown("""
<style>
    .stButton button { width: 100%; border-radius: 8px; font-weight: 600; }
    .info-box { padding: 1rem; border-radius: 10px; background-color: #f0f2f6; margin-bottom: 1rem; }
    .price-tag { font-size: 1.2rem; font-weight: bold; color: #2e7d32; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 🧠 GESTIÓN DE SESIÓN (STATE)
# ==========================================
if 'step' not in st.session_state: st.session_state.step = 1
if 'selected_service' not in st.session_state: st.session_state.selected_service = None
if 'service_details' not in st.session_state: st.session_state.service_details = {}

def reiniciar_flujo():
    st.session_state.step = 1
    st.session_state.selected_service = None
    st.session_state.service_details = {}

# ==========================================
# 🔌 FUNCIONES DE INTEGRACIÓN (Backend)
# ==========================================

def get_calendar_service():
    """Conecta a Google Calendar usando las credenciales procesadas."""
    try:
        creds = service_account.Credentials.from_service_account_info(
            GOOGLE_JSON_CREDS, 
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Error de conexión con Google: {e}")
        return None

@st.cache_data(ttl=60)
def fetch_services_data():
    """Descarga y normaliza el menú de servicios desde Google Sheets."""
    try:
        df = pd.read_csv(SHEET_MENU_URL)
        df.columns = df.columns.str.lower().str.strip()
        
        inventory = {}
        for _, row in df.iterrows():
            desc = row['descripcion'] if 'descripcion' in row else "Sin descripción."
            precio = int(row['precio'])
            abono = int(row['abono']) if 'abono' in row and pd.notna(row['abono']) else precio
            
            inventory[row['servicio']] = {
                "duration_min": int(row['duracion_min']), 
                "total_price": precio,
                "deposit_required": abono,
                "balance_due": precio - abono,
                "description": desc
            }
        return inventory
    except Exception as e:
        st.error(f"Error leyendo la lista de precios: {e}")
        return {}

def check_availability(target_date, duration_minutes):
    """Calcula bloques libres basándose en eventos existentes."""
    service = get_calendar_service()
    if not service: return []
    
    day_start = datetime.combine(target_date, time.min)
    day_end = datetime.combine(target_date, time.max)
    
    utc_start = TIMEZONE.localize(day_start).astimezone(pytz.UTC).isoformat()
    utc_end = TIMEZONE.localize(day_end).astimezone(pytz.UTC).isoformat()
    
    events_result = service.events().list(
        calendarId=CALENDAR_TARGET_EMAIL, 
        timeMin=utc_start, 
        timeMax=utc_end, 
        singleEvents=True
    ).execute()
    events = events_result.get('items', [])
    
    current_slot = TIMEZONE.localize(datetime.combine(target_date, time(10, 0))) 
    close_time = TIMEZONE.localize(datetime.combine(target_date, time(20, 0))) 
    
    available_slots = []
    
    while current_slot + timedelta(minutes=duration_minutes) <= close_time:
        slot_end = current_slot + timedelta(minutes=duration_minutes)
        is_conflict = False
        
        for ev in events:
            if 'dateTime' not in ev['start']: continue
            ev_start = datetime.fromisoformat(ev['start']['dateTime']).astimezone(TIMEZONE)
            ev_end = datetime.fromisoformat(ev['end']['dateTime']).astimezone(TIMEZONE)
            
            if (current_slot < ev_end) and (slot_end > ev_start):
                is_conflict = True
                break
        
        if not is_conflict: 
            available_slots.append(current_slot.strftime("%H:%M"))
        
        current_slot += timedelta(minutes=30)
        
    return available_slots

def create_calendar_event(booking_data, payment_id):
    """Inserta el evento confirmado en el calendario."""
    service = get_calendar_service()
    if not service: return False

    date_obj = datetime.strptime(booking_data['date_str'], "%Y-%m-%d").date()
    h, m = map(int, booking_data['time_str'].split(":"))
    
    start_dt = TIMEZONE.localize(datetime.combine(date_obj, time(h, m)))
    end_dt = start_dt + timedelta(minutes=booking_data['duration'])
    
    clean_phone = WHATSAPP_LINK_BASE.replace("https://wa.me/", "").replace("/", "")
    msg = f"Hola, soy {booking_data['client_name']}. Quiero modificar mi reserva del {booking_data['date_str']}."
    wa_link = f"https://wa.me/{clean_phone}?text={urllib.parse.quote(msg)}"

    event_body = {
        'summary': f"✅ {booking_data['client_name']} - {booking_data['service_name']}",
        'description': f"""
        ESTADO: CONFIRMADO (Pago ID: {payment_id})
        -----------------------------------
        Cliente: {booking_data['client_name']}
        Email: {booking_data['client_email']}
        Tel: {booking_data['client_phone']}
        -----------------------------------
        💰 Pagado: ${booking_data['deposit']:,}
        ⚠️ PENDIENTE: ${booking_data['balance']:,}
        -----------------------------------
        [Link para contactar por cambio]({wa_link})
        """,
        'start': {'dateTime': start_dt.isoformat()}, 
        'end': {'dateTime': end_dt.isoformat()},
        'attendees': [{'email': booking_data['client_email']}],
        'colorId': '11'
    }
    
    try: 
        service.events().insert(calendarId=CALENDAR_TARGET_EMAIL, body=event_body, sendUpdates='all').execute()
        return True
    except Exception as e:
        print(f"Fallo al insertar evento: {e}")
        return False

# ==========================================
# 💳 UTILIDADES DE PAGO Y URL
# ==========================================
def encode_payload(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()

def decode_payload(token):
    try: return json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except: return None

def generate_payment_preference(booking_data):
    try:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        
        current_url = "https://reserva-barberia-9jzeauyq6n2eaosbgz6xec.streamlit.app/" 
        
        preference = {
            "items": [{
                "title": f"Reserva: {booking_data['service_name']}",
                "quantity": 1,
                "unit_price": float(booking_data['deposit']),
                "currency_id": "CLP"
            }],
            "payer": {"email": booking_data['client_email']},
            "external_reference": encode_payload(booking_data),
            "back_urls": {
                "success": current_url,
                "failure": current_url,
                "pending": current_url
            },
            "auto_return": "approved"
        }
        
        result = sdk.preference().create(preference)
        if result["status"] == 201:
            return result["response"]["init_point"], None
        return None, "Error creando preferencia en MercadoPago"
        
    except Exception as e: return None, str(e)

# ==========================================
# 🔄 MANEJO DE RETORNO DE PAGO
# ==========================================
qp = st.query_params
if "status" in qp and qp["status"] == "approved":
    ref_token = qp.get("external_reference")
    pay_id = qp.get("payment_id")
    
    if ref_token:
        data = decode_payload(ref_token)
        if data:
            with st.spinner("Confirmando tu cita en el calendario..."):
                if create_calendar_event(data, pay_id):
                    st.balloons()
                    st.success("✅ ¡Reserva Exitosa!")
                    st.markdown(f"""
                    <div class='info-box'>
                        <h4>Detalles de la Cita</h4>
                        <p><strong>Servicio:</strong> {data['service_name']}</p>
                        <p><strong>Fecha:</strong> {data['date_str']} a las {data['time_str']}</p>
                        <p><strong>Pendiente a pagar en local:</strong> ${data['balance']:,}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button("Volver al Inicio"):
                        st.query_params.clear()
                        st.rerun()
                    st.stop()
                else:
                    st.error("Pago recibido, pero hubo un error agendando. Guarda tu comprobante.")
    st.stop()

# ==========================================
# 🖥️ INTERFAZ DE USUARIO (Frontend)
# ==========================================

with st.sidebar:
    st.header("📍 Tu Barbería")
    if st.session_state.step > 1:
        if st.button("⬅️ Cancelar y Volver"):
            reiniciar_flujo()
            st.rerun()
    st.divider()
    st.map(LOCAL_COORDS, zoom=15, height=200)
    st.markdown("**Horario:** 10:00 - 20:00")
    st.link_button("Contactar por WhatsApp", WHATSAPP_LINK_BASE)

st.title("✂️ Reserva Online")
services = fetch_services_data()

if st.session_state.step == 1:
    st.subheader("Elige tu servicio")
    if not services:
        st.warning("No se pudo cargar el menú de servicios.")
    else:
        for name, details in services.items():
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                c1.markdown(f"**{name}**")
                c1.caption(f"{details['duration_min']} min • {details['description']}")
                c2.markdown(f"<div class='price-tag'>${details['total_price']:,}</div>", unsafe_allow_html=True)
                if c2.button("Agendar", key=f"btn_{name}"):
                    st.session_state.selected_service = name
                    st.session_state.service_details = details
                    st.session_state.step = 2
                    st.rerun()

elif st.session_state.step == 2:
    svc_name = st.session_state.selected_service
    svc_info = st.session_state.service_details
    st.markdown(f"### Reservando: **{svc_name}**")
    st.info(f"Abono requerido: ${svc_info['deposit_required']:,} (Saldo pendiente: ${svc_info['balance_due']:,})")
    
    col_date, col_form = st.columns(2)
    with col_date:
        st.subheader("1. Fecha")
        today = datetime.now(TIMEZONE).date()
        date_sel = st.date_input("Selecciona día", min_value=today, max_value=today+timedelta(days=20))
        slots = []
        if date_sel:
            with st.spinner("Verificando disponibilidad..."):
                slots = check_availability(date_sel, svc_info['duration_min'])
            if not slots:
                st.warning("No quedan horas disponibles para este día.")
            else:
                time_sel = st.selectbox("Horarios disponibles", slots)

    with col_form:
        st.subheader("2. Datos")
        if 'time_sel' in locals() and slots:
            with st.form("booking_form"):
                fname = st.text_input("Nombre Completo")
                femail = st.text_input("Correo Electrónico")
                fphone = st.text_input("Teléfono Móvil")
                submitted = st.form_submit_button("Ir al Pago 💳", type="primary")
                if submitted:
                    if len(fname) < 3 or "@" not in femail:
                        st.error("Por favor completa los datos correctamente.")
                    else:
                        booking_payload = {
                            "date_str": str(date_sel), "time_str": time_sel,
                            "service_name": svc_name, "duration": svc_info['duration_min'],
                            "deposit": svc_info['deposit_required'], "balance": svc_info['balance_due'],
                            "client_name": fname, "client_email": femail, "client_phone": fphone
                        }
                        link, err = generate_payment_preference(booking_payload)
                        if link: st.link_button("👉 FINALIZAR RESERVA", link, type="primary", use_container_width=True)
                        else: st.error(f"Error: {err}")
