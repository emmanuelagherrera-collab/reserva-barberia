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
import urllib.parse # Necesario para WhatsApp

# ==========================================
# 🔧 ZONA DE CONFIGURACIÓN
# ==========================================
# ⚠️ REEMPLAZA CON TUS DATOS REALES
CALENDAR_ID = "emmanuelagherrera@gmail.com"
CREDENTIALS_FILE = 'credentials.json'
URL_SHEETS = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSQsZwUWKZAbBMSbJoOAoZOS6ZqbBoFEYAoSOHBvV7amaOPPkXxEYnTnHAelBa-g_EzFibe6jDyvMuc/pub?output=csv"
MP_ACCESS_TOKEN = "APP_USR-3110718966988352-120714-d3a0dd0e9831c38237e3450cea4fc5ef-3044196256"

# 📍 CONFIGURACIÓN DEL LOCAL
UBICACION_LAT_LON = pd.DataFrame({'lat': [-33.5226], 'lon': [-70.5986]}) 
LINK_WHATSAPP = "https://wa.me/56912345678" 

ZONA_HORARIA = pytz.timezone('America/Santiago')

st.set_page_config(page_title="Reserva Estilo", page_icon="💈", layout="wide")

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
        # 🔥 LA SOLUCIÓN: Esto arregla el problema de "Precio" vs "precio"
        df.columns = df.columns.str.lower().str.strip()
        
        servicios = {}
        for _, row in df.iterrows():
            desc = row['descripcion'] if 'descripcion' in row else "Servicio profesional."
            
            # Ahora es seguro leer 'precio' porque forzamos minúsculas arriba
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
        # Esto nos ayudará si vuelve a fallar
        st.error(f"Error leyendo Excel: {e}")
        return {}

def conectar_calendario():
    try:
        # 1. Intentamos leer desde los Secretos de la Nube (Streamlit Cloud)
        if "google_credentials" in st.secrets:
            creds_dict = dict(st.secrets["google_credentials"])
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/calendar']
            )
            return build('calendar', 'v3', credentials=creds)
            
        # 2. Si falla, intentamos leer el archivo local (Tu PC)
        else:
            creds = service_account.Credentials.from_service_account_file(
                CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/calendar']
            )
            return build('calendar', 'v3', credentials=creds)
            
    except Exception as e:
        # Esto imprimirá el error en la nube si falla para que sepamos qué pasó
        print(f"Error conectando al calendario: {e}")
        return None

def sanitizar_input(texto):
    if not texto: return ""
    texto = str(texto).strip()
    return f"'{texto}" if texto.startswith(("=", "+", "-", "@")) else texto

def validar_datos(nombre, email, telefono):
    if not re.match(r"^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s\']{2,50}$", nombre): return False, "Nombre inválido."
    solo_numeros = re.sub(r"[^0-9]", "", telefono)
    if not (8 <= len(solo_numeros) <= 15): return False, "Teléfono inválido."
    if email and not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email): return False, "Email inválido."
    return True, ""

def obtener_bloques_disponibles(fecha, duracion):
    service = conectar_calendario()
    if not service: return []
    inicio_dia = datetime.combine(fecha, time.min)
    fin_dia = datetime.combine(fecha, time.max)
    inicio_utc = ZONA_HORARIA.localize(inicio_dia).astimezone(pytz.UTC).isoformat()
    fin_utc = ZONA_HORARIA.localize(fin_dia).astimezone(pytz.UTC).isoformat()
    events = service.events().list(calendarId=CALENDAR_ID, timeMin=inicio_utc, timeMax=fin_utc, singleEvents=True).execute().get('items', [])
    
    hora_act = ZONA_HORARIA.localize(datetime.combine(fecha, time(10, 0))) 
    hora_fin = ZONA_HORARIA.localize(datetime.combine(fecha, time(20, 0))) 
    
    bloques = []
    while hora_act + timedelta(minutes=duracion) <= hora_fin:
        fin_cand = hora_act + timedelta(minutes=duracion)
        choque = False
        for ev in events:
            start = ev['start'].get('dateTime'); end = ev['end'].get('dateTime')
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
    
    telefono_ws = LINK_WHATSAPP.replace("https://wa.me/", "").replace("/", "")
    link_cambio = generar_link_ws_dinamico(
        telefono_ws, 
        datos_cita['cliente'], 
        f"{datos_cita['fecha']} {datos_cita['hora']}", 
        datos_cita['servicio']
    )

    evento = {
        'summary': f"✅ {datos_cita['cliente']} - {datos_cita['servicio']}",
        'description': f"""
        ESTADO: CONFIRMADO
        -----------------------------------
        💰 Abono Web: ${datos_cita['abono']:,} (ID: {id_pago})
        ⚠️ PENDIENTE EN LOCAL: ${datos_cita['pendiente']:,}
        -----------------------------------
        PARA CAMBIAR TU HORA:
        Haz clic aquí para avisar por WhatsApp:
        {link_cambio}
        -----------------------------------
        Cliente: {datos_cita['cliente']}
        Tel: {datos_cita['tel']}
        Email: {datos_cita['email']}
        """,
        'attendees': [{'email': datos_cita['email']}], 
        'start': {'dateTime': dt_ini.isoformat()}, 'end': {'dateTime': dt_fin.isoformat()},
        'colorId': '11'
    }
    
    try: 
        service.events().insert(calendarId=CALENDAR_ID, body=evento, sendUpdates='all').execute()
        return True
    except: return False

def generar_link_pago(datos_reserva):
    if len(MP_ACCESS_TOKEN) < 10: return None, "⚠️ Error: Token inválido."
    
    try:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        referencia = empaquetar_datos(datos_reserva)
        
        titulo_item = f"Reserva: {datos_reserva['servicio']}"
        email_cliente = datos_reserva['email'] if "@" in datos_reserva['email'] else "test@user.com"

        # ⚠️ CAMBIO CLAVE: Poner tu URL real aquí cuando vayas a la nube
        # Si estás en local, usa localhost. Si vas a la nube, pon la de .app
        # url_base = "http://localhost:8501" 
        url_base = "https://reserva-barberia-9jzeauyq6n2eaosbgz6xec.streamlit.app/" 

        preference_data = {
            "items": [{"title": titulo_item, "quantity": 1, "unit_price": float(datos_reserva['abono']), "currency_id": "CLP"}],
            "payer": {"email": email_cliente},
            "external_reference": referencia,
            
            "back_urls": {
                "success": url_base,
                "failure": url_base,
                "pending": url_base
            },
            # ✅ En Producción (Nube) SÍ puedes usar auto_return
            "auto_return": "approved" 
        }
        
        result = sdk.preference().create(preference_data)
        
        if result["status"] not in [200, 201]:
             err_msg = result.get("response", {}).get("message", "Error desconocido")
             return None, f"MP Error: {err_msg}"
             
        return result["response"]["init_point"], None
        
    except Exception as e: return None, str(e)

# ==========================================
# 🔄 LÓGICA DE PAGO (Retorno)
# ==========================================
qp = st.query_params
if "status" in qp and qp["status"] == "approved":
    ref = qp.get("external_reference")
    pid = qp.get("payment_id")
    if ref:
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
                        Si necesitas cambiar la hora, busca el enlace en ese correo.
                        """)
                    
                    c_inicio, c_cambio = st.columns(2)
                    with c_inicio:
                        if st.button("🏠 Volver al Inicio", use_container_width=True):
                            st.query_params.clear()
                            st.rerun()
                    with c_cambio:
                        st.link_button("🔄 Cambio via WhatsApp", link_cambio_ui, type="secondary", use_container_width=True)
                    
                    st.stop()
                else: st.error("Error agendando, pero tu pago llegó. Contacta al local.")
    st.stop()

# ==========================================
# 🖥️ SIDEBAR
# ==========================================
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3504/3504100.png", width=60)
    st.subheader("Barbería Pro")

    if st.session_state.step > 1:
        st.divider()
        if st.button("⬅️ Volver al Inicio", type="secondary", use_container_width=True):
            resetear_proceso()
            st.rerun()

    st.divider()
    st.markdown("### ¿Ayuda?")
    st.link_button("💬 WhatsApp", LINK_WHATSAPP, type="primary", use_container_width=True)
    
    st.write("") 
    with st.container(border=True):
        st.markdown("**🕒 Horario**")
        st.caption("Lun - Sab")
        st.markdown(":green[**10:00 - 20:00**]")

    st.write(""); st.caption("📍 Av. Siempre Viva 123")
    st.map(UBICACION_LAT_LON, zoom=15, size=20, height=150, use_container_width=True)

# ==========================================
# 🖥️ CUERPO PRINCIPAL
# ==========================================
st.title("💈 Reserva tu Turno")
servicios_db = cargar_servicios()

if st.session_state.step == 1:
    st.subheader("Selecciona un servicio")
    
    if not servicios_db:
        st.warning("No se cargaron los servicios.")
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

elif st.session_state.step == 2:
    svc = st.session_state.datos_servicio
    st.info(f"""
    Estás reservando: **{st.session_state.servicio_seleccionado}**
    * Total: ${svc['precio_total']:,}
    * **A pagar ahora: ${svc['abono']:,}**
    * Pendiente: ${svc['pendiente']:,}
    """)
    
    col_cal, col_dat = st.columns([1, 1])
    
    with col_cal:
        st.subheader("1. Fecha y Hora")
        hoy = datetime.now(ZONA_HORARIA).date()
        fecha = st.date_input("Día", min_value=hoy, max_value=hoy+timedelta(days=30))
        
        bloques = []
        if fecha:
            with st.spinner("Buscando horas..."):
                bloques = obtener_bloques_disponibles(fecha, svc['duracion'])
            
            if not bloques:
                st.error("Sin horas.")
                hora = None
            else:
                hora = st.selectbox("Horas libres", bloques, placeholder="Elige...")

    with col_dat:
        st.subheader("2. Tus Datos")
        if 'hora' in locals() and hora:
            with st.form("confirmar"):
                nom = st.text_input("Nombre *")
                tel = st.text_input("Teléfono *")
                mail = st.text_input("Email *", help="Te llegará la invitación aquí")
                
                st.divider()
                st.markdown(f"Abono a pagar: :green[**${svc['abono']:,}**]")
                
                if st.form_submit_button("💳 Pagar Abono", type="primary", use_container_width=True):
                    ok, msg = validar_datos(nom, mail, tel)
                    if not ok: st.error(msg)
                    else:
                        datos = {
                            "fecha": str(fecha), "hora": hora,
                            "servicio": st.session_state.servicio_seleccionado,
                            "precio_total": svc['precio_total'],
                            "abono": svc['abono'], 
                            "pendiente": svc['pendiente'],
                            "duracion": svc['duracion'],
                            "cliente": nom, "email": mail, "tel": tel
                        }
                        link, err = generar_link_pago(datos)
                        if link: st.link_button("👉 IR A PAGAR", link, type="primary", use_container_width=True)
                        else: st.error(err)
