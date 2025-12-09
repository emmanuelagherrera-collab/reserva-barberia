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
st.set_page_config(page_title="Reserva Estilo", page_icon="💈", layout="wide")

CALENDAR_ID = "emmanuelagherrera@gmail.com"
CREDENTIALS_FILE = 'credentials.json'
URL_SHEETS = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSQsZwUWKZAbBMSbJoOAoZOS6ZqbBoFEYAoSOHBvV7amaOPPkXxEYnTnHAelBa-g_EzFibe6jDyvMuc/pub?output=csv"
MP_ACCESS_TOKEN = "APP_USR-3110718966988352-120714-d3a0dd0e9831c38237e3450cea4fc5ef-3044196256"
UBICACION_LAT_LON = pd.DataFrame({'lat': [-33.5226], 'lon': [-70.5986]}) 
LINK_WHATSAPP = "https://wa.me/56912345678" 
ZONA_HORARIA = pytz.timezone('America/Santiago')

# ==========================================
# 🎨 ESTILOS CSS (ORIGINALES)
# ==========================================
st.markdown("""
<style>
    .stButton button { width: 100%; border-radius: 8px; font-weight: bold; }
    div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"] { background-color: transparent; }
    .price-abono { font-size: 1.4rem; font-weight: 800; color: #2e7d32; text-align: right; }
    .price-total { font-size: 0.9rem; color: #757575; text-align: right; text-decoration: none; }
    .badge-pago { background-color: #e8f5e9; color: #2e7d32; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8rem;}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 🧠 FUNCIONES BACKEND
# ==========================================
def empaquetar_datos(datos):
    return base64.urlsafe_b64encode(json.dumps(datos).encode()).decode()

def desempaquetar_datos(token):
    try: return json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except: return None

def generar_link_ws_dinamico(tel, nombre, fecha, serv):
    msg = urllib.parse.quote(f"Hola, soy {nombre}. Reserva: {fecha} - {serv}. Necesito modificarla.")
    return f"https://wa.me/{tel}?text={msg}"

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

def agendar_evento_confirmado(datos, id_pago):
    service = conectar_calendario()
    if not service: return False
    fecha = datetime.strptime(datos['fecha'], "%Y-%m-%d").date()
    h, m = map(int, datos['hora'].split(":"))
    dt_ini = ZONA_HORARIA.localize(datetime.combine(fecha, time(h, m)))
    dt_fin = dt_ini + timedelta(minutes=datos['duracion'])
    ws_link = generar_link_ws_dinamico(LINK_WHATSAPP.replace("https://wa.me/", ""), datos['cliente'], f"{datos['fecha']} {datos['hora']}", datos['servicio'])
    body = {
        'summary': f"✅ {datos['cliente']} - {datos['servicio']}",
        'description': f"Abono Web: ${datos['abono']:,} (ID: {id_pago})\nPENDIENTE: ${datos['pendiente']:,}\nTel: {datos['tel']}\nCambios: {ws_link}",
        'start': {'dateTime': dt_ini.isoformat()}, 'end': {'dateTime': dt_fin.isoformat()},
        'attendees': [{'email': datos['email']}],
        'colorId': '11'
    }
    try: service.events().insert(calendarId=CALENDAR_ID, body=body).execute(); return True
    except: return False

def generar_link_pago(datos):
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
    # ⚠️ ASEGURATE QUE ESTA URL SEA CORRECTA
    url_base = "https://reserva-barberia-9jzeauyq6n2eaosbgz6xec.streamlit.app/"
    
    pref = {
        "items": [{"title": f"Reserva: {datos['servicio']}", "quantity": 1, "unit_price": float(datos['abono']), "currency_id": "CLP"}],
        "payer": {"email": datos['email'] if "@" in datos['email'] else "cliente@test.com"},
        "external_reference": empaquetar_datos(datos),
        "back_urls": {"success": url_base, "failure": url_base, "pending": url_base},
        "auto_return": "approved"
    }
    res = sdk.preference().create(pref)
    return res["response"]["init_point"] if res["status"] in [200, 201] else None

def obtener_bloques_disponibles(fecha, duracion):
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

def validar_datos(nombre, email, telefono):
    if not re.match(r"^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s\']{2,50}$", nombre): return False, "Nombre inválido."
    solo_numeros = re.sub(r"[^0-9]", "", telefono)
    if not (8 <= len(solo_numeros) <= 15): return False, "Teléfono inválido."
    if email and not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email): return False, "Email inválido."
    return True, ""

# ==========================================
# 🔄 LÓGICA DE RETORNO INVISIBLE (AQUÍ ESTÁ LA SOLUCIÓN)
# ==========================================
# Esto se ejecuta ANTES de pintar la interfaz. 
# Si hay basura en la URL, la limpia y recarga para mostrar la app normal.
qp = st.query_params

if "status" in qp or "external_reference" in qp:
    status = qp.get("status")
    
    # CASO 1: ÉXITO (Ticket) - Mantenemos la visual de éxito original
    if status == "approved":
        ref = qp.get("external_reference")
        pid = qp.get("payment_id")
        data = desempaquetar_datos(ref)
        if data:
            with st.spinner("Registrando reserva..."):
                if agendar_evento_confirmado(data, pid):
                    st.balloons()
                    st.success("✅ ¡Reserva Asegurada!")
                    tel_ws = LINK_WHATSAPP.replace("https://wa.me/", "").replace("/", "")
                    link_cambio_ui = generar_link_ws_dinamico(tel_ws, data['cliente'], f"{data['fecha']} {data['hora']}", data['servicio'])
                    
                    with st.container(border=True):
                        st.markdown(f"""
                        ### 🎫 Ticket de Atención
                        * 🗓️ **Cuándo:** {data['fecha']} a las {data['hora']}
                        * 💇 **Servicio:** {data['servicio']}
                        * 💳 **Abono Pagado:** ${data['abono']:,}
                        * 🏠 **Saldo Pendiente:** :red[**${data['pendiente']:,}**]
                        ---
                        ℹ️ **Importante:** Te enviamos una invitación a tu correo (**{data['email']}**). 
                        """)
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("🏠 Inicio", use_container_width=True):
                                st.query_params.clear()
                                st.rerun()
                        with c2: st.link_button("🔄 Modificar", link_cambio_ui, use_container_width=True)
                    st.stop() # Detenemos aquí para mostrar solo el ticket

    # CASO 2: "VOLVER AL SITIO" (Status null/failure)
    # Aquí está la clave: Limpiamos la URL y recargamos silenciosamente.
    # El usuario verá la pantalla de inicio (Paso 1) tal como querías.
    else:
        st.query_params.clear()
        st.rerun()

# ==========================================
# 🖥️ VISUAL ORIGINAL (TU CÓDIGO INTACTO)
# ==========================================
if 'step' not in st.session_state: st.session_state.step = 1
if 'servicio_seleccionado' not in st.session_state: st.session_state.servicio_seleccionado = None
if 'datos_servicio' not in st.session_state: st.session_state.datos_servicio = {}

def resetear_proceso():
    st.session_state.step = 1
    st.session_state.servicio_seleccionado = None
    st.session_state.datos_servicio = {}

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3504/3504100.png", width=60)
    st.subheader("Barbería Pro")
    if st.session_state.step > 1:
        st.divider()
        if st.button("⬅️ Volver", type="secondary", use_container_width=True):
            resetear_proceso()
            st.rerun()
    st.divider()
    st.link_button("💬 Ayuda WhatsApp", LINK_WHATSAPP, type="primary", use_container_width=True)
    st.write("") 
    with st.container(border=True):
        st.markdown("**🕒 Horario**\nLun - Sab\n:green[**10:00 - 20:00**]")
    st.write(""); st.caption("📍 Av. Siempre Viva 123")
    st.map(UBICACION_LAT_LON, zoom=15, size=20, height=150, use_container_width=True)

st.title("💈 Reserva tu Turno")
servicios_db = cargar_servicios()

# >>> PASO 1: SELECCIÓN <<<
if st.session_state.step == 1:
    st.subheader("Selecciona un servicio")
    if not servicios_db: st.warning("Cargando servicios...")
    else:
        for nombre, info in servicios_db.items():
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(f"### {nombre}")
                    st.caption(f"⏱️ {info['duracion']} min • {info['descripcion']}")
                    st.markdown(f"<span class='badge-pago'>Reserva con ${info['abono']:,}</span>", unsafe_allow_html=True)
                with c2:
                    st.markdown(f"<div class='price-total'>Total: ${info['precio_total']:,}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='price-abono'>Abono: ${info['abono']:,}</div>", unsafe_allow_html=True)
                    if st.button("Reservar", key=f"btn_{nombre}", use_container_width=True):
                        st.session_state.servicio_seleccionado = nombre
                        st.session_state.datos_servicio = info
                        st.session_state.step = 2
                        st.rerun()

# >>> PASO 2: FORMULARIO Y PAGO AUTOMÁTICO <<<
elif st.session_state.step == 2:
    svc = st.session_state.datos_servicio
    st.info(f"Reservando: **{st.session_state.servicio_seleccionado}** (Abono: ${svc['abono']:,})")
    
    c1, c2 = st.columns(2)
    with c1:
        hoy = datetime.now(ZONA_HORARIA).date()
        fecha = st.date_input("Fecha", min_value=hoy, max_value=hoy+timedelta(days=30))
        bloques = obtener_bloques_disponibles(fecha, svc['duracion']) if fecha else []
        hora = st.selectbox("Hora", bloques) if bloques else None
        if not bloques: st.error("Sin disponibilidad.")
    with c2:
        with st.form("form_final"):
            nom = st.text_input("Nombre *")
            tel = st.text_input("Teléfono *")
            mail = st.text_input("Email *")
            submitted = st.form_submit_button("💳 Confirmar y Pagar Abono", type="primary", use_container_width=True)
            
            if submitted:
                ok, msg = validar_datos(nom, mail, tel)
                if not ok: st.error(msg)
                else:
                    datos = {"fecha": str(fecha), "hora": hora, "servicio": st.session_state.servicio_seleccionado, "precio_total": svc['precio_total'], "abono": svc['abono'], "pendiente": svc['pendiente'], "duracion": svc['duracion'], "cliente": nom, "email": mail, "tel": tel}
                    link = generar_link_pago(datos)
                    if link:
                        st.success("✅ Procesando... Redirigiendo a MercadoPago")
                        st.markdown(f'<meta http-equiv="refresh" content="0;url={link}">', unsafe_allow_html=True)
                        st.link_button("👉 Si no redirige autom., haz clic aquí", link, type="primary", use_container_width=True)
                    else: st.error("Error al generar pago.")
