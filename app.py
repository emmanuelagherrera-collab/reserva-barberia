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
# --- 1. NUEVOS IMPORTS PARA CORREO ---
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

# VERIFICACIÓN DE ESTADO DE CUENTA (KILL SWITCH)
try:
    if st.secrets["sistema"]["estado_cuenta"] != "ACTIVO":
        st.error("⚠️ SERVICIO SUSPENDIDO")
        st.warning("La suscripción de este local se encuentra vencida. Por favor contacte a soporte para reactivar el sistema de reservas.")
        st.stop() # Esto detiene la ejecución del resto del código
except:
    pass # Si no existe la variable, asumimos que está activo

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
# 📧 2. FUNCIÓN DE CORREO (NUEVA)
# ==========================================
def enviar_correo_confirmacion(datos):
    """Envía un correo HTML bonito al cliente."""
    # Verificar credenciales
    if "email" not in st.secrets:
        return False

    remitente = st.secrets["email"]["usuario"]
    password = st.secrets["email"]["password"]
    destinatario = datos['email']
    
    # Generar Link de Modificación para el correo
    tel_ws = LINK_WHATSAPP.replace("https://wa.me/", "").replace("/", "")
    link_cambio = generar_link_ws_dinamico(tel_ws, datos['cliente'], f"{datos['fecha']} {datos['hora']}", datos['servicio'])

    # Construir el Mensaje
    msg = MIMEMultipart()
    # TRUCO: Nombre personalizado para que se vea profesional
    msg['From'] = f"Barbería Pro (Reserva) <{remitente}>"
    msg['To'] = destinatario
    msg['Subject'] = f"✅ Reserva Confirmada: {datos['servicio']} - {datos['fecha']}"

    cuerpo_html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <div style="max-width: 600px; margin: auto; border: 1px solid #ddd; border-radius: 10px; overflow: hidden;">
            <div style="background-color: #2e7d32; color: white; padding: 20px; text-align: center;">
                <h1 style="margin: 0;">¡Reserva Confirmada!</h1>
            </div>
            <div style="padding: 20px;">
                <p>Hola <strong>{datos['cliente']}</strong>,</p>
                <p>Tu turno ha sido agendado correctamente. Aquí tienes los detalles:</p>
                
                <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <p>📅 <strong>Fecha:</strong> {datos['fecha']}</p>
                    <p>⏰ <strong>Hora:</strong> {datos['hora']}</p>
                    <p>💇 <strong>Servicio:</strong> {datos['servicio']}</p>
                    <p>💰 <strong>Abono Pagado:</strong> ${datos['abono']:,}</p>
                    <p>🏠 <strong>Saldo Pendiente:</strong> <span style="color: #d32f2f; font-weight: bold;">${datos['pendiente']:,}</span></p>
                </div>

                <p>📍 <strong>Ubicación:</strong> Av. Siempre Viva 123, Santiago.</p>
                
                <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
                
                <p style="text-align: center;">
                    <strong>¿Necesitas cambiar o cancelar?</strong><br>
                    Avísanos directamente por WhatsApp haciendo clic abajo:
                </p>
                <div style="text-align: center; margin-top: 15px;">
                    <a href="{link_cambio}" style="background-color: #25D366; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">
                        Contactar por WhatsApp
                    </a>
                </div>
            </div>
        </div>
      </body>
    </html>
    """
    
    msg.attach(MIMEText(cuerpo_html, 'html'))

    # Enviar
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(remitente, password)
        server.sendmail(remitente, destinatario, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Error enviando correo: {e}")
        return False

# ==========================================
# 🚦 GESTIÓN DE BLOQUEOS (SEMÁFORO)
# ==========================================
def reservar_cupo_temporal(datos_cita):
    """Crea un evento PROVISORIO (Gris) para bloquear el horario."""
    service = conectar_calendario()
    if not service: 
        st.error("Error: No se pudo conectar al calendario.")
        return None
    
    fecha = datetime.strptime(datos_cita['fecha'], "%Y-%m-%d").date()
    h, m = map(int, datos_cita['hora'].split(":"))
    dt_ini = ZONA_HORARIA.localize(datetime.combine(fecha, time(h, m)))
    dt_fin = dt_ini + timedelta(minutes=datos_cita['duracion'])
    
    evento = {
        'summary': f"⏳ RESERVANDO - {datos_cita['cliente']}",
        'description': f"Esperando pago... (El cupo expira en 5 min)",
        'start': {'dateTime': dt_ini.isoformat()}, 'end': {'dateTime': dt_fin.isoformat()},
        'colorId': '8'
    }
    try: 
        ev = service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
        return ev['id']
    except Exception as e:
        st.error("Lo sentimos, no pudimos bloquear el horario. Intente nuevamente.")
        print(f"DEBUG Error Calendar: {e}")
        return None

def confirmar_cupo_final(event_id, datos_cita, id_pago):
    """Transforma el evento temporal en uno CONFIRMADO (Rojo)."""
    service = conectar_calendario()
    if not service: return False
    
    tel_ws = LINK_WHATSAPP.replace("https://wa.me/", "").replace("/", "")
    link_cambio = generar_link_ws_dinamico(tel_ws, datos_cita['cliente'], f"{datos_cita['fecha']} {datos_cita['hora']}", datos_cita['servicio'])
    
    evento_update = {
        'summary': f"✅ {datos_cita['cliente']} - {datos_cita['servicio']}",
        'description': f"""ESTADO: CONFIRMADO\n💰 Abono: ${datos_cita['abono']:,} (ID: {id_pago})\n⚠️ Pendiente: ${datos_cita['pendiente']:,}\n\nPara cambios: {link_cambio}\nTel: {datos_cita['tel']}\nEmail: {datos_cita['email']}""",
        'colorId': '11'
    }
    try:
        service.events().patch(calendarId=CALENDAR_ID, eventId=event_id, body=evento_update).execute()
        return True
    except Exception as e:
        print(f"DEBUG Error Confirmar: {e}")
        return False

def liberar_cupo(event_id):
    """Borra el evento temporal si se acaba el tiempo o cancelan."""
    service = conectar_calendario()
    if not service or not event_id: return
    try: service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    except: pass

# --- VERSIÓN TRUCADA (PRUEBA) ---
def verificar_estado_manual(ref_codificada):
    time_lib.sleep(1) 
    return True, "ID-PRUEBA-SIMULADA-123" 

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
    
    try:
        events_result = service.events().list(calendarId=CALENDAR_ID, timeMin=inicio_utc, timeMax=fin_utc, singleEvents=True).execute()
        events = events_result.get('items', [])
    except:
        return []
    
    hora_act = ZONA_HORARIA.localize(datetime.combine(fecha, time(10, 0))) 
    hora_fin = ZONA_HORARIA.localize(datetime.combine(fecha, time(20, 0))) 
    
    ahora = datetime.now(ZONA_HORARIA)
    margen_minimo = timedelta(minutes=60) 
    
    bloques = []
    while hora_act + timedelta(minutes=duracion) <= hora_fin:
        fin_cand = hora_act + timedelta(minutes=duracion)
        
        if fecha == ahora.date():
            if hora_act < (ahora + margen_minimo):
                hora_act += timedelta(minutes=30)
                continue

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
        'start': {'dateTime': dt_ini.isoformat()}, 'end': {'dateTime': dt_fin.isoformat()},
        'colorId': '11'
    }
    
    try: 
        service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
        return True
    except: return False

def generar_link_pago(datos_reserva):
    if len(MP_ACCESS_TOKEN) < 10: return None, "⚠️ Error: Token inválido."
    
    try:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        referencia = empaquetar_datos(datos_reserva)
        
        titulo_item = f"Reserva: {datos_reserva['servicio']}"
        email_cliente = datos_reserva['email'] if "@" in datos_reserva['email'] else "test@user.com"

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
                    
                    # 📧 3. ENVIAR CORREO AQUÍ
                    enviar_correo_confirmacion(data)
                    
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
                        ℹ️ **Importante:** Hemos enviado un correo a **{data['email']}** con los detalles.
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
# 🤖 SONDEO AUTOMÁTICO (MINIMALISTA)
# ==========================================
@st.fragment(run_every=5)
def panel_espera_pago():
    if not st.session_state.get("proceso_pago"): return

    # 1. Chequeo de tiempo
    start = st.session_state.get("start_time_pago")
    if not start: return
    
    segundos = (datetime.now() - start).total_seconds()
    limite = 300 
    restante = int(limite - segundos)
    
    status_container = st.empty()
    
    # CASO A: Se acabó el tiempo
    if restante <= 0:
        status_container.error("⏳ Tiempo agotado.")
        liberar_cupo(st.session_state.get("event_id_temp"))
        time_lib.sleep(2)
        st.session_state.proceso_pago = False
        st.rerun()
        return

    # CASO B: Consulta Silenciosa
    mins = restante // 60
    secs = restante % 60
    status_container.info(f"⏳ Esperando pago... ({mins}:{secs:02d})")
    
    # Consultamos
    pagado, id_pago = verificar_estado_manual(st.session_state.get("ref_pago"))
    
    if pagado:
        # Intentamos confirmar
        exito_calendar = confirmar_cupo_final(st.session_state.get("event_id_temp"), st.session_state.get("datos_backup"), id_pago)
        
        if exito_calendar:
            st.session_state.exito_final = True
            st.session_state.id_comprobante = id_pago
            st.session_state.proceso_pago = False
            
            # 📧 3. ENVIAR CORREO TAMBIÉN EN EL FLUJO AUTOMÁTICO
            enviar_correo_confirmacion(st.session_state.get("datos_backup"))
            
            st.rerun()
        else:
            st.error("Pago recibido, pero hubo un problema agendando. Toma captura de pantalla.")

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

#--NUEVO CODIGO
    st.divider() # Una línea para separar visualmente
    with st.sidebar.expander("🔐 Administración"):
        pass_admin = st.text_input("Contraseña", type="password")
        # Asegúrate de haber configurado esta clave en secrets.toml
        if "admin_password" in st.secrets and pass_admin == st.secrets["admin_password"]:
            st.success("Acceso Concedido")
            st.markdown("### Panel de Control")
            # Reemplaza con tus links reales
            st.link_button("📊 Editar Servicios (Excel)", "https://docs.google.com/spreadsheets/d/1bDuKqoqvjZ4b-2i007s11AfMWQ5lmk4tbHFqVM350Ao/edit?gid=0#gid=0", icon="📝")
            st.link_button("📁 Carpeta de Imágenes (Drive)", "https://drive.google.com/...", icon="📷")
            st.info("💡 Tip: Recuerda que los cambios en Excel tardan unos segundos en reflejarse.")
#--FIN NUEVO CODIGO

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
    
    if 'proceso_pago' not in st.session_state: st.session_state.proceso_pago = False
    if 'exito_final' not in st.session_state: st.session_state.exito_final = False

    # --- ESCENARIO: ÉXITO FINAL (Ticket) ---
    if st.session_state.exito_final:
        st.balloons()
        data = st.session_state.datos_backup
        st.success("✅ ¡RESERVA CONFIRMADA!")
        
        with st.container(border=True):
            st.markdown(f"""
            ### 🎫 Ticket de Atención
            * 🗓️ **Fecha:** {data['fecha']} - {data['hora']}
            * 💇 **Servicio:** {data['servicio']}
            * 👤 **Cliente:** {data['cliente']}
            * 💳 **Comprobante:** {st.session_state.id_comprobante}
            """)
            if st.button("Inicio"): resetear_proceso()
        st.stop()

    # --- ESCENARIO: FORMULARIO (Si no estamos pagando aún) ---
    if not st.session_state.proceso_pago:
        st.info(f"Reservando: **{st.session_state.servicio_seleccionado}** (Abono: ${svc['abono']:,})")
        
        c_cal, c_dat = st.columns(2)
        with c_cal:
            fecha = st.date_input("Fecha", min_value=datetime.now(ZONA_HORARIA).date())
            bloques = obtener_bloques_disponibles(fecha, svc['duracion']) if fecha else []
            hora = st.selectbox("Hora", bloques) if bloques else None
            if not bloques: st.warning("Sin cupos.")

        with c_dat:
            with st.form("pre_pago"):
                nom = st.text_input("Nombre")
                tel = st.text_input("Teléfono")
                mail = st.text_input("Email")
                btn_pagar = st.form_submit_button("💳 Ir a Pagar", type="primary", use_container_width=True)
                
                if btn_pagar:
                    ok, msg = validar_datos(nom, mail, tel)
                    if ok and hora:
                        datos = {
                            "fecha": str(fecha), "hora": hora, "servicio": st.session_state.servicio_seleccionado,
                            "precio_total": svc['precio_total'], "abono": svc['abono'], "pendiente": svc['pendiente'],
                            "duracion": svc['duracion'], "cliente": nom, "email": mail, "tel": tel
                        }
                        
                        with st.spinner("Bloqueando agenda y generando pago..."):
                            # 1. SEMÁFORO ROJO: Creamos el evento temporal en Calendar
                            ev_id = reservar_cupo_temporal(datos)
                            
                            if ev_id:
                                # 2. GENERAMOS LINK MP
                                link, ref = generar_link_pago(datos)
                                if link:
                                    st.session_state.update({
                                        "proceso_pago": True, 
                                        "link_pago": link, 
                                        "ref_pago": ref,
                                        "datos_backup": datos, 
                                        "event_id_temp": ev_id,
                                        "start_time_pago": datetime.now()
                                    })
                                    st.rerun()
                                else:
                                    liberar_cupo(ev_id) 
                                    st.error("Error conectando con el banco.")
                            else:
                                pass
                    else:
                        st.error(msg or "Selecciona una hora.")

    # --- ESCENARIO: ESPERANDO PAGO (El Loop) ---
    else:
        st.info("⚠️ **Tu cupo está reservado por 5 minutos.**")
        st.write("Por favor, realiza el pago en la pestaña que se abrirá.")
        
        st.link_button(f"👉 Pagar ${svc['abono']:,} en MercadoPago", st.session_state.link_pago, type="primary", use_container_width=True)
        
        st.divider()
        
        # AQUÍ INVOCAMOS AL FRAGMENTO AUTOMÁTICO
        panel_espera_pago()
        
        # Botón de escape manual
        if st.button("Cancelar y Liberar Hora"):
            liberar_cupo(st.session_state.event_id_temp)
            st.session_state.proceso_pago = False
            st.rerun()
